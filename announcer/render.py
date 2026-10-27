"""Builds everything the bot posts: the announcement, its roster and the follow-ups.

Nothing here talks to Discord. An event and its sign-ups go in, text and embeds
come out, always inside Discord's size limits.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime

import discord

from .catalog import Catalog
from .i18n import LANGUAGES, day_label, t
from .store import Event, GuildConfig, Signup

EMBED_LIMIT = 6000
FIELD_LIMIT = 1024
FIELD_COUNT_LIMIT = 25
CONTENT_LIMIT = 2000
# A field's name may not be empty; a zero-width space is how one goes without.
BLANK = "​"
PANEL_COLOR = 0xE5A823
CLOSED_COLOR = 0x5C6370

# "@everyone", "@here", and the "@" of "<@id>", "<@!id>" and "<@&id>".
_MENTION = re.compile(r"@(?=everyone|here)|(?<=<)@")

NO_MENTIONS = discord.AllowedMentions.none()
ROSTER_MENTIONS = discord.AllowedMentions(
    everyone=False, users=True, roles=False, replied_user=False
)


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def plain(text: str, limit: int) -> str:
    """User text made safe for a message's content line: one line, no pings, no markdown.

    ``allowed_mentions`` is what really stops a typed mention from notifying anyone;
    this keeps it from even looking like one. Channel mentions are left alone.
    """
    text = clip(" ".join(text.split()), limit)
    return discord.utils.escape_markdown(_MENTION.sub("@\u200b", text))


# --- pings ---------------------------------------------------------------------


def ping_text(kind: str, role_id: int | None) -> str:
    if kind in ("everyone", "here"):
        return f"@{kind}"
    if kind == "role" and role_id:
        return f"<@&{role_id}>"
    return ""


def ping_mentions(kind: str, role_id: int | None) -> discord.AllowedMentions:
    """Lets exactly the configured ping through, and nothing a caller typed."""
    if kind in ("everyone", "here"):
        return discord.AllowedMentions(everyone=True, users=False, roles=False, replied_user=False)
    if kind == "role" and role_id:
        return discord.AllowedMentions(
            everyone=False, users=False, roles=[discord.Object(role_id)], replied_user=False
        )
    return NO_MENTIONS


# --- time ----------------------------------------------------------------------


def _utc(timestamp: int) -> datetime:
    return datetime.fromtimestamp(timestamp, UTC)


def _same_day(event: Event) -> bool:
    assert event.starts_at is not None
    return _utc(event.starts_at).date() == _utc(event.created_at).date()


def utc_label(event: Event) -> str:
    """Albion server time, as plain text: ``20:30 UTC`` or ``Sat 5 Oct · 20:30 UTC``."""
    assert event.starts_at is not None
    moment = _utc(event.starts_at)
    clock = f"{moment:%H:%M} UTC"
    return clock if _same_day(event) else f"{day_label(event.language, moment)} · {clock}"


def relative(lang: str, seconds: float) -> str:
    """``in 25 min`` / ``10 min ago``, as plain text (it has to read well in a push notification)."""
    minutes = round(abs(seconds) / 60)
    if minutes == 0:
        return t(lang, "rel.now")
    direction = "in" if seconds > 0 else "ago"
    if minutes < 90:
        return t(lang, f"rel.{direction}_min", n=minutes)
    return t(lang, f"rel.{direction}_hours", h=minutes // 60, m=minutes % 60)


def when_plain(event: Event) -> str | None:
    """The time for a message's content line. No Discord timestamp markup: push
    notifications show it raw."""
    if event.when_kind == "at" and event.starts_at is not None:
        return utc_label(event)
    if event.when_kind == "now":
        return t(event.language, "when.now.plain")
    if event.when_kind == "text":
        return plain(event.when_text, 60)
    return None


def when_field(event: Event) -> str | None:
    """The time inside the embed: server time, each reader's own time, and a countdown."""
    lang = event.language
    if event.when_kind == "at" and event.starts_at is not None:
        style = "t" if _same_day(event) else "f"
        return (
            f"**{utc_label(event)}** · <t:{event.starts_at}:{style}> {t(lang, 'when.local')}"
            f" · <t:{event.starts_at}:R>"
        )
    if event.when_kind == "now":
        return t(lang, "when.now")
    if event.when_kind == "text":
        return event.when_text
    return None


