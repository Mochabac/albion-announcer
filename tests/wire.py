"""A real AnnouncerBot, fed real gateway payloads, with its HTTP exits captured.

The other tests use stand-in guilds and channels. Here nothing is stood in for
except the network: discord.py parses a GUILD_CREATE and INTERACTION_CREATE the
way it would from the gateway, dispatches them to the bot's buttons, forms and
commands, and every request the bot would make to Discord is recorded as the
JSON it would put on the wire.
"""

from __future__ import annotations

import asyncio
import itertools
import re
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import discord
from discord.webhook.async_ import AsyncWebhookAdapter

from announcer import commands, views
from announcer.bot import AnnouncerBot

GUILD = 1
BOT = 900
BOT_ROLE = 600
OFFICER_ROLE = 500
ADMIN, OFFICER, RECRUIT = 2, 3, 4
PANEL_CHANNEL, ANNOUNCEMENTS, VOICE = 10, 20, 55

P = discord.Permissions
EVERYONE_PERMISSIONS = P(view_channel=True, send_messages=True, read_message_history=True)
BOT_PERMISSIONS = P(embed_links=True, mention_everyone=True, send_messages_in_threads=True)

# Tasks worth waiting for after an event; the rest (view timeouts) run for minutes.
_BUSY = (
    "discord-ui-dynamic-item",
    "discord-ui-modal-dispatch",
    "CommandTree-invoker",
    "redraw-event",
)

NAMES = {ADMIN: "Admin", OFFICER: "Mocha", RECRUIT: "Recruit", BOT: "Announcer"}
ROLES = {ADMIN: [], OFFICER: [OFFICER_ROLE], RECRUIT: [], BOT: [BOT_ROLE]}


def user(user_id: int) -> dict[str, Any]:
    name = NAMES.get(user_id, f"Player{user_id}")
    return {
        "id": str(user_id),
        "username": name.lower(),
        "global_name": name,
        "discriminator": "0",
        "avatar": None,
        "bot": user_id == BOT,
    }


def member(user_id: int) -> dict[str, Any]:
    return {
        "user": user(user_id),
        "roles": [str(role) for role in ROLES.get(user_id, [])],
        "nick": None,
        "joined_at": "2026-01-01T00:00:00+00:00",
        "deaf": False,
        "mute": False,
        "flags": 0,
    }


def role(
    role_id: int, name: str, permissions: discord.Permissions, position: int
) -> dict[str, Any]:
    return {
        "id": str(role_id),
        "name": name,
        "permissions": str(permissions.value),
        "position": position,
        "color": 0,
        "hoist": False,
        "managed": False,
        "mentionable": False,
    }


def channel(
    channel_id: int,
    name: str,
    kind: int = 0,
    *,
    private: bool = False,
    guests: Sequence[int] = (),
) -> dict[str, Any]:
    # A private channel is hidden from @everyone and lets the officers in, and its guests:
    # members added one by one. Not the bot, unless it is one of them.
    view = str(P(view_channel=True).value)
    access = [
        {"id": str(GUILD), "type": 0, "allow": "0", "deny": view},
        {"id": str(OFFICER_ROLE), "type": 0, "allow": view, "deny": "0"},
        *({"id": str(guest), "type": 1, "allow": view, "deny": "0"} for guest in guests),
    ]
    return {
        "id": str(channel_id),
        "type": kind,
        "name": name,
        "position": channel_id,
        "permission_overwrites": access if private else [],
        "parent_id": None,
        "nsfw": False,
    }


def guild_create(
    *,
    bot_permissions: discord.Permissions = BOT_PERMISSIONS,
    private: Sequence[int] = (),
    guests: Sequence[int] = (),
) -> dict[str, Any]:
    """The guild as the gateway announces it. ``guests`` are let into the ``private`` channels."""
    return {
        "id": str(GUILD),
        "name": "Guild",
        "owner_id": str(ADMIN),
        "unavailable": False,
        "member_count": 4,
        "large": False,
        "features": [],
        "emojis": [],
        "stickers": [],
        "threads": [],
        "roles": [
            role(GUILD, "@everyone", EVERYONE_PERMISSIONS, 0),
            role(OFFICER_ROLE, "Officer", P(mention_everyone=True), 1),
            role(BOT_ROLE, "Announcer", bot_permissions, 2),
        ],
        "channels": [
            channel(PANEL_CHANNEL, "officers", private=PANEL_CHANNEL in private, guests=guests),
            channel(
                ANNOUNCEMENTS,
                "announcements",
                private=ANNOUNCEMENTS in private,
                guests=guests,
            ),
            {
                **channel(VOICE, "ZvZ", kind=2),
                "bitrate": 64000,
                "user_limit": 0,
                "rtc_region": None,
            },
        ],
        "members": [member(BOT)],
        "voice_states": [
            {
                "user_id": str(OFFICER),
                "channel_id": str(VOICE),
                "session_id": "s",
                "deaf": False,
                "mute": False,
                "self_deaf": False,
                "self_mute": False,
                "self_video": False,
                "suppress": False,
                "request_to_speak_timestamp": None,
                "member": member(OFFICER),
            }
        ],
    }


