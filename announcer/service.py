"""What the bot does, with Discord kept at arm's length.

Guilds, members and channels are used only through the few attributes read here,
and the client only has to hand out partial messageables, so the whole
announce → sign up → follow up cycle runs in the tests without a gateway.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
import discord

from . import render
from .catalog import CUSTOM_KEY, Activity, Catalog
from .errors import UserError
from .i18n import permission_name, t
from .store import Event, GuildConfig, Signup, Store
from .timeparse import parse_when

log = logging.getLogger(__name__)

# Seconds between two announcements by the same member: a second press of a button
# that pings the whole server should be a decision, not a double click.
ANNOUNCE_COOLDOWN = 15.0
# Seconds between two manual reminders of the same activity.
REMIND_COOLDOWN = 60.0
# Seconds between two edits of the same announcement. Discord keeps a separate, much
# tighter and unpublished allowance for editing messages older than an hour, so the
# roster of an announcement made well ahead is redrawn in bigger batches.
EDIT_SPACING = 1.0
OLD_EDIT_SPACING = 15.0
OLD_MESSAGE_AGE = 3600
# A reminder that would fire within a minute of the announcement is just noise.
REMINDER_MIN_LEAD = 60

# What the bot needs in a channel to announce there. Replying to its own
# announcement (every follow-up does) is what takes Read Message History.
REQUIRED_PERMISSIONS = ("view_channel", "send_messages", "embed_links", "read_message_history")

# What a request to Discord fails with when the problem is Discord or the network,
# as opposed to a bug here. discord.py lets connection errors through unwrapped.
NETWORK_ERRORS = (discord.HTTPException, aiohttp.ClientError, OSError)

EventView = Callable[[Event, Catalog, Sequence[Signup]], discord.ui.View]


@dataclass(frozen=True)
class Draft:
    """What a caller typed into the form."""

    when: str = ""
    location: str = ""
    gear: str = ""
    notes: str = ""
    # Custom announcements only.
    title: str = ""
    body: str = ""


@dataclass(frozen=True)
class Precheck:
    activity: Activity
    config: GuildConfig
    channel: Any
    # Why the configured ping would reach nobody, already translated. Empty when it works.
    ping_problem: str


@dataclass(frozen=True)
class Announced:
    event: Event
    url: str
    # Things the caller should know: no ping went out, the time was not understood...
    notes: tuple[str, ...]


@dataclass(frozen=True)
class Edited:
    event: Event
    notes: tuple[str, ...]


def zone(name: str) -> tzinfo:
    if name == "UTC":
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def _permissions(channel: Any, member: Any) -> discord.Permissions:
    resolve = getattr(channel, "permissions_for", None)
    return resolve(member) if resolve else discord.Permissions.none()


def _is_admin(member: Any) -> bool:
    permissions = member.guild_permissions
    return bool(permissions.administrator or permissions.manage_guild)


def _holds_any(member: Any, role_ids: Sequence[int]) -> bool:
    return any(role.id in role_ids for role in member.roles)


def _permission_list(lang: str, flags: Sequence[str]) -> str:
    return ", ".join(f"**{permission_name(lang, flag)}**" for flag in flags)


def cannot_post(lang: str, channel: str, missing: Sequence[str], *, check: bool = False) -> str:
    """What to say about a channel where the bot lacks ``missing``.

    Discord grants nothing in a channel the bot cannot see, so every permission comes
    back missing there; naming them all would hide the one thing to fix.
    """
    if "view_channel" in missing:
        key = "config.check.hidden" if check else "error.bot_hidden"
        return t(lang, key, channel=channel, perm=permission_name(lang, "view_channel"))
    key = "config.check.missing" if check else "error.bot_perms"
    return t(lang, key, channel=channel, perms=_permission_list(lang, missing))


def _is_permanent(exc: BaseException) -> bool:
    """Whether Discord said "no" (a 4xx) rather than "not now" (a 5xx, a rate limit, no network)."""
    return isinstance(exc, discord.HTTPException) and 400 <= exc.status < 500 and exc.status != 429


def _reason(exc: BaseException) -> str:
    if isinstance(exc, discord.HTTPException):
        return render.clip(exc.text or f"HTTP {exc.status}", 120)
    return render.clip(str(exc) or type(exc).__name__, 120)


class Redrawer:
    """Keeps the announcements on screen in step with the database.

    At most one edit per announcement is in flight; requests that arrive meanwhile
    are folded into a single edit afterwards. Right after an @everyone ping dozens
    of people sign up within seconds: an edit per click would run into Discord's
    rate limit, and edits racing each other could leave a stale roster on screen.

    It does not retry. Whether an announcement is up to date is recorded in the
    database (``Event.is_stale``), and whoever owns this asks again later.
    """

    def __init__(
        self, redraw: Callable[[int], Awaitable[None]], spacing: Callable[[int], float]
    ) -> None:
        self._redraw = redraw
        self._spacing = spacing
        self._dirty: set[int] = set()
        self._tasks: dict[int, asyncio.Task[None]] = {}

    def request(self, event_id: int) -> None:
        self._dirty.add(event_id)
        if event_id not in self._tasks:
            self._tasks[event_id] = asyncio.create_task(
                self._run(event_id), name=f"redraw-event-{event_id}"
            )

    async def _run(self, event_id: int) -> None:
        try:
            while event_id in self._dirty:
                self._dirty.discard(event_id)
                try:
                    await self._redraw(event_id)
                except Exception:
                    log.exception("Redrawing the announcement of event %s failed", event_id)
                await asyncio.sleep(self._spacing(event_id))
        finally:
            del self._tasks[event_id]

    async def drain(self) -> None:
        """Wait until every pending redraw has been sent."""
        while self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)


class Service:
    def __init__(
        self,
        client: Any,
        store: Store,
        catalog: Catalog,
        *,
        event_view: EventView,
        clock: Callable[[], float] = time.time,
        edit_spacing: float | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.catalog = catalog
        self._event_view = event_view
        self._clock = clock
        # None: by the age of the announcement. The tests pass 0.
        self._fixed_spacing = edit_spacing
        self._redraws = Redrawer(self._redraw, self._edit_spacing)
        # One caller action per activity at a time: two officers pressing "Starting now"
        # together must not both call the group.
        self._locks: dict[int, asyncio.Lock] = {}
        self._announced: dict[tuple[int, int], float] = {}
        self._reminded: dict[int, float] = {}

    async def drain(self) -> None:
        await self._redraws.drain()

    def resume(self) -> None:
        """Pick up what a previous run left unfinished. Call once at startup."""
        dropped = self.store.purge_unsent()
        if dropped:
            log.info("Dropped %d event(s) whose announcement never reached Discord", dropped)
        self.redraw_stale()

    def redraw_stale(self) -> None:
        """Redraw every announcement that does not show its latest state: an edit
        Discord refused or the network dropped, or one cut short by a restart."""
        for event in self.store.stale_events():
            self._redraws.request(event.id)

    # --- who may do what ---------------------------------------------------------

    def announcer_roles(self, guild: Any, config: GuildConfig) -> tuple[int, ...]:
        """The configured announcer roles that still exist. Roles deleted in Discord are
        ignored: left in, the last one going would lock out everyone but the admins."""
        return tuple(
            role_id for role_id in config.announcer_role_ids if guild.get_role(role_id) is not None
        )

    def can_announce(self, guild: Any, member: Any, config: GuildConfig) -> bool:
        """Whether ``member``, who pressed a panel button, may announce.

        Until announcer roles are picked, the panel's channel is the gate: whoever can see
        a panel may use it, so a panel in an officers' channel is for the officers.
        """
        if _is_admin(member):
            return True
        roles = self.announcer_roles(guild, config)
        return not roles or _holds_any(member, roles)

    def can_manage(self, guild: Any, member: Any, event: Event) -> bool:
        """Whether ``member`` may use the caller tools of ``event``: its caller, and
        whoever could have announced it from the same panel."""
        if member.id == event.author_id or _is_admin(member):
            return True
        roles = self.announcer_roles(guild, self.store.get_config(guild.id))
        if roles:
            return _holds_any(member, roles)
        # No roles picked: the panel decides, as it did for the caller. Announcements made
        # before panels were recorded, or whose panel's channel is gone, are left to their
        # caller and the admins.
        panel_id = event.panel_channel_id
        panel = guild.get_channel_or_thread(panel_id) if panel_id is not None else None
        return panel is not None and _permissions(panel, member).view_channel

    def panel_is_public(self, guild: Any, config: GuildConfig, channel: Any) -> bool:
        """Whether a panel in ``channel`` lets the whole server announce: @everyone can
        see the channel and no announcer roles narrow it down."""
        if self.announcer_roles(guild, config):
            return False
        return _permissions(channel, guild.default_role).view_channel

    def missing_permissions(self, guild: Any, channel: Any) -> list[str]:
        """The permissions the bot lacks to announce in ``channel``."""
        permissions = _permissions(channel, guild.me)
        needed = list(REQUIRED_PERMISSIONS)
        if isinstance(channel, discord.Thread):
            needed[needed.index("send_messages")] = "send_messages_in_threads"
        return [flag for flag in needed if not getattr(permissions, flag)]

    def ping_problem(self, guild: Any, config: GuildConfig, channel: Any) -> str:
        """Why the configured ping would notify nobody in ``channel``; empty if it works."""
        lang = config.language
        if config.ping == "none":
            return ""
        mention_all = _permissions(channel, guild.me).mention_everyone
        permission = permission_name(lang, "mention_everyone")
        if config.ping == "role":
            role = guild.get_role(config.ping_role_id) if config.ping_role_id else None
            if role is None:
                return t(lang, "warn.no_ping_role")
            if role.mentionable or mention_all:
                return ""
            return t(
                lang, "warn.no_ping", ping=role.mention, channel=channel.mention, perm=permission
            )
        if mention_all:
            return ""
        return t(
            lang, "warn.no_ping", ping=f"`@{config.ping}`", channel=channel.mention, perm=permission
        )

    def target_channel(self, guild: Any, config: GuildConfig, origin: Any) -> Any:
        """Where an announcement goes: the configured channel, else where the button was."""
        if config.channel_id is None:
            return origin
        channel = guild.get_channel_or_thread(config.channel_id)
        if channel is None:
            raise UserError(t(config.language, "error.channel_gone"))
        return channel

    def health(self, guild: Any, config: GuildConfig, origin: Any) -> list[str]:
        """One line per thing an admin should know before the first announcement."""
        lang = config.language
        try:
            channel = self.target_channel(guild, config, origin)
        except UserError as exc:
            return [f"❌ {exc}"]
        missing = self.missing_permissions(guild, channel)
        if missing:
            lines = [cannot_post(lang, channel.mention, missing, check=True)]
        else:
            lines = [t(lang, "config.check.ok", channel=channel.mention)]
        if config.ping == "role" and (
            not config.ping_role_id or guild.get_role(config.ping_role_id) is None
        ):
            lines.append(t(lang, "config.check.no_ping_role"))
        # Unseen, the channel also "lacks" the ping permission: that is the same problem.
        elif "view_channel" not in missing and self.ping_problem(guild, config, channel):
            permission = permission_name(lang, "mention_everyone")
            lines.append(t(lang, "config.check.no_ping", perm=permission))
        return lines

    # --- announcing ----------------------------------------------------------------

    def precheck(self, guild: Any, member: Any, origin: Any, key: str) -> Precheck:
        """Everything that can be refused before the caller fills in the form."""
        config = self.store.get_config(guild.id)
        lang = config.language
        activity = self.catalog.get(key)
        if activity is None:
            raise UserError(t(lang, "error.activity_gone"))
        channel = self.target_channel(guild, config, origin)

        if not self.can_announce(guild, member, config):
            roles = ", ".join(f"<@&{role_id}>" for role_id in self.announcer_roles(guild, config))
            raise UserError(t(lang, "error.not_announcer", roles=roles))

        missing = self.missing_permissions(guild, channel)
        if missing:
            raise UserError(cannot_post(lang, channel.mention, missing))

        last = self._announced.get((guild.id, member.id), -math.inf)
        wait = ANNOUNCE_COOLDOWN - (self._clock() - last)
        if wait > 0:
            raise UserError(t(lang, "error.cooldown", seconds=math.ceil(wait)))

        return Precheck(activity, config, channel, self.ping_problem(guild, config, channel))

    async def announce(
        self, guild: Any, member: Any, origin: Any, key: str, draft: Draft
    ) -> Announced:
        # Checked again: minutes may have passed since the button was pressed.
        pre = self.precheck(guild, member, origin, key)
        activity, config, channel = pre.activity, pre.config, pre.channel
        lang = config.language
        now = self._clock()

        when_text = " ".join(draft.when.split())
        when_kind, starts_at = self._read_when(when_text, config, now, optional=activity.custom)
        ping, ping_role_id = (config.ping, config.ping_role_id)
        if pre.ping_problem:
            # Writing "@everyone" without being able to ping would just look broken.
            ping, ping_role_id = "none", None
        voice = getattr(getattr(member, "voice", None), "channel", None)

        event = self.store.create_event(
            guild_id=guild.id,
            channel_id=channel.id,
            panel_channel_id=getattr(origin, "id", None),
            activity=activity.key,
            language=lang,
            emoji=activity.emoji,
            title=(draft.title.strip() if activity.custom else "") or activity.label(lang),
            message=draft.body.strip() if activity.custom else activity.message(lang),
            color=activity.color,
            icon=activity.icon,
            roles=activity.roles,
            ping=ping,
            ping_role_id=ping_role_id,
            author_id=member.id,
            author_name=member.display_name,
            when_kind=when_kind,
            when_text=when_text,
            starts_at=starts_at,
            location=draft.location.strip(),
            gear=draft.gear.strip(),
            notes=draft.notes.strip(),
            voice_channel_id=voice.id if voice else None,
            status="open",
            remind_at=self._remind_at(config, starts_at, now, activity.roles),
            created_at=int(now),
        )
        try:
            message = await channel.send(
                content=render.announcement_content(event),
                embed=render.announcement_embed(event, [], self.catalog),
                view=self._event_view(event, self.catalog, []),
                allowed_mentions=render.ping_mentions(event.ping, event.ping_role_id),
            )
        except NETWORK_ERRORS as exc:
            self.store.delete_event(event.id)
            raise UserError(
                t(lang, "error.send_failed", channel=channel.mention, reason=_reason(exc))
            ) from exc
        except BaseException:
            # A bug, or the bot being shut down mid-send: nothing was announced either way.
            self.store.delete_event(event.id)
            raise
        event = self.store.update_event(event.id, message_id=message.id)
        if event.is_stale:
            # Someone signed up between Discord showing the message and telling us its id.
            self._redraws.request(event.id)
        self._announced[(guild.id, member.id)] = self._clock()

        notes = [t(lang, "announce.note.no_ping")] if pre.ping_problem else []
        return Announced(event, message.jump_url, (*notes, *self._time_notes(event)))

    @staticmethod
    def _time_notes(event: Event) -> list[str]:
        """What the caller should know about the time they typed."""
        notes = []
        if event.when_kind == "text":
            notes.append(t(event.language, "announce.note.time_text"))
        if event.remind_at:
            notes.append(t(event.language, "announce.note.reminder", ts=event.remind_at))
        return notes

    def _read_when(
        self, text: str, config: GuildConfig, now: float, *, optional: bool
    ) -> tuple[str, int | None]:
        """(kind, start timestamp) for what the caller typed in the "When" box."""
        if optional and not text:
            # An activity with no time is happening now; a notice with no time has none.
            return "none", None
        when = parse_when(text, now=datetime.fromtimestamp(now, UTC), tz=zone(config.timezone))
        return when.kind, int(when.at.timestamp()) if when.at else None

    def _remind_at(
        self, config: GuildConfig, starts_at: int | None, now: float, roles: Sequence[str]
    ) -> int | None:
        # The automatic reminder goes to the people signed up, so it needs sign-ups.
        if starts_at is None or not roles or config.reminder_minutes <= 0:
            return None
        remind_at = starts_at - config.reminder_minutes * 60
        return remind_at if remind_at - now >= REMINDER_MIN_LEAD else None

    # --- signing up ------------------------------------------------------------------

    def _open_event(self, guild_id: int, event_id: int) -> Event:
        event = self.store.get_event(event_id)
        if event is None or event.guild_id != guild_id:
            raise UserError(t(self.store.get_config(guild_id).language, "error.event_gone"))
        if not event.is_open:
            raise UserError(t(event.language, "error.event_closed"))
        return event

    def signup(self, guild_id: int, event_id: int, user_id: int, role: str) -> None:
        event = self._open_event(guild_id, event_id)
        if role not in event.roles:
            raise UserError(t(event.language, "error.event_gone"))
        self._check_room(event, user_id, role)
        if self.store.set_signup(event.id, user_id, role, at=self._clock()) != role:
            self._roster_changed(event.id)

    def _check_room(self, event: Event, user_id: int, role: str) -> None:
        """Refuse a position that already has all the players it takes.

        The button of a full position is greyed out, but a screen that has not caught
        up with the last sign-up still offers it.
        """
        spot = self.catalog.roles.get(role)
        if spot is None or spot.limit is None:
            return
        holders = [s.user_id for s in self.store.signups(event.id) if s.role == role]
        if user_id in holders or len(holders) < spot.limit:
            return
        lang = event.language
        name = f"{spot.emoji} **{spot.label(lang)}**"
        if spot.limit == 1:
            raise UserError(t(lang, "error.spot_taken", spot=name, who=f"<@{holders[0]}>"))
        raise UserError(t(lang, "error.spot_full", spot=name))

    def leave(self, guild_id: int, event_id: int, user_id: int) -> None:
        event = self._open_event(guild_id, event_id)
        if self.store.remove_signup(event.id, user_id):
            self._roster_changed(event.id)

    def _roster_changed(self, event_id: int) -> None:
        self.store.touch(event_id)
        self._redraws.request(event_id)

    # --- caller tools ------------------------------------------------------------------

    def managed(self, guild: Any, member: Any, event_id: int) -> Event:
        """The open event ``member`` is about to manage, or a UserError saying why not."""
        event = self._open_event(guild.id, event_id)
        if not self.can_manage(guild, member, event):
            raise UserError(t(event.language, "error.not_manager"))
        return event

    def _lock(self, event_id: int) -> asyncio.Lock:
        return self._locks.setdefault(event_id, asyncio.Lock())

    async def _acting_on(self, guild: Any, member: Any, event_id: int) -> Event:
        """``managed``, for an action about to post in the channel: the announcement
        it replies to must still be there. Call with the event's lock held."""
        event = self.managed(guild, member, event_id)
        if not await self._announcement_exists(event):
            raise UserError(t(event.language, "error.announcement_deleted"))
        return event

    async def _announcement_exists(self, event: Event) -> bool:
        """False, and the event closed, once a moderator has deleted the announcement."""
        if event.message_id is None:
            return False
        channel = self.client.get_partial_messageable(event.channel_id, guild_id=event.guild_id)
        try:
            await channel.get_partial_message(event.message_id).fetch()
        except discord.NotFound:
            self._announcement_deleted(event.id)
            return False
        except NETWORK_ERRORS:
            log.debug("Could not look at the announcement of event %s", event.id, exc_info=True)
        return True

    def _announcement_deleted(self, event_id: int) -> None:
        """Someone deleted the announcement: the activity is off, and nothing is left to draw."""
        event = self.store.get_event(event_id)
        if event is None:
            return
        if event.is_open:
            event = self.store.update_event(event.id, status="cancelled", remind_at=None)
        self.store.mark_drawn(event.id, event.revision)

    def _change(self, event_id: int, *, content: bool = False, **changes: Any) -> Event:
        """Store a change that shows in the announcement, and have it redrawn."""
        event = self.store.change_event(event_id, content=content, **changes)
        self._redraws.request(event_id)
        return event

    async def remind(self, guild: Any, member: Any, event_id: int) -> Event:
        """Repeat the ping of the announcement."""
        async with self._lock(event_id):
            event = self.managed(guild, member, event_id)
            wait = REMIND_COOLDOWN - (self._clock() - self._reminded.get(event.id, -math.inf))
            if wait > 0:
                raise UserError(t(event.language, "error.remind_cooldown", seconds=math.ceil(wait)))
            event = await self._acting_on(guild, member, event_id)
            ping = render.ping_text(event.ping, event.ping_role_id)
            text = f"{ping} {render.reminder_text(event, self._clock())}".strip()
            await self._reply(event, [text], render.ping_mentions(event.ping, event.ping_role_id))
            # Counted only now: a reminder Discord refused did not "just go out".
            self._reminded[event.id] = self._clock()
            return event

    # The three below post first and change the status after. If Discord refuses the
    # message, the activity stays as it was and the caller can press the button again.

    async def start(self, guild: Any, member: Any, event_id: int) -> Event:
        async with self._lock(event_id):
            event = await self._acting_on(guild, member, event_id)
            if event.status != "open":
                raise UserError(t(event.language, "error.already_started"))
            await self._reply_roster(event, render.start_text(event))
            return self._change(event.id, status="started", remind_at=None)

    async def finish(self, guild: Any, member: Any, event_id: int) -> Event:
        async with self._lock(event_id):
            event = await self._acting_on(guild, member, event_id)
            await self._reply(event, [render.finish_text(event)], render.NO_MENTIONS)
            return self._change(event.id, status="finished", remind_at=None)

    async def cancel(self, guild: Any, member: Any, event_id: int) -> Event:
        async with self._lock(event_id):
            event = await self._acting_on(guild, member, event_id)
            await self._reply_roster(event, render.cancel_text(event))
            return self._change(event.id, status="cancelled", remind_at=None)

    async def edit(self, guild: Any, member: Any, event_id: int, draft: Draft) -> Edited:
        async with self._lock(event_id):
            before = await self._acting_on(guild, member, event_id)
            config = self.store.get_config(guild.id)
            now = self._clock()
            custom = before.activity == CUSTOM_KEY

            changes: dict[str, Any] = {
                "location": draft.location.strip(),
                "gear": draft.gear.strip(),
                "notes": draft.notes.strip(),
            }
            if custom:
                changes["title"] = draft.title.strip() or before.title
                changes["message"] = draft.body.strip()
            when_text = " ".join(draft.when.split())
            retimed = when_text != before.when_text
            if retimed:
                # Only a changed time is read again: re-reading an untouched "in 30m"
                # would silently push the start back by however long ago it was typed.
                when_kind, starts_at = self._read_when(when_text, config, now, optional=custom)
                changes.update(
                    when_kind=when_kind,
                    when_text=when_text,
                    starts_at=starts_at,
                    remind_at=self._remind_at(config, starts_at, now, before.roles),
                )

            after = replace(before, **changes)
            content = render.announcement_content(after) != render.announcement_content(before)
            event = self._change(before.id, content=content, **changes)

            notes = self._time_notes(event) if retimed else []
            if (event.when_text, event.location) != (before.when_text, before.location):
                # People planned around the old time or place: tell the ones signed up.
                try:
                    await self._reply_roster(event, render.update_text(event, now))
                except UserError as exc:
                    # The announcement itself is updated; only this notice did not go out.
                    notes.append(f"⚠️ {exc}")
            return Edited(event, tuple(notes))

    # --- automatic reminders -------------------------------------------------------------

    async def run_due_reminders(self) -> None:
        for due in self.store.due_reminders(self._clock()):
            # Claimed before sending, so that a crash half-way cannot ping twice. And only
            # if it is still this reminder: while an earlier one of the batch was being
            # sent, the caller may have moved this activity or called it off.
            if due.remind_at is None or not self.store.claim_reminder(due.id, due.remind_at):
                continue
            try:
                again = await self._auto_remind(due.id)
            except Exception:
                log.exception("The automatic reminder of event %s failed", due.id)
                continue
            if again:
                self.store.restore_reminder(due.id, due.remind_at)

    async def _auto_remind(self, event_id: int) -> bool:
        """Remind the people signed up. True: it could not be sent just now, try again."""
        async with self._lock(event_id):
            event = self.store.get_event(event_id)
            if event is None or event.status != "open" or event.starts_at is None:
                return False
            if event.remind_at is not None:
                return False  # the caller just moved it: a new reminder is scheduled
            if event.starts_at <= self._clock():
                return False  # too late: the bot was offline, or Discord unreachable
            if not self.store.signups(event.id):
                return False  # nobody to remind
            if not await self._announcement_exists(event):
                return False
            try:
                await self._reply_roster(event, render.auto_reminder_text(event, self._clock()))
            except UserError as exc:
                log.warning("The automatic reminder of event %s did not go out: %s", event.id, exc)
                # Missing permissions will not fix themselves in the next 20 seconds.
                return exc.__cause__ is not None and not _is_permanent(exc.__cause__)
            return False

    # --- talking to Discord ------------------------------------------------------------------

    async def _reply(
        self, event: Event, messages: Sequence[str], mentions: discord.AllowedMentions
    ) -> None:
        """Post follow-up messages, the first one as a reply to the announcement."""
        channel = self.client.get_partial_messageable(event.channel_id, guild_id=event.guild_id)
        reference = None
        if event.message_id is not None:
            reference = discord.MessageReference(
                message_id=event.message_id,
                channel_id=event.channel_id,
                guild_id=event.guild_id,
                fail_if_not_exists=False,
            )
        try:
            for index, content in enumerate(messages):
                await channel.send(
                    content,
                    reference=reference if index == 0 else None,
                    allowed_mentions=mentions,
                )
        except NETWORK_ERRORS as exc:
            raise UserError(
                t(
                    event.language,
                    "error.send_failed",
                    channel=f"<#{event.channel_id}>",
                    reason=_reason(exc),
                )
            ) from exc

    async def _reply_roster(self, event: Event, text: str) -> None:
        """Post a follow-up that mentions everyone signed up."""
        users = [signup.user_id for signup in self.store.signups(event.id)]
        mentions = render.ROSTER_MENTIONS if users else render.NO_MENTIONS
        await self._reply(event, render.with_mentions(text, users), mentions)

    def _edit_spacing(self, event_id: int) -> float:
        if self._fixed_spacing is not None:
            return self._fixed_spacing
        event = self.store.get_event(event_id)
        old = event is not None and self._clock() - event.created_at >= OLD_MESSAGE_AGE
        return OLD_EDIT_SPACING if old else EDIT_SPACING

    async def _redraw(self, event_id: int) -> None:
        """Make one announcement show what the database holds. On failure the event
        simply stays stale, and `redraw_stale` asks again."""
        event = self.store.get_event(event_id)
        if event is None or not event.is_stale or event.message_id is None:
            # Nothing to do, or the announcement itself is still on its way to Discord.
            return
        channel = self.client.get_partial_messageable(event.channel_id, guild_id=event.guild_id)
        try:
            signups = self.store.signups(event.id)
            fields: dict[str, Any] = {
                "embed": render.announcement_embed(event, signups, self.catalog),
                "view": self._event_view(event, self.catalog, signups) if event.is_open else None,
                "allowed_mentions": render.ping_mentions(event.ping, event.ping_role_id),
            }
            if event.content_revision > event.drawn_revision:
                fields["content"] = render.announcement_content(event)
            await channel.get_partial_message(event.message_id).edit(**fields)
        except discord.NotFound:
            self._announcement_deleted(event.id)
        except NETWORK_ERRORS as exc:
            if _is_permanent(exc):
                # Refused for a reason that asking again will not change: the bot was shut
                # out of the channel, or Discord rejects the edit itself. Retrying every
                # few seconds would only pile up refusals; the next change tries afresh.
                log.error(
                    "Discord refuses to redraw the announcement of event %s", event.id, exc_info=exc
                )
                self.store.mark_drawn(event.id, event.revision)
            else:
                log.warning("Could not redraw the announcement of event %s", event.id, exc_info=exc)
        except Exception:
            # A bug in drawing this particular event: it would fail the same way every
            # time, so it is reported once rather than every few seconds.
            log.exception("Redrawing the announcement of event %s failed", event.id)
            self.store.mark_drawn(event.id, event.revision)
        else:
            self.store.mark_drawn(event.id, event.revision)
