"""SQLite persistence: per-server settings, announced activities and their sign-ups.

The calls are synchronous on purpose. Each one touches a handful of rows in a
local file and returns in microseconds, far below anything the event loop would
notice, and it keeps "write, then render what was written" atomic between awaits.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from .i18n import DEFAULT_LANGUAGE

PINGS = ("everyone", "here", "role", "none")
DEFAULT_REMINDER_MINUTES = 15

_SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id         INTEGER PRIMARY KEY,
    channel_id       INTEGER,
    ping             TEXT    NOT NULL,
    ping_role_id     INTEGER,
    announcer_roles  TEXT    NOT NULL,
    language         TEXT    NOT NULL,
    timezone         TEXT    NOT NULL,
    reminder_minutes INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id         INTEGER NOT NULL,
    channel_id       INTEGER NOT NULL,
    panel_channel_id INTEGER,
    message_id       INTEGER,
    activity         TEXT    NOT NULL,
    language         TEXT    NOT NULL,
    emoji            TEXT    NOT NULL,
    title            TEXT    NOT NULL,
    message          TEXT    NOT NULL,
    color            INTEGER NOT NULL,
    icon             TEXT,
    roles            TEXT    NOT NULL,
    ping             TEXT    NOT NULL,
    ping_role_id     INTEGER,
    author_id        INTEGER NOT NULL,
    author_name      TEXT    NOT NULL,
    when_kind        TEXT    NOT NULL,
    when_text        TEXT    NOT NULL,
    starts_at        INTEGER,
    location         TEXT    NOT NULL,
    gear             TEXT    NOT NULL,
    notes            TEXT    NOT NULL,
    voice_channel_id INTEGER,
    status           TEXT    NOT NULL,
    remind_at        INTEGER,
    created_at       INTEGER NOT NULL,
    revision         INTEGER NOT NULL DEFAULT 0,
    content_revision INTEGER NOT NULL DEFAULT 0,
    drawn_revision   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS events_remind_at ON events (remind_at) WHERE remind_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS signups (
    event_id  INTEGER NOT NULL REFERENCES events (id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL,
    role      TEXT    NOT NULL,
    joined_at REAL    NOT NULL,
    PRIMARY KEY (event_id, user_id)
);
"""


@dataclass(frozen=True)
class GuildConfig:
    guild_id: int
    # None: announce in whatever channel the button was pressed in.
    channel_id: int | None = None
    ping: str = "everyone"
    ping_role_id: int | None = None
    # Empty: anyone who can see a panel may announce.
    announcer_role_ids: tuple[int, ...] = ()
    language: str = DEFAULT_LANGUAGE
    timezone: str = "UTC"
    reminder_minutes: int = DEFAULT_REMINDER_MINUTES


@dataclass(frozen=True)
class Event:
    """One announced activity.

    The title, colour, texts and sign-up roles are copied in when it is announced,
    so an announcement keeps working after activities.toml or the settings change.
    """

    id: int
    guild_id: int
    channel_id: int
    # Where the panel it was called from is: whoever can see that panel may run it.
    # None for announcements made before this was recorded.
    panel_channel_id: int | None
    message_id: int | None
    activity: str
    language: str
    emoji: str
    title: str
    message: str
    color: int
    icon: str | None
    roles: tuple[str, ...]
    ping: str
    ping_role_id: int | None
    author_id: int
    author_name: str
    # now | at | text | none.  `when_text` is always what the caller typed.
    when_kind: str
    when_text: str
    starts_at: int | None
    location: str
    gear: str
    notes: str
    voice_channel_id: int | None
    # open | started | finished | cancelled
    status: str
    remind_at: int | None
    created_at: int
    # Whether the announcement on screen matches this row. `revision` counts the changes
    # that show in the announcement, `content_revision` is the last one that changed its
    # ping line, `drawn_revision` the last one Discord confirmed drawing. They live in
    # the database so that a failed or interrupted edit is caught up later.
    revision: int
    content_revision: int
    drawn_revision: int

    @property
    def is_open(self) -> bool:
        return self.status in ("open", "started")

    @property
    def is_stale(self) -> bool:
        return self.drawn_revision < self.revision


