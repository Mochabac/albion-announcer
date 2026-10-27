"""Process-level settings, read once from the environment (which `.env` feeds)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .i18n import DEFAULT_LANGUAGE, LANGUAGES

ROOT = Path(__file__).resolve().parent.parent
TOKEN_PLACEHOLDER = "paste-your-bot-token-here"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class ConfigError(Exception):
    """The environment is missing something the bot cannot start without."""


@dataclass(frozen=True)
class Settings:
    token: str
    activities_file: Path
    database_file: Path
    default_language: str
    log_level: str


def _path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def load_settings(env: Mapping[str, str] | None = None, *, require_token: bool = True) -> Settings:
    env = os.environ if env is None else env

    token = env.get("DISCORD_TOKEN", "").strip()
    if token == TOKEN_PLACEHOLDER:
        token = ""
    if require_token and not token:
        raise ConfigError(
            "DISCORD_TOKEN is not set. Run ./run.sh --setup, or copy .env.example to .env and "
            "paste the bot token from https://discord.com/developers/applications "
            "(your application → Bot)."
        )

    language = env.get("DEFAULT_LANGUAGE", "").strip().lower() or DEFAULT_LANGUAGE
    if language not in LANGUAGES:
        raise ConfigError(
            f"DEFAULT_LANGUAGE is {language!r}; it must be one of: {', '.join(LANGUAGES)}."
        )

    log_level = env.get("LOG_LEVEL", "").strip().upper() or "INFO"
    if log_level not in LOG_LEVELS:
        raise ConfigError(
            f"LOG_LEVEL is {log_level!r}; it must be one of: {', '.join(LOG_LEVELS)}."
        )

    return Settings(
        token=token,
        activities_file=_path(env.get("ACTIVITIES_FILE", "").strip() or "activities.toml"),
        database_file=_path(env.get("DATABASE_FILE", "").strip() or "data/announcer.db"),
        default_language=language,
        log_level=log_level,
    )
