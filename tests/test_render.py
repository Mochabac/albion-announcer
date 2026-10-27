from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

import discord
import pytest

from announcer import render
from announcer.catalog import MAX_LIMIT, MAX_ROLES, ROLE_LABELS_TOTAL, Catalog, Role
from announcer.store import GuildConfig, Signup, Store

from .conftest import NOW, capped
from .fakes import new_event

CREATED = int(NOW.timestamp())  # Sunday 2026-10-04, 18:00 UTC
AT_2030 = CREATED + 150 * 60
SATURDAY_19 = CREATED + 6 * 86_400 + 3_600
# The longest names twenty roles can all have at once.
LONGEST = ROLE_LABELS_TOTAL // MAX_ROLES


def field(embed: discord.Embed, name: str) -> str:
    return next(f.value or "" for f in embed.fields if f.name == name)


def names(embed: discord.Embed) -> list[str]:
    return [f.name or "" for f in embed.fields]


def roster(embed: discord.Embed) -> list[Any]:
    """A comp's list of positions: its first field and the ones it goes on in."""
    start = next(n for n, f in enumerate(embed.fields) if (f.name or "").startswith("👥"))
    return embed.fields[start:]


# --- the content line --------------------------------------------------------------


def test_content_carries_the_ping_and_the_essentials(store: Store) -> None:
    event = new_event(
        store,
        created_at=CREATED,
        when_kind="at",
        starts_at=AT_2030,
        when_text="20:30",
        location="Martlock portal",
    )
    assert render.announcement_content(event) == (
        "@everyone ⚔️ **ZvZ** — 20:30 UTC · 📍 Martlock portal"
    )


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, "@everyone ⚔️ **ZvZ** — now!"),
        ({"language": "es"}, "@everyone ⚔️ **ZvZ** — ¡ahora!"),
        ({"ping": "here"}, "@here ⚔️ **ZvZ** — now!"),
        ({"ping": "none"}, "⚔️ **ZvZ** — now!"),
        ({"ping": "role", "ping_role_id": 777}, "<@&777> ⚔️ **ZvZ** — now!"),
        ({"when_kind": "text", "when_text": "after reset"}, "@everyone ⚔️ **ZvZ** — after reset"),
        ({"when_kind": "none", "location": "HO"}, "@everyone ⚔️ **ZvZ** — 📍 HO"),
        (
            {"when_kind": "none", "emoji": "📢", "title": "Guild meeting"},
            "@everyone 📢 **Guild meeting**",
        ),
        (
            {"when_kind": "at", "starts_at": SATURDAY_19, "created_at": CREATED},
            "@everyone ⚔️ **ZvZ** — Sat 10 Oct · 19:00 UTC",
        ),
    ],
)
def test_content_variants(store: Store, overrides: dict, expected: str) -> None:
    assert render.announcement_content(new_event(store, **overrides)) == expected


def test_what_a_caller_types_cannot_ping_or_break_the_line(store: Store) -> None:
    event = new_event(
        store,
        ping="role",
        ping_role_id=777,
        title="**loud** @here",
        location="@everyone <@&123456789012345678> <@123456789012345678>\n<@&500> <@!4> <#55>",
    )
    content = render.announcement_content(event)
    assert content.startswith("<@&777> ")
    assert "\n" not in content
    # Every mention the caller typed is defused, whatever its id looks like; only the
    # configured ping is live.
    assert "@everyone" not in content and "@here" not in content
    assert re.findall(r"<@[!&]?\d+>", content) == ["<@&777>"]
    # A channel link pings nobody, and makes a fine meeting point.
    assert content.endswith("<#55>")
    assert "\\*\\*loud\\*\\*" in content


def test_only_the_configured_ping_is_allowed_through() -> None:
    everyone = render.ping_mentions("everyone", None)
    assert (everyone.everyone, everyone.users, everyone.roles) == (True, False, False)
    assert render.ping_mentions("here", None).everyone is True

    role = render.ping_mentions("role", 777)
    assert role.everyone is False and role.users is False
    assert [r.id for r in role.roles] == [777]  # type: ignore[union-attr]

    for kind, role_id in (("none", None), ("role", None), ("nonsense", 5)):
        silent = render.ping_mentions(kind, role_id)
        assert (silent.everyone, silent.users, silent.roles) == (False, False, False)
        assert render.ping_text(kind, role_id) == ""

    roster = render.ROSTER_MENTIONS
    assert (roster.everyone, roster.users, roster.roles) == (False, True, False)