@dataclass(frozen=True)
class Signup:
    user_id: int
    role: str


_EVENT_COLUMNS = tuple(f.name for f in fields(Event))
# Columns the store keeps itself.
_BOOKKEEPING = frozenset({"id", "revision", "content_revision", "drawn_revision"})


def _event(row: sqlite3.Row) -> Event:
    data = dict(row)
    data["roles"] = tuple(filter(None, data["roles"].split(",")))
    return Event(**data)


class Store:
    def __init__(self, path: Path | str, *, default_language: str = DEFAULT_LANGUAGE) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: autocommit. Every method below is one statement.
        self._db = sqlite3.connect(str(path), isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)
        columns = {row["name"] for row in self._db.execute("PRAGMA table_info(events)")}
        if "panel_channel_id" not in columns:
            # A database made before announcements remembered their panel.
            self._db.execute("ALTER TABLE events ADD COLUMN panel_channel_id INTEGER")
        self._default_language = default_language

    def close(self) -> None:
        self._db.close()

    # --- server settings -------------------------------------------------------

    def get_config(self, guild_id: int) -> GuildConfig:
        row = self._db.execute(
            "SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)
        ).fetchone()
        if row is None:
            return GuildConfig(guild_id=guild_id, language=self._default_language)
        return GuildConfig(
            guild_id=row["guild_id"],
            channel_id=row["channel_id"],
            ping=row["ping"],
            ping_role_id=row["ping_role_id"],
            announcer_role_ids=tuple(int(x) for x in row["announcer_roles"].split(",") if x),
            language=row["language"],
            timezone=row["timezone"],
            reminder_minutes=row["reminder_minutes"],
        )

    def save_config(self, config: GuildConfig) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO guild_config VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                config.guild_id,
                config.channel_id,
                config.ping,
                config.ping_role_id,
                ",".join(str(role_id) for role_id in config.announcer_role_ids),
                config.language,
                config.timezone,
                config.reminder_minutes,
            ),
        )

    def reset_config(self, guild_id: int) -> None:
        self._db.execute("DELETE FROM guild_config WHERE guild_id = ?", (guild_id,))

    # --- events ------------------------------------------------------------------

    def create_event(self, **values: Any) -> Event:
        unknown = set(values) - set(_EVENT_COLUMNS) | set(values) & _BOOKKEEPING
        if unknown:
            raise TypeError(f"unknown event fields: {sorted(unknown)}")
        values["roles"] = ",".join(values.get("roles", ()))
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        cursor = self._db.execute(
            f"INSERT INTO events ({columns}) VALUES ({marks})", tuple(values.values())
        )
        event = self.get_event(cursor.lastrowid or 0)
        assert event is not None
        return event

    def get_event(self, event_id: int) -> Event | None:
        row = self._db.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return _event(row) if row else None

    def update_event(self, event_id: int, **changes: Any) -> Event:
        """Change fields that do not show in the announcement (its message id, its reminder)."""
        return self._update(event_id, changes, [])

    def change_event(self, event_id: int, *, content: bool = False, **changes: Any) -> Event:
        """Change fields and, in the same statement, note that the announcement is now
        out of date. ``content`` says its ping line changed too, not just the embed."""
        return self._update(event_id, changes, self._touch(content))

    def touch(self, event_id: int) -> None:
        """Note that the announcement is out of date (its sign-ups changed)."""
        self._update(event_id, {}, self._touch(False))

    @staticmethod
    def _touch(content: bool) -> list[str]:
        # Every expression in a SET sees the row as it was, so both are the new revision.
        bump = ["revision = revision + 1"]
        if content:
            bump.append("content_revision = revision + 1")
        return bump

    def _update(self, event_id: int, changes: dict[str, Any], extra: list[str]) -> Event:
        unknown = set(changes) - set(_EVENT_COLUMNS) | set(changes) & _BOOKKEEPING
        if unknown:
            raise TypeError(f"cannot update event fields: {sorted(unknown)}")
        if "roles" in changes:
            changes["roles"] = ",".join(changes["roles"])
        assignments = ", ".join([*(f"{column} = ?" for column in changes), *extra])
        if assignments:
            self._db.execute(
                f"UPDATE events SET {assignments} WHERE id = ?", (*changes.values(), event_id)
            )
        event = self.get_event(event_id)
        if event is None:
            raise LookupError(f"event {event_id} does not exist")
        return event

    def mark_drawn(self, event_id: int, revision: int) -> None:
        """Discord now shows the announcement as of ``revision``."""
        self._db.execute(
            "UPDATE events SET drawn_revision = ? WHERE id = ? AND drawn_revision < ?",
            (revision, event_id, revision),
        )

    def stale_events(self) -> list[Event]:
        """Announcements that are posted but do not show the latest state."""
        rows = self._db.execute(
            "SELECT * FROM events WHERE drawn_revision < revision AND message_id IS NOT NULL "
            "ORDER BY id"
        ).fetchall()
        return [_event(row) for row in rows]

    def purge_unsent(self) -> int:
        """Drop events whose announcement never reached Discord (the bot died mid-send)."""
        return self._db.execute("DELETE FROM events WHERE message_id IS NULL").rowcount

    def delete_event(self, event_id: int) -> None:
        self._db.execute("DELETE FROM events WHERE id = ?", (event_id,))

    def due_reminders(self, now: float) -> list[Event]:
        rows = self._db.execute(
            "SELECT * FROM events WHERE remind_at IS NOT NULL AND remind_at <= ? ORDER BY remind_at",
            (now,),
        ).fetchall()
        return [_event(row) for row in rows]

    def claim_reminder(self, event_id: int, remind_at: int) -> bool:
        """Take the reminder scheduled for ``remind_at`` off the event, if it is still there."""
        cursor = self._db.execute(
            "UPDATE events SET remind_at = NULL WHERE id = ? AND remind_at = ?",
            (event_id, remind_at),
        )
        return cursor.rowcount == 1

    def restore_reminder(self, event_id: int, remind_at: int) -> None:
        """Put a claimed reminder back, unless the event moved on or got a new one."""
        self._db.execute(
            "UPDATE events SET remind_at = ? "
            "WHERE id = ? AND remind_at IS NULL AND status = 'open'",
            (remind_at, event_id),
        )

    # --- sign-ups ------------------------------------------------------------------

    def set_signup(self, event_id: int, user_id: int, role: str, *, at: float) -> str | None:
        """Sign a member up under ``role``. Returns the role they held before, if any."""
        row = self._db.execute(
            "SELECT role FROM signups WHERE event_id = ? AND user_id = ?", (event_id, user_id)
        ).fetchone()
        previous = row["role"] if row else None
        if previous != role:
            self._db.execute(
                "INSERT INTO signups (event_id, user_id, role, joined_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (event_id, user_id) DO UPDATE SET "
                "role = excluded.role, joined_at = excluded.joined_at",
                (event_id, user_id, role, at),
            )
        return previous

    def remove_signup(self, event_id: int, user_id: int) -> bool:
        cursor = self._db.execute(
            "DELETE FROM signups WHERE event_id = ? AND user_id = ?", (event_id, user_id)
        )
        return cursor.rowcount > 0

    def signups(self, event_id: int) -> list[Signup]:
        rows = self._db.execute(
            "SELECT user_id, role FROM signups WHERE event_id = ? ORDER BY joined_at, rowid",
            (event_id,),
        ).fetchall()
        return [Signup(user_id=row["user_id"], role=row["role"]) for row in rows]
