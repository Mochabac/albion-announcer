"""The Discord client: wires the buttons, the commands and the reminder clock together."""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import tasks

from . import commands, views
from .catalog import Catalog
from .config import Settings
from .i18n import t
from .service import Service
from .store import Store

log = logging.getLogger(__name__)

# What the invite link asks for. "Mention Everyone" is the one that lets the ping notify.
INVITE_PERMISSIONS = discord.Permissions(
    view_channel=True,
    send_messages=True,
    send_messages_in_threads=True,
    embed_links=True,
    read_message_history=True,
    mention_everyone=True,
)
REMINDER_TICK = 20  # seconds


class AnnouncerBot(discord.Client):
    def __init__(self, settings: Settings, catalog: Catalog, store: Store) -> None:
        # No privileged intents: buttons and slash commands arrive as interactions.
        # `guilds` gives the channel and role cache that permission checks need;
        # `voice_states` is how the bot knows which voice channel a caller is in.
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        super().__init__(
            intents=intents,
            # Nothing pings unless a send says so explicitly.
            allowed_mentions=discord.AllowedMentions.none(),
            activity=discord.Game(name="Albion Online"),
        )
        self.settings = settings
        self.catalog = catalog
        self.store = store
        # The bot has to be a member of a server to post there. Left unsaid, Discord also
        # offers the commands to anyone who adds the app to their own account, in servers
        # the bot is not in, where they cannot work.
        self.tree = app_commands.CommandTree(
            self, allowed_installs=app_commands.AppInstallationType(guild=True, user=False)
        )
        self.service = Service(self, store, catalog, event_view=views.event_view)
        self._greeted = False

    async def setup_hook(self) -> None:
        self.add_dynamic_items(*views.DYNAMIC_ITEMS)
        commands.register(self)
        try:
            synced = await self.tree.sync()
        except discord.HTTPException:
            log.exception(
                "Could not register the slash commands; /panel and /config may be missing"
            )
        else:
            log.info("Slash commands registered: %s", ", ".join(f"/{c.name}" for c in synced))
        # Announcements a previous run changed but did not get to redraw.
        self.service.resume()
        self.reminders.start()

    async def on_ready(self) -> None:
        if self._greeted or self.user is None:
            return
        self._greeted = True
        log.info("Logged in as %s, in %d server(s)", self.user, len(self.guilds))
        invite = discord.utils.oauth_url(
            self.user.id, permissions=INVITE_PERMISSIONS, scopes=("bot", "applications.commands")
        )
        log.info("Invite link: %s", invite)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Answer a form nobody is waiting for any more.

        A form opened before a restart, or left open past its timeout, has no handler;
        Discord would show its own "Something went wrong". Say what happened instead.
        """
        if interaction.type is not discord.InteractionType.modal_submit:
            return
        data: dict[str, Any] = dict(interaction.data or {})
        custom_id = str(data.get("custom_id", ""))
        if not custom_id.startswith(views.FORM_PREFIX):
            return
        if views.AnnounceForm.is_expected(custom_id) or interaction.response.is_done():
            return
        await views.tell(interaction, t(views.language_of(interaction), "error.form_expired"))

    @tasks.loop(seconds=REMINDER_TICK)
    async def reminders(self) -> None:
        # An exception escaping here would stop the loop for good.
        try:
            await self.service.run_due_reminders()
            # Also the moment to retry announcements whose last redraw did not go through.
            self.service.redraw_stale()
        except Exception:
            log.exception("The reminder tick failed")

    @reminders.before_loop
    async def _wait_until_ready(self) -> None:
        await self.wait_until_ready()

    async def close(self) -> None:
        self.reminders.cancel()
        await super().close()
