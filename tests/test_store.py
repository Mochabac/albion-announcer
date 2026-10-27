from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from announcer.store import GuildConfig, Signup, Store

from .fakes import new_event


def test_a_new_guild_gets_the_defaults() -> None:
    store = Store(":memory:", default_language="es")
    config = store.get_config(42)
    assert config == GuildConfig(guild_id=42, language="es")
    assert (config.ping, config.channel_id, config.timezone) == ("everyone", None, "UTC")
    assert config.reminder_minutes == 15


def test_settings_round_trip_and_reset(store: Store) -> None:
    config = replace(
        store.get_config(1),
        channel_id=20,
        ping="role",
        ping_role_id=777,
        announcer_role_ids=(500, 501),
        language="es",
        timezone="America/Bogota",
        reminder_minutes=0,
    )
    store.save_config(config)
    assert store.get_config(1) == config
    assert store.get_config(2) == GuildConfig(guild_id=2)  # other guilds are untouched

    store.save_config(replace(config, ping="here", ping_role_id=None))
    assert store.get_config(1).ping == "here"

    store.reset_config(1)
    assert store.get_config(1) == GuildConfig(guild_id=1)


def test_events_round_trip(store: Store) -> None:
    event = new_event(store, starts_at=5_000, remind_at=4_100, icon="https://x/y.png")
    assert event.id == 1 and event.message_id is None
    assert event.roles == ("tank", "healer")
    assert store.get_event(event.id) == event
    assert store.get_event(999) is None

    updated = store.update_event(event.id, message_id=123, status="started", roles=("tank",))
    assert (updated.message_id, updated.status, updated.roles) == (123, "started", ("tank",))
    assert updated.is_open
    assert not store.update_event(event.id, status="finished").is_open

    store.delete_event(event.id)
    assert store.get_event(event.id) is None


def test_an_event_without_roles_keeps_an_empty_tuple(store: Store) -> None:
    assert new_event(store, roles=()).roles == ()


def test_unknown_fields_are_rejected(store: Store) -> None:
    with pytest.raises(TypeError):
        new_event(store, nonsense=1)
    event = new_event(store)
    with pytest.raises(TypeError):
        store.update_event(event.id, nonsense=1)
    with pytest.raises(TypeError):
        store.update_event(event.id, id=7)
    with pytest.raises(LookupError):
        store.update_event(999, status="finished")


def test_signups_switch_roles_and_keep_arrival_order(store: Store) -> None:
    event = new_event(store)
    assert store.set_signup(event.id, 10, "tank", at=1.0) is None
    assert store.set_signup(event.id, 11, "healer", at=2.0) is None
    assert store.set_signup(event.id, 12, "tank", at=3.0) is None
    assert store.signups(event.id) == [Signup(10, "tank"), Signup(11, "healer"), Signup(12, "tank")]

    # Signing up again under the same role changes nothing, not even the order.
    assert store.set_signup(event.id, 10, "tank", at=9.0) == "tank"
    assert [s.user_id for s in store.signups(event.id)] == [10, 11, 12]

    # Switching role moves the member to the back of the new role's queue.
    assert store.set_signup(event.id, 10, "healer", at=4.0) == "tank"
    assert store.signups(event.id) == [
        Signup(11, "healer"),
        Signup(12, "tank"),
        Signup(10, "healer"),
    ]

    assert store.remove_signup(event.id, 11) is True
    assert store.remove_signup(event.id, 11) is False
    assert [s.user_id for s in store.signups(event.id)] == [12, 10]


def test_signups_belong_to_their_event(store: Store) -> None:
    first, second = new_event(store), new_event(store)
    store.set_signup(first.id, 10, "tank", at=1.0)
    store.set_signup(second.id, 10, "healer", at=1.0)
    assert store.signups(first.id) == [Signup(10, "tank")]

    store.delete_event(first.id)
    assert store.signups(first.id) == []
    assert store.signups(second.id) == [Signup(10, "healer")]


def test_due_reminders(store: Store) -> None:
    soon = new_event(store, remind_at=100)
    later = new_event(store, remind_at=500)
    new_event(store, remind_at=None)

    assert store.due_reminders(99) == []
    assert [e.id for e in store.due_reminders(100)] == [soon.id]
    assert [e.id for e in store.due_reminders(1_000)] == [soon.id, later.id]

    store.update_event(soon.id, remind_at=None)
    assert [e.id for e in store.due_reminders(1_000)] == [later.id]