# --- the embed -----------------------------------------------------------------------


def test_embed_lays_out_the_details(store: Store, catalog: Catalog) -> None:
    event = new_event(
        store,
        created_at=CREATED,
        when_kind="at",
        starts_at=AT_2030,
        location="Martlock portal",
        gear="T8 · 1300 IP",
        notes="Regear up to T8.",
        voice_channel_id=55,
        icon="https://render.albiononline.com/v1/item/T8_2H_TOOL_PICK.png",
        roles=("tank", "healer"),
    )
    embed = render.announcement_embed(event, [], catalog)
    assert embed.title == "⚔️ ZvZ"
    assert embed.description == "Mass up."
    assert embed.color is not None and embed.color.value == 0xC0392B
    assert embed.thumbnail.url == event.icon
    assert names(embed) == [
        "🕒 When",
        "📍 Meeting point",
        "🛡️ Gear",
        "🎙️ Voice",
        "📝 Notes",
        "🛡️ Tank · 0",
        "💚 Healer · 0",
    ]
    assert field(embed, "🕒 When") == (
        f"**20:30 UTC** · <t:{AT_2030}:t> your time · <t:{AT_2030}:R>"
    )
    assert field(embed, "🎙️ Voice") == "<#55>"
    assert field(embed, "🛡️ Tank · 0") == "—"
    assert embed.footer.text == "Called by Mocha · Signed up: 0"
    assert embed.timestamp is not None and int(embed.timestamp.timestamp()) == CREATED


def test_embed_leaves_out_what_was_not_filled_in(store: Store, catalog: Catalog) -> None:
    event = new_event(store, when_kind="none", roles=(), message="")
    embed = render.announcement_embed(event, [], catalog)
    assert embed.fields == [] and embed.description is None
    assert embed.footer.text == "Called by Mocha"


def test_when_field_forms(store: Store) -> None:
    assert render.when_field(new_event(store)) == "**Now!**"
    assert render.when_field(new_event(store, language="es")) == "**¡Ahora!**"
    assert render.when_field(new_event(store, when_kind="text", when_text="20h30")) == "20h30"
    assert render.when_field(new_event(store, when_kind="none")) is None
    later = new_event(
        store, when_kind="at", starts_at=SATURDAY_19, created_at=CREATED, language="es"
    )
    assert render.when_field(later) == (
        f"**sáb 10 oct · 19:00 UTC** · <t:{SATURDAY_19}:f> tu hora · <t:{SATURDAY_19}:R>"
    )


@pytest.mark.parametrize(
    ("status", "banner", "closed"),
    [
        ("started", "🚀 **In progress**", False),
        ("finished", "✅ **Finished**", True),
        ("cancelled", "🚫 **Cancelled**", True),
    ],
)
def test_embed_shows_the_status(
    store: Store, catalog: Catalog, status: str, banner: str, closed: bool
) -> None:
    embed = render.announcement_embed(new_event(store, status=status), [], catalog)
    assert embed.description == f"{banner}\n\nMass up."
    assert embed.color is not None
    assert embed.color.value == (render.CLOSED_COLOR if closed else 0xC0392B)


def test_roster_is_one_column_per_role(store: Store, catalog: Catalog) -> None:
    event = new_event(store, roles=("tank", "healer", "dps"), language="es")
    signups = [Signup(10, "tank"), Signup(11, "dps"), Signup(12, "tank")]
    embed = render.announcement_embed(event, signups, catalog)
    assert names(embed)[1:] == ["🛡️ Tanque · 2", "💚 Healer · 0", "⚔️ DPS · 1"]
    assert field(embed, "🛡️ Tanque · 2") == "<@10>\n<@12>"
    assert field(embed, "💚 Healer · 0") == "—"
    assert embed.footer.text == "Convoca Mocha · Anotados: 3"


def test_roster_keeps_people_whose_role_left_the_config(store: Store, catalog: Catalog) -> None:
    event = new_event(store, roles=("tank", "retired"))
    embed = render.announcement_embed(event, [Signup(10, "retired"), Signup(11, "ghost")], catalog)
    assert names(embed)[1:] == ["🛡️ Tank · 0", "retired · 1", "ghost · 1"]


