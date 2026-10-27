"""Button presses, form submissions and slash commands, as Discord would deliver them."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest
from discord import app_commands

from announcer import commands, views
from announcer.bot import INVITE_PERMISSIONS, AnnouncerBot
from announcer.config import Settings
from announcer.errors import UserError
from announcer.service import REQUIRED_PERMISSIONS, Draft
from announcer.store import Event

from .conftest import OFFICER_ROLE, World
from .fakes import FakeInteraction, FakeMember, FakeRole
from .test_service import NO_PING, configure


def press(world: World, user: FakeMember, channel: Any = None) -> FakeInteraction:
    client = SimpleNamespace(service=world.service, store=world.store)
    return FakeInteraction(client, world.guild, user, channel or world.panel)


def ids(view: discord.ui.View | None) -> list[str]:
    assert view is not None
    return [b["custom_id"] for row in view.to_components() for b in row["components"]]


async def submit(form: views.AnnounceForm, interaction: FakeInteraction) -> None:
    """Submit a form the way discord.py does: errors go to the form's own handler."""
    try:
        await form.on_submit(interaction)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - exactly what discord.py's dispatcher does
        await form.on_error(interaction, exc)  # type: ignore[arg-type]


async def announced(world: World, key: str = "zvz", **draft: str) -> Event:
    done = await world.service.announce(
        world.guild, world.officer, world.panel, key, Draft(**draft)
    )
    return done.event


# --- the panel ------------------------------------------------------------------------


async def test_pressing_a_panel_button_opens_the_form_and_announces(world: World) -> None:
    click = press(world, world.officer)
    await views.PanelButton("zvz").callback(click)  # type: ignore[arg-type]
    kind, form, _ = click.answer
    assert kind == "send_modal" and isinstance(form, views.AnnounceForm)
    assert form.title == "⚔️ ZvZ"
    assert world.panel.sent == []  # nothing is posted until the form comes back

    form.when._value = "20:30"
    form.where._value = "Martlock portal"
    answer = press(world, world.officer)
    await submit(form, answer)

    assert answer.answer == ("defer", None, {"ephemeral": True, "thinking": True})
    (sent,) = world.panel.sent
    assert sent.content == "@everyone ⚔️ **ZvZ** — 20:30 UTC · 📍 Martlock portal"
    ((text, options),) = answer.followup.sent
    assert text.splitlines()[0] == f"✅ Announced in {world.panel.mention} → {sent.jump_url}"
    assert "⏰ I'll remind" in text.splitlines()[1]
    assert options == {"ephemeral": True}


async def test_the_form_speaks_the_guild_language(world: World) -> None:
    configure(world, language="es", timezone="America/Bogota")
    click = press(world, world.officer)
    await views.PanelButton("custom").callback(click)  # type: ignore[arg-type]
    form = click.answer[1]
    assert form.title == "📢 Anuncio libre"
    payload = form.to_dict()["components"]
    assert [label["label"] for label in payload][:2] == ["Título", "Mensaje"]
    assert payload[2]["description"].endswith("(America/Bogota)")


async def test_a_member_without_an_announcer_role_is_told_why(world: World) -> None:
    configure(world, announcer_role_ids=(OFFICER_ROLE,))
    click = press(world, world.member)
    await views.PanelButton("zvz").callback(click)  # type: ignore[arg-type]
    kind, text, options = click.answer
    assert kind == "send_message" and options == {"ephemeral": True}
    assert text == (
        f"You can't announce activities here. You need one of these roles: <@&{OFFICER_ROLE}>."
    )


async def test_a_stale_panel_button_is_explained(world: World) -> None:
    click = press(world, world.officer)
    await views.PanelButton("tea_party").callback(click)  # type: ignore[arg-type]
    assert "/panel" in click.answer[1]


