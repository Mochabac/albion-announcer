"""The bot as Discord sees it: gateway payloads in, the exact JSON of every request out.

See ``wire.py``. Nothing is faked here but the network, so these tests are what stands
in for pressing the buttons in a real server.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from announcer import views
from announcer.bot import AnnouncerBot
from announcer.catalog import Catalog
from announcer.config import Settings
from announcer.service import Service
from announcer.store import Store

from .conftest import NOW, capped
from .fakes import FakeClock
from .wire import (
    ADMIN,
    ANNOUNCEMENTS,
    BOT,
    BOT_PERMISSIONS,
    GUILD,
    OFFICER,
    OFFICER_ROLE,
    PANEL_CHANNEL,
    RECRUIT,
    VOICE,
    Wire,
    buttons,
    field,
)

EPHEMERAL = 64
ACKNOWLEDGED = {"type": 6}  # "deferred update": the press is accepted, nothing is shown
THINKING = {"type": 5, "data": {"flags": EPHEMERAL}}  # a private "thinking…" placeholder
ROLE_BUTTONS = ["ao:join:1:tank", "ao:join:1:healer", "ao:join:1:dps", "ao:join:1:support"]
STRANGER = 77  # a member with no role, let into no private channel


def make_bot(store: Store, catalog: Catalog, tmp_path: Path, clock: FakeClock) -> AnnouncerBot:
    settings = Settings("token", tmp_path / "a.toml", tmp_path / "a.db", "en", "INFO")
    bot = AnnouncerBot(settings, catalog, store)
    bot.service = Service(
        bot, store, catalog, event_view=views.event_view, clock=clock, edit_spacing=0
    )
    return bot


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW.timestamp())


@pytest.fixture
async def wire(
    store: Store,
    catalog: Catalog,
    tmp_path: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Wire]:
    wire = Wire(make_bot(store, catalog, tmp_path, clock), monkeypatch)
    await wire.start()
    yield wire
    await wire.stop()


@pytest.fixture(autouse=True)
def nothing_goes_wrong_quietly(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """discord.py logs and swallows an exception in a button; that must fail the test."""
    yield
    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert not errors, [record.getMessage() for record in errors]


async def post_panel(wire: Wire) -> int:
    await wire.command("panel", ADMIN)
    return wire.last_message_id()


async def announce(wire: Wire, panel: int, key: str = "zvz", **boxes: str) -> int:
    """The officer presses a panel button and submits the form. Returns the announcement's id."""
    form = await wire.press(f"ao:new:{key}", OFFICER, on=panel)
    assert form["type"] == 9, form
    assert await wire.submit(form, OFFICER, on=panel, **boxes) == THINKING
    return wire.last_message_id()


async def open_tools(wire: Wire, announcement: int, event: int = 1) -> int:
    tools = await wire.press(f"ao:manage:{event}", OFFICER, on=announcement)
    assert tools["type"] == 4 and tools["data"]["flags"] == EPHEMERAL
    return wire.last_message_id()


# --- the main story ---------------------------------------------------------------------


async def test_panel_command(wire: Wire) -> None:
    assert await wire.command("panel", ADMIN) == THINKING
    ((method, path, panel),) = wire.rest
    assert (method, path) == ("POST", "/channels/{channel_id}/messages")
    assert panel["content"] is None
    assert panel["allowed_mentions"] == {"parse": []}
    assert panel["embeds"][0]["title"] == "📯 Activity announcer"
    assert buttons(panel)[:3] == ["ao:new:zvz", "ao:new:castle", "ao:new:smallscale"]
    assert buttons(panel)[-1] == "ao:new:custom" and len(buttons(panel)) == 14
    assert [len(row["components"]) for row in panel["components"]] == [5, 5, 4]
    # This "officers" channel is open to the whole server, which the admin is told about.
    (followup,) = wire.followups
    assert followup["content"] == (
        "Panel posted. Pin it so it stays easy to find.\n"
        "⚠️ Everyone in the server can see this channel, so anyone can announce with these "
        "buttons. To keep them for your callers, post the panel in a channel only they can "
        "see, or pick announcer roles with `/config announcer_role`."
    )
    assert followup["flags"] == EPHEMERAL


