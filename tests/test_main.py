"""The command line: configuration errors, --check, and shutting down cleanly."""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from announcer.__main__ import main
from announcer.config import ROOT, TOKEN_PLACEHOLDER, ConfigError, load_settings

from .conftest import ACTIVITIES

# Runs the real entry point, with the connection to Discord replaced by "wait forever".
STAND_IN = """
import asyncio, sys
from announcer import __main__ as entry
from announcer.bot import AnnouncerBot

async def start(self, token, *, reconnect=True):
    print("started", flush=True)
    await asyncio.Event().wait()

real_close = AnnouncerBot.close

async def close(self):
    print("signed off", flush=True)
    await real_close(self)

AnnouncerBot.start = start
AnnouncerBot.close = close
sys.exit(entry.main([]))
"""


def test_settings_defaults() -> None:
    settings = load_settings({"DISCORD_TOKEN": "  abc  "})
    assert settings.token == "abc"
    assert settings.activities_file == ROOT / "activities.toml"
    assert settings.database_file == ROOT / "data" / "announcer.db"
    assert (settings.default_language, settings.log_level) == ("en", "INFO")


def test_settings_overrides(tmp_path: Path) -> None:
    settings = load_settings(
        {
            "DISCORD_TOKEN": "abc",
            "ACTIVITIES_FILE": "guild/activities.toml",
            "DATABASE_FILE": str(tmp_path / "a.db"),
            "DEFAULT_LANGUAGE": "ES",
            "LOG_LEVEL": "debug",
        }
    )
    assert settings.activities_file == ROOT / "guild" / "activities.toml"  # relative to the project
    assert settings.database_file == tmp_path / "a.db"
    assert (settings.default_language, settings.log_level) == ("es", "DEBUG")


@pytest.mark.parametrize("token", ["", "   ", TOKEN_PLACEHOLDER])
def test_a_missing_token_is_explained(token: str) -> None:
    with pytest.raises(ConfigError, match=r"DISCORD_TOKEN is not set\. Copy \.env\.example"):
        load_settings({"DISCORD_TOKEN": token})
    # --check runs without one.
    assert load_settings({"DISCORD_TOKEN": token}, require_token=False).token == ""


@pytest.mark.parametrize(
    ("env", "complaint"),
    [
        ({"DEFAULT_LANGUAGE": "fr"}, "DEFAULT_LANGUAGE is 'fr'"),
        ({"LOG_LEVEL": "loud"}, "LOG_LEVEL is 'LOUD'"),
    ],
)
def test_bad_settings_are_explained(env: dict[str, str], complaint: str) -> None:
    with pytest.raises(ConfigError, match=complaint):
        load_settings({"DISCORD_TOKEN": "abc", **env})


def test_check_reports_the_panel(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "abc")
    monkeypatch.setenv("ACTIVITIES_FILE", str(ACTIVITIES))
    assert main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "activities.toml: 14 buttons — ZvZ, Castle / Outposts" in out
    assert "sign-up roles: tank, healer, dps, support, scout, going" in out
    assert "token: set" in out


def test_check_reports_a_broken_activities_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    broken = tmp_path / "activities.toml"
    broken.write_text(
        '[[activity]]\nkey = "zvz"\nemoji = ":swords:"\n'
        '[activity.en]\nlabel = "ZvZ"\nmessage = "Mass up."\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ACTIVITIES_FILE", str(broken))
    assert main(["--check"]) == 2
    assert "error: activity `zvz`: `emoji` must be one emoji" in capsys.readouterr().err


def test_starting_without_a_token_exits_with_an_explanation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", TOKEN_PLACEHOLDER)
    assert main([]) == 2
    assert "error: DISCORD_TOKEN is not set" in capsys.readouterr().err


@pytest.mark.parametrize("stop", [signal.SIGTERM, signal.SIGINT])
def test_a_stop_signal_shuts_down_cleanly(tmp_path: Path, stop: signal.Signals) -> None:
    """systemd stops a service with SIGTERM. The bot must sign off and close its
    database, not be cut off wherever it happens to be."""
    database = tmp_path / "announcer.db"
    env = {**os.environ, "DISCORD_TOKEN": "abc", "DATABASE_FILE": str(database)}
    process = subprocess.Popen(
        [sys.executable, "-c", STAND_IN],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "started"
        process.send_signal(stop)
        out, err = process.communicate(timeout=20)
    finally:
        process.kill()

    assert process.returncode == 0, err
    assert "signed off" in out
    assert "Traceback" not in err
    # The noise about voice audio that discord.py prints at every start is filtered out.
    assert "voice will NOT be supported" not in err
    # The database was closed properly: nothing left to recover, and it opens cleanly.
    assert not database.with_name(database.name + "-wal").exists()
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT count(*) FROM events").fetchone() == (0,)