@pytest.mark.parametrize("lang", ["en", "es"])
def test_the_worst_case_still_fits_in_an_embed(store: Store, catalog: Catalog, lang: str) -> None:
    roles = ("tank", "healer", "dps", "support", "scout", "going")
    event = new_event(
        store,
        language=lang,
        title="T" * 100,
        message="M" * 1500,
        when_kind="text",
        when_text="W" * 60,
        location="L" * 100,
        gear="G" * 100,
        notes="N" * 1000,
        voice_channel_id=1234567890123456789,
        author_name="A" * 32,
        status="started",
        roles=roles,
    )
    signups = [Signup(1234567890123456000 + n, roles[n % len(roles)]) for n in range(600)]
    embed = render.announcement_embed(event, signups, catalog)

    assert len(embed) <= render.EMBED_LIMIT
    assert len(embed.title or "") <= 256 and len(embed.description or "") <= 4096
    assert len(embed.fields) <= 25
    for item in embed.fields:
        assert 0 < len(item.name or "") <= 256
        assert 0 < len(item.value or "") <= render.FIELD_LIMIT
    # All six role columns survived, each one honest about who did not fit.
    columns = embed.fields[-len(roles) :]
    assert all(" · 100" in (column.name or "") for column in columns)
    more = "more" if lang == "en" else "más"
    for column in columns:
        value = column.value or ""
        shown = value.count("<@")
        hidden = int(value.rsplit("+", 1)[1].split()[0])
        assert value.endswith(more) and shown + hidden == 100 and shown >= 1


# --- a comp: positions for a set number of players ------------------------------------


def test_a_comp_is_listed_position_by_position(store: Store, catalog: Catalog) -> None:
    event = new_event(store, roles=("tank", "healer", "dps"), language="es")
    comp = capped(catalog)
    embed = render.announcement_embed(event, [Signup(10, "tank")], comp)
    assert names(embed)[1:] == ["👥 Posiciones · 1/3"]
    assert field(embed, "👥 Posiciones · 1/3") == (
        "🛡️ **Tanque** — <@10>\n💚 **Healer** — *libre*\n⚔️ **DPS** — *libre*"
    )
    assert embed.fields[-1].inline is False
    # The heading already counts them.
    assert embed.footer.text == "Convoca Mocha"

    everyone = [Signup(10, "tank"), Signup(11, "healer"), Signup(12, "dps")]
    full = render.announcement_embed(event, everyone, comp)
    assert names(full)[1:] == ["👥 Posiciones · 3/3 ✅"]
    assert "libre" not in field(full, "👥 Posiciones · 3/3 ✅")


def test_a_position_for_several_players_counts_them(store: Store, catalog: Catalog) -> None:
    event = new_event(store, roles=("tank", "dps"))
    roles = {**capped(catalog).roles, "dps": replace(catalog.roles["dps"], limit=2)}
    comp = replace(catalog, roles=roles)
    one = render.announcement_embed(event, [Signup(11, "dps")], comp)
    assert field(one, "👥 Positions · 1/3") == "🛡️ **Tank** — *open*\n⚔️ **DPS** (1/2) — <@11>"
    both = render.announcement_embed(event, [Signup(11, "dps"), Signup(12, "dps")], comp)
    assert field(both, "👥 Positions · 2/3") == (
        "🛡️ **Tank** — *open*\n⚔️ **DPS** (2/2) — <@11>, <@12>"
    )


def test_one_role_anyone_can_join_makes_it_a_head_count(store: Store, catalog: Catalog) -> None:
    event = new_event(store, roles=("tank", "going"))
    mixed = replace(
        catalog, roles={**catalog.roles, "tank": replace(catalog.roles["tank"], limit=1)}
    )
    embed = render.announcement_embed(event, [Signup(10, "tank"), Signup(11, "going")], mixed)
    assert names(embed)[1:] == ["🛡️ Tank · 1/1", "✅ I'm in · 1"]
    assert embed.footer.text == "Called by Mocha · Signed up: 2"


