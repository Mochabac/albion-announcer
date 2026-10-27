"""The whole cycle — announce, sign up, follow up, remind — against a fake guild."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from announcer import render
from announcer.errors import UserError
from announcer.service import ANNOUNCE_COOLDOWN, REMIND_COOLDOWN, Draft, cannot_post
from announcer.store import Event, Signup

from .conftest import NOW, OFFICER_ROLE, World, capped
from .fakes import MEMBER, FakeMember, FakeRole, forbidden, new_event

T0 = int(NOW.timestamp())  # Sunday 18:00 UTC
AT_2030 = T0 + 150 * 60

NO_PING = discord.Permissions(
    view_channel=True, send_messages=True, embed_links=True, read_message_history=True
)


async def announce(world: World, key: str = "zvz", member: Any = None, **draft: str) -> Event:
    done = await world.service.announce(
        world.guild, member or world.officer, world.panel, key, Draft(**draft)
    )
    return done.event


def configure(world: World, **changes: Any) -> None:
    world.store.save_config(replace(world.store.get_config(world.guild.id), **changes))


def view_ids(view: discord.ui.View | None) -> list[str]:
    assert view is not None
    return [b["custom_id"] for row in view.to_components() for b in row["components"]]


def field(embed: discord.Embed | None, name: str) -> str:
    assert embed is not None
    return next(f.value or "" for f in embed.fields if f.name == name)


# --- announcing ----------------------------------------------------------------------


async def test_announce_pings_everyone_with_the_details(world: World) -> None:
    done = await world.service.announce(
        world.guild,
        world.officer,
        world.panel,
        "zvz",
        Draft(when="20:30", location="Martlock portal", gear="T8 · 1300 IP", notes="Regear to T8."),
    )
    (sent,) = world.panel.sent
    assert sent.content == "@everyone ⚔️ **ZvZ** — 20:30 UTC · 📍 Martlock portal"
    assert sent.allowed_mentions is not None
    assert sent.allowed_mentions.everyone is True
    assert (sent.allowed_mentions.users, sent.allowed_mentions.roles) == (False, False)

    assert sent.embed is not None and sent.embed.title == "⚔️ ZvZ"
    assert sent.embed.description == world.catalog.activities[0].message("en")
    assert field(sent.embed, "🕒 When").startswith("**20:30 UTC** · ")
    assert field(sent.embed, "📍 Meeting point") == "Martlock portal"
    assert field(sent.embed, "🛡️ Gear") == "T8 · 1300 IP"
    assert field(sent.embed, "📝 Notes") == "Regear to T8."
    assert sent.embed.footer.text == "Called by Mocha · Signed up: 0"

    event = done.event
    assert view_ids(sent.view) == [
        f"ao:join:{event.id}:tank",
        f"ao:join:{event.id}:healer",
        f"ao:join:{event.id}:dps",
        f"ao:join:{event.id}:support",
        f"ao:leave:{event.id}",
        f"ao:manage:{event.id}",
    ]
    assert (event.message_id, event.channel_id, event.status) == (sent.id, world.panel.id, "open")
    assert (event.when_kind, event.starts_at, event.when_text) == ("at", AT_2030, "20:30")
    assert (event.author_id, event.author_name) == (world.officer.id, "Mocha")
    assert world.store.get_event(event.id) == event

    assert done.url == sent.jump_url
    # 15 minutes before the start, the people signed up get a reminder.
    assert event.remind_at == AT_2030 - 15 * 60
    assert done.notes == (f"⏰ I'll remind the people signed up at <t:{event.remind_at}:t>.",)


async def test_an_empty_form_announces_for_right_now(world: World) -> None:
    done = await world.service.announce(world.guild, world.officer, world.panel, "zvz", Draft())
    (sent,) = world.panel.sent
    assert sent.content == "@everyone ⚔️ **ZvZ** — now!"
    assert field(sent.embed, "🕒 When") == "**Now!**"
    assert [f.name for f in sent.embed.fields][:1] == ["🕒 When"]  # type: ignore[union-attr]
    assert (done.event.when_kind, done.event.starts_at, done.event.remind_at) == ("now", None, None)
    assert done.notes == ()


async def test_announcements_go_to_the_configured_channel(world: World) -> None:
    configure(world, channel_id=world.announcements.id)
    event = await announce(world)
    assert world.panel.sent == []
    assert len(world.announcements.sent) == 1
    assert event.channel_id == world.announcements.id


async def test_a_deleted_announcement_channel_is_reported(world: World) -> None:
    configure(world, channel_id=999)
    with pytest.raises(UserError, match="no longer exists"):
        await announce(world)


async def test_the_guild_language_is_used(world: World) -> None:
    configure(world, language="es")
    await announce(world, "gathering", when="20:30", location="Portal de Martlock")
    (sent,) = world.panel.sent
    assert sent.content == "@everyone ⛏️ **Recolección** — 20:30 UTC · 📍 Portal de Martlock"
    assert field(sent.embed, "📍 Punto de reunión") == "Portal de Martlock"
    labels = [b["label"] for row in sent.view.to_components() for b in row["components"]]  # type: ignore[union-attr]
    assert labels == ["Voy", "Scout", "Salir", "Gestionar"]


async def test_typed_times_use_the_guild_time_zone(world: World) -> None:
    configure(world, timezone="America/Bogota")
    event = await announce(world, when="8pm")  # 20:00 in Bogotá is 01:00 UTC
    assert event.starts_at == T0 + 7 * 3600
    assert "Mon 5 Oct · 01:00 UTC" in (world.panel.sent[0].content or "")


async def test_a_time_only_a_human_can_read_is_shown_as_typed(world: World) -> None:
    done = await world.service.announce(
        world.guild, world.officer, world.panel, "zvz", Draft(when=" 20h30   BR ")
    )
    (sent,) = world.panel.sent
    assert sent.content == "@everyone ⚔️ **ZvZ** — 20h30 BR"
    assert field(sent.embed, "🕒 When") == "20h30 BR"
    assert (done.event.when_kind, done.event.starts_at, done.event.remind_at) == (
        "text",
        None,
        None,
    )
    assert len(done.notes) == 1 and "couldn't read the time" in done.notes[0]


async def test_the_callers_voice_channel_is_shown(world: World) -> None:
    world.officer.voice = SimpleNamespace(channel=SimpleNamespace(id=55))
    event = await announce(world)
    assert event.voice_channel_id == 55
    assert field(world.panel.sent[0].embed, "🎙️ Voice") == "<#55>"


async def test_custom_announcement(world: World) -> None:
    done = await world.service.announce(
        world.guild,
        world.officer,
        world.panel,
        "custom",
        Draft(title="Guild meeting", body="Everyone on voice after reset."),
    )
    (sent,) = world.panel.sent
    assert sent.content == "@everyone 📢 **Guild meeting**"
    assert sent.embed is not None
    assert (sent.embed.title, sent.embed.description) == (
        "📢 Guild meeting",
        "Everyone on voice after reset.",
    )
    assert sent.embed.fields == []  # a notice has no time unless one is given
    assert sent.embed.footer.text == "Called by Mocha"
    assert view_ids(sent.view) == [f"ao:manage:{done.event.id}"]
    assert done.event.when_kind == "none" and done.notes == ()


async def test_custom_announcement_with_a_time_has_no_reminder(world: World) -> None:
    event = await announce(world, "custom", title="Guild meeting", body="On voice.", when="20:30")
    assert (event.when_kind, event.starts_at) == ("at", AT_2030)
    # The automatic reminder goes to the people signed up, and a notice has no sign-ups.
    assert event.remind_at is None


async def test_an_unknown_activity_is_refused(world: World) -> None:
    with pytest.raises(UserError, match="no longer exists"):
        await announce(world, "tea_party")
    assert world.panel.sent == []


# --- who may announce ------------------------------------------------------------------


async def test_whoever_can_see_the_panel_may_announce(world: World) -> None:
    # Pressing a button takes seeing the panel, so its channel is the gate: no permission
    # of the caller's own is asked for, not even the one to ping @everyone.
    guest = FakeMember(9, "Guest")  # let into the officers' channel, and nothing more
    world.panel.member_permissions[guest.id] = MEMBER
    configure(world, channel_id=world.announcements.id)
    event = await announce(world, member=guest)
    (sent,) = world.announcements.sent
    assert sent.content == "@everyone ⚔️ **ZvZ** — now!"
    assert event.panel_channel_id == world.panel.id  # the caller tools go by it


async def test_admins_can_always_announce(world: World) -> None:
    await announce(world, member=world.admin)
    assert len(world.panel.sent) == 1


async def test_announcer_roles_limit_who_may_announce(world: World) -> None:
    configure(world, announcer_role_ids=(OFFICER_ROLE,))
    await announce(world)  # the officer holds the role
    assert len(world.panel.sent) == 1

    # Seeing the panel is no longer enough without the role...
    guest = FakeMember(9, "Guest")
    world.panel.member_permissions[guest.id] = MEMBER
    with pytest.raises(UserError) as refusal:
        await announce(world, member=guest)
    assert str(refusal.value) == (
        f"You can't announce activities here. You need one of these roles: <@&{OFFICER_ROLE}>."
    )
    # ...and the role is enough by itself, as being an admin is.
    caller = FakeMember(10, "Caller", roles=[FakeRole(OFFICER_ROLE)])
    await announce(world, member=caller)
    await announce(world, member=world.admin)
    assert len(world.panel.sent) == 3


async def test_missing_bot_permissions_are_named(world: World) -> None:
    world.panel.bot_permissions = discord.Permissions(view_channel=True, send_messages=True)
    with pytest.raises(UserError) as refusal:
        await announce(world)
    assert "**Embed Links**, **Read Message History**" in str(refusal.value)
    assert world.store.get_event(1) is None

    # A channel the bot cannot see lacks everything; only the way in is worth naming.
    world.panel.bot_permissions = discord.Permissions.none()
    with pytest.raises(UserError) as hidden:
        await announce(world)
    assert str(hidden.value) == (
        f"I can't see {world.panel.mention}, so I can't post there. Open **Edit Channel → "
        "Permissions**, add me, and make sure **View Channel** is allowed."
    )
    assert cannot_post("es", "<#1>", ["view_channel", "send_messages"]) == (
        "No puedo ver <#1>, así que no puedo publicar ahí. Abre **Editar canal → Permisos**, "
        "agrégame y comprueba que **Ver canal** esté permitido."
    )


async def test_a_second_announcement_right_away_is_held_back(world: World) -> None:
    await announce(world)
    with pytest.raises(UserError, match="Try again in 15s"):
        await announce(world, "fame")
    await announce(world, "fame", member=world.admin)  # someone else is not held back

    world.clock.advance(ANNOUNCE_COOLDOWN)
    await announce(world, "fame")
    assert len(world.panel.sent) == 3


async def test_a_refused_send_leaves_nothing_behind(world: World) -> None:
    world.panel.fail_send = forbidden()
    with pytest.raises(UserError, match="Discord refused"):
        await announce(world)
    assert world.store.get_event(1) is None

    world.panel.fail_send = None
    event = await announce(world)  # no cooldown was started by the failure
    assert world.store.get_event(event.id) is not None


# --- pings -------------------------------------------------------------------------------


async def test_without_the_mention_permission_the_caller_is_warned_first(world: World) -> None:
    world.panel.bot_permissions = NO_PING
    pre = world.service.precheck(world.guild, world.officer, world.panel, "zvz")
    assert "`@everyone`" in pre.ping_problem and world.panel.mention in pre.ping_problem
    assert "Mention @everyone, @here, and All Roles" in pre.ping_problem

    done = await world.service.announce(world.guild, world.officer, world.panel, "zvz", Draft())
    (sent,) = world.panel.sent
    # No "@everyone" that would sit there pinging nobody.
    assert sent.content == "⚔️ **ZvZ** — now!"
    assert sent.allowed_mentions is not None and sent.allowed_mentions.everyone is False
    assert done.event.ping == "none"
    assert done.notes == ("⚠️ Posted without a ping — I couldn't mention anyone there.",)


@pytest.mark.parametrize(
    ("ping", "prefix"), [("here", "@here "), ("none", ""), ("everyone", "@everyone ")]
)
async def test_configured_pings(world: World, ping: str, prefix: str) -> None:
    configure(world, ping=ping)
    await announce(world)
    (sent,) = world.panel.sent
    assert sent.content == f"{prefix}⚔️ **ZvZ** — now!"
    assert sent.allowed_mentions is not None
    assert sent.allowed_mentions.everyone is (ping != "none")


async def test_role_ping(world: World) -> None:
    world.guild.add_role(FakeRole(777))
    configure(world, ping="role", ping_role_id=777)
    await announce(world)
    (sent,) = world.panel.sent
    assert sent.content == "<@&777> ⚔️ **ZvZ** — now!"
    mentions = sent.allowed_mentions
    assert mentions is not None and mentions.everyone is False
    assert [role.id for role in mentions.roles] == [777]  # type: ignore[union-attr]


async def test_role_ping_needs_a_mentionable_role_or_the_permission(world: World) -> None:
    role = world.guild.add_role(FakeRole(777))
    configure(world, ping="role", ping_role_id=777)
    world.panel.bot_permissions = NO_PING

    problem = world.service.precheck(world.guild, world.officer, world.panel, "zvz").ping_problem
    assert role.mention in problem

    role.mentionable = True
    assert world.service.precheck(world.guild, world.officer, world.panel, "zvz").ping_problem == ""


async def test_a_deleted_ping_role_is_reported(world: World) -> None:
    configure(world, ping="role", ping_role_id=777)
    problem = world.service.precheck(world.guild, world.officer, world.panel, "zvz").ping_problem
    assert "no longer exists" in problem
    event = await announce(world)
    assert event.ping == "none" and world.panel.sent[0].content == "⚔️ **ZvZ** — now!"


# --- signing up -----------------------------------------------------------------------------


async def test_signing_up_redraws_the_roster(world: World) -> None:
    event = await announce(world)
    world.service.signup(world.guild.id, event.id, 100, "tank")
    await world.service.drain()

    ((message_id, edit),) = world.panel.edits
    assert message_id == event.message_id
    assert field(edit["embed"], "🛡️ Tank · 1") == "<@100>"
    assert edit["embed"].footer.text == "Called by Mocha · Signed up: 1"
    assert view_ids(edit["view"]) == view_ids(world.panel.sent[0].view)
    # The ping line is left alone, and the edit can never notify anyone new.
    assert "content" not in edit
    assert edit["allowed_mentions"].everyone is True and edit["allowed_mentions"].users is False


async def test_switching_role_and_leaving(world: World) -> None:
    event = await announce(world)
    service, guild = world.service, world.guild.id

    service.signup(guild, event.id, 100, "tank")
    await service.drain()
    service.signup(guild, event.id, 100, "tank")  # same role again: nothing to redraw
    await service.drain()
    assert len(world.panel.edits) == 1

    service.signup(guild, event.id, 100, "healer")
    await service.drain()
    embed = world.panel.last_edit()["embed"]
    assert field(embed, "🛡️ Tank · 0") == "—" and field(embed, "💚 Healer · 1") == "<@100>"

    service.leave(guild, event.id, 100)
    await service.drain()
    assert field(world.panel.last_edit()["embed"], "💚 Healer · 0") == "—"
    service.leave(guild, event.id, 100)  # not signed up: nothing to redraw
    await service.drain()
    assert len(world.panel.edits) == 3


async def test_a_rush_of_signups_becomes_a_few_edits(world: World) -> None:
    event = await announce(world)
    world.panel.edit_delay = 0.01  # an edit takes a while; clicks keep coming meanwhile
    roles = ("tank", "healer", "dps", "support")

    for index, player in enumerate(world.players(80)):
        world.service.signup(world.guild.id, event.id, player.id, roles[index % 4])
        await asyncio.sleep(0)
    await world.service.drain()

    assert len(world.panel.edits) < 10
    assert world.panel.most_edits_in_flight == 1  # never two edits racing each other
    final = world.panel.last_edit()["embed"]
    assert final.footer.text.endswith("Signed up: 80")
    assert sum(f.value.count("<@") for f in final.fields) == 80


async def test_a_position_takes_one_player(world: World) -> None:
    world.service.catalog = capped(world.catalog)
    event = await announce(world)
    service, guild = world.service, world.guild.id

    service.signup(guild, event.id, 100, "tank")
    with pytest.raises(UserError) as refusal:
        service.signup(guild, event.id, 101, "tank")
    assert str(refusal.value) == ("🛡️ **Tank** is already taken by <@100>. Pick another position.")
    service.signup(guild, event.id, 100, "tank")  # pressing your own position changes nothing
    service.signup(guild, event.id, 101, "healer")
    assert world.store.signups(event.id) == [Signup(100, "tank"), Signup(101, "healer")]

    # Moving to another position, or leaving, frees the one held.
    service.signup(guild, event.id, 100, "dps")
    service.signup(guild, event.id, 101, "tank")
    service.leave(guild, event.id, 101)
    service.signup(guild, event.id, 102, "tank")
    held = {(signup.user_id, signup.role) for signup in world.store.signups(event.id)}
    assert held == {(100, "dps"), (102, "tank")}


async def test_a_position_for_two_fills_up(world: World) -> None:
    world.service.catalog = capped(world.catalog, limit=2)
    event = await announce(world)
    for player in (100, 101):
        world.service.signup(world.guild.id, event.id, player, "dps")
    with pytest.raises(UserError, match=r"⚔️ \*\*DPS\*\* is already full\. Pick another"):
        world.service.signup(world.guild.id, event.id, 102, "dps")
    assert len(world.store.signups(event.id)) == 2


async def test_signups_are_refused_when_they_make_no_sense(world: World) -> None:
    event = await announce(world)
    service = world.service
    with pytest.raises(UserError, match="no longer exists"):
        service.signup(world.guild.id, event.id, 100, "battle_mount")  # not a role of this event
    with pytest.raises(UserError, match="no longer exists"):
        service.signup(world.guild.id, 999, 100, "tank")
    with pytest.raises(UserError, match="no longer exists"):
        service.signup(2, event.id, 100, "tank")  # a button from another server
    assert world.store.signups(event.id) == []

    await service.finish(world.guild, world.officer, event.id)
    with pytest.raises(UserError, match="already closed"):
        service.signup(world.guild.id, event.id, 100, "tank")
    with pytest.raises(UserError, match="already closed"):
        service.leave(world.guild.id, event.id, 100)


async def test_a_deleted_announcement_cancels_the_activity(world: World) -> None:
    event = await announce(world, when="20:30")
    world.panel.deleted.add(event.message_id or 0)  # a moderator removed the message

    world.service.signup(world.guild.id, event.id, 100, "tank")
    await world.service.drain()

    stored = world.store.get_event(event.id)
    assert stored is not None and (stored.status, stored.remind_at) == ("cancelled", None)


# --- caller tools ------------------------------------------------------------------------------


async def test_who_may_manage(world: World) -> None:
    event = await announce(world)

    def may(member: FakeMember) -> bool:
        try:
            return world.service.managed(world.guild, member, event.id).id == event.id
        except UserError as refusal:
            assert str(refusal) == "Only the caller or an officer can manage this activity."
            return False

    assert may(world.officer)  # the caller
    assert may(world.admin)
    # Whoever could have called it from the same panel may run it too...
    guest = FakeMember(9, "Guest")
    world.panel.member_permissions[guest.id] = MEMBER
    assert may(guest)
    # ...but not someone who cannot see that panel.
    assert not may(world.member)

    # Once announcer roles are picked, they decide this too.
    configure(world, announcer_role_ids=(OFFICER_ROLE,))
    assert not may(guest)
    assert may(FakeMember(10, "Caller", roles=[FakeRole(OFFICER_ROLE)]))
    assert may(world.admin)


async def test_without_a_known_panel_only_the_caller_and_admins_manage(world: World) -> None:
    guest = FakeMember(9, "Guest")
    world.panel.member_permissions[guest.id] = MEMBER
    older = new_event(world.store)  # announced before panels were recorded
    orphan = new_event(world.store, panel_channel_id=99)  # its panel's channel was deleted
    for event in (older, orphan):
        assert world.service.managed(world.guild, world.officer, event.id)  # its caller
        assert world.service.managed(world.guild, world.admin, event.id)
        with pytest.raises(UserError, match="Only the caller or an officer"):
            world.service.managed(world.guild, guest, event.id)


async def test_remind_repeats_the_ping(world: World) -> None:
    event = await announce(world, when="20:30", location="Martlock portal")
    world.clock.advance(125 * 60)
    await world.service.remind(world.guild, world.officer, event.id)

    reminder = world.panel.sent[-1]
    assert reminder.content == (
        "@everyone 🔔 Reminder: ⚔️ **ZvZ**\n"
        "20:30 UTC (in 25 min) · 📍 Martlock portal\n"
        "Sign up on the announcement above ☝️"
    )
    assert reminder.allowed_mentions is not None and reminder.allowed_mentions.everyone is True
    assert reminder.reference is not None
    assert reminder.reference.message_id == event.message_id
    assert reminder.reference.fail_if_not_exists is False

    with pytest.raises(UserError, match="Try again in 60s"):
        await world.service.remind(world.guild, world.officer, event.id)
    world.clock.advance(REMIND_COOLDOWN)
    await world.service.remind(world.guild, world.officer, event.id)
    assert len(world.panel.sent) == 3


async def test_a_reminder_never_pings_more_than_the_announcement_did(world: World) -> None:
    configure(world, ping="none")
    event = await announce(world)
    await world.service.remind(world.guild, world.officer, event.id)
    reminder = world.panel.sent[-1]
    assert (reminder.content or "").startswith("🔔 Reminder")
    assert reminder.allowed_mentions is not None and reminder.allowed_mentions.everyone is False


async def test_starting_calls_the_people_signed_up(world: World) -> None:
    world.officer.voice = SimpleNamespace(channel=SimpleNamespace(id=55))
    event = await announce(world, when="20:30", location="Martlock portal")
    world.service.signup(world.guild.id, event.id, 100, "tank")
    world.service.signup(world.guild.id, event.id, 101, "healer")
    await world.service.drain()

    started = await world.service.start(world.guild, world.officer, event.id)
    await world.service.drain()
    assert (started.status, started.remind_at) == ("started", None)

    call = world.panel.sent[-1]
    assert call.content == (
        "🚀 ⚔️ **ZvZ** is starting now — last call!\n📍 Martlock portal · 🎙️ <#55>\n<@100> <@101>"
    )
    mentions = call.allowed_mentions
    assert mentions is not None and (mentions.everyone, mentions.users) == (False, True)

    redrawn = world.panel.last_edit()
    assert redrawn["embed"].description.startswith("🚀 **In progress**")
    assert redrawn["view"] is not None  # late joiners can still sign up

    with pytest.raises(UserError, match="already marked as started"):
        await world.service.start(world.guild, world.officer, event.id)


async def test_finishing_closes_the_announcement(world: World) -> None:
    event = await announce(world, when="20:30")
    world.service.signup(world.guild.id, event.id, 100, "tank")
    finished = await world.service.finish(world.guild, world.officer, event.id)
    await world.service.drain()
    assert (finished.status, finished.remind_at) == ("finished", None)

    thanks = world.panel.sent[-1]
    assert thanks.content == "✅ ⚔️ **ZvZ** is over. Thanks to everyone who showed up! o7"
    mentions = thanks.allowed_mentions
    assert mentions is not None and (mentions.everyone, mentions.users) == (False, False)

    redrawn = world.panel.last_edit()
    assert redrawn["view"] is None  # the buttons are gone
    assert redrawn["embed"].color.value == render.CLOSED_COLOR
    assert field(redrawn["embed"], "🛡️ Tank · 1") == "<@100>"  # the roster stays as a record

    with pytest.raises(UserError, match="already closed"):
        world.service.managed(world.guild, world.officer, event.id)


async def test_cancelling_tells_the_people_signed_up(world: World) -> None:
    event = await announce(world, when="20:30")
    world.service.signup(world.guild.id, event.id, 100, "tank")
    cancelled = await world.service.cancel(world.guild, world.admin, event.id)
    await world.service.drain()
    assert cancelled.status == "cancelled"

    notice = world.panel.sent[-1]
    assert notice.content == "🚫 ⚔️ **ZvZ** has been cancelled.\n<@100>"
    assert notice.allowed_mentions is not None and notice.allowed_mentions.users is True
    assert world.panel.last_edit()["view"] is None
    assert world.panel.last_edit()["embed"].description.startswith("🚫 **Cancelled**")


async def test_a_follow_up_with_nobody_signed_up_mentions_nobody(world: World) -> None:
    event = await announce(world)
    await world.service.cancel(world.guild, world.officer, event.id)
    notice = world.panel.sent[-1]
    assert notice.content == "🚫 ⚔️ **ZvZ** has been cancelled."
    assert notice.allowed_mentions is not None and notice.allowed_mentions.users is False


async def test_a_big_roster_is_called_in_several_messages(world: World) -> None:
    event = await announce(world)
    for player in world.players(200, start=1234567890123456000):
        world.service.signup(world.guild.id, event.id, player.id, "dps")
    await world.service.start(world.guild, world.officer, event.id)

    calls = world.panel.sent[1:]
    assert len(calls) > 1 and all(len(call.content or "") <= 2000 for call in calls)
    assert sum((call.content or "").count("<@") for call in calls) == 200
    assert calls[0].reference is not None and all(call.reference is None for call in calls[1:])
    assert all(call.allowed_mentions.users is True for call in calls)  # type: ignore[union-attr]


async def test_strangers_cannot_use_the_caller_tools(world: World) -> None:
    event = await announce(world)
    for action in (
        world.service.remind,
        world.service.start,
        world.service.finish,
        world.service.cancel,
    ):
        with pytest.raises(UserError, match="Only the caller or an officer"):
            await action(world.guild, world.member, event.id)
    with pytest.raises(UserError, match="Only the caller or an officer"):
        await world.service.edit(world.guild, world.member, event.id, Draft())
    assert len(world.panel.sent) == 1
    stored = world.store.get_event(event.id)
    assert stored is not None and stored.status == "open"


# --- editing -------------------------------------------------------------------------------------


async def test_editing_the_notes_touches_nothing_else(world: World) -> None:
    event = await announce(world, when="in 30m", location="Martlock portal")
    world.service.signup(world.guild.id, event.id, 100, "tank")
    world.clock.advance(10 * 60)

    edited = await world.service.edit(
        world.guild,
        world.officer,
        event.id,
        Draft(when="in 30m", location="Martlock portal", notes="Bring 2 sets."),
    )
    await world.service.drain()

    # "in 30m" was not typed again, so it still means 30 minutes after the announcement.
    assert (edited.event.starts_at, edited.event.remind_at) == (event.starts_at, event.remind_at)
    assert edited.event.notes == "Bring 2 sets." and edited.notes == ()
    assert len(world.panel.sent) == 1  # nobody is pinged over a note
    assert field(world.panel.last_edit()["embed"], "📝 Notes") == "Bring 2 sets."


async def test_moving_the_time_tells_the_people_signed_up(world: World) -> None:
    event = await announce(world, when="20:30", location="Martlock portal")
    world.service.signup(world.guild.id, event.id, 100, "tank")

    edited = await world.service.edit(
        world.guild, world.officer, event.id, Draft(when="21:00", location="Martlock portal")
    )
    await world.service.drain()
    assert edited.event.starts_at == AT_2030 + 30 * 60
    assert edited.event.remind_at == AT_2030 + 15 * 60
    # The caller is told when the people signed up will now be reminded.
    assert edited.notes == (f"⏰ I'll remind the people signed up at <t:{AT_2030 + 900}:t>.",)

    update = world.panel.sent[-1]
    assert update.content == (
        "✏️ ⚔️ **ZvZ** was updated — here is the new plan:\n"
        "21:00 UTC (in 3 h 00 min) · 📍 Martlock portal\n<@100>"
    )
    assert update.allowed_mentions is not None and update.allowed_mentions.users is True

    redrawn = world.panel.last_edit()
    assert redrawn["content"] == "@everyone ⚔️ **ZvZ** — 21:00 UTC · 📍 Martlock portal"
    assert redrawn["allowed_mentions"].everyone is True
    assert field(redrawn["embed"], "🕒 When").startswith("**21:00 UTC**")


async def test_moving_the_place_tells_the_people_signed_up(world: World) -> None:
    event = await announce(world, when="20:30", location="Martlock portal")
    await world.service.edit(
        world.guild, world.officer, event.id, Draft(when="20:30", location="Thetford portal")
    )
    assert "📍 Thetford portal" in (world.panel.sent[-1].content or "")
    assert len(world.panel.sent) == 2


async def test_editing_a_custom_announcement(world: World) -> None:
    event = await announce(world, "custom", title="Guild meeting", body="On voice.")
    edited = await world.service.edit(
        world.guild,
        world.officer,
        event.id,
        Draft(title="Guild meeting (moved)", body="On voice, one hour later.", when="21:00"),
    )
    await world.service.drain()
    assert (edited.event.title, edited.event.message) == (
        "Guild meeting (moved)",
        "On voice, one hour later.",
    )
    assert edited.event.when_kind == "at"
    redrawn = world.panel.last_edit()
    assert redrawn["content"] == "@everyone 📢 **Guild meeting (moved)** — 21:00 UTC"
    assert redrawn["embed"].title == "📢 Guild meeting (moved)"

    # Emptying the time takes it off the notice again; an emptied title keeps the old one.
    cleared = (
        await world.service.edit(
            world.guild, world.officer, event.id, Draft(title="", body="Never mind.", when="")
        )
    ).event
    assert (cleared.title, cleared.when_kind, cleared.starts_at) == (
        "Guild meeting (moved)",
        "none",
        None,
    )


# --- automatic reminders ------------------------------------------------------------------------------


async def test_the_reminder_goes_out_once_to_the_people_signed_up(world: World) -> None:
    world.officer.voice = SimpleNamespace(channel=SimpleNamespace(id=55))
    event = await announce(world, when="20:30", location="Martlock portal")
    world.service.signup(world.guild.id, event.id, 100, "tank")
    world.service.signup(world.guild.id, event.id, 101, "dps")

    world.clock.advance(134 * 60)  # 20:14, one minute early
    await world.service.run_due_reminders()
    assert len(world.panel.sent) == 1

    world.clock.advance(65)  # 20:15:05
    await world.service.run_due_reminders()
    reminder = world.panel.sent[-1]
    assert reminder.content == (
        "⏰ ⚔️ **ZvZ** starts in 15 min!\n20:30 UTC · 📍 Martlock portal · 🎙️ <#55>\n<@100> <@101>"
    )
    mentions = reminder.allowed_mentions
    assert mentions is not None and (mentions.everyone, mentions.users) == (False, True)
    assert reminder.reference is not None and reminder.reference.message_id == event.message_id

    await world.service.run_due_reminders()
    world.clock.advance(60)
    await world.service.run_due_reminders()
    assert len(world.panel.sent) == 2
    stored = world.store.get_event(event.id)
    assert stored is not None and stored.remind_at is None


async def test_no_reminder_when_nobody_signed_up(world: World) -> None:
    event = await announce(world, when="20:30")
    world.clock.advance(136 * 60)
    await world.service.run_due_reminders()
    assert len(world.panel.sent) == 1
    stored = world.store.get_event(event.id)
    assert stored is not None and stored.remind_at is None


@pytest.mark.parametrize("action", ["start", "finish", "cancel"])
async def test_no_reminder_once_the_activity_moved_on(world: World, action: str) -> None:
    event = await announce(world, when="20:30")
    world.service.signup(world.guild.id, event.id, 100, "tank")
    await getattr(world.service, action)(world.guild, world.officer, event.id)
    sent = len(world.panel.sent)

    world.clock.advance(136 * 60)
    await world.service.run_due_reminders()
    assert len(world.panel.sent) == sent


async def test_no_stale_reminder_after_downtime(world: World) -> None:
    event = await announce(world, when="20:30")
    world.service.signup(world.guild.id, event.id, 100, "tank")
    world.clock.advance(151 * 60)  # the bot was down until 20:31
    await world.service.run_due_reminders()
    assert len(world.panel.sent) == 1


async def test_no_reminder_for_a_deleted_announcement(world: World) -> None:
    event = await announce(world, when="20:30")
    world.service.signup(world.guild.id, event.id, 100, "tank")
    await world.service.drain()
    world.panel.deleted.add(event.message_id or 0)

    world.clock.advance(136 * 60)
    await world.service.run_due_reminders()
    assert len(world.panel.sent) == 1
    stored = world.store.get_event(event.id)
    assert stored is not None and stored.status == "cancelled"


@pytest.mark.parametrize(
    ("when", "minutes", "expected"),
    [
        ("20:30", 15, AT_2030 - 15 * 60),
        ("20:30", 60, AT_2030 - 60 * 60),
        ("20:30", 0, None),  # switched off
        ("in 10m", 15, None),  # would already be late
        ("in 15m", 15, None),  # would fire the moment it is announced
        ("in 17m", 15, T0 + 2 * 60),
        ("", 15, None),  # happening now
        ("after reset", 15, None),  # no clock time to count from
    ],
)
async def test_when_a_reminder_is_scheduled(
    world: World, when: str, minutes: int, expected: int | None
) -> None:
    configure(world, reminder_minutes=minutes)
    event = await announce(world, when=when)
    assert event.remind_at == expected


async def test_one_failing_reminder_does_not_block_the_next(world: World) -> None:
    first = await announce(world, when="20:30")
    world.service.signup(world.guild.id, first.id, 100, "tank")
    configure(world, channel_id=world.announcements.id)
    second = await announce(world, "fame", member=world.admin, when="20:30")
    world.service.signup(world.guild.id, second.id, 101, "dps")
    await world.service.drain()

    world.panel.fail_send = forbidden()
    world.clock.advance(136 * 60)
    await world.service.run_due_reminders()

    assert "starts in 14 min" in (world.announcements.sent[-1].content or "")
    for event in (first, second):
        stored = world.store.get_event(event.id)
        assert stored is not None and stored.remind_at is None  # neither will be retried forever


# --- health check -----------------------------------------------------------------------------------


def test_health_check(world: World) -> None:
    service, guild = world.service, world.guild
    config = world.store.get_config(guild.id)
    assert service.health(guild, config, world.panel) == [
        f"✅ I have every permission I need in {world.panel.mention}."
    ]

    world.panel.bot_permissions = NO_PING
    assert service.health(guild, config, world.panel)[1] == (
        "⚠️ I can't ping there: I'm missing **Mention @everyone, @here, and All Roles**."
    )
    assert service.health(guild, replace(config, ping="none"), world.panel) == [
        f"✅ I have every permission I need in {world.panel.mention}."
    ]

    world.panel.bot_permissions = discord.Permissions(view_channel=True)
    lines = service.health(guild, replace(config, ping="none"), world.panel)
    assert lines == [
        f"❌ In {world.panel.mention} I'm missing: "
        "**Send Messages**, **Embed Links**, **Read Message History**."
    ]

    # Hidden from the bot: one line, and no second one about the ping it also "lacks".
    world.panel.bot_permissions = discord.Permissions.none()
    assert service.health(guild, config, world.panel) == [
        f"❌ I can't see {world.panel.mention}. Open **Edit Channel → Permissions**, add me, "
        "and make sure **View Channel** is allowed."
    ]

    target = replace(config, channel_id=world.announcements.id, ping="role", ping_role_id=777)
    assert service.health(guild, target, world.panel) == [
        f"✅ I have every permission I need in {world.announcements.mention}.",
        "⚠️ The ping role no longer exists.",
    ]
    gone = service.health(guild, replace(config, channel_id=999), world.panel)
    assert len(gone) == 1 and gone[0].startswith("❌") and "no longer exists" in gone[0]


def test_threads_need_the_thread_permission(world: World) -> None:
    class Thread(discord.Thread):
        __slots__ = ("granted",)
        granted: discord.Permissions

        def permissions_for(self, obj: Any, /) -> discord.Permissions:
            return self.granted

    thread: Any = Thread.__new__(Thread)
    thread.granted = NO_PING  # may send in channels, but not in threads
    assert world.service.missing_permissions(world.guild, thread) == ["send_messages_in_threads"]
    thread.granted = discord.Permissions(NO_PING.value, send_messages_in_threads=True)
    assert world.service.missing_permissions(world.guild, thread) == []