async def test_button_form_announcement(wire: Wire) -> None:
    panel = await post_panel(wire)

    form = await wire.press("ao:new:zvz", OFFICER, on=panel)
    assert form["type"] == 9  # a modal
    assert form["data"]["title"] == "⚔️ ZvZ"
    assert [label["label"] for label in form["data"]["components"]] == [
        "When",
        "Meeting point",
        "Gear / requirements",
        "Notes",
    ]
    assert len(wire.sent()) == 1  # only the panel so far: nothing is posted before the form

    answer = await wire.submit(
        form,
        OFFICER,
        on=panel,
        **{"When": "20:30", "Meeting point": "Martlock portal", "Gear / requirements": "T8"},
    )
    assert answer == THINKING

    announcement = wire.sent()[-1]
    assert announcement["content"] == "@everyone ⚔️ **ZvZ** — 20:30 UTC · 📍 Martlock portal"
    # The one thing that makes @everyone actually notify, and nothing else may ping.
    assert announcement["allowed_mentions"] == {"parse": ["everyone"]}
    (embed,) = announcement["embeds"]
    assert embed["title"] == "⚔️ ZvZ"
    assert embed["thumbnail"]["url"].endswith("/T8_MOUNT_MAMMOTH_BATTLE@1.png")
    assert field(announcement, "🕒 When").startswith("**20:30 UTC** · <t:")
    assert field(announcement, "📍 Meeting point") == "Martlock portal"
    assert field(announcement, "🛡️ Gear") == "T8"
    # The officer sits in a voice channel; the announcement links it.
    assert field(announcement, "🎙️ Voice") == f"<#{VOICE}>"
    assert embed["footer"]["text"] == "Called by Mocha · Signed up: 0"
    assert buttons(announcement) == [*ROLE_BUTTONS, "ao:leave:1", "ao:manage:1"]

    confirmation = wire.followups[-1]
    assert confirmation["flags"] == EPHEMERAL
    link = f"https://discord.com/channels/{GUILD}/{PANEL_CHANNEL}/{wire.last_message_id()}"
    assert confirmation["content"].splitlines()[0] == f"✅ Announced in <#{PANEL_CHANNEL}> → {link}"


async def test_signing_up(wire: Wire) -> None:
    announcement = await announce(wire, await post_panel(wire), When="20:30")

    assert await wire.press("ao:join:1:tank", RECRUIT, on=announcement) == ACKNOWLEDGED
    method, path, edit = wire.rest[-1]
    assert (method, path) == ("PATCH", "/channels/{channel_id}/messages/{message_id}")
    assert field(edit, "🛡️ Tank · 1") == f"<@{RECRUIT}>"
    assert edit["embeds"][0]["footer"]["text"] == "Called by Mocha · Signed up: 1"
    assert buttons(edit) == [*ROLE_BUTTONS, "ao:leave:1", "ao:manage:1"]
    assert "content" not in edit  # the line with the ping is not rewritten
    assert edit["allowed_mentions"] == {"parse": ["everyone"]}

    assert await wire.press("ao:join:1:healer", RECRUIT, on=announcement) == ACKNOWLEDGED
    assert field(wire.rest[-1][2], "🛡️ Tank · 0") == "—"
    assert field(wire.rest[-1][2], "💚 Healer · 1") == f"<@{RECRUIT}>"

    assert await wire.press("ao:leave:1", RECRUIT, on=announcement) == ACKNOWLEDGED
    assert field(wire.rest[-1][2], "💚 Healer · 0") == "—"

    edits = len(wire.sent("PATCH"))
    assert await wire.press("ao:leave:1", RECRUIT, on=announcement) == ACKNOWLEDGED
    assert len(wire.sent("PATCH")) == edits  # nothing changed, nothing redrawn


