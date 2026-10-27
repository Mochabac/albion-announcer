"""Buttons and the form.

Every button is a DynamicItem: its custom_id says what it acts on (an activity
key, an event id), so the buttons on a panel or an announcement posted months ago
keep working after a restart, with nothing to register again.

Views that hold them are created with ``timeout=None`` and never stopped. A view
that times out takes its dynamic items out of the client's dispatch table when it
also holds ordinary items, which would silently kill every button of that kind.
"""

from __future__ import annotations

import functools
import logging
import re
import secrets
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from typing import Any, ClassVar

import discord

from . import render
from .catalog import CUSTOM_KEY, Catalog
from .errors import UserError
from .i18n import DEFAULT_LANGUAGE, t
from .service import Draft, Service
from .store import Event, Signup

log = logging.getLogger(__name__)

# How long an opened form may sit before its answer is no longer accepted.
FORM_TIMEOUT = 30 * 60.0
FORM_PREFIX = "ao:form:"
# Discord shows five buttons to a row.
ROW_WIDTH = 5

STYLES = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}
ACTIONS: dict[str, tuple[str, discord.ButtonStyle]] = {
    "remind": ("🔔", discord.ButtonStyle.primary),
    "start": ("🚀", discord.ButtonStyle.success),
    "edit": ("✏️", discord.ButtonStyle.secondary),
    "finish": ("✅", discord.ButtonStyle.secondary),
    "cancel": ("🚫", discord.ButtonStyle.danger),
}

type Button = discord.ui.Button[discord.ui.View]
type Submit = Callable[[discord.Interaction[Any], Draft], Awaitable[None]]


# --- answering interactions --------------------------------------------------------


async def tell(interaction: discord.Interaction[Any], text: str) -> None:
    """Send a private note, whether or not the interaction was already answered."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException:
        log.warning("Could not answer an interaction", exc_info=True)


def language_of(interaction: discord.Interaction[Any]) -> str:
    if interaction.guild_id is None:
        return DEFAULT_LANGUAGE
    return interaction.client.store.get_config(interaction.guild_id).language


async def report(interaction: discord.Interaction[Any], error: BaseException) -> None:
    """Tell the user what went wrong: the reason if it is theirs to fix, an apology if not."""
    if isinstance(error, UserError):
        await tell(interaction, str(error))
        return
    log.error("Unhandled error while handling an interaction", exc_info=error)
    await tell(interaction, t(language_of(interaction), "error.generic"))


type Handler[T] = Callable[[T, discord.Interaction[Any]], Coroutine[Any, Any, None]]


def guarded[T](callback: Handler[T]) -> Handler[T]:
    """Without this, a failing button just shows Discord's "This interaction failed"."""

    @functools.wraps(callback)
    async def wrapper(self: T, interaction: discord.Interaction[Any]) -> None:
        try:
            await callback(self, interaction)
        except Exception as exc:  # noqa: BLE001 - report() logs it and tells the user
            await report(interaction, exc)

    return wrapper


def _context(interaction: discord.Interaction[Any]) -> tuple[Service, discord.Guild, Any]:
    """The service, the guild and the member behind a button press."""
    guild = interaction.guild
    if guild is None:
        raise UserError(t(DEFAULT_LANGUAGE, "error.guild_only"))
    service: Service = interaction.client.service
    # Inside a guild the user of an interaction is always a Member.
    return service, guild, interaction.user


def _button(item: discord.ui.Item[Any]) -> Button:
    """The pressed component as discord.py rebuilt it from the message. Ours are all buttons."""
    assert isinstance(item, discord.ui.Button)
    return item


# --- the form ------------------------------------------------------------------------


