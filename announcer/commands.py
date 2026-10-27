"""The two slash commands an admin needs: /panel and /config."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any
from zoneinfo import available_timezones

import discord
from discord import app_commands

from . import render, views
from .errors import UserError
from .i18n import DEFAULT_LANGUAGE, LANGUAGES, t
from .service import cannot_post

if TYPE_CHECKING:
    from .bot import AnnouncerBot

# Case-insensitive lookup, so "utc" and "america/bogota" are accepted as typed.
_ZONES = {name.casefold(): name for name in available_timezones()}
_ZONE_NAMES = sorted(_ZONES.values())
# What the timezone box suggests before anything is typed.
_COMMON_ZONES = (
    "UTC",
    "America/Mexico_City",
    "America/Bogota",
    "America/Lima",
    "America/Santiago",
    "America/Argentina/Buenos_Aires",
    "America/Sao_Paulo",
    "America/New_York",
    "Europe/Madrid",
    "Europe/London",
)


def timezone_name(text: str) -> str | None:
    """The canonical name of the time zone ``text`` refers to, if it is one."""
    return _ZONES.get(text.strip().casefold())


def timezone_choices(typed: str) -> list[str]:
    typed = typed.strip().casefold().replace(" ", "_")
    if not typed:
        return [name for name in _COMMON_ZONES if name in _ZONE_NAMES]
    return [name for name in _ZONE_NAMES if typed in name.casefold()][:25]


def register(bot: AnnouncerBot) -> None:
    tree, store, service, catalog = bot.tree, bot.store, bot.service, bot.catalog

    @tree.error
    async def on_error(
        interaction: discord.Interaction[Any], error: app_commands.AppCommandError
    ) -> None:
        await views.report(interaction, getattr(error, "original", error))

    @tree.command(name="panel", description="Post the activity announcer buttons in this channel")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def panel(interaction: discord.Interaction) -> None:
        guild, channel = interaction.guild, interaction.channel
        if guild is None or not isinstance(channel, discord.abc.Messageable):
            raise UserError(t(DEFAULT_LANGUAGE, "error.guild_only"))
        config = store.get_config(guild.id)
        lang = config.language

        # The panel itself is never replied to, so history is not needed here.
        missing = [
            flag
            for flag in service.missing_permissions(guild, channel)
            if flag != "read_message_history"
        ]
        if missing:
            raise UserError(cannot_post(lang, f"<#{channel.id}>", missing))

        await interaction.response.defer(ephemeral=True)
        try:
            await channel.send(
                embed=render.panel_embed(lang, config.timezone),
                view=views.panel_view(catalog, lang),
            )
        except discord.HTTPException as exc:
            reason = render.clip(exc.text or f"HTTP {exc.status}", 120)
            raise UserError(
                t(lang, "error.send_failed", channel=f"<#{channel.id}>", reason=reason)
            ) from exc
        lines = [t(lang, "panel.posted")]
        if service.panel_is_public(guild, config, channel):
            lines.append(t(lang, "panel.public"))
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @tree.command(name="config", description="Show or change the announcer settings")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        channel="Channel the announcements are posted in",
        ping="Who an announcement pings",
        ping_role="Ping this role instead of @everyone",
        announcer_role="Add (or, if already added, remove) a role that may announce",
        language="Language of the bot's messages",
        reminder="Minutes before the start to remind the people signed up (0 = off)",
        timezone="Time zone of typed times such as 20:30 (Albion server time is UTC)",
        reset="Put every setting back to its default",
    )
    @app_commands.choices(
        ping=[
            app_commands.Choice(name="@everyone", value="everyone"),
            app_commands.Choice(name="@here", value="here"),
            app_commands.Choice(name="No ping", value="none"),
        ],
        language=[app_commands.Choice(name=name, value=code) for code, name in LANGUAGES.items()],
    )
    async def config(
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        ping: app_commands.Choice[str] | None = None,
        ping_role: discord.Role | None = None,
        announcer_role: discord.Role | None = None,
        language: app_commands.Choice[str] | None = None,
        reminder: app_commands.Range[int, 0, 180] | None = None,
        timezone: str | None = None,
        reset: bool = False,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise UserError(t(DEFAULT_LANGUAGE, "error.guild_only"))
        current = store.get_config(guild.id)
        # Roles deleted in Discord can no longer be picked, so they could never be
        # removed by hand: drop them here.
        surviving = service.announcer_roles(guild, current)
        if surviving != current.announcer_role_ids:
            current = replace(current, announcer_role_ids=surviving)
            store.save_config(current)

        changes: dict[str, Any] = {}
        if channel is not None:
            changes["channel_id"] = channel.id
        if ping is not None:
            changes.update(ping=ping.value, ping_role_id=None)
        if ping_role is not None:
            if ping_role.is_default():
                # The role picker lists @everyone as a role; it is not pinged like one.
                changes.update(ping="everyone", ping_role_id=None)
            else:
                changes.update(ping="role", ping_role_id=ping_role.id)
        if announcer_role is not None:
            toggled = set(current.announcer_role_ids) ^ {announcer_role.id}
            changes["announcer_role_ids"] = tuple(sorted(toggled))
        if language is not None:
            changes["language"] = language.value
        if reminder is not None:
            changes["reminder_minutes"] = reminder
        if timezone is not None:
            name = timezone_name(timezone)
            if name is None:
                raise UserError(t(current.language, "config.bad_timezone", name=timezone))
            changes["timezone"] = name

        note = None
        if reset:
            store.reset_config(guild.id)
            current = store.get_config(guild.id)
            note = t(current.language, "config.reset")
        elif changes:
            current = replace(current, **changes)
            store.save_config(current)
            note = t(current.language, "config.saved")

        exists = (
            current.channel_id is not None
            and guild.get_channel_or_thread(current.channel_id) is not None
        )
        embed = render.config_embed(
            current,
            channel_exists=exists,
            checks=service.health(guild, current, interaction.channel),
        )
        await interaction.response.send_message(note, embed=embed, ephemeral=True)

    @config.autocomplete("timezone")
    async def timezone_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [app_commands.Choice(name=name, value=name) for name in timezone_choices(current)]