# --- the announcement ------------------------------------------------------------


def headline(event: Event) -> str:
    return f"{event.emoji} **{plain(event.title, 100)}**"


def announcement_content(event: Event) -> str:
    """The line above the embed. It carries the ping, and it is all a phone shows."""
    details = [when_plain(event)]
    if event.location:
        details.append(f"📍 {plain(event.location, 80)}")
    line = headline(event)
    if any(details):
        line += " — " + " · ".join(filter(None, details))
    return f"{ping_text(event.ping, event.ping_role_id)} {line}".strip()


def announcement_embed(event: Event, signups: Sequence[Signup], catalog: Catalog) -> discord.Embed:
    lang = event.language
    embed = discord.Embed(
        title=clip(f"{event.emoji} {event.title}", 256),
        color=event.color if event.is_open else CLOSED_COLOR,
        timestamp=_utc(event.created_at),
    )

    description = event.message
    if event.status != "open":
        banner = t(lang, f"status.{event.status}")
        description = f"{banner}\n\n{description}" if description else banner
    if description:
        embed.description = clip(description, 4000)
    if event.icon:
        embed.set_thumbnail(url=event.icon)

    when = when_field(event)
    if when:
        embed.add_field(name=t(lang, "field.when"), value=clip(when, FIELD_LIMIT), inline=False)
    if event.location:
        embed.add_field(name=t(lang, "field.where"), value=clip(event.location, FIELD_LIMIT))
    if event.gear:
        embed.add_field(name=t(lang, "field.gear"), value=clip(event.gear, FIELD_LIMIT))
    if event.voice_channel_id:
        embed.add_field(name=t(lang, "field.voice"), value=f"<#{event.voice_channel_id}>")
    if event.notes:
        embed.add_field(
            name=t(lang, "field.notes"), value=clip(event.notes, FIELD_LIMIT), inline=False
        )

    by_role: dict[str, list[int]] = {key: [] for key in event.roles}
    for signup in signups:
        by_role.setdefault(signup.role, []).append(signup.user_id)
    # A comp: every role is a position for a set number of players.
    comp = bool(by_role) and all(catalog.limit(key) for key in by_role)

    footer = t(lang, "footer.caller", name=event.author_name)
    if event.roles and not comp:
        footer += " · " + t(lang, "footer.signed", n=len(signups))
    embed.set_footer(text=clip(footer, 200))

    budget = EMBED_LIMIT - len(embed)
    if comp:
        for name, value in _positions(by_role, catalog, lang, budget=budget):
            embed.add_field(name=name, value=value, inline=False)
    else:
        for name, value in _columns(by_role, catalog, lang, budget=budget):
            embed.add_field(name=name, value=value)
    # Discord rejects the whole message if an embed is over a limit; a missing
    # column is the lesser evil. The roster's budget makes this all but unreachable.
    while (len(embed) > EMBED_LIMIT or len(embed.fields) > FIELD_COUNT_LIMIT) and embed.fields:
        embed.remove_field(len(embed.fields) - 1)
    return embed


def _columns(
    by_role: dict[str, list[int]], catalog: Catalog, lang: str, *, budget: int
) -> list[tuple[str, str]]:
    """One column per sign-up role, sharing whatever room the embed has left."""
    if not by_role:
        return []

    names = {}
    for key, users in by_role.items():
        role = catalog.roles.get(key)
        title = f"{role.emoji} {role.label(lang)}" if role else key
        limit = catalog.limit(key)
        names[key] = f"{title} · {len(users)}/{limit}" if limit else f"{title} · {len(users)}"

    # No floor under the share: twenty of the longest names leave less than one would give.
    room = (budget - sum(len(name) for name in names.values())) // len(by_role)
    room = min(FIELD_LIMIT, room)
    return [(names[key], _mention_block(users, room, lang)) for key, users in by_role.items()]