class AnnounceForm(discord.ui.Modal):
    """The short form behind every panel button; also used to edit an announcement."""

    # custom_id -> until when (time.monotonic) this process still expects an answer.
    # It is how the bot tells a form that outlived a restart from one being handled.
    _expected: ClassVar[dict[str, float]] = {}

    def __init__(
        self,
        *,
        heading: str,
        custom: bool,
        lang: str,
        timezone: str,
        submit: Submit,
        event: Event | None = None,
    ) -> None:
        custom_id = FORM_PREFIX + secrets.token_hex(8)
        super().__init__(title=render.clip(heading, 45), timeout=FORM_TIMEOUT, custom_id=custom_id)
        now = time.monotonic()
        for forgotten in [key for key, until in self._expected.items() if until <= now]:
            del self._expected[forgotten]
        # A minute longer than the modal's own timeout: while discord.py may still
        # hand the answer to this form, the bot must not also call it expired.
        self._expected[custom_id] = now + FORM_TIMEOUT + 60
        self._lang = lang
        self._submit = submit
        self.headline: discord.ui.TextInput[Any] | None = None
        self.body: discord.ui.TextInput[Any] | None = None
        self.gear: discord.ui.TextInput[Any] | None = None
        self.notes: discord.ui.TextInput[Any] | None = None

        if custom:
            self.headline = self._box("title", event.title if event else "", 100, required=True)
            self.body = self._box(
                "body", event.message if event else "", 1500, required=True, paragraph=True
            )
            when_hint = t(lang, "modal.when.hint.custom", tz=timezone)
        else:
            when_hint = t(lang, "modal.when.hint", tz=timezone)
        self.when = self._box("when", event.when_text if event else "", 60, hint=when_hint)
        self.where = self._box("where", event.location if event else "", 100)
        if not custom:
            self.gear = self._box("gear", event.gear if event else "", 100)
            self.notes = self._box("notes", event.notes if event else "", 1000, paragraph=True)

    def _box(
        self,
        name: str,
        value: str,
        limit: int,
        *,
        required: bool = False,
        paragraph: bool = False,
        hint: str | None = None,
    ) -> discord.ui.TextInput[Any]:
        lang = self._lang
        box: discord.ui.TextInput[Any] = discord.ui.TextInput(
            style=discord.TextStyle.paragraph if paragraph else discord.TextStyle.short,
            placeholder=render.clip(t(lang, f"modal.{name}.placeholder"), 100),
            default=value[:limit] or None,
            required=required,
            max_length=limit,
        )
        self.add_item(
            discord.ui.Label(
                text=t(lang, f"modal.{name}"),
                description=render.clip(hint or t(lang, f"modal.{name}.hint"), 100),
                component=box,
            )
        )
        return box

    @classmethod
    def is_expected(cls, custom_id: str) -> bool:
        """Whether a form with this id was opened by this process and may still answer."""
        return cls._expected.get(custom_id, 0.0) > time.monotonic()

    def draft(self) -> Draft:
        def text(box: discord.ui.TextInput[Any] | None) -> str:
            return box.value.strip() if box else ""

        return Draft(
            when=text(self.when),
            location=text(self.where),
            gear=text(self.gear),
            notes=text(self.notes),
            title=text(self.headline),
            body=text(self.body),
        )

    async def on_submit(self, interaction: discord.Interaction[Any], /) -> None:
        await self._submit(interaction, self.draft())

    # Modal narrows BaseView.on_error to (interaction, error); so does discord.py itself.
    async def on_error(  # type: ignore[override]
        self, interaction: discord.Interaction[Any], error: Exception, /
    ) -> None:
        await report(interaction, error)


# --- the panel -------------------------------------------------------------------------


class PanelButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"ao:new:(?P<key>[a-z0-9_]{1,32})(?P<force>!)?",
):
    """Announce an activity. The ``!`` variant skips the "this won't ping anyone" warning."""

    def __init__(
        self, key: str, *, force: bool = False, button: Button | None = None, **look: Any
    ) -> None:
        custom_id = f"ao:new:{key}{'!' if force else ''}"
        super().__init__(button or discord.ui.Button(custom_id=custom_id, **look))
        self.key = key
        self.force = force

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[Any],
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> PanelButton:
        return cls(match["key"], force=bool(match["force"]), button=_button(item))

    @guarded
    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        service, guild, member = _context(interaction)
        origin = interaction.channel
        pre = service.precheck(guild, member, origin, self.key)
        lang = pre.config.language

        if pre.ping_problem and not self.force:
            view = discord.ui.View(timeout=None)
            view.add_item(
                PanelButton(
                    self.key,
                    force=True,
                    label=t(lang, "panel.continue"),
                    style=discord.ButtonStyle.secondary,
                )
            )
            await interaction.response.send_message(pre.ping_problem, view=view, ephemeral=True)
            return

        key = self.key

        async def submit(answer: discord.Interaction[Any], draft: Draft) -> None:
            # Posting takes a round trip to Discord; the form must be acknowledged first.
            await answer.response.defer(ephemeral=True, thinking=True)
            done = await service.announce(guild, answer.user, origin, key, draft)
            channel = f"<#{done.event.channel_id}>"
            lines = [t(lang, "announce.done", channel=channel, url=done.url), *done.notes]
            await answer.followup.send("\n".join(lines), ephemeral=True)

        activity = pre.activity
        await interaction.response.send_modal(
            AnnounceForm(
                heading=f"{activity.emoji} {activity.label(lang)}",
                custom=activity.custom,
                lang=lang,
                timezone=pre.config.timezone,
                submit=submit,
            )
        )


def panel_view(catalog: Catalog, lang: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for activity in catalog.buttons:
        view.add_item(
            PanelButton(
                activity.key,
                label=activity.label(lang),
                emoji=activity.emoji,
                style=STYLES[activity.style],
            )
        )
    return view


# --- the announcement ----------------------------------------------------------------------


class JoinButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"ao:join:(?P<event>[0-9]{1,18}):(?P<role>[a-z0-9_]{1,32})",
):
    def __init__(self, event_id: int, role: str, *, button: Button | None = None, **look: Any):
        super().__init__(
            button or discord.ui.Button(custom_id=f"ao:join:{event_id}:{role}", **look)
        )
        self.event_id = event_id
        self.role = role

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[Any],
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> JoinButton:
        return cls(int(match["event"]), match["role"], button=_button(item))

    @guarded
    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        service, guild, member = _context(interaction)
        service.signup(guild.id, self.event_id, member.id, self.role)
        # The roster is redrawn by the service; this only acknowledges the click.
        await interaction.response.defer()


class LeaveButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"ao:leave:(?P<event>[0-9]{1,18})",
):
    def __init__(self, event_id: int, *, button: Button | None = None, **look: Any) -> None:
        super().__init__(button or discord.ui.Button(custom_id=f"ao:leave:{event_id}", **look))
        self.event_id = event_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[Any],
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LeaveButton:
        return cls(int(match["event"]), button=_button(item))

    @guarded
    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        service, guild, member = _context(interaction)
        service.leave(guild.id, self.event_id, member.id)
        await interaction.response.defer()


class ManageButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"ao:manage:(?P<event>[0-9]{1,18})",
):
    """Opens the caller tools, privately, for the caller and the officers."""

    def __init__(self, event_id: int, *, button: Button | None = None, **look: Any) -> None:
        super().__init__(button or discord.ui.Button(custom_id=f"ao:manage:{event_id}", **look))
        self.event_id = event_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[Any],
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> ManageButton:
        return cls(int(match["event"]), button=_button(item))

    @guarded
    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        service, guild, member = _context(interaction)
        event = service.managed(guild, member, self.event_id)
        view = manage_view(event)
        assert view is not None  # managed() only returns open events
        await interaction.response.send_message(
            render.manage_text(event), view=view, ephemeral=True
        )