async def test_taking_a_position(
    store: Store,
    catalog: Catalog,
    tmp_path: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = Wire(make_bot(store, capped(catalog), tmp_path, clock), monkeypatch)
    await wire.start()
    try:
        announcement = await announce(wire, await post_panel(wire))
        posted = copy.deepcopy(wire.messages[announcement])
        assert field(wire.sent()[-1], "👥 Positions · 0/4") == (
            "🛡️ **Tank** — *open*\n💚 **Healer** — *open*\n"
            "⚔️ **DPS** — *open*\n✨ **Support** — *open*"
        )

        assert await wire.press("ao:join:1:tank", RECRUIT, on=announcement) == ACKNOWLEDGED
        edit = wire.rest[-1][2]
        assert field(edit, "👥 Positions · 1/4").startswith(
            f"🛡️ **Tank** — <@{RECRUIT}>\n💚 **Healer** — *open*\n"
        )
        tank, healer = edit["components"][0]["components"][:2]
        assert (tank["custom_id"], tank["style"], tank["disabled"]) == ("ao:join:1:tank", 2, True)
        assert (healer["style"], healer["disabled"]) == (1, False)
        assert edit["embeds"][0]["footer"]["text"] == "Called by Mocha"

        # A screen that has not caught up still offers the position.
        refusal = await wire.press("ao:join:1:tank", ADMIN, on=announcement, shown=posted)
        assert refusal["type"] == 4 and refusal["data"]["flags"] == EPHEMERAL
        assert refusal["data"]["content"] == (
            f"🛡️ **Tank** is already taken by <@{RECRUIT}>. Pick another position."
        )
        assert refusal["data"]["allowed_mentions"] == {"parse": []}  # telling, not pinging

        assert await wire.press("ao:leave:1", RECRUIT, on=announcement) == ACKNOWLEDGED
        edit = wire.rest[-1][2]
        assert field(edit, "👥 Positions · 0/4").startswith("🛡️ **Tank** — *open*")
        assert edit["components"][0]["components"][0]["disabled"] is False
    finally:
        await wire.stop()


async def test_a_rush_of_signups(wire: Wire) -> None:
    announcement = await announce(wire, await post_panel(wire))
    shown = dict(wire.messages[announcement])
    data = {"custom_id": "ao:join:1:dps", "component_type": 2}
    for player in range(1000, 1060):
        wire.state.parse_interaction_create(wire._interaction(3, player, data, message=shown))  # type: ignore[arg-type]
    await wire.settle()

    assert wire.callbacks[-60:] == [ACKNOWLEDGED] * 60  # every press got its answer
    patches = wire.sent("PATCH")
    assert len(patches) <= 3  # sixty presses, a couple of edits
    assert patches[-1]["embeds"][0]["footer"]["text"].endswith("Signed up: 60")
    assert field(patches[-1], "⚔️ DPS · 60").count("<@") == 60


async def test_caller_tools(wire: Wire, clock: FakeClock) -> None:
    announcement = await announce(
        wire, await post_panel(wire), **{"When": "20:30", "Meeting point": "Martlock portal"}
    )
    await wire.press("ao:join:1:tank", RECRUIT, on=announcement)

    tools = await wire.press("ao:manage:1", OFFICER, on=announcement)
    assert tools["type"] == 4 and tools["data"]["flags"] == EPHEMERAL
    assert tools["data"]["content"].startswith("⚙️ ⚔️ **ZvZ** — caller tools")
    actions = [f"ao:act:1:{name}" for name in ("remind", "start", "edit", "finish", "cancel")]
    assert buttons(tools["data"]) == actions
    tools_id = wire.last_message_id()

    # Remind: the ping again, as a reply to the announcement.
    clock.advance(125 * 60)
    assert await wire.press("ao:act:1:remind", OFFICER, on=tools_id) == ACKNOWLEDGED
    reminder = wire.sent()[-1]
    assert reminder["content"] == (
        "@everyone 🔔 Reminder: ⚔️ **ZvZ**\n"
        f"20:30 UTC (in 25 min) · 📍 Martlock portal · 🎙️ <#{VOICE}>\n"
        "Sign up on the announcement above ☝️"
    )
    assert reminder["allowed_mentions"] == {"parse": ["everyone"]}
    assert reminder["message_reference"] == {
        "type": 0,
        "message_id": announcement,
        "channel_id": PANEL_CHANNEL,
        "guild_id": GUILD,
        "fail_if_not_exists": False,
    }
    assert wire.original_edits[-1]["content"].startswith("🔔 Reminder sent.\n⚙️")
    assert buttons(wire.original_edits[-1]) == actions

    # A second reminder right away is refused, privately.
    assert await wire.press("ao:act:1:remind", OFFICER, on=tools_id) == ACKNOWLEDGED
    assert wire.followups[-1]["content"] == "A reminder just went out. Try again in 60s."
    assert wire.followups[-1]["flags"] == EPHEMERAL

    # Starting now: only the people signed up are mentioned.
    assert await wire.press("ao:act:1:start", OFFICER, on=tools_id) == ACKNOWLEDGED
    call = wire.sent()[-1]
    assert call["content"] == (
        "🚀 ⚔️ **ZvZ** is starting now — last call!\n"
        f"📍 Martlock portal · 🎙️ <#{VOICE}>\n<@{RECRUIT}>"
    )
    assert call["allowed_mentions"] == {"parse": ["users"]}
    assert wire.sent("PATCH")[-1]["embeds"][0]["description"].startswith("🚀 **In progress**")
    assert "ao:act:1:start" not in buttons(wire.messages[tools_id])  # offered only once

    # Finish: thanks, no ping, and the announcement loses its buttons.
    assert await wire.press("ao:act:1:finish", OFFICER, on=tools_id) == ACKNOWLEDGED
    thanks = wire.sent()[-1]
    assert thanks["content"] == "✅ ⚔️ **ZvZ** is over. Thanks to everyone who showed up! o7"
    assert thanks["allowed_mentions"] == {"parse": []}
    closing = wire.sent("PATCH")[-1]
    assert closing["components"] == []
    assert closing["embeds"][0]["description"].startswith("✅ **Finished**")
    assert field(closing, "🛡️ Tank · 1") == f"<@{RECRUIT}>"
    assert wire.original_edits[-1] == {
        "content": "✅ Activity closed.\n⚙️ ⚔️ **ZvZ** — caller tools",
        "components": [],
        "tts": False,
        "allowed_mentions": {"parse": []},
    }


async def test_a_press_on_a_stale_screen_is_explained(wire: Wire) -> None:
    announcement = await announce(wire, await post_panel(wire))
    before = {**wire.messages[announcement]}  # what a slow client still shows
    tools = await open_tools(wire, announcement)
    await wire.press("ao:act:1:cancel", OFFICER, on=tools)
    assert wire.messages[announcement]["components"] == []

    late = await wire.press("ao:join:1:tank", RECRUIT, on=announcement, shown=before)
    assert late["type"] == 4 and late["data"]["flags"] == EPHEMERAL
    assert late["data"]["content"] == "This activity is already closed."


async def test_editing(wire: Wire) -> None:
    announcement = await announce(
        wire, await post_panel(wire), **{"When": "20:30", "Meeting point": "Martlock portal"}
    )
    await wire.press("ao:join:1:dps", RECRUIT, on=announcement)
    tools = await open_tools(wire, announcement)

    form = await wire.press("ao:act:1:edit", OFFICER, on=tools)
    assert form["type"] == 9 and form["data"]["title"] == "Edit · ⚔️ ZvZ"
    filled = {
        label["label"]: label["component"].get("value") for label in form["data"]["components"]
    }
    assert filled == {
        "When": "20:30",
        "Meeting point": "Martlock portal",
        "Gear / requirements": None,
        "Notes": None,
    }

    posts = len(wire.sent())
    assert await wire.submit(form, OFFICER, on=tools, **{"Notes": "Bring 2 sets."}) == THINKING
    assert wire.followups[-1]["content"] == "✏️ Announcement updated."
    assert len(wire.sent()) == posts  # a note pings nobody
    assert field(wire.sent("PATCH")[-1], "📝 Notes") == "Bring 2 sets."

    form = await wire.press("ao:act:1:edit", OFFICER, on=tools)
    await wire.submit(
        form, OFFICER, on=tools, **{"When": "21:00", "Meeting point": "Thetford portal"}
    )
    redraw = wire.sent("PATCH")[-1]
    assert redraw["content"] == "@everyone ⚔️ **ZvZ** — 21:00 UTC · 📍 Thetford portal"
    assert redraw["allowed_mentions"] == {"parse": ["everyone"]}
    update = wire.sent()[-1]
    assert update["content"] == (
        "✏️ ⚔️ **ZvZ** was updated — here is the new plan:\n"
        f"21:00 UTC (in 3 h 00 min) · 📍 Thetford portal · 🎙️ <#{VOICE}>\n<@{RECRUIT}>"
    )
    assert update["allowed_mentions"] == {"parse": ["users"]}


async def test_automatic_reminder(wire: Wire, clock: FakeClock) -> None:
    announcement = await announce(wire, await post_panel(wire), When="20:30")
    await wire.press("ao:join:1:tank", RECRUIT, on=announcement)
    posts = len(wire.sent())

    clock.advance(134 * 60)
    await wire.bot.service.run_due_reminders()
    assert len(wire.sent()) == posts  # 20:14: not yet

    clock.advance(70)
    await wire.bot.service.run_due_reminders()
    # It first looks whether the announcement is still there...
    assert wire.rest[-2][:2] == ("GET", "/channels/{channel_id}/messages/{message_id}")
    reminder = wire.sent()[-1]
    assert reminder["content"] == (
        f"⏰ ⚔️ **ZvZ** starts in 15 min!\n20:30 UTC · 🎙️ <#{VOICE}>\n<@{RECRUIT}>"
    )
    assert reminder["allowed_mentions"] == {"parse": ["users"]}  # never @everyone on its own
    assert reminder["message_reference"]["message_id"] == announcement

    clock.advance(60)
    await wire.bot.service.run_due_reminders()
    assert len(wire.sent()) == posts + 1  # once


async def test_a_deleted_announcement_gets_no_reminder(wire: Wire, clock: FakeClock) -> None:
    announcement = await announce(wire, await post_panel(wire), When="20:30")
    await wire.press("ao:join:1:tank", RECRUIT, on=announcement)
    wire.deleted.add(announcement)
    posts = len(wire.sent())

    clock.advance(136 * 60)
    await wire.bot.service.run_due_reminders()
    assert len(wire.sent()) == posts
    event = wire.bot.store.get_event(1)
    assert event is not None and event.status == "cancelled"


# --- who and where ------------------------------------------------------------------------


async def test_anyone_who_can_see_the_panel_may_announce(wire: Wire) -> None:
    # The recruit holds no role and may not ping @everyone by hand; seeing the panel is enough.
    panel = await post_panel(wire)
    form = await wire.press("ao:new:zvz", RECRUIT, on=panel)
    assert form["type"] == 9
    assert await wire.submit(form, RECRUIT, on=panel) == THINKING
    assert wire.messages[wire.last_message_id()]["content"] == "@everyone ⚔️ **ZvZ** — now!"


async def test_announcer_roles_refuse_everyone_else(wire: Wire) -> None:
    store = wire.bot.store
    store.save_config(replace(store.get_config(GUILD), announcer_role_ids=(OFFICER_ROLE,)))
    panel = await post_panel(wire)
    refusal = await wire.press("ao:new:zvz", RECRUIT, on=panel)
    assert refusal["type"] == 4 and refusal["data"]["flags"] == EPHEMERAL
    assert refusal["data"]["content"] == (
        f"You can't announce activities here. You need one of these roles: <@&{OFFICER_ROLE}>."
    )
    assert refusal["data"]["allowed_mentions"] == {"parse": []}
    assert len(wire.sent()) == 1  # the panel, and no announcement


async def test_members_let_into_the_panel_channel_may_announce_and_manage(
    store: Store,
    catalog: Catalog,
    tmp_path: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The guild's own setup: the panel in a private channel that the officers' role and a
    # few members were let into one by one; announcements in a channel everyone sees.
    wire = Wire(make_bot(store, catalog, tmp_path, clock), monkeypatch)
    await wire.start(private=[PANEL_CHANNEL], guests=[BOT, RECRUIT])
    try:
        store.save_config(replace(store.get_config(GUILD), channel_id=ANNOUNCEMENTS))
        panel = await post_panel(wire)
        # Only those let in can see it, so there is nothing to warn about.
        assert wire.followups[-1]["content"] == "Panel posted. Pin it so it stays easy to find."

        form = await wire.press("ao:new:zvz", RECRUIT, on=panel)
        assert form["type"] == 9
        assert await wire.submit(form, RECRUIT, on=panel) == THINKING
        own = wire.messages[wire.last_message_id()]
        assert own["content"] == "@everyone ⚔️ **ZvZ** — now!"
        assert own["channel_id"] == str(ANNOUNCEMENTS)

        # The recruit may also run what an officer called from the same panel...
        officers = await announce(wire, panel, "fame")
        tools = await wire.press("ao:manage:2", RECRUIT, on=officers)
        assert tools["type"] == 4 and tools["data"]["flags"] == EPHEMERAL
        assert buttons(tools["data"])[0] == "ao:act:2:remind"

        # ...and someone who cannot see that panel may not.
        refusal = await wire.press("ao:manage:2", STRANGER, on=officers)
        assert refusal["type"] == 4 and refusal["data"]["flags"] == EPHEMERAL
        assert refusal["data"]["content"] == (
            "Only the caller or an officer can manage this activity."
        )
    finally:
        await wire.stop()


async def test_the_guild_owner_may_always_announce(wire: Wire) -> None:
    announcement = await announce(wire, await post_panel(wire))
    assert wire.messages[announcement]["content"] == "@everyone ⚔️ **ZvZ** — now!"
    # ...and so may the admin, who holds no role at all but owns the server.
    form = await wire.press("ao:new:fame", ADMIN, on=await post_panel(wire))
    assert form["type"] == 9


async def test_without_the_mention_permission(
    store: Store,
    catalog: Catalog,
    tmp_path: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = Wire(make_bot(store, catalog, tmp_path, clock), monkeypatch)
    await wire.start(bot_permissions=discord.Permissions(embed_links=True))
    try:
        panel = await post_panel(wire)
        warning = await wire.press("ao:new:zvz", OFFICER, on=panel)
        assert warning["type"] == 4 and warning["data"]["flags"] == EPHEMERAL
        assert warning["data"]["content"].startswith("⚠️ I can't ping `@everyone` in <#10>")
        assert buttons(warning["data"]) == ["ao:new:zvz!"]
        warning_id = wire.last_message_id()

        form = await wire.press("ao:new:zvz!", OFFICER, on=warning_id)
        assert form["type"] == 9
        await wire.submit(form, OFFICER, on=warning_id)
        announcement = wire.sent()[-1]
        assert announcement["content"] == "⚔️ **ZvZ** — now!"
        assert announcement["allowed_mentions"] == {"parse": []}
        assert "Posted without a ping" in wire.followups[-1]["content"]
    finally:
        await wire.stop()


async def test_a_private_channel_the_bot_was_not_added_to(
    store: Store,
    catalog: Catalog,
    tmp_path: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = Wire(make_bot(store, catalog, tmp_path, clock), monkeypatch)
    await wire.start(private=[PANEL_CHANNEL])
    try:
        # Discord grants nothing in a channel the bot cannot see. Naming every permission
        # it lacks there would hide the one thing to fix: letting it in.
        way_in = (
            "Open **Edit Channel → Permissions**, add me, and make sure **View Channel** "
            "is allowed."
        )
        refusal = await wire.command("panel", ADMIN)
        assert refusal["type"] == 4 and refusal["data"]["flags"] == EPHEMERAL
        assert refusal["data"]["content"] == (
            f"I can't see <#{PANEL_CHANNEL}>, so I can't post there. {way_in}"
        )
        assert wire.sent() == []

        shown = await wire.command("config", ADMIN)
        assert field(shown["data"], "Health check") == (
            f"❌ I can't see <#{PANEL_CHANNEL}>. {way_in}"
        )
    finally:
        await wire.stop()


async def test_announcing_into_a_private_channel_the_bot_was_not_added_to(
    store: Store,
    catalog: Catalog,
    tmp_path: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = Wire(make_bot(store, catalog, tmp_path, clock), monkeypatch)
    await wire.start(private=[ANNOUNCEMENTS])
    try:
        store.save_config(replace(store.get_config(GUILD), channel_id=ANNOUNCEMENTS))
        panel = await post_panel(wire)
        refusal = await wire.press("ao:new:zvz", OFFICER, on=panel)
        assert refusal["type"] == 4 and refusal["data"]["flags"] == EPHEMERAL
        assert refusal["data"]["content"].startswith(
            f"I can't see <#{ANNOUNCEMENTS}>, so I can't post there."
        )
        assert len(wire.sent()) == 1  # the panel, and no announcement
    finally:
        await wire.stop()


async def test_custom_announcement(wire: Wire) -> None:
    panel = await post_panel(wire)
    form = await wire.press("ao:new:custom", OFFICER, on=panel)
    boxes = form["data"]["components"]
    assert [label["label"] for label in boxes] == ["Title", "Message", "When", "Meeting point"]
    assert [label["component"]["required"] for label in boxes] == [True, True, False, False]

    await wire.submit(
        form, OFFICER, on=panel, **{"Title": "Guild meeting", "Message": "Everyone on voice."}
    )
    notice = wire.sent()[-1]
    assert notice["content"] == "@everyone 📢 **Guild meeting**"
    (embed,) = notice["embeds"]
    assert (embed["title"], embed["description"]) == ("📢 Guild meeting", "Everyone on voice.")
    assert [f["name"] for f in embed.get("fields", [])] == ["🎙️ Voice"]
    assert buttons(notice) == ["ao:manage:1"]


async def test_what_a_caller_types_cannot_ping(wire: Wire) -> None:
    panel = await post_panel(wire)
    form = await wire.press("ao:new:zvz", OFFICER, on=panel)
    await wire.submit(
        form,
        OFFICER,
        on=panel,
        **{"Meeting point": f"<@&{OFFICER_ROLE}> <@{RECRUIT}> @here", "Notes": "@everyone"},
    )
    announcement = wire.sent()[-1]
    # In the line that pings: defused. And the request allows @everyone, nothing else.
    assert f"<@&{OFFICER_ROLE}>" not in announcement["content"]
    assert "@here" not in announcement["content"]
    assert announcement["content"].count("@everyone") == 1
    assert announcement["allowed_mentions"] == {"parse": ["everyone"]}


# --- /config --------------------------------------------------------------------------------


async def test_config_command(wire: Wire) -> None:
    shown = await wire.command("config", ADMIN)
    assert shown["type"] == 4 and shown["data"]["flags"] == EPHEMERAL
    assert "content" not in shown["data"] or shown["data"]["content"] is None
    assert field(shown["data"], "Ping") == "@everyone"
    assert field(shown["data"], "Health check") == (
        f"✅ I have every permission I need in <#{PANEL_CHANNEL}>."
    )

    saved = await wire.command(
        "config",
        ADMIN,
        options=[
            {"name": "channel", "type": 7, "value": str(ANNOUNCEMENTS)},
            {"name": "ping", "type": 3, "value": "here"},
            {"name": "announcer_role", "type": 8, "value": str(OFFICER_ROLE)},
            {"name": "language", "type": 3, "value": "es"},
            {"name": "reminder", "type": 4, "value": 30},
            {"name": "timezone", "type": 3, "value": "america/bogota"},
        ],
        resolved={
            "channels": {
                str(ANNOUNCEMENTS): {
                    "id": str(ANNOUNCEMENTS),
                    "type": 0,
                    "name": "announcements",
                    "permissions": "0",
                }
            },
            "roles": {
                str(OFFICER_ROLE): {
                    "id": str(OFFICER_ROLE),
                    "name": "Officer",
                    "permissions": "0",
                    "position": 1,
                    "color": 0,
                    "hoist": False,
                    "managed": False,
                    "mentionable": False,
                }
            },
        },
    )
    assert saved["data"]["content"] == "Guardado."
    assert field(saved["data"], "Canal de anuncios") == f"<#{ANNOUNCEMENTS}>"
    assert field(saved["data"], "Ping") == "@here"
    assert field(saved["data"], "Quién puede anunciar") == f"Los admins y <@&{OFFICER_ROLE}>"
    assert field(saved["data"], "Zona horaria de las horas escritas") == "America/Bogota"

    config = wire.bot.store.get_config(GUILD)
    assert (config.channel_id, config.ping, config.language) == (ANNOUNCEMENTS, "here", "es")
    assert (config.announcer_role_ids, config.reminder_minutes) == ((OFFICER_ROLE,), 30)

    # From now on: Spanish, @here, in #announcements, times typed in Bogotá time.
    panel = await post_panel(wire)
    assert wire.sent()[-1]["embeds"][0]["title"] == "📯 Anuncio de actividades"
    form = await wire.press("ao:new:gathering", OFFICER, on=panel)
    assert form["data"]["components"][0]["description"].endswith("(America/Bogota)")
    await wire.submit(form, OFFICER, on=panel, **{"Cuándo": "8pm"})

    method, path, announcement = wire.rest[-1]
    assert (method, path) == ("POST", "/channels/{channel_id}/messages")
    assert wire.messages[wire.last_message_id()]["channel_id"] == str(ANNOUNCEMENTS)
    assert announcement["content"] == "@here ⛏️ **Recolección** — lun 5 oct · 01:00 UTC"
    assert announcement["allowed_mentions"] == {"parse": ["everyone"]}
    labels = [b["label"] for row in announcement["components"] for b in row["components"]]
    assert labels == ["Voy", "Scout", "Salir", "Gestionar"]


async def test_config_rejects_bad_input_privately(wire: Wire) -> None:
    refusal = await wire.command(
        "config", ADMIN, options=[{"name": "timezone", "type": 3, "value": "Narnia"}]
    )
    assert refusal["type"] == 4 and refusal["data"]["flags"] == EPHEMERAL
    assert refusal["data"]["content"].startswith("I don't know the time zone `Narnia`.")


async def test_role_ping_on_the_wire(wire: Wire) -> None:
    wire.bot.store.save_config(
        replace(wire.bot.store.get_config(GUILD), ping="role", ping_role_id=OFFICER_ROLE)
    )
    await announce(wire, await post_panel(wire))
    announcement = wire.sent()[-1]
    assert announcement["content"] == f"<@&{OFFICER_ROLE}> ⚔️ **ZvZ** — now!"
    assert announcement["allowed_mentions"] == {"parse": [], "roles": [OFFICER_ROLE]}


async def test_time_zone_autocomplete(wire: Wire) -> None:
    data = {
        "id": "4242",
        "name": "config",
        "type": 1,
        "options": [{"name": "timezone", "type": 3, "value": "bogo", "focused": True}],
    }
    wire.state.parse_interaction_create(wire._interaction(4, ADMIN, data))  # type: ignore[arg-type]
    await wire.settle()
    assert wire.callbacks[-1] == {
        "type": 8,
        "data": {"choices": [{"name": "America/Bogota", "value": "America/Bogota"}]},
    }


# --- failures and restarts ---------------------------------------------------------------------


async def test_a_refused_post_reaches_the_caller(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = await post_panel(wire)
    form = await wire.press("ao:new:zvz", OFFICER, on=panel)

    async def refuse(route: Any, **kwargs: Any) -> Any:
        response = SimpleNamespace(status=403, reason="Forbidden")
        raise discord.Forbidden(response, {"code": 50013, "message": "Missing Permissions"})  # type: ignore[arg-type]

    monkeypatch.setattr(wire.bot.http, "request", refuse)
    assert await wire.submit(form, OFFICER, on=panel) == THINKING
    assert wire.followups[-1] == {
        "content": f"Discord refused my message in <#{PANEL_CHANNEL}> (Missing Permissions).",
        "tts": False,
        "flags": EPHEMERAL,
        "allowed_mentions": {"parse": []},
    }
    assert wire.bot.store.get_event(1) is None


async def test_buttons_survive_a_restart(
    wire: Wire,
    store: Store,
    catalog: Catalog,
    tmp_path: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel = await post_panel(wire)
    announcement = await announce(wire, panel, When="20:30")
    await wire.stop()

    # A new process: nothing in memory, the same database, the same messages on screen.
    reborn = Wire(make_bot(store, catalog, tmp_path, clock), monkeypatch)
    reborn.messages = wire.messages
    await reborn.start()
    try:
        assert await reborn.press("ao:join:1:healer", RECRUIT, on=announcement) == ACKNOWLEDGED
        assert field(reborn.sent("PATCH")[-1], "💚 Healer · 1") == f"<@{RECRUIT}>"
        assert (await reborn.press("ao:new:fame", OFFICER, on=panel))["type"] == 9
        tools = await reborn.press("ao:manage:1", OFFICER, on=announcement)
        assert buttons(tools["data"])[0] == "ao:act:1:remind"
    finally:
        await reborn.stop()


async def test_a_form_left_open_across_a_restart_is_answered(
    wire: Wire,
    store: Store,
    catalog: Catalog,
    tmp_path: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel = await post_panel(wire)
    form = await wire.press("ao:new:zvz", OFFICER, on=panel)
    await wire.stop()
    monkeypatch.setattr(views.AnnounceForm, "_expected", {})  # a new process remembers no forms

    reborn = Wire(make_bot(store, catalog, tmp_path, clock), monkeypatch)
    reborn.messages = wire.messages
    await reborn.start()
    try:
        answer_to_old_form = {"custom_id": form["data"]["custom_id"], "components": []}
        late = reborn._interaction(5, OFFICER, answer_to_old_form, message=reborn.messages[panel])
        reborn.state.parse_interaction_create(late)  # type: ignore[arg-type]
        await reborn.settle()
        await asyncio.sleep(0.01)  # the listener runs as its own task
        (answer,) = reborn.callbacks
        assert answer["type"] == 4 and answer["data"]["flags"] == EPHEMERAL
        assert answer["data"]["content"].startswith("That form is no longer valid")
        assert len(reborn.sent()) == 0  # and nothing was announced
    finally:
        await reborn.stop()


async def test_startup_catches_up_and_cleans_up(
    wire: Wire,
    store: Store,
    catalog: Catalog,
    tmp_path: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    announcement = await announce(wire, await post_panel(wire))
    # The old process recorded a sign-up and died before redrawing the announcement.
    store.set_signup(1, RECRUIT, "tank", at=1.0)
    store.touch(1)
    await wire.stop()

    bot = make_bot(store, catalog, tmp_path, clock)
    reborn = Wire(bot, monkeypatch)
    await bot._async_setup_hook()
    reborn.state.application_id = 900
    try:
        await bot.setup_hook()
        await reborn.settle()
        method, path, redraw = reborn.rest[-1]
        assert (method, path) == ("PATCH", "/channels/{channel_id}/messages/{message_id}")
        assert field(redraw, "🛡️ Tank · 1") == f"<@{RECRUIT}>"
        assert announcement in reborn.messages
    finally:
        bot.reminders.cancel()
        await reborn.stop()


async def test_startup_registers_the_commands(
    store: Store,
    catalog: Catalog,
    tmp_path: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = make_bot(store, catalog, tmp_path, clock)
    wire = Wire(bot, monkeypatch)
    await bot._async_setup_hook()
    wire.state.application_id = 900
    try:
        await bot.setup_hook()
        ((method, path, body),) = wire.rest
        assert (method, path) == ("PUT", "/applications/{application_id}/commands")
        registered: list[dict[str, Any]] = body  # type: ignore[assignment]
        assert sorted(command["name"] for command in registered) == ["config", "panel"]
        for command in registered:
            # Offered only in a server the bot was added to: not in DMs, and not to
            # someone who added the app to their own account, where it could not post.
            assert command["integration_types"] == [0], command["name"]
            assert command["contexts"] == [0], command["name"]
            assert command["dm_permission"] is False, command["name"]
            manage_server = discord.Permissions(manage_guild=True).value
            assert command["default_member_permissions"] == manage_server, command["name"]
        patterns = {cls.__discord_ui_compiled_template__ for cls in views.DYNAMIC_ITEMS}
        assert patterns <= set(wire.state._view_store._dynamic_items)
        assert bot.reminders.is_running()
    finally:
        bot.reminders.cancel()
        await wire.stop()


def test_the_test_guild_matches_the_invite_link() -> None:
    # The fake guild grants the bot what the invite link asks for, no more.
    from announcer.bot import INVITE_PERMISSIONS

    granted = BOT_PERMISSIONS | discord.Permissions(
        view_channel=True, send_messages=True, read_message_history=True
    )
    assert granted == INVITE_PERMISSIONS
