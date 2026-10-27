"""What happens when Discord, the network or the timing does not cooperate."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from announcer import views
from announcer.errors import UserError
from announcer.service import (
    EDIT_SPACING,
    OLD_EDIT_SPACING,
    OLD_MESSAGE_AGE,
    REMIND_COOLDOWN,
    Draft,
    Service,
)
from announcer.store import Event

from .conftest import OFFICER_ROLE, World
from .fakes import FakeMember, forbidden, new_event, not_found, unavailable
from .test_service import AT_2030, T0, announce, configure, field


def stored(world: World, event: Event) -> Event:
    fresh = world.store.get_event(event.id)
    assert fresh is not None
    return fresh


# --- a redraw that fails is caught up later ---------------------------------------------


async def test_a_failed_roster_redraw_is_retried(world: World) -> None:
    event = await announce(world)
    world.panel.fail_edit = unavailable()  # Discord answers 503 to the edit

    world.service.signup(world.guild.id, event.id, 100, "tank")
    await world.service.drain()
    assert world.panel.edits == []
    assert stored(world, event).is_stale  # the database remembers it is not on screen

    # Pressing the same role again changes nothing in the database, so it cannot be
    # what repairs the announcement...
    world.service.signup(world.guild.id, event.id, 100, "tank")
    await world.service.drain()
    assert world.panel.edits == []

    # ...the periodic catch-up is.
    world.panel.fail_edit = None
    world.service.redraw_stale()
    await world.service.drain()
    assert field(world.panel.last_edit()["embed"], "🛡️ Tank · 1") == "<@100>"
    assert not stored(world, event).is_stale

    world.service.redraw_stale()  # and once it is drawn, there is nothing left to do
    await world.service.drain()
    assert len(world.panel.edits) == 1


async def test_a_failed_closing_redraw_still_removes_the_buttons(world: World) -> None:
    event = await announce(world)
    world.panel.fail_edit = unavailable()
    await world.service.finish(world.guild, world.officer, event.id)
    await world.service.drain()
    assert world.panel.edits == []  # finished in the database, still open on screen

    world.panel.fail_edit = None
    world.service.redraw_stale()
    await world.service.drain()
    assert world.panel.last_edit()["view"] is None
    assert world.panel.last_edit()["embed"].description.startswith("✅ **Finished**")


async def test_a_failed_edit_redraw_still_rewrites_the_ping_line(world: World) -> None:
    event = await announce(world, when="20:30")
    world.panel.fail_edit = unavailable()
    await world.service.edit(world.guild, world.officer, event.id, Draft(when="21:00"))
    await world.service.drain()

    # A sign-up in between must not make the bot forget that the line is out of date.
    world.panel.fail_edit = None
    world.service.signup(world.guild.id, event.id, 100, "tank")
    await world.service.drain()
    redrawn = world.panel.last_edit()
    assert redrawn["content"] == "@everyone ⚔️ **ZvZ** — 21:00 UTC"
    assert field(redrawn["embed"], "🛡️ Tank · 1") == "<@100>"

    # Once drawn, later redraws leave the line alone again.
    world.service.signup(world.guild.id, event.id, 101, "healer")
    await world.service.drain()
    assert "content" not in world.panel.last_edit()


async def test_only_a_changed_ping_line_is_rewritten(world: World) -> None:
    event = await announce(world, when="20:30", location="Martlock portal")
    await world.service.edit(
        world.guild,
        world.officer,
        event.id,
        Draft(when="20:30", location="Martlock portal", gear="T8", notes="Regear."),
    )
    await world.service.drain()
    assert "content" not in world.panel.last_edit()  # gear and notes live in the embed only


async def test_a_restart_catches_up_on_what_was_left_undrawn(world: World) -> None:
    event = await announce(world, when="20:30")
    world.panel.fail_edit = unavailable()
    world.service.signup(world.guild.id, event.id, 100, "tank")
    await world.service.cancel(world.guild, world.officer, event.id)
    await world.service.drain()
    # Half-way through announcing, the old process died: a row, and no message.
    orphan = new_event(world.store, guild_id=world.guild.id, channel_id=world.panel.id)
    world.panel.fail_edit = None

    reborn = Service(
        world.client, world.store, world.catalog, event_view=views.event_view, edit_spacing=0
    )
    reborn.resume()
    await reborn.drain()

    ((message_id, redrawn),) = world.panel.edits
    assert message_id == event.message_id and redrawn["view"] is None
    assert redrawn["embed"].description.startswith("🚫 **Cancelled**")
    assert world.store.get_event(orphan.id) is None
    assert world.store.get_event(event.id) is not None


def bad_request() -> discord.HTTPException:
    response = SimpleNamespace(status=400, reason="Bad Request")
    return discord.HTTPException(response, {"code": 50035, "message": "Invalid Form Body"})  # type: ignore[arg-type]


@pytest.mark.parametrize("refusal", [forbidden, bad_request])
async def test_an_edit_discord_refuses_outright_is_not_retried(
    world: World, refusal: Any, caplog: pytest.LogCaptureFixture
) -> None:
    event = await announce(world)
    world.panel.fail_edit = refusal()  # shut out of the channel, or the edit itself is rejected
    world.service.signup(world.guild.id, event.id, 100, "tank")
    await world.service.drain()
    assert not stored(world, event).is_stale  # nothing a retry could fix
    world.service.redraw_stale()
    await world.service.drain()
    assert world.panel.edits == []
    assert caplog.text.count("Discord refuses to redraw") == 1  # said once, not every tick

    world.panel.fail_edit = None  # let back in: the next change draws everything
    world.service.signup(world.guild.id, event.id, 101, "healer")
    await world.service.drain()
    embed = world.panel.last_edit()["embed"]
    assert field(embed, "🛡️ Tank · 1") == "<@100>" and field(embed, "💚 Healer · 1") == "<@101>"


async def test_a_bug_while_drawing_is_reported_once(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    event = await announce(world)

    def explode(*args: Any) -> None:
        raise RuntimeError("no such role")

    monkeypatch.setattr("announcer.render.announcement_embed", explode)
    world.service.signup(world.guild.id, event.id, 100, "tank")
    await world.service.drain()
    for _ in range(3):  # the ticks that follow do not try the same thing again
        world.service.redraw_stale()
        await world.service.drain()
    assert caplog.text.count("RuntimeError: no such role") == 1
    assert world.store.signups(event.id) != []  # the sign-up itself is safe


async def test_older_announcements_are_redrawn_in_bigger_batches(world: World) -> None:
    service = Service(
        world.client, world.store, world.catalog, event_view=views.event_view, clock=world.clock
    )
    done = await service.announce(world.guild, world.officer, world.panel, "zvz", Draft())
    assert service._edit_spacing(done.event.id) == EDIT_SPACING
    world.clock.advance(OLD_MESSAGE_AGE)
    assert service._edit_spacing(done.event.id) == OLD_EDIT_SPACING
    assert world.service._edit_spacing(done.event.id) == 0  # the tests' own setting


# --- announcing ----------------------------------------------------------------------------


@pytest.mark.parametrize("failure", [TimeoutError(), ConnectionResetError("reset by peer")])
async def test_a_network_failure_while_announcing_leaves_nothing_behind(
    world: World, failure: Exception
) -> None:
    world.panel.fail_send = failure
    with pytest.raises(UserError, match="Discord refused my message"):
        await announce(world, when="20:30")
    assert world.store.get_event(1) is None
    assert world.store.due_reminders(T0 + 10**6) == []


async def test_being_shut_down_while_announcing_leaves_nothing_behind(world: World) -> None:
    world.panel.fail_send = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await announce(world, when="20:30")
    assert world.store.get_event(1) is None


async def test_a_signup_that_beats_the_message_id_is_still_drawn(world: World) -> None:
    # Discord shows the announcement a moment before it tells the bot its id; a fast
    # member can press a role in between.
    world.panel.while_sending = lambda: world.service.signup(world.guild.id, 1, 100, "tank")
    event = await announce(world)
    await world.service.drain()

    ((message_id, redrawn),) = world.panel.edits
    assert message_id == event.message_id
    assert field(redrawn["embed"], "🛡️ Tank · 1") == "<@100>"


# --- caller tools --------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["remind", "start", "finish", "cancel"])
async def test_tools_notice_a_deleted_announcement(world: World, action: str) -> None:
    event = await announce(world, when="20:30")
    world.panel.deleted.add(event.message_id or 0)  # a moderator removed it

    with pytest.raises(UserError, match="announcement was deleted"):
        await getattr(world.service, action)(world.guild, world.officer, event.id)
    assert len(world.panel.sent) == 1  # no "sign up on the announcement above" into the void
    assert (stored(world, event).status, stored(world, event).remind_at) == ("cancelled", None)

    with pytest.raises(UserError, match="already closed"):
        await world.service.remind(world.guild, world.officer, event.id)


async def test_editing_a_deleted_announcement_is_refused(world: World) -> None:
    event = await announce(world)
    world.panel.deleted.add(event.message_id or 0)
    with pytest.raises(UserError, match="announcement was deleted"):
        await world.service.edit(world.guild, world.officer, event.id, Draft(notes="x"))
    assert stored(world, event).notes == ""


async def test_a_reminder_discord_refused_does_not_start_the_cooldown(world: World) -> None:
    event = await announce(world)
    world.panel.fail_send = unavailable()
    with pytest.raises(UserError, match="Discord refused"):
        await world.service.remind(world.guild, world.officer, event.id)

    world.panel.fail_send = None
    await world.service.remind(world.guild, world.officer, event.id)  # right away
    assert (world.panel.sent[-1].content or "").startswith("@everyone 🔔 Reminder")
    with pytest.raises(UserError, match="Try again in 60s"):
        await world.service.remind(world.guild, world.officer, event.id)
    world.clock.advance(REMIND_COOLDOWN)
    await world.service.remind(world.guild, world.officer, event.id)


@pytest.mark.parametrize(
    ("action", "status"), [("start", "started"), ("finish", "finished"), ("cancel", "cancelled")]
)
async def test_a_refused_call_can_be_repeated(world: World, action: str, status: str) -> None:
    event = await announce(world, when="20:30")
    world.service.signup(world.guild.id, event.id, 100, "tank")
    await world.service.drain()
    edits = len(world.panel.edits)

    world.panel.fail_send = unavailable()
    with pytest.raises(UserError, match="Discord refused"):
        await getattr(world.service, action)(world.guild, world.officer, event.id)
    await world.service.drain()
    # Nothing was said, so nothing changed: not the status, not the announcement.
    assert stored(world, event).status == "open"
    assert stored(world, event).remind_at == event.remind_at
    assert len(world.panel.edits) == edits

    world.panel.fail_send = None
    done = await getattr(world.service, action)(world.guild, world.officer, event.id)
    assert done.status == status and len(world.panel.sent) == 2


async def test_two_officers_pressing_start_together_call_the_group_once(world: World) -> None:
    event = await announce(world)
    world.panel.fetch_delay = 0.01  # long enough for the second press to arrive
    results = await asyncio.gather(
        world.service.start(world.guild, world.officer, event.id),
        world.service.start(world.guild, world.admin, event.id),
        return_exceptions=True,
    )
    assert [type(result).__name__ for result in results] == ["Event", "UserError"]
    assert "already marked as started" in str(results[1])
    calls = [m for m in world.panel.sent if "starting now" in (m.content or "")]
    assert len(calls) == 1


async def test_an_edit_reports_an_unreadable_time(world: World) -> None:
    event = await announce(world, when="20:30")
    edited = await world.service.edit(world.guild, world.officer, event.id, Draft(when="21h"))
    assert (edited.event.when_kind, edited.event.remind_at) == ("text", None)
    assert len(edited.notes) == 1 and "couldn't read the time" in edited.notes[0]


async def test_an_edit_survives_a_notice_discord_refuses(world: World) -> None:
    event = await announce(world, when="20:30")
    world.service.signup(world.guild.id, event.id, 100, "tank")
    world.panel.fail_send = unavailable()

    edited = await world.service.edit(world.guild, world.officer, event.id, Draft(when="21:00"))
    await world.service.drain()
    # The announcement is updated; the caller learns that the notice did not go out.
    assert edited.event.starts_at == AT_2030 + 1800
    assert world.panel.last_edit()["content"] == "@everyone ⚔️ **ZvZ** — 21:00 UTC"
    assert edited.notes[-1].startswith("⚠️ Discord refused my message")


# --- automatic reminders ----------------------------------------------------------------------


async def two_due_reminders(world: World) -> tuple[Event, Event]:
    """Two activities at 20:30, one sign-up each, and the clock at 20:16."""
    first = await announce(world, when="20:30")
    second = await announce(world, "fame", member=world.admin, when="20:30")
    world.service.signup(world.guild.id, first.id, 100, "tank")
    world.service.signup(world.guild.id, second.id, 101, "dps")
    await world.service.drain()
    world.clock.advance(136 * 60)
    return first, second


def reminders(world: World) -> list[str]:
    return [m.content or "" for m in world.panel.sent if (m.content or "").startswith("⏰")]


async def tick_busy_with_the_first(
    world: World, *, caller_is_done_first: bool
) -> asyncio.Task[None]:
    """Start a reminder tick and return while it is still sending the first reminder.

    What the caller does to the second activity meanwhile either completes before the
    tick gets to it, or is still under way when it does. Both must come out right.
    """
    world.panel.fetch_delay = 0.02
    tick = asyncio.create_task(world.service.run_due_reminders())
    await asyncio.sleep(0)
    world.panel.fetch_delay = 0 if caller_is_done_first else 0.05
    return tick


@pytest.mark.parametrize("caller_is_done_first", [True, False])
async def test_an_activity_cancelled_during_the_tick_gets_no_reminder(
    world: World, caller_is_done_first: bool
) -> None:
    _, second = await two_due_reminders(world)
    tick = await tick_busy_with_the_first(world, caller_is_done_first=caller_is_done_first)
    await world.service.cancel(world.guild, world.admin, second.id)
    await tick

    assert len(reminders(world)) == 1 and "**ZvZ**" in reminders(world)[0]
    announced_as_off = [m.content or "" for m in world.panel.sent].index(
        "🚫 📚 **Fame Farm** has been cancelled.\n<@101>"
    )
    assert not any("Fame Farm" in text for text in reminders(world))
    assert announced_as_off > 0


@pytest.mark.parametrize("caller_is_done_first", [True, False])
async def test_an_activity_moved_during_the_tick_keeps_its_new_reminder(
    world: World, caller_is_done_first: bool
) -> None:
    _, second = await two_due_reminders(world)
    tick = await tick_busy_with_the_first(world, caller_is_done_first=caller_is_done_first)
    await world.service.edit(world.guild, world.admin, second.id, Draft(when="22:00"))
    await tick

    assert len(reminders(world)) == 1  # nothing quoting the old 20:30
    assert stored(world, second).remind_at == AT_2030 + 75 * 60  # 21:45

    world.clock.advance(90 * 60)  # 21:46
    world.panel.fetch_delay = 0
    await world.service.run_due_reminders()
    assert "**Fame Farm** starts in 14 min" in reminders(world)[-1]
    assert "22:00 UTC" in reminders(world)[-1]


async def test_a_reminder_that_could_not_be_sent_is_tried_again(world: World) -> None:
    event = await announce(world, when="20:30")
    world.service.signup(world.guild.id, event.id, 100, "tank")
    world.clock.advance(135 * 60 + 5)

    world.panel.fail_send = unavailable()
    await world.service.run_due_reminders()
    assert reminders(world) == [] and stored(world, event).remind_at == event.remind_at

    world.clock.advance(20)
    world.panel.fail_send = None
    await world.service.run_due_reminders()
    assert len(reminders(world)) == 1
    await world.service.run_due_reminders()
    assert len(reminders(world)) == 1 and stored(world, event).remind_at is None


async def test_reminder_retries_stop_at_the_start_time(world: World) -> None:
    event = await announce(world, when="20:30")
    world.service.signup(world.guild.id, event.id, 100, "tank")
    world.panel.fail_send = unavailable()
    world.clock.advance(140 * 60)
    await world.service.run_due_reminders()
    assert stored(world, event).remind_at is not None

    world.clock.advance(11 * 60)  # 20:31: reminding now would be telling people they are late
    world.panel.fail_send = None
    await world.service.run_due_reminders()
    assert reminders(world) == [] and stored(world, event).remind_at is None


@pytest.mark.parametrize("refusal", [forbidden, not_found])
async def test_a_reminder_that_can_never_be_sent_is_not_retried(world: World, refusal: Any) -> None:
    event = await announce(world, when="20:30")
    world.service.signup(world.guild.id, event.id, 100, "tank")
    world.clock.advance(136 * 60)
    world.panel.fail_send = refusal()
    await world.service.run_due_reminders()
    assert stored(world, event).remind_at is None


# --- announcer roles that no longer exist ---------------------------------------------------------


async def test_a_deleted_announcer_role_locks_nobody_out(world: World) -> None:
    configure(world, announcer_role_ids=(999,))  # the only announcer role was deleted
    assert world.service.announcer_roles(world.guild, world.store.get_config(1)) == ()
    # Back to the default, where the panel decides: whoever can press it may announce.
    await announce(world, member=FakeMember(9, "Guest"))


async def test_remaining_announcer_roles_still_decide(world: World) -> None:
    configure(world, announcer_role_ids=(OFFICER_ROLE, 999))
    veteran = FakeMember(9, "Veteran")
    world.panel.member_permissions[veteran.id] = world.panel.member_permissions[world.officer.id]
    with pytest.raises(UserError) as refusal:
        await announce(world, member=veteran)
    assert f"<@&{OFFICER_ROLE}>" in str(refusal.value) and "999" not in str(refusal.value)
    await announce(world)


def test_fakes_fail_the_way_discord_does() -> None:
    # discord.py retries 500/502/504 itself and hands a 503 straight to the caller.
    assert isinstance(unavailable(), discord.HTTPException) and unavailable().status == 503
    assert replace(new_event_defaults(), status="open").is_open


def new_event_defaults() -> Event:
    from announcer.store import Store

    return new_event(Store(":memory:"))