def event_view(event: Event, catalog: Catalog, signups: Sequence[Signup] = ()) -> discord.ui.View:
    """The buttons under an announcement: one per sign-up role, Leave, and Manage.

    A position that has all the players it takes is greyed out, so the open ones stand out.
    Leave and Manage get a row of their own, under the roles.
    """
    lang = event.language
    taken = Counter(signup.role for signup in signups)
    view = discord.ui.View(timeout=None)
    tools_row = (len(event.roles) + ROW_WIDTH - 1) // ROW_WIDTH
    for index, key in enumerate(event.roles):
        role = catalog.roles.get(key)
        limit = catalog.limit(key)
        full = limit is not None and taken[key] >= limit
        view.add_item(
            JoinButton(
                event.id,
                key,
                label=role.label(lang) if role else key,
                emoji=role.emoji if role else None,
                style=discord.ButtonStyle.secondary if full else discord.ButtonStyle.primary,
                disabled=full,
                row=index // ROW_WIDTH,
            )
        )
    if event.roles:
        view.add_item(
            LeaveButton(
                event.id,
                label=t(lang, "button.leave"),
                style=discord.ButtonStyle.secondary,
                row=tools_row,
            )
        )
    view.add_item(
        ManageButton(
            event.id,
            label=t(lang, "button.manage"),
            emoji="⚙️",
            style=discord.ButtonStyle.secondary,
            row=tools_row,
        )
    )
    return view


# --- caller tools ------------------------------------------------------------------------------


class ActionButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"ao:act:(?P<event>[0-9]{1,18}):(?P<action>remind|start|edit|finish|cancel)",
):
    def __init__(self, event_id: int, action: str, *, button: Button | None = None, **look: Any):
        super().__init__(
            button or discord.ui.Button(custom_id=f"ao:act:{event_id}:{action}", **look)
        )
        self.event_id = event_id
        self.action = action

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[Any],
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> ActionButton:
        return cls(int(match["event"]), match["action"], button=_button(item))

    @guarded
    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        service, guild, member = _context(interaction)
        # Refuse before acknowledging, so a refusal can still be the direct answer.
        event = service.managed(guild, member, self.event_id)
        event_id = self.event_id

        if self.action == "edit":
            lang = event.language

            async def submit(answer: discord.Interaction[Any], draft: Draft) -> None:
                await answer.response.defer(ephemeral=True, thinking=True)
                edited = await service.edit(guild, answer.user, event_id, draft)
                lines = [t(lang, "manage.done.edit"), *edited.notes]
                await answer.followup.send("\n".join(lines), ephemeral=True)

            config = service.store.get_config(guild.id)
            await interaction.response.send_modal(
                AnnounceForm(
                    heading=t(lang, "modal.edit", title=f"{event.emoji} {event.title}"),
                    custom=event.activity == CUSTOM_KEY,
                    lang=lang,
                    timezone=config.timezone,
                    submit=submit,
                    event=event,
                )
            )
            return

        perform: dict[str, Callable[[Any, Any, int], Awaitable[Event]]] = {
            "remind": service.remind,
            "start": service.start,
            "finish": service.finish,
            "cancel": service.cancel,
        }
        await interaction.response.defer()
        event = await perform[self.action](guild, member, event_id)
        await interaction.edit_original_response(
            content=render.manage_text(event, done=self.action), view=manage_view(event)
        )


def manage_view(event: Event) -> discord.ui.View | None:
    """The caller tools that still make sense for the event's state; None once it is closed."""
    if not event.is_open:
        return None
    lang = event.language
    view = discord.ui.View(timeout=None)
    for action, (emoji, style) in ACTIONS.items():
        if action == "start" and event.status != "open":
            continue
        view.add_item(
            ActionButton(
                event.id, action, label=t(lang, f"manage.{action}"), emoji=emoji, style=style
            )
        )
    return view


DYNAMIC_ITEMS = (PanelButton, JoinButton, LeaveButton, ManageButton, ActionButton)
