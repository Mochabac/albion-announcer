from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import discord
import pytest

from announcer import views
from announcer.catalog import Catalog, load_catalog
from announcer.service import Service
from announcer.store import Store

from .fakes import EVERYTHING, FakeChannel, FakeClient, FakeClock, FakeGuild, FakeMember, FakeRole

# A Sunday, 18:00 UTC.
NOW = datetime(2026, 10, 4, 18, 0, tzinfo=UTC)

OFFICER_ROLE = 500

# The tests' own catalog, not the guild's activities.toml.
ACTIVITIES = Path(__file__).parent / "activities.toml"


@dataclass
class World:
    """A small guild: an officers' channel with the panel, which the rest of the server
    cannot see, an announcements channel, an admin, an officer, and a regular member."""

    guild: FakeGuild
    panel: FakeChannel
    announcements: FakeChannel
    admin: FakeMember
    officer: FakeMember
    member: FakeMember
    client: FakeClient
    clock: FakeClock
    store: Store
    catalog: Catalog
    service: Service

    def players(self, count: int, start: int = 100) -> list[FakeMember]:
        return [FakeMember(start + n, f"Player{n}") for n in range(count)]


def capped(catalog: Catalog, limit: int = 1) -> Catalog:
    """``catalog`` with every sign-up role made a position for ``limit`` players: a comp."""
    roles = {key: replace(role, limit=limit) for key, role in catalog.roles.items()}
    return replace(catalog, roles=roles)


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    return load_catalog(ACTIVITIES)


@pytest.fixture
def store() -> Iterator[Store]:
    store = Store(":memory:")
    yield store
    store.close()


@pytest.fixture
def world(store: Store, catalog: Catalog) -> World:
    guild = FakeGuild()
    panel = guild.add_channel(FakeChannel(10, everyone=discord.Permissions.none()))
    announcements = guild.add_channel(FakeChannel(20))
    guild.add_role(FakeRole(OFFICER_ROLE))

    admin = FakeMember(2, "Admin", guild_permissions=discord.Permissions(manage_guild=True))
    officer = FakeMember(3, "Mocha", roles=[guild.roles[OFFICER_ROLE]])
    member = FakeMember(4, "Recruit")
    for channel in (panel, announcements):
        channel.member_permissions[officer.id] = EVERYTHING

    client = FakeClient(guild)
    clock = FakeClock(NOW.timestamp())
    service = Service(
        client, store, catalog, event_view=views.event_view, clock=clock, edit_spacing=0
    )
    return World(
        guild, panel, announcements, admin, officer, member, client, clock, store, catalog, service
    )
