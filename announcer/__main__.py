"""Entry point: ``python -m announcer`` (or ``./run.sh``)."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from collections.abc import Sequence

import discord
from dotenv import load_dotenv

from . import setup
from .bot import AnnouncerBot
from .catalog import CatalogError, load_catalog
from .config import ROOT, ConfigError, load_settings
from .store import Store


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="announcer", description="Albion Online guild activity announcer for Discord."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="validate .env and activities.toml, then exit without connecting to Discord",
    )
    mode.add_argument(
        "--setup",
        action="store_true",
        help="ask for the bot token, check it with Discord and save it in .env",
    )
    args = parser.parse_args(argv)
    if args.setup:
        return setup.run(ROOT / ".env")

    load_dotenv(ROOT / ".env")
    try:
        settings = load_settings(require_token=not args.check)
        catalog = load_catalog(settings.activities_file)
    except (ConfigError, CatalogError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        names = ", ".join(activity.label(settings.default_language) for activity in catalog.buttons)
        print(f"{settings.activities_file.name}: {len(catalog.buttons)} buttons — {names}")
        print(f"sign-up roles: {', '.join(catalog.roles)}")
        print("token: " + ("set" if settings.token else "NOT SET (run ./run.sh --setup)"))
        return 0

    discord.utils.setup_logging(level=getattr(logging, settings.log_level))
    # discord.py warns at every start that voice *audio* is unavailable. This bot never
    # joins a voice channel, and the warning reads as if something were broken.
    logging.getLogger("discord.client").addFilter(
        lambda record: "voice will NOT be supported" not in record.getMessage()
    )
    # systemd stops a service with SIGTERM; treat it like Ctrl-C so the bot signs off
    # from Discord and closes its database instead of being cut off mid-request.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    store = Store(settings.database_file, default_language=settings.default_language)
    bot = AnnouncerBot(settings, catalog, store)
    try:
        bot.run(settings.token, log_handler=None)
    except discord.LoginFailure:
        print(
            "error: Discord rejected the token. Reset it in the Developer Portal "
            "(your application → Bot → Reset Token) and save the new one with ./run.sh --setup.",
            file=sys.stderr,
        )
        return 2
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