def message(message_id: int, channel_id: int, sent: dict[str, Any] | None = None) -> dict[str, Any]:
    """The message object Discord returns for (or attaches to an interaction on) what was sent."""
    sent = sent or {}
    return {
        "id": str(message_id),
        "channel_id": str(channel_id),
        "author": user(BOT),
        "content": sent.get("content") or "",
        "timestamp": "2026-10-04T18:00:00+00:00",
        "edited_timestamp": None,
        "tts": False,
        "mention_everyone": False,
        "mentions": [],
        "mention_roles": [],
        "attachments": [],
        "embeds": sent.get("embeds") or [],
        "components": sent.get("components") or [],
        "pinned": False,
        "type": 0,
        "flags": sent.get("flags") or 0,
    }


class Wire:
    def __init__(self, bot: AnnouncerBot, monkeypatch: Any) -> None:
        self.bot = bot
        self.state = bot._connection
        # (method, path template, JSON body) of every call made with the bot token.
        self.rest: list[tuple[str, str, dict[str, Any]]] = []
        # The direct answer to each interaction: {"type": ..., "data": ...}.
        self.callbacks: list[dict[str, Any]] = []
        # Follow-up messages and edits of the original response, sent through the webhook.
        self.followups: list[dict[str, Any]] = []
        self.original_edits: list[dict[str, Any]] = []
        self.deleted: set[int] = set()
        # message id -> what is currently on screen for it
        self.messages: dict[int, dict[str, Any]] = {}
        # interaction token -> the message its "original response" is
        self._original: dict[str, int] = {}
        self._ids = itertools.count(7000)
        monkeypatch.setattr(bot.http, "request", self._rest_request)
        monkeypatch.setattr(AsyncWebhookAdapter, "request", self._webhook_request)

    async def start(self, **guild_options: Any) -> None:
        bot = self.bot
        await bot._async_setup_hook()
        self.state.user = discord.ClientUser(state=self.state, data=user(BOT))  # type: ignore[arg-type]
        self.state.application_id = BOT
        bot.add_dynamic_items(*views.DYNAMIC_ITEMS)
        commands.register(bot)
        self.state.parse_guild_create(guild_create(**guild_options))  # type: ignore[arg-type]

    async def stop(self) -> None:
        current = asyncio.current_task()
        leftovers = [task for task in asyncio.all_tasks() if task is not current]
        for task in leftovers:
            task.cancel()
        await asyncio.gather(*leftovers, return_exceptions=True)

    # --- the two exits --------------------------------------------------------------

    async def _rest_request(self, route: Any, **kwargs: Any) -> Any:
        body = kwargs.get("json") or {}
        self.rest.append((route.method, route.path, body))
        ids = [int(part) for part in re.findall(r"/(\d+)", route.url.split("/api/v10")[1])]

        if route.path == "/channels/{channel_id}/messages":
            message_id = next(self._ids)
            self.messages[message_id] = message(message_id, ids[0], body)
            return self.messages[message_id]
        if route.path == "/channels/{channel_id}/messages/{message_id}":
            channel_id, message_id = ids
            if message_id in self.deleted:
                response = SimpleNamespace(status=404, reason="Not Found")
                raise discord.NotFound(response, {"code": 10008, "message": "Unknown Message"})  # type: ignore[arg-type]
            shown = self.messages.setdefault(message_id, message(message_id, channel_id))
            if route.method == "PATCH":
                shown.update({key: value for key, value in body.items() if key in shown})
            return shown
        if route.path == "/applications/{application_id}/commands":
            return [
                {**command, "id": str(next(self._ids)), "application_id": str(BOT), "version": "1"}
                for command in body  # type: ignore[union-attr]
            ]
        raise AssertionError(f"unexpected REST call: {route.method} {route.path}")

    async def _webhook_request(self, route: Any, session: Any, **kwargs: Any) -> Any:
        body = kwargs.get("payload") or {}
        if route.path.endswith("/callback"):
            self.callbacks.append(body)
            answer: dict[str, Any] = {"id": str(route.webhook_id), "type": 3}
            if body["type"] == 4:  # a message: Discord says which one it became
                message_id = next(self._ids)
                self.messages[message_id] = message(message_id, PANEL_CHANNEL, body["data"])
                self._original[route.webhook_token] = message_id
                answer["response_message_id"] = str(message_id)
            return {"interaction": answer}
        if route.path.endswith("/messages/@original"):
            self.original_edits.append(body)
            # For a button press that was only acknowledged, "@original" is the
            # message the button sits on.
            shown = self.messages[self._original[route.webhook_token]]
            shown.update({key: value for key, value in body.items() if key in shown})
            return shown
        if route.method == "POST":
            self.followups.append(body)
            return message(next(self._ids), PANEL_CHANNEL, body)
        raise AssertionError(f"unexpected webhook call: {route.method} {route.path}")

    # --- gateway events ---------------------------------------------------------------

    def _interaction(
        self, kind: int, who: int, data: dict[str, Any], **extra: Any
    ) -> dict[str, Any]:
        number = next(self._ids)
        return {
            "id": str(number),
            "application_id": str(BOT),
            "type": kind,
            "token": f"token-{number}",
            "version": 1,
            "guild_id": str(GUILD),
            "channel_id": str(PANEL_CHANNEL),
            "channel": channel(PANEL_CHANNEL, "officers"),
            "member": {**member(who), "permissions": "0"},
            "data": data,
            "app_permissions": "0",
            "locale": "en-US",
            "guild_locale": "en-US",
            "entitlements": [],
            "authorizing_integration_owners": {"0": str(GUILD)},
            "context": 0,
            "attachment_size_limit": 10 * 1024 * 1024,
            **extra,
        }

    async def settle(self) -> None:
        while True:
            busy = [
                task
                for task in asyncio.all_tasks()
                if task.get_name().startswith(_BUSY) and not task.done()
            ]
            if not busy:
                return
            await asyncio.gather(*busy, return_exceptions=True)

    async def press(
        self, custom_id: str, who: int, on: int, *, shown: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """``who`` presses the button ``custom_id`` on message ``on``. Returns the answer.

        ``shown`` replays an older state of the message: a client that has not caught
        up with an edit can still press a button that is no longer there.
        """
        shown = shown or self.messages[on]
        on_screen = [b["custom_id"] for row in shown["components"] for b in row["components"]]
        assert custom_id in on_screen, f"{custom_id} is not on message {on}: {on_screen}"
        before = len(self.callbacks)
        data = {"custom_id": custom_id, "component_type": 2}
        event = self._interaction(3, who, data, message=shown)
        self._original[event["token"]] = on
        self.state.parse_interaction_create(event)  # type: ignore[arg-type]
        await self.settle()
        assert len(self.callbacks) == before + 1, "the press was not answered exactly once"
        return self.callbacks[-1]

    async def submit(
        self, form: dict[str, Any], who: int, on: int, **values: str
    ) -> dict[str, Any]:
        """``who`` submits the modal ``form`` (as the bot sent it), typing ``values`` by label."""
        labels = [label["label"] for label in form["data"]["components"]]
        assert set(values) <= set(labels), f"no such box: {set(values) - set(labels)}"
        components = [
            {
                "type": 18,
                "id": index * 2 + 1,
                "component": {
                    "type": 4,
                    "id": index * 2 + 2,
                    "custom_id": label["component"]["custom_id"],
                    # An untouched box comes back holding what it was pre-filled with.
                    "value": values.get(label["label"], label["component"].get("value", "")),
                },
            }
            for index, label in enumerate(form["data"]["components"])
        ]
        before = len(self.callbacks)
        data = {"custom_id": form["data"]["custom_id"], "components": components}
        event = self._interaction(5, who, data, message=self.messages[on])
        self.state.parse_interaction_create(event)  # type: ignore[arg-type]
        await self.settle()
        assert len(self.callbacks) == before + 1, "the form was not answered exactly once"
        return self.callbacks[-1]

    async def command(
        self,
        name: str,
        who: int,
        options: list[dict[str, Any]] | None = None,
        resolved: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        before = len(self.callbacks)
        data = {
            "id": "4242",
            "name": name,
            "type": 1,
            "options": options or [],
            "resolved": resolved or {},
        }
        self.state.parse_interaction_create(self._interaction(2, who, data))  # type: ignore[arg-type]
        await self.settle()
        assert len(self.callbacks) == before + 1, "the command was not answered exactly once"
        return self.callbacks[-1]

    # --- reading what was sent ----------------------------------------------------------

    def posts(self, channel_id: int | None = None) -> list[dict[str, Any]]:
        """The messages posted with the bot token, oldest first, as currently on screen."""
        return [
            shown
            for shown in self.messages.values()
            if channel_id is None or shown["channel_id"] == str(channel_id)
        ]

    def sent(self, method: str = "POST") -> list[dict[str, Any]]:
        """The JSON bodies of the message requests made with ``method``."""
        return [body for verb, path, body in self.rest if verb == method and "/messages" in path]

    def last_message_id(self) -> int:
        return int(next(reversed(self.messages)))


def buttons(payload: dict[str, Any]) -> list[str]:
    return [b["custom_id"] for row in payload.get("components") or [] for b in row["components"]]


def field(payload: dict[str, Any], name: str) -> str:
    (embed,) = payload["embeds"]
    return next(f["value"] for f in embed["fields"] if f["name"] == name)