@pytest.mark.parametrize("lang", ["en", "es"])
def test_the_biggest_comp_still_fits_in_an_embed(store: Store, lang: str) -> None:
    # The most positions, the longest names, animated server emoji, each one full.
    emoji = "<a:" + "e" * 32 + ":123456789012345678901>"
    roles = {
        f"r{n}": Role(f"r{n}", emoji, {"en": "L" * LONGEST}, limit=MAX_LIMIT)
        for n in range(MAX_ROLES)
    }
    event = new_event(
        store,
        language=lang,
        title="T" * 100,
        message="M" * 1500,
        when_kind="text",
        when_text="W" * 60,
        location="L" * 100,
        gear="G" * 100,
        notes="N" * 1000,
        voice_channel_id=1234567890123456789,
        author_name="A" * 32,
        status="started",
        roles=tuple(roles),
    )
    players = MAX_ROLES * MAX_LIMIT
    signups = [Signup(1234567890123456000 + n, f"r{n % MAX_ROLES}") for n in range(players)]
    embed = render.announcement_embed(event, signups, Catalog(roles=roles, activities=()))

    assert len(embed) <= render.EMBED_LIMIT and len(embed.fields) <= render.FIELD_COUNT_LIMIT
    positions = roster(embed)
    assert (positions[0].name or "").endswith(f"· {players}/{players} ✅")
    assert all(f.name == render.BLANK and f.inline is False for f in positions[1:])
    assert all(len(f.value or "") <= render.FIELD_LIMIT for f in positions)
    lines = [line for f in positions for line in (f.value or "").split("\n")]
    assert len(lines) == MAX_ROLES
    more = "more" if lang == "en" else "más"
    for line in lines:
        # Each position is honest about who did not fit on its line.
        hidden = int(line.rsplit("+", 1)[1].split()[0])
        assert line.endswith(more) and line.count("<@") + hidden == MAX_LIMIT


def test_a_big_comp_shows_every_name_while_they_fit(store: Store) -> None:
    # The guild's ZvZ: sixteen positions for one player and one for three. An even share
    # of the field would leave the line of three too short for its names.
    roles = {f"r{n}": Role(f"r{n}", "⚔️", {"en": f"Position {n}"}, limit=1) for n in range(16)}
    roles["trio"] = Role("trio", "⚔️", {"en": "Galatinas"}, limit=3)
    event = new_event(store, roles=tuple(roles))
    signups = [Signup(1234567890123456000 + n, f"r{n}") for n in range(16)]
    signups += [Signup(1234567890123456100 + n, "trio") for n in range(3)]
    embed = render.announcement_embed(event, signups, Catalog(roles=roles, activities=()))

    roster = embed.fields[-1]
    assert roster.name == "👥 Positions · 19/19 ✅"
    assert all(f"<@{signup.user_id}>" in (roster.value or "") for signup in signups)
    assert "more" not in (roster.value or "")
    assert (roster.value or "").endswith(
        "⚔️ **Galatinas** (3/3) — <@1234567890123456100>, <@1234567890123456101>, "
        "<@1234567890123456102>"
    )


def test_a_comp_too_long_for_one_field_goes_on_in_the_next(store: Store) -> None:
    roles = {f"r{n}": Role(f"r{n}", "⚔️", {"en": "L" * LONGEST}, limit=1) for n in range(MAX_ROLES)}
    event = new_event(store, roles=tuple(roles))
    signups = [Signup(1234567890123456000 + n, f"r{n}") for n in range(MAX_ROLES)]
    embed = render.announcement_embed(event, signups, Catalog(roles=roles, activities=()))

    first, second = roster(embed)
    assert first.name == f"👥 Positions · {MAX_ROLES}/{MAX_ROLES} ✅"
    assert second.name == render.BLANK and second.inline is False
    assert len(first.value or "") <= render.FIELD_LIMIT
    # In order, everyone named, and no line split between the two.
    assert f"{first.value}\n{second.value}".split("\n") == [
        f"⚔️ **{'L' * LONGEST}** — <@{signup.user_id}>" for signup in signups
    ]


def test_a_big_position_gets_the_room_the_small_ones_leave(store: Store) -> None:
    roles = {f"r{n}": Role(f"r{n}", "⚔️", {"en": f"Position {n}"}, limit=1) for n in range(19)}
    roles["falce"] = Role("falce", "🪓", {"en": "Falce"}, limit=MAX_LIMIT)
    event = new_event(store, roles=tuple(roles))
    singles = [Signup(1234567890123456000 + n, f"r{n}") for n in range(19)]
    falces = [Signup(1234567890123456100 + n, "falce") for n in range(MAX_LIMIT)]
    embed = render.announcement_embed(event, singles + falces, Catalog(roles=roles, activities=()))

    *lines, falce = [line for f in roster(embed) for line in (f.value or "").split("\n")]
    assert [line.rsplit(" ", 1)[1] for line in lines] == [f"<@{s.user_id}>" for s in singles]
    # Nearly a field's worth of names: an even share of the embed would have shown eleven.
    shown, hidden = falce.count("<@"), int(falce.rsplit("+", 1)[1].split()[0])
    assert shown + hidden == MAX_LIMIT and shown >= 40