def _positions(
    by_role: dict[str, list[int]], catalog: Catalog, lang: str, *, budget: int
) -> list[tuple[str, str]]:
    """A comp as one list: each position on its own line, with whoever took it.

    A list too long for one field goes on in the next ones, under a blank name.
    """
    limits = {key: catalog.limit(key) or 0 for key in by_role}
    filled = sum(min(len(users), limits[key]) for key, users in by_role.items())
    total = sum(limits.values())
    name = t(lang, "roster.positions", filled=filled, total=total)
    if filled >= total:
        name += " ✅"

    heads = {}
    for key, users in by_role.items():
        role = catalog.roles[key]
        head = f"{role.emoji} **{role.label(lang)}**"
        if limits[key] > 1:
            head += f" ({len(users)}/{limits[key]})"
        heads[key] = f"{head} — "

    # Each line gets the room its names need while the list fits. When it does not, the
    # lines that need least keep all of it and the others share what is left evenly.
    # Between two lines goes a newline, or the blank name of the next field.
    caps = {key: FIELD_LIMIT - len(head) for key, head in heads.items()}
    needs = {key: len(_taken_by(users, caps[key], lang)) for key, users in by_role.items()}
    room = budget - len(name) - sum(len(head) for head in heads.values()) - (len(by_role) - 1)
    bodies = {}
    for index, key in enumerate(sorted(by_role, key=needs.__getitem__)):
        share = min(caps[key], room // (len(by_role) - index))
        bodies[key] = _taken_by(by_role[key], share, lang)
        room -= len(bodies[key])

    values = [""]
    for line in (clip(heads[key] + bodies[key], FIELD_LIMIT) for key in by_role):
        if values[-1] and len(values[-1]) + 1 + len(line) > FIELD_LIMIT:
            values.append("")
        values[-1] += f"\n{line}" if values[-1] else line
    return [(name if index == 0 else BLANK, value) for index, value in enumerate(values)]


def _taken_by(users: Sequence[int], limit: int, lang: str) -> str:
    """Who holds a position, or that it is open."""
    return _mention_block(users, limit, lang, separator=", ") if users else t(lang, "roster.open")


def _mention_block(
    users: Sequence[int], limit: int, lang: str, *, separator: str | None = None
) -> str:
    """Mentions for one role, ending in "+N more" when they do not all fit."""
    if not users:
        return "—"
    if separator is None:
        separator = "\n" if len(users) <= 10 else " "
    parts: list[str] = []
    used = 0
    for index, user_id in enumerate(users):
        mention = f"<@{user_id}>"
        left_after = len(users) - index - 1
        cost = len(mention) + (len(separator) if parts else 0)
        tail = len(separator) + len(t(lang, "roster.more", n=left_after)) if left_after else 0
        if used + cost + tail > limit:
            parts.append(t(lang, "roster.more", n=left_after + 1))
            break
        parts.append(mention)
        used += cost
    return separator.join(parts)


# --- follow-up messages ------------------------------------------------------------


def _details(event: Event, now: float | None) -> str:
    """Time, place and voice channel on one line, for the messages that follow up."""
    parts = []
    if event.when_kind == "at" and event.starts_at is not None:
        label = utc_label(event)
        if now is not None:
            label += f" ({relative(event.language, event.starts_at - now)})"
        parts.append(label)
    elif event.when_kind == "text":
        parts.append(plain(event.when_text, 60))
    if event.location:
        parts.append(f"📍 {plain(event.location, 80)}")
    if event.voice_channel_id:
        parts.append(f"🎙️ <#{event.voice_channel_id}>")
    return " · ".join(parts)


def _lines(*lines: str) -> str:
    return "\n".join(line for line in lines if line)


def reminder_text(event: Event, now: float) -> str:
    lang = event.language
    call_to_action = t(lang, "followup.remind.cta") if event.roles else ""
    return _lines(
        t(lang, "followup.remind", head=headline(event)), _details(event, now), call_to_action
    )


def auto_reminder_text(event: Event, now: float) -> str:
    assert event.starts_at is not None
    lang = event.language
    starts = relative(lang, event.starts_at - now)
    return _lines(t(lang, "followup.auto", head=headline(event), rel=starts), _details(event, None))


def start_text(event: Event) -> str:
    details = " · ".join(
        filter(
            None,
            (
                f"📍 {plain(event.location, 80)}" if event.location else "",
                f"🎙️ <#{event.voice_channel_id}>" if event.voice_channel_id else "",
            ),
        )
    )
    return _lines(t(event.language, "followup.start", head=headline(event)), details)


def finish_text(event: Event) -> str:
    return t(event.language, "followup.finish", head=headline(event))


def cancel_text(event: Event) -> str:
    return t(event.language, "followup.cancel", head=headline(event))


def update_text(event: Event, now: float) -> str:
    return _lines(t(event.language, "followup.edit", head=headline(event)), _details(event, now))


def with_mentions(text: str, users: Sequence[int]) -> list[str]:
    """``text`` followed by a mention of every user, split into as many messages as needed."""
    messages = [text]
    for user_id in users:
        mention = f"<@{user_id}>"
        joiner = "\n" if messages[-1] == text and len(messages) == 1 else " "
        if len(messages[-1]) + len(joiner) + len(mention) > CONTENT_LIMIT:
            messages.append(mention)
        else:
            messages[-1] += joiner + mention
    return messages


# --- panel and caller tools ----------------------------------------------------------


def panel_embed(lang: str, timezone: str) -> discord.Embed:
    return discord.Embed(
        title=t(lang, "panel.title"),
        description=t(lang, "panel.body", tz=timezone),
        color=PANEL_COLOR,
    )


def config_embed(
    config: GuildConfig, *, channel_exists: bool, checks: Sequence[str]
) -> discord.Embed:
    """The settings as `/config` shows them, with the health check underneath."""
    lang = config.language
    if config.channel_id is None:
        channel = t(lang, "config.channel.same")
    elif channel_exists:
        channel = f"<#{config.channel_id}>"
    else:
        channel = t(lang, "config.channel.gone")

    if config.announcer_role_ids:
        roles = ", ".join(f"<@&{role_id}>" for role_id in config.announcer_role_ids)
        announcers = t(lang, "config.announcers.roles", roles=roles)
    else:
        announcers = t(lang, "config.announcers.default")

    if config.reminder_minutes > 0:
        reminder = t(lang, "config.reminder.value", n=config.reminder_minutes)
    else:
        reminder = t(lang, "config.reminder.off")

    embed = discord.Embed(title=t(lang, "config.title"), color=PANEL_COLOR)
    embed.add_field(name=t(lang, "config.channel"), value=channel, inline=False)
    embed.add_field(
        name=t(lang, "config.ping"),
        value=ping_text(config.ping, config.ping_role_id) or t(lang, "config.ping.none"),
    )
    embed.add_field(
        name=t(lang, "config.language"), value=LANGUAGES.get(config.language, config.language)
    )
    embed.add_field(name=t(lang, "config.timezone"), value=config.timezone)
    embed.add_field(
        name=t(lang, "config.announcers"), value=clip(announcers, FIELD_LIMIT), inline=False
    )
    embed.add_field(name=t(lang, "config.reminder"), value=reminder, inline=False)
    embed.add_field(
        name=t(lang, "config.checks"), value=clip("\n".join(checks), FIELD_LIMIT), inline=False
    )
    embed.set_footer(text=t(lang, "config.footer"))
    return embed


def manage_text(event: Event, done: str | None = None) -> str:
    lang = event.language
    header = t(lang, "manage.header", head=headline(event))
    if done:
        header = f"{t(lang, f'manage.done.{done}')}\n{header}"
    return _lines(header, t(lang, "manage.help") if event.is_open else "")
