"""``./run.sh --setup``: puts the bot token in .env, so nobody has to edit the file by hand."""

from __future__ import annotations

import asyncio
import getpass
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import discord
from dotenv import dotenv_values

from .bot import INVITE_PERMISSIONS
from .config import TOKEN_PLACEHOLDER
from .i18n import DEFAULT_LANGUAGE, LANGUAGES

PORTAL = "https://discord.com/developers/applications"
ATTEMPTS = 3
YES = ("y", "yes", "s", "si", "sí")


class TokenRejected(Exception):
    """Discord does not know the token."""


def check_token(token: str) -> tuple[str, int]:
    """The bot account a token belongs to, as Discord sees it: its name and id."""

    async def ask_discord() -> tuple[str, int]:
        client = discord.Client(intents=discord.Intents.none())
        try:
            user = await client.http.static_login(token)
        except discord.LoginFailure as exc:
            raise TokenRejected from exc
        finally:
            await client.close()
        return user["username"], int(user["id"])

    return asyncio.run(ask_discord())


def write_env(path: Path, token: str, language: str) -> None:
    """Put the token and the language in ``path``, keeping every other line as it was.

    A new .env starts as a copy of .env.example. Only its owner can ever read it: it is
    written to a private temporary file, which then takes its place.
    """
    template = path if path.exists() else path.with_name(".env.example")
    lines = template.read_text(encoding="utf-8").splitlines() if template.exists() else []
    values = {"DISCORD_TOKEN": token, "DEFAULT_LANGUAGE": language}
    for index, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if "=" in line and key in values:
            lines[index] = f"{key}={values.pop(key)}"
    lines += [f"{key}={value}" for key, value in values.items()]

    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".env.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write("\n".join(lines) + "\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def run(
    env_file: Path,
    *,
    ask: Callable[[str], str] = input,
    ask_secret: Callable[[str], str] = getpass.getpass,
    check: Callable[[str], tuple[str, int]] = check_token,
    say: Callable[[str], None] = print,
) -> int:
    """Ask for the token and the language, check the token with Discord, write .env.

    Returns the exit code. The token is never shown, neither while typing nor after.
    """
    try:
        return _setup(env_file, ask, ask_secret, check, say)
    except (EOFError, KeyboardInterrupt):
        say("\nSetup cancelled; .env was not changed.")
        return 1


def _setup(
    env_file: Path,
    ask: Callable[[str], str],
    ask_secret: Callable[[str], str],
    check: Callable[[str], tuple[str, int]],
    say: Callable[[str], None],
) -> int:
    current = dotenv_values(env_file) if env_file.exists() else {}
    if (current.get("DISCORD_TOKEN") or "").strip() not in ("", TOKEN_PLACEHOLDER):
        if ask("A token is already set. Replace it? [y/N] ").strip().lower() not in YES:
            say(".env was not changed.")
            return 0

    say(f"Copy the token from {PORTAL} → your application → Bot → Reset Token.")
    say("Nothing is shown while you paste it.")
    for _ in range(ATTEMPTS):
        token = ask_secret("Bot token: ").strip()
        if not token:
            say("Nothing was pasted.")
            continue
        try:
            name, user_id = check(token)
        except TokenRejected:
            say(
                "Discord does not know that token. Copy it again, or reset it and copy the new one."
            )
            continue
        except (OSError, discord.HTTPException) as exc:
            say(f"Could not reach Discord to check the token ({exc}); saving it anyway.")
            name, user_id = "", 0
        break
    else:
        say(".env was not changed.")
        return 1

    default = (current.get("DEFAULT_LANGUAGE") or "").strip().lower()
    default = default if default in LANGUAGES else DEFAULT_LANGUAGE
    language = ""
    while language not in LANGUAGES:
        prompt = f"Language a server starts with ({'/'.join(LANGUAGES)}) [{default}]: "
        language = ask(prompt).strip().lower() or default

    write_env(env_file, token, language)
    say(f"Saved in {env_file}; only you can read it.")
    if user_id:
        invite = discord.utils.oauth_url(
            user_id, permissions=INVITE_PERMISSIONS, scopes=("bot", "applications.commands")
        )
        say(f"It is the token of {name}. Add the bot to a server with this link")
        say("(the profile's 'Add App' button only adds its commands, not the bot):")
        say(f"  {invite}")
    say("Then run ./run.sh --check, and ./run.sh to start the bot")
    say("(or systemctl --user restart albion-announcer, if it runs as a service).")
    return 0