@pytest.mark.parametrize("lang", ["en", "es"])
@pytest.mark.parametrize("each", [2, 50])
def test_the_most_columns_still_fit_in_an_embed(store: Store, lang: str, each: int) -> None:
    # The most head-count roles, the longest names, animated server emoji, everything else
    # at its longest too, and a sign-up for a role the event no longer offers. Two players
    # a role is the tightest case: their two names would fill a column of 48 characters.
    emoji = "<a:" + "e" * 32 + ":123456789012345678901>"
    roles = {f"r{n}": Role(f"r{n}", emoji, {"en": "L" * LONGEST}) for n in range(MAX_ROLES)}
    event = new_event(
        store,
        language=lang,
        title="T" * 100,
        message="M" * 1500,
        when_kind="text",
        when_text="W" * 60,
        location="L" * 100,
        gear="G" * 100,
        notes="N" * 1000,
        voice_channel_id=1234567890123456789,
        author_name="A" * 32,
        status="started",
        roles=tuple(roles),
    )
    signups = [
        Signup(1234567890123456000 + n, f"r{n % MAX_ROLES}") for n in range(MAX_ROLES * each)
    ]
    signups.append(Signup(10, "ghost"))
    embed = render.announcement_embed(event, signups, Catalog(roles=roles, activities=()))

    assert len(embed) <= render.EMBED_LIMIT
    # Five details and every role's column: Discord's 25 fields. The stray sign-up gave way.
    assert len(embed.fields) == render.FIELD_COUNT_LIMIT
    columns = embed.fields[-MAX_ROLES:]
    assert [column.name for column in columns] == [f"{emoji} {'L' * LONGEST} · {each}"] * MAX_ROLES
    more = "more" if lang == "en" else "más"
    for column in columns:
        value = column.value or ""
        hidden = int(value.rsplit("+", 1)[1].split()[0])
        assert value.endswith(more) and value.count("<@") + hidden == each and "<@" in value


@pytest.mark.parametrize("count", [1, 2, 9, 10, 11, 40, 45, 200])
@pytest.mark.parametrize("limit", [48, 64, 200, 1024])
def test_a_role_column_never_overflows_and_never_miscounts(count: int, limit: int) -> None:
    users = [1234567890123456000 + n for n in range(count)]
    block = render._mention_block(users, limit, "en")
    assert len(block) <= limit
    shown = block.count("<@")
    hidden = int(block.rsplit("+", 1)[1].split()[0]) if "+" in block else 0
    assert shown + hidden == count
    # Listed in arrival order.
    assert [f"<@{u}>" for u in users[:shown]] == [p for p in block.split() if p.startswith("<@")]


# --- follow-ups ----------------------------------------------------------------------------


def test_reminder_reads_well_as_a_notification(store: Store) -> None:
    event = new_event(
        store,
        created_at=CREATED,
        when_kind="at",
        starts_at=AT_2030,
        location="Martlock portal",
        voice_channel_id=55,
    )
    assert render.reminder_text(event, CREATED + 125 * 60) == (
        "🔔 Reminder: ⚔️ **ZvZ**\n"
        "20:30 UTC (in 25 min) · 📍 Martlock portal · 🎙️ <#55>\n"
        "Sign up on the announcement above ☝️"
    )
    # No sign-up buttons, no invitation to use them.
    silent = replace(event, roles=())
    assert "Sign up" not in render.reminder_text(silent, CREATED)


def test_follow_up_messages(store: Store) -> None:
    event = new_event(
        store,
        created_at=CREATED,
        when_kind="at",
        starts_at=AT_2030,
        location="Martlock portal",
        voice_channel_id=55,
    )
    assert render.auto_reminder_text(event, AT_2030 - 15 * 60) == (
        "⏰ ⚔️ **ZvZ** starts in 15 min!\n20:30 UTC · 📍 Martlock portal · 🎙️ <#55>"
    )
    assert render.start_text(event) == (
        "🚀 ⚔️ **ZvZ** is starting now — last call!\n📍 Martlock portal · 🎙️ <#55>"
    )
    assert render.finish_text(event) == "✅ ⚔️ **ZvZ** is over. Thanks to everyone who showed up! o7"
    assert render.cancel_text(event) == "🚫 ⚔️ **ZvZ** has been cancelled."
    assert render.update_text(event, CREATED) == (
        "✏️ ⚔️ **ZvZ** was updated — here is the new plan:\n"
        "20:30 UTC (in 2 h 30 min) · 📍 Martlock portal · 🎙️ <#55>"
    )

    bare = new_event(store)
    assert render.start_text(bare) == "🚀 ⚔️ **ZvZ** is starting now — last call!"
    assert render.reminder_text(bare, CREATED).splitlines()[0] == "🔔 Reminder: ⚔️ **ZvZ**"

    spanish = replace(event, language="es")
    assert render.auto_reminder_text(spanish, AT_2030 - 15 * 60).startswith(
        "⏰ ¡⚔️ **ZvZ** empieza en 15 min!"
    )
    assert render.cancel_text(spanish) == "🚫 ⚔️ **ZvZ** se canceló."