def test_everything_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "announcer.db"
    store = Store(path)
    store.save_config(replace(store.get_config(1), language="es", announcer_role_ids=(500,)))
    event = new_event(store, message_id=77)
    store.set_signup(event.id, 10, "tank", at=1.0)
    store.close()

    reopened = Store(path)
    assert reopened.get_config(1).announcer_role_ids == (500,)
    assert reopened.get_event(event.id) == event
    assert reopened.signups(event.id) == [Signup(10, "tank")]
    # Ids keep counting up, so a button from a deleted event can never hit a new one.
    reopened.delete_event(event.id)
    assert new_event(reopened).id == event.id + 1
    reopened.close()


def test_a_database_from_before_panels_were_recorded_is_brought_up_to_date(
    tmp_path: Path,
) -> None:
    path = tmp_path / "announcer.db"
    store = Store(path)
    older = new_event(store, message_id=77)
    store.close()
    db = sqlite3.connect(path)  # leave it the way the previous version made it
    db.execute("ALTER TABLE events DROP COLUMN panel_channel_id")
    db.commit()
    db.close()

    for _ in range(2):  # brought up to date once, then left alone
        store = Store(path)
        assert store.get_event(older.id) == older  # its panel is unknown
        store.close()
    store = Store(path)
    assert new_event(store, panel_channel_id=10).panel_channel_id == 10
    store.close()


def test_a_change_marks_the_announcement_out_of_date(store: Store) -> None:
    event = new_event(store, message_id=77)
    assert (event.revision, event.content_revision, event.drawn_revision) == (0, 0, 0)
    assert not event.is_stale and store.stale_events() == []

    store.touch(event.id)  # a sign-up
    changed = store.change_event(event.id, status="started")  # a caller action
    assert (changed.revision, changed.content_revision, changed.status) == (2, 0, "started")
    assert changed.is_stale and [e.id for e in store.stale_events()] == [event.id]

    moved = store.change_event(event.id, content=True, location="Thetford")
    assert (moved.revision, moved.content_revision, moved.location) == (3, 3, "Thetford")

    # Fields that do not show in the announcement leave it alone.
    assert store.update_event(event.id, remind_at=5).revision == 3


def test_drawing_only_ever_moves_forward(store: Store) -> None:
    event = new_event(store, message_id=77)
    for _ in range(3):
        store.touch(event.id)

    store.mark_drawn(event.id, 2)  # an edit that rendered revision 2 came back...
    fresh = store.get_event(event.id)
    assert fresh is not None and fresh.drawn_revision == 2 and fresh.is_stale
    store.mark_drawn(event.id, 1)  # ...and a slower, older one must not undo it
    fresh = store.get_event(event.id)
    assert fresh is not None and fresh.drawn_revision == 2

    store.mark_drawn(event.id, 3)
    assert store.stale_events() == []


def test_the_bookkeeping_cannot_be_set_by_hand(store: Store) -> None:
    event = new_event(store)
    for column in ("revision", "content_revision", "drawn_revision"):
        by_hand: dict[str, Any] = {column: 9}
        with pytest.raises(TypeError):
            store.update_event(event.id, **by_hand)
        with pytest.raises(TypeError):
            store.change_event(event.id, **by_hand)
        with pytest.raises(TypeError):
            new_event(store, **by_hand)


def test_unsent_events_are_purged_and_never_redrawn(store: Store) -> None:
    sent = new_event(store, message_id=77)
    unsent = new_event(store)
    store.touch(sent.id)
    store.touch(unsent.id)
    assert [e.id for e in store.stale_events()] == [sent.id]  # nothing to edit for the other

    assert store.purge_unsent() == 1
    assert store.get_event(unsent.id) is None and store.get_event(sent.id) is not None
    assert store.purge_unsent() == 0


def test_a_reminder_is_claimed_once_and_only_if_unchanged(store: Store) -> None:
    event = new_event(store, remind_at=100)
    assert store.claim_reminder(event.id, 100) is True
    assert store.claim_reminder(event.id, 100) is False  # already taken
    assert store.due_reminders(1_000) == []

    store.restore_reminder(event.id, 100)
    assert [e.id for e in store.due_reminders(1_000)] == [event.id]

    store.update_event(event.id, remind_at=500)  # the caller moved the activity
    assert store.claim_reminder(event.id, 100) is False
    fresh = store.get_event(event.id)
    assert fresh is not None and fresh.remind_at == 500


def test_a_reminder_is_not_restored_onto_an_event_that_moved_on(store: Store) -> None:
    rescheduled = new_event(store, remind_at=500)
    store.restore_reminder(rescheduled.id, 100)
    closed = new_event(store, status="cancelled")
    store.restore_reminder(closed.id, 100)
    assert [(e.id, e.remind_at) for e in store.due_reminders(1_000)] == [(rescheduled.id, 500)]