async def test_a_ping_that_would_reach_nobody_asks_first(world: World) -> None:
    world.panel.bot_permissions = NO_PING
    click = press(world, world.officer)
    await views.PanelButton("zvz").callback(click)  # type: ignore[arg-type]
    kind, text, options = click.answer
    assert kind == "send_message" and text.startswith("⚠️ I can't ping `@everyone`")
    assert options["ephemeral"] is True
    assert ids(options["view"]) == ["ao:new:zvz!"]
    assert options["view"].timeout is None

    # "Announce without a ping" goes straight to the form.
    again = press(world, world.officer)
    await views.PanelButton("zvz", force=True).callback(again)  # type: ignore[arg-type]
    assert again.answer[0] == "send_modal"


async def test_a_refusal_after_the_form_reaches_the_caller(world: World) -> None:
    click = press(world, world.officer)
    await views.PanelButton("zvz").callback(click)  # type: ignore[arg-type]
    form = click.answer[1]

    configure(world, channel_id=999)  # the channel disappears while the form is open
    answer = press(world, world.officer)
    await submit(form, answer)
    ((text, options),) = answer.followup.sent
    assert "no longer exists" in text and options == {"ephemeral": True}


async def test_an_unexpected_failure_apologises_instead_of_hanging(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def explode(*args: Any) -> None:
        raise RuntimeError("database on fire")

    monkeypatch.setattr(world.service, "precheck", explode)
    configure(world, language="es")
    click = press(world, world.officer)
    await views.PanelButton("zvz").callback(click)  # type: ignore[arg-type]
    assert click.answer == (
        "send_message",
        "Algo falló de mi lado. Inténtalo de nuevo en un momento.",
        {"ephemeral": True},
    )
    assert "database on fire" in caplog.text  # and the real reason is in the log


async def test_buttons_do_nothing_outside_a_server(world: World) -> None:
    client = SimpleNamespace(service=world.service, store=world.store)
    click = FakeInteraction(client, None, world.officer, world.panel)
    await views.PanelButton("zvz").callback(click)  # type: ignore[arg-type]
    assert click.answer == ("send_message", "This only works inside a server.", {"ephemeral": True})


# --- signing up -------------------------------------------------------------------------


async def test_join_and_leave_buttons(world: World) -> None:
    event = await announced(world)
    player = FakeMember(100, "Player")

    click = press(world, player)
    await views.JoinButton(event.id, "tank").callback(click)  # type: ignore[arg-type]
    assert click.answer == ("defer", None, {})  # acknowledged without a visible reply
    await world.service.drain()
    assert [(s.user_id, s.role) for s in world.store.signups(event.id)] == [(100, "tank")]
    assert len(world.panel.edits) == 1

    click = press(world, player)
    await views.LeaveButton(event.id).callback(click)  # type: ignore[arg-type]
    assert click.answer == ("defer", None, {})
    assert world.store.signups(event.id) == []


async def test_signing_up_for_a_closed_activity_is_explained(world: World) -> None:
    event = await announced(world)
    await world.service.cancel(world.guild, world.officer, event.id)
    click = press(world, FakeMember(100))
    await views.JoinButton(event.id, "tank").callback(click)  # type: ignore[arg-type]
    assert click.answer == ("send_message", "This activity is already closed.", {"ephemeral": True})


# --- caller tools ---------------------------------------------------------------------------


async def test_manage_opens_the_tools_privately(world: World) -> None:
    event = await announced(world)
    click = press(world, world.officer)
    await views.ManageButton(event.id).callback(click)  # type: ignore[arg-type]
    kind, text, options = click.answer
    assert kind == "send_message" and options["ephemeral"] is True
    assert text.startswith("⚙️ ⚔️ **ZvZ** — caller tools")
    assert ids(options["view"]) == [
        f"ao:act:{event.id}:{action}" for action in ("remind", "start", "edit", "finish", "cancel")
    ]

    stranger = press(world, world.member)
    await views.ManageButton(event.id).callback(stranger)  # type: ignore[arg-type]
    assert stranger.answer == (
        "send_message",
        "Only the caller or an officer can manage this activity.",
        {"ephemeral": True},
    )


async def test_tool_buttons_act_and_update_the_tools_message(world: World) -> None:
    event = await announced(world, when="20:30")

    click = press(world, world.officer)
    await views.ActionButton(event.id, "remind").callback(click)  # type: ignore[arg-type]
    assert click.answer == ("defer", None, {})
    (edit,) = click.original_edits
    assert edit["content"].startswith("🔔 Reminder sent.\n⚙️ ⚔️ **ZvZ**")
    assert len(ids(edit["view"])) == 5
    assert (world.panel.sent[-1].content or "").startswith("@everyone 🔔 Reminder")

    click = press(world, world.officer)
    await views.ActionButton(event.id, "start").callback(click)  # type: ignore[arg-type]
    (edit,) = click.original_edits
    assert edit["content"].startswith("🚀 Marked as started")
    assert f"ao:act:{event.id}:start" not in ids(edit["view"])  # no longer offered

    click = press(world, world.officer)
    await views.ActionButton(event.id, "finish").callback(click)  # type: ignore[arg-type]
    (edit,) = click.original_edits
    assert edit == {"content": "✅ Activity closed.\n⚙️ ⚔️ **ZvZ** — caller tools", "view": None}
    stored = world.store.get_event(event.id)
    assert stored is not None and stored.status == "finished"


async def test_a_refused_tool_button_answers_directly(world: World) -> None:
    event = await announced(world)
    click = press(world, world.member)
    await views.ActionButton(event.id, "cancel").callback(click)  # type: ignore[arg-type]
    assert click.answer[0] == "send_message"  # refused before anything was acknowledged
    assert click.original_edits == []
    stored = world.store.get_event(event.id)
    assert stored is not None and stored.status == "open"

    # Refusals that only show up after the acknowledgement arrive as a follow-up.
    await world.service.remind(world.guild, world.officer, event.id)
    again = press(world, world.officer)
    await views.ActionButton(event.id, "remind").callback(again)  # type: ignore[arg-type]
    assert again.answer == ("defer", None, {})
    assert again.followup.sent == [
        ("A reminder just went out. Try again in 60s.", {"ephemeral": True})
    ]


async def test_edit_reopens_the_form_filled_in(world: World) -> None:
    event = await announced(world, when="20:30", location="Martlock portal", gear="T8")
    click = press(world, world.officer)
    await views.ActionButton(event.id, "edit").callback(click)  # type: ignore[arg-type]
    kind, form, _ = click.answer
    assert kind == "send_modal" and form.title == "Edit · ⚔️ ZvZ"
    assert form.draft() == Draft(when="20:30", location="Martlock portal", gear="T8")

    form.where._value = "Thetford portal"
    answer = press(world, world.officer)
    await submit(form, answer)
    await world.service.drain()

    assert answer.answer == ("defer", None, {"ephemeral": True, "thinking": True})
    assert answer.followup.sent == [("✏️ Announcement updated.", {"ephemeral": True})]
    stored = world.store.get_event(event.id)
    assert stored is not None and stored.location == "Thetford portal"
    assert world.panel.last_edit()["content"].endswith("📍 Thetford portal")


async def test_the_edit_confirmation_carries_what_the_caller_should_know(world: World) -> None:
    event = await announced(world, when="20:30")
    click = press(world, world.officer)
    await views.ActionButton(event.id, "edit").callback(click)  # type: ignore[arg-type]
    form = click.answer[1]
    form.when._value = "21h"  # not a time the bot can read
    answer = press(world, world.officer)
    await submit(form, answer)
    ((text, _),) = answer.followup.sent
    assert text.splitlines()[0] == "✏️ Announcement updated."
    assert "couldn't read the time" in text.splitlines()[1]


async def test_every_form_gets_its_own_recognisable_id(world: World) -> None:
    forms = []
    for _ in range(2):
        click = press(world, world.officer)
        await views.PanelButton("zvz").callback(click)  # type: ignore[arg-type]
        forms.append(click.answer[1])
    first, second = (form.custom_id for form in forms)
    assert first != second and first.startswith(views.FORM_PREFIX) and len(first) <= 100
    assert views.AnnounceForm.is_expected(first) and views.AnnounceForm.is_expected(second)
    assert not views.AnnounceForm.is_expected(views.FORM_PREFIX + "0" * 16)


async def test_forms_are_forgotten_after_their_timeout(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    click = press(world, world.officer)
    await views.PanelButton("zvz").callback(click)  # type: ignore[arg-type]
    old = click.answer[1].custom_id

    later = time.monotonic() + views.FORM_TIMEOUT + 61
    monkeypatch.setattr(time, "monotonic", lambda: later)
    assert not views.AnnounceForm.is_expected(old)
    click = press(world, world.officer)
    await views.PanelButton("fame").callback(click)  # type: ignore[arg-type]
    assert old not in views.AnnounceForm._expected  # opening a form tidies up the old ones


async def test_editing_a_custom_announcement_shows_its_own_form(world: World) -> None:
    event = await announced(world, "custom", title="Guild meeting", body="On voice.")
    click = press(world, world.officer)
    await views.ActionButton(event.id, "edit").callback(click)  # type: ignore[arg-type]
    form = click.answer[1]
    assert [label["label"] for label in form.to_dict()["components"]] == [
        "Title",
        "Message",
        "When",
        "Meeting point",
    ]
    assert form.draft() == Draft(title="Guild meeting", body="On voice.")


# --- slash commands ----------------------------------------------------------------------------


@pytest.fixture
def bot(world: World, tmp_path: Path) -> AnnouncerBot:
    settings = Settings(
        token="x",
        activities_file=tmp_path / "activities.toml",
        database_file=tmp_path / "announcer.db",
        default_language="en",
        log_level="INFO",
    )
    bot = AnnouncerBot(settings, world.catalog, world.store)
    commands.register(bot)
    return bot


def command(bot: AnnouncerBot, name: str) -> app_commands.Command[Any, ..., Any]:
    found = bot.tree.get_command(name)
    assert isinstance(found, app_commands.Command)
    return found


def slash(world: World, bot: AnnouncerBot, user: FakeMember) -> FakeInteraction:
    return FakeInteraction(bot, world.guild, user, world.panel)


def settings_field(interaction: FakeInteraction, name: str) -> str:
    embed = interaction.answer[2]["embed"]
    return next(f.value for f in embed.fields if f.name == name)


def test_the_commands_discord_will_show(bot: AnnouncerBot) -> None:
    assert sorted(c.name for c in bot.tree.get_commands()) == ["config", "panel"]
    manage_server = discord.Permissions(manage_guild=True).value

    panel = command(bot, "panel").to_dict(bot.tree)
    assert panel["options"] == [] and len(panel["description"]) <= 100
    assert int(panel["default_member_permissions"]) == manage_server
    assert panel["contexts"] == [0]  # servers only

    config = command(bot, "config").to_dict(bot.tree)
    assert int(config["default_member_permissions"]) == manage_server
    options = {option["name"]: option for option in config["options"]}
    assert list(options) == [
        "channel",
        "ping",
        "ping_role",
        "announcer_role",
        "language",
        "reminder",
        "timezone",
        "reset",
    ]
    assert not any(option.get("required") for option in options.values())
    assert all(1 <= len(option["description"]) <= 100 for option in options.values())
    assert options["channel"]["channel_types"] == [0, 5]  # text and announcement channels
    assert [choice["value"] for choice in options["ping"]["choices"]] == [
        "everyone",
        "here",
        "none",
    ]
    assert [choice["value"] for choice in options["language"]["choices"]] == ["en", "es"]
    assert (options["reminder"]["min_value"], options["reminder"]["max_value"]) == (0, 180)
    assert options["timezone"]["autocomplete"] is True


def test_the_bot_asks_for_no_privileged_intents(bot: AnnouncerBot) -> None:
    intents = bot.intents
    assert not (intents.members or intents.message_content or intents.presences)
    assert intents.guilds and intents.voice_states
    # The invite link asks for everything the health check will look for.
    for flag in (*REQUIRED_PERMISSIONS, "send_messages_in_threads", "mention_everyone"):
        assert getattr(INVITE_PERMISSIONS, flag)
    # Nothing pings unless a send says so.
    default = bot.allowed_mentions
    assert default is not None and (default.everyone, default.users, default.roles) == (False,) * 3


async def test_panel_command_posts_the_buttons(world: World, bot: AnnouncerBot) -> None:
    interaction = slash(world, bot, world.admin)
    await command(bot, "panel").callback(interaction)  # type: ignore[call-arg, arg-type]

    assert interaction.answer == ("defer", None, {"ephemeral": True})
    (sent,) = world.panel.sent
    assert sent.embed is not None and sent.embed.title == "📯 Activity announcer"
    assert sent.content is None  # the panel itself pings nobody
    assert ids(sent.view)[:2] == ["ao:new:zvz", "ao:new:castle"] and len(ids(sent.view)) == 14
    assert interaction.followup.sent == [
        ("Panel posted. Pin it so it stays easy to find.", {"ephemeral": True})
    ]


async def test_a_panel_the_whole_server_can_see_comes_with_a_warning(
    world: World, bot: AnnouncerBot
) -> None:
    world.panel.everyone_permissions = world.announcements.everyone_permissions
    interaction = slash(world, bot, world.admin)
    await command(bot, "panel").callback(interaction)  # type: ignore[call-arg, arg-type]
    ((text, _),) = interaction.followup.sent
    assert text == (
        "Panel posted. Pin it so it stays easy to find.\n"
        "⚠️ Everyone in the server can see this channel, so anyone can announce with these "
        "buttons. To keep them for your callers, post the panel in a channel only they can "
        "see, or pick announcer roles with `/config announcer_role`."
    )

    # Announcer roles keep it for the callers wherever the panel is.
    configure(world, announcer_role_ids=(OFFICER_ROLE,))
    interaction = slash(world, bot, world.admin)
    await command(bot, "panel").callback(interaction)  # type: ignore[call-arg, arg-type]
    assert interaction.followup.sent == [
        ("Panel posted. Pin it so it stays easy to find.", {"ephemeral": True})
    ]


async def test_panel_command_names_what_the_bot_is_missing(world: World, bot: AnnouncerBot) -> None:
    world.panel.bot_permissions = discord.Permissions(view_channel=True, send_messages=True)
    with pytest.raises(UserError, match=r"\*\*Embed Links\*\*\.$"):
        await command(bot, "panel").callback(slash(world, bot, world.admin))  # type: ignore[call-arg, arg-type]
    assert world.panel.sent == []


async def test_config_alone_shows_the_settings(world: World, bot: AnnouncerBot) -> None:
    interaction = slash(world, bot, world.admin)
    await command(bot, "config").callback(interaction)  # type: ignore[call-arg, arg-type]
    kind, note, options = interaction.answer
    assert (kind, note, options["ephemeral"]) == ("send_message", None, True)
    assert options["embed"].title == "⚙️ Announcer settings"
    assert settings_field(interaction, "Ping") == "@everyone"
    assert settings_field(interaction, "Health check") == (
        f"✅ I have every permission I need in {world.panel.mention}."
    )


async def test_config_changes_are_saved(world: World, bot: AnnouncerBot) -> None:
    interaction = slash(world, bot, world.admin)
    await command(bot, "config").callback(  # type: ignore[call-arg]
        interaction,  # type: ignore[arg-type]
        channel=world.announcements,
        ping=app_commands.Choice(name="@here", value="here"),
        language=app_commands.Choice(name="Español", value="es"),
        reminder=30,
        timezone="america/bogota",
    )
    saved = world.store.get_config(world.guild.id)
    assert (saved.channel_id, saved.ping, saved.language) == (world.announcements.id, "here", "es")
    assert (saved.reminder_minutes, saved.timezone) == (30, "America/Bogota")

    assert interaction.answer[1] == "Guardado."
    assert settings_field(interaction, "Canal de anuncios") == world.announcements.mention
    assert settings_field(interaction, "Ping") == "@here"


async def test_config_toggles_announcer_roles(world: World, bot: AnnouncerBot) -> None:
    config = command(bot, "config").callback
    officers = world.guild.roles[OFFICER_ROLE]
    callers = world.guild.add_role(FakeRole(501))

    await config(slash(world, bot, world.admin), announcer_role=officers)  # type: ignore[call-arg, arg-type]
    await config(slash(world, bot, world.admin), announcer_role=callers)  # type: ignore[call-arg, arg-type]
    assert world.store.get_config(world.guild.id).announcer_role_ids == (OFFICER_ROLE, 501)

    last = slash(world, bot, world.admin)
    await config(last, announcer_role=officers)  # type: ignore[call-arg, arg-type]
    assert world.store.get_config(world.guild.id).announcer_role_ids == (501,)
    assert settings_field(last, "Who can announce") == "Admins and <@&501>"


async def test_config_ping_role(world: World, bot: AnnouncerBot) -> None:
    config = command(bot, "config").callback
    raiders = world.guild.add_role(FakeRole(777, mentionable=True))

    await config(slash(world, bot, world.admin), ping_role=raiders)  # type: ignore[call-arg, arg-type]
    saved = world.store.get_config(world.guild.id)
    assert (saved.ping, saved.ping_role_id) == ("role", 777)

    # Picking @everyone in the role list means the real @everyone ping, not a role mention.
    everyone = FakeRole(world.guild.id, default=True)
    await config(slash(world, bot, world.admin), ping_role=everyone)  # type: ignore[call-arg, arg-type]
    saved = world.store.get_config(world.guild.id)
    assert (saved.ping, saved.ping_role_id) == ("everyone", None)

    # Choosing a plain ping forgets the role.
    world.store.save_config(replace(saved, ping="role", ping_role_id=777))
    none = app_commands.Choice(name="No ping", value="none")
    await config(slash(world, bot, world.admin), ping=none)  # type: ignore[call-arg, arg-type]
    saved = world.store.get_config(world.guild.id)
    assert (saved.ping, saved.ping_role_id) == ("none", None)


async def test_config_rejects_an_unknown_time_zone(world: World, bot: AnnouncerBot) -> None:
    with pytest.raises(UserError, match="Narnia/Cair_Paravel"):
        await command(bot, "config").callback(  # type: ignore[call-arg]
            slash(world, bot, world.admin),  # type: ignore[arg-type]
            timezone="Narnia/Cair_Paravel",
            reminder=45,
        )
    assert world.store.get_config(world.guild.id).reminder_minutes == 15  # nothing half-saved


async def test_config_reset(world: World, bot: AnnouncerBot) -> None:
    configure(world, channel_id=world.announcements.id, language="es", ping="none")
    interaction = slash(world, bot, world.admin)
    await command(bot, "config").callback(interaction, reset=True)  # type: ignore[call-arg, arg-type]
    saved = world.store.get_config(world.guild.id)
    assert (saved.channel_id, saved.language, saved.ping) == (None, "en", "everyone")
    assert interaction.answer[1] == "Settings restored to their defaults."


async def test_config_warns_about_a_channel_that_is_gone(world: World, bot: AnnouncerBot) -> None:
    configure(world, channel_id=999)
    interaction = slash(world, bot, world.admin)
    await command(bot, "config").callback(interaction)  # type: ignore[call-arg, arg-type]
    assert (
        settings_field(interaction, "Announcement channel") == "⚠️ A channel that no longer exists"
    )
    assert settings_field(interaction, "Health check").startswith("❌")


async def test_config_forgets_announcer_roles_deleted_in_discord(
    world: World, bot: AnnouncerBot
) -> None:
    configure(world, announcer_role_ids=(OFFICER_ROLE, 999))  # 999 was deleted in Discord
    interaction = slash(world, bot, world.admin)
    await command(bot, "config").callback(interaction)  # type: ignore[call-arg, arg-type]
    assert world.store.get_config(world.guild.id).announcer_role_ids == (OFFICER_ROLE,)
    assert settings_field(interaction, "Who can announce") == f"Admins and <@&{OFFICER_ROLE}>"
    assert interaction.answer[1] is None  # tidying up is not announced as a change


class FormAnswer(FakeInteraction):
    """A modal submission as `on_interaction` sees it."""

    def __init__(self, world: World, bot: AnnouncerBot, custom_id: str) -> None:
        super().__init__(bot, world.guild, world.officer, world.panel)
        self.type = discord.InteractionType.modal_submit
        self.data = {"custom_id": custom_id, "components": []}


async def test_a_form_nobody_is_waiting_for_is_answered(world: World, bot: AnnouncerBot) -> None:
    configure(world, language="es")
    orphan = FormAnswer(world, bot, views.FORM_PREFIX + "feedfacefeedface")  # from before a restart
    await bot.on_interaction(orphan)  # type: ignore[arg-type]
    assert orphan.answer == (
        "send_message",
        "Ese formulario ya no vale: me reiniciaron o estuvo abierto demasiado tiempo. "
        "Pulsa el botón otra vez.",
        {"ephemeral": True},
    )


async def test_a_form_being_handled_is_left_to_its_handler(world: World, bot: AnnouncerBot) -> None:
    click = press(world, world.officer)
    await views.PanelButton("zvz").callback(click)  # type: ignore[arg-type]
    live = FormAnswer(world, bot, click.answer[1].custom_id)
    await bot.on_interaction(live)  # type: ignore[arg-type]
    assert live.response.calls == []

    # Not ours at all: some other modal, or not a modal.
    foreign = FormAnswer(world, bot, "someone-elses-modal")
    await bot.on_interaction(foreign)  # type: ignore[arg-type]
    button = FormAnswer(world, bot, views.FORM_PREFIX + "feedfacefeedface")
    button.type = discord.InteractionType.component
    await bot.on_interaction(button)  # type: ignore[arg-type]
    assert foreign.response.calls == [] and button.response.calls == []


def test_time_zone_suggestions() -> None:
    assert commands.timezone_choices("")[0] == "UTC"
    assert "America/Bogota" in commands.timezone_choices("")
    assert commands.timezone_choices("bogo") == ["America/Bogota"]
    assert commands.timezone_choices("buenos aires") == [
        "America/Argentina/Buenos_Aires",
        "America/Buenos_Aires",
    ]
    assert len(commands.timezone_choices("america")) == 25  # Discord's cap on suggestions
    assert commands.timezone_choices("narnia") == []

    assert commands.timezone_name(" utc ") == "UTC"
    assert commands.timezone_name("europe/madrid") == "Europe/Madrid"
    assert commands.timezone_name("Madrid") is None


async def test_command_errors_reach_the_user(world: World, bot: AnnouncerBot) -> None:
    interaction = slash(world, bot, world.admin)
    wrapped = app_commands.CommandInvokeError(command(bot, "panel"), UserError("Nope."))
    await bot.tree.on_error(interaction, wrapped)  # type: ignore[arg-type]
    assert interaction.answer == ("send_message", "Nope.", {"ephemeral": True})