@pytest.mark.parametrize(
    ("seconds", "english", "spanish"),
    [
        (0, "now", "ahora"),
        (20, "now", "ahora"),
        (15 * 60, "in 15 min", "en 15 min"),
        (89 * 60, "in 89 min", "en 89 min"),
        (125 * 60, "in 2 h 05 min", "en 2 h 05 min"),
        (-10 * 60, "10 min ago", "hace 10 min"),
        (-3 * 3600, "3 h 00 min ago", "hace 3 h 00 min"),
    ],
)
def test_relative_times(seconds: int, english: str, spanish: str) -> None:
    assert render.relative("en", seconds) == english
    assert render.relative("es", seconds) == spanish


@pytest.mark.parametrize("count", [0, 1, 85, 86, 300])
def test_roster_mentions_are_split_across_messages(count: int) -> None:
    users = [1234567890123456000 + n for n in range(count)]
    text = "🚀 ⚔️ **ZvZ** is starting now — last call!"
    messages = render.with_mentions(text, users)

    assert messages[0].startswith(text)
    assert all(len(message) <= render.CONTENT_LIMIT for message in messages)
    mentioned = " ".join(messages).replace("\n", " ").split()
    assert [word for word in mentioned if word.startswith("<@")] == [f"<@{u}>" for u in users]
    if count:
        assert messages[0].split("\n")[1].startswith(f"<@{users[0]}>")
    else:
        assert messages == [text]


# --- panel and settings ---------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "es"])
def test_panel_embed(lang: str) -> None:
    embed = render.panel_embed(lang, "America/Bogota")
    assert embed.title and "📯" in embed.title
    assert "**America/Bogota**" in (embed.description or "")
    assert len(embed) <= render.EMBED_LIMIT


def test_settings_embed_defaults() -> None:
    embed = render.config_embed(GuildConfig(guild_id=1), channel_exists=False, checks=["✅ fine"])
    assert field(embed, "Announcement channel") == "The channel where the button is pressed"
    assert field(embed, "Ping") == "@everyone"
    assert field(embed, "Language") == "English"
    assert field(embed, "Time zone for typed times") == "UTC"
    assert field(embed, "Who can announce") == (
        "Admins, and anyone who can see a panel: keep it in a channel only your callers can see"
    )
    assert field(embed, "Automatic reminder").startswith("15 min before")
    assert field(embed, "Health check") == "✅ fine"


def test_settings_embed_configured() -> None:
    config = GuildConfig(
        guild_id=1,
        channel_id=20,
        ping="role",
        ping_role_id=777,
        announcer_role_ids=(500, 501),
        language="es",
        timezone="America/Bogota",
        reminder_minutes=0,
    )
    embed = render.config_embed(config, channel_exists=True, checks=["a", "b"])
    assert field(embed, "Canal de anuncios") == "<#20>"
    assert field(embed, "Ping") == "<@&777>"
    assert field(embed, "Idioma") == "Español"
    assert field(embed, "Quién puede anunciar") == "Los admins y <@&500>, <@&501>"
    assert field(embed, "Recordatorio automático") == "Desactivado"
    assert field(embed, "Chequeo") == "a\nb"

    gone = render.config_embed(config, channel_exists=False, checks=["x"])
    assert "ya no existe" in field(gone, "Canal de anuncios")
    assert (
        field(
            render.config_embed(replace(config, ping="none"), channel_exists=True, checks=["x"]),
            "Ping",
        )
        == "Sin ping"
    )


def test_manage_text(store: Store) -> None:
    event = new_event(store)
    text = render.manage_text(event)
    assert text.startswith("⚙️ ⚔️ **ZvZ** — caller tools\n") and "**Remind**" in text
    assert render.manage_text(event, done="remind").startswith("🔔 Reminder sent.\n⚙️")
    closed = replace(event, status="finished")
    assert (
        render.manage_text(closed, done="finish")
        == "✅ Activity closed.\n⚙️ ⚔️ **ZvZ** — caller tools"
    )
