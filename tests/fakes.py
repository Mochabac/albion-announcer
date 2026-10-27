"""Stand-ins for the slice of Discord the bot touches.

They record what was sent instead of sending it, and can be told to fail the way
Discord does (a deleted message, a refused send).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import discord

from announcer.store import Event, Store

EVERYTHING = discord.Permissions(
    view_channel=True,
    send_messages=True,
    send_messages_in_threads=True,
    embed_links=True,
    read_message_history=True,
    mention_everyone=True,
)
MEMBER = discord.Permissions(view_channel=True, send_messages=True, read_message_history=True)


def not_found() -> discord.NotFound:
    response = SimpleNamespace(status=404, reason="Not Found")
    return discord.NotFound(response, {"code": 10008, "message": "Unknown Message"})  # type: ignore[arg-type]


def forbidden() -> discord.Forbidden:
    response = SimpleNamespace(status=403, reason="Forbidden")
    return discord.Forbidden(response, {"code": 50013, "message": "Missing Permissions"})  # type: ignore[arg-type]


def unavailable() -> discord.DiscordServerError:
    """What discord.py raises when Discord answers 503: it does not retry that one."""
    response = SimpleNamespace(status=503, reason="Service Unavailable")
    return discord.DiscordServerError(response, "upstream connect error")  # type: ignore[arg-type]


@dataclass
class FakeRole:
    id: int
    mentionable: bool = False
    default: bool = False

    @property
    def mention(self) -> str:
        return f"<@&{self.id}>"

    def is_default(self) -> bool:
        return self.default


@dataclass
class FakeMember:
    id: int
    display_name: str = "Member"
    roles: list[FakeRole] = field(default_factory=list)
    guild_permissions: discord.Permissions = field(default_factory=discord.Permissions.none)
    voice: Any = None


@dataclass
class Sent:
    """One message the bot posted."""

    id: int
    content: str | None
    embed: discord.Embed | None
    view: discord.ui.View | None
    allowed_mentions: discord.AllowedMentions | None
    reference: discord.MessageReference | None
    jump_url: str = ""


class FakePartialMessage:
    def __init__(self, channel: FakeChannel, message_id: int) -> None:
        self.channel = channel
        self.id = message_id

    async def edit(self, **fields: Any) -> None:
        channel = self.channel
        channel.edits_in_flight += 1
        channel.most_edits_in_flight = max(channel.most_edits_in_flight, channel.edits_in_flight)
        try:
            await asyncio.sleep(channel.edit_delay)
            if self.id in channel.deleted:
                raise not_found()
            if channel.fail_edit is not None:
                raise channel.fail_edit
            channel.edits.append((self.id, fields))
        finally:
            channel.edits_in_flight -= 1

    async def fetch(self) -> FakePartialMessage:
        self.channel.fetches += 1
        await asyncio.sleep(self.channel.fetch_delay)
        if self.id in self.channel.deleted:
            raise not_found()
        return self


class FakeChannel(discord.abc.Messageable):
    def __init__(
        self,
        channel_id: int,
        *,
        bot_permissions: discord.Permissions = EVERYTHING,
        everyone: discord.Permissions = MEMBER,
    ):
        self.id = channel_id
        self.mention = f"<#{channel_id}>"
        self.bot_permissions = bot_permissions
        # member id -> permissions in this channel; anyone else gets what @everyone gets
        self.member_permissions: dict[int, discord.Permissions] = {}
        self.everyone_permissions = everyone
        self.bot_id = 1
        self.sent: list[Sent] = []
        self.edits: list[tuple[int, dict[str, Any]]] = []
        self.deleted: set[int] = set()
        self.fail_send: BaseException | None = None
        self.fail_edit: Exception | None = None
        # Called while a message is "on its way": Discord already shows it, the bot
        # has not been told its id yet.
        self.while_sending: Any = None
        self.fetches = 0
        self.fetch_delay = 0.0
        # How long Discord "takes" to apply an edit, and how many overlapped at worst.
        self.edit_delay = 0.0
        self.edits_in_flight = 0
        self.most_edits_in_flight = 0
        self._next_id = 1000

    def permissions_for(self, obj: Any) -> discord.Permissions:
        if isinstance(obj, FakeRole):
            assert obj.is_default(), "only @everyone is ever asked about"
            return self.everyone_permissions
        if obj.id == self.bot_id:
            return self.bot_permissions
        return self.member_permissions.get(obj.id, self.everyone_permissions)

    async def send(  # type: ignore[override]
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        allowed_mentions: discord.AllowedMentions | None = None,
        reference: discord.MessageReference | None = None,
    ) -> Sent:
        if self.fail_send is not None:
            raise self.fail_send
        if self.while_sending is not None:
            self.while_sending()
            await asyncio.sleep(0.001)  # ...and the answer is still travelling back
        self._next_id += 1
        message = Sent(
            id=self._next_id,
            content=content,
            embed=embed,
            view=view,
            allowed_mentions=allowed_mentions,
            reference=reference,
            jump_url=f"https://discord.com/channels/1/{self.id}/{self._next_id}",
        )
        self.sent.append(message)
        return message

    def get_partial_message(self, message_id: int) -> FakePartialMessage:
        return FakePartialMessage(self, message_id)

    def last_edit(self) -> dict[str, Any]:
        return self.edits[-1][1]


class FakeGuild:
    def __init__(self, guild_id: int = 1) -> None:
        self.id = guild_id
        self.me = FakeMember(1, "Announcer")
        self.default_role = FakeRole(guild_id, default=True)
        self.roles: dict[int, FakeRole] = {}
        self.channels: dict[int, FakeChannel] = {}

    def add_channel(self, channel: FakeChannel) -> FakeChannel:
        self.channels[channel.id] = channel
        return channel

    def add_role(self, role: FakeRole) -> FakeRole:
        self.roles[role.id] = role
        return role

    def get_role(self, role_id: int) -> FakeRole | None:
        return self.roles.get(role_id)

    def get_channel_or_thread(self, channel_id: int) -> FakeChannel | None:
        return self.channels.get(channel_id)


class FakeClient:
    """Hands out the same channel objects the guild holds, like a partial messageable would."""

    def __init__(self, guild: FakeGuild) -> None:
        self.guild = guild

    def get_partial_messageable(self, channel_id: int, *, guild_id: int | None = None) -> Any:
        return self.guild.channels[channel_id]


def new_event(store: Store, **overrides: Any) -> Event:
    """An event straight into the store, for tests that do not go through an announcement."""
    values: dict[str, Any] = {
        "guild_id": 1,
        "channel_id": 20,
        "activity": "zvz",
        "language": "en",
        "emoji": "⚔️",
        "title": "ZvZ",
        "message": "Mass up.",
        "color": 0xC0392B,
        "icon": None,
        "roles": ("tank", "healer"),
        "ping": "everyone",
        "ping_role_id": None,
        "author_id": 3,
        "author_name": "Mocha",
        "when_kind": "now",
        "when_text": "",
        "starts_at": None,
        "location": "",
        "gear": "",
        "notes": "",
        "voice_channel_id": None,
        "status": "open",
        "remind_at": None,
        "created_at": 1_000,
    }
    values.update(overrides)
    return store.create_event(**values)


class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- interactions ------------------------------------------------------------------


class FakeResponse:
    def __init__(self) -> None:
        self.done = False
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []

    def is_done(self) -> bool:
        return self.done

    def _record(self, name: str, first: Any, kwargs: dict[str, Any]) -> None:
        assert not self.done, "an interaction can only be answered once"
        self.done = True
        self.calls.append((name, first, kwargs))

    async def send_message(self, content: Any = None, **kwargs: Any) -> None:
        self._record("send_message", content, kwargs)

    async def send_modal(self, modal: Any) -> None:
        self._record("send_modal", modal, {})

    async def defer(self, **kwargs: Any) -> None:
        self._record("defer", None, kwargs)


class FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[tuple[Any, dict[str, Any]]] = []

    async def send(self, content: Any = None, **kwargs: Any) -> None:
        self.sent.append((content, kwargs))


class FakeInteraction:
    def __init__(self, client: Any, guild: FakeGuild | None, user: FakeMember, channel: Any):
        self.client = client
        self.guild = guild
        self.guild_id = guild.id if guild else None
        self.user = user
        self.channel = channel
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.original_edits: list[dict[str, Any]] = []

    async def edit_original_response(self, **fields: Any) -> None:
        self.original_edits.append(fields)

    @property
    def answer(self) -> tuple[str, Any, dict[str, Any]]:
        """The one direct answer this interaction got."""
        assert len(self.response.calls) == 1, self.response.calls
        return self.response.calls[0]
