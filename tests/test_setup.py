"""./run.sh --setup: the token goes into .env without anyone editing the file, or seeing it."""

from __future__ import annotations

import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import discord
import pytest

from announcer import setup
from announcer.__main__ import main
from announcer.config import ROOT

# Deliberately nothing like a real token, which GitHub's secret scanning would report.
TOKEN = "not-a-real-token-only-for-these-tests"
BOT_ID = 123456789012345678


def replies(*answers: str) -> Callable[[str], str]:
    """Stands in for input() and getpass(): the next answer each time, then end of input."""
    queue = list(answers)

    def reply(prompt: str) -> str:
        if not queue:
            raise EOFError
        return queue.pop(0)

    return reply


def accepts(token: str) -> tuple[str, int]:
    """Discord, knowing only TOKEN."""
    if token != TOKEN:
        raise setup.TokenRejected
    return "ReminHelper", BOT_ID


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    (tmp_path / ".env.example").write_text(example, encoding="utf-8")
    return tmp_path / ".env"


def test_a_new_env_comes_from_the_example_and_only_its_owner_can_read_it(env_file: Path) -> None:
    said: list[str] = []
    code = setup.run(
        env_file, ask=replies("es"), ask_secret=replies(TOKEN), check=accepts, say=said.append
    )

    assert code == 0
    text = env_file.read_text(encoding="utf-8")
    assert f"\nDISCORD_TOKEN={TOKEN}\n" in text and "\nDEFAULT_LANGUAGE=es\n" in text
    assert "# Optional. Relative paths are resolved against this folder." in text
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert sorted(path.name for path in env_file.parent.iterdir()) == [".env", ".env.example"]
    # The token is never shown; the link that adds the bot itself is.
    assert not any(TOKEN in line for line in said)
    assert any(f"client_id={BOT_ID}&scope=bot+applications.commands" in line for line in said)


def test_replacing_a_token_keeps_every_other_setting(env_file: Path) -> None:
    env_file.write_text(
        "DISCORD_TOKEN=old.token.value\nDEFAULT_LANGUAGE=es\nLOG_LEVEL=DEBUG\n# a note\n",
        encoding="utf-8",
    )
    env_file.chmod(0o644)
    code = setup.run(
        env_file, ask=replies("sí", ""), ask_secret=replies(TOKEN), check=accepts, say=print
    )

    assert code == 0
    assert env_file.read_text(encoding="utf-8") == (
        f"DISCORD_TOKEN={TOKEN}\nDEFAULT_LANGUAGE=es\nLOG_LEVEL=DEBUG\n# a note\n"
    )
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_a_token_already_set_stays_unless_the_answer_is_yes(env_file: Path) -> None:
    env_file.write_text("DISCORD_TOKEN=old.token.value\n", encoding="utf-8")
    said: list[str] = []
    assert (
        setup.run(env_file, ask=replies(""), ask_secret=replies(), check=accepts, say=said.append)
        == 0
    )
    assert env_file.read_text(encoding="utf-8") == "DISCORD_TOKEN=old.token.value\n"
    assert said == [".env was not changed."]


def test_a_token_discord_does_not_know_is_asked_for_again(env_file: Path) -> None:
    said: list[str] = []
    code = setup.run(
        env_file,
        ask=replies("fr", "en"),
        ask_secret=replies("wrong", "", TOKEN),
        check=accepts,
        say=said.append,
    )

    assert code == 0
    assert f"\nDISCORD_TOKEN={TOKEN}\n" in env_file.read_text(encoding="utf-8")
    assert "\nDEFAULT_LANGUAGE=en\n" in env_file.read_text(encoding="utf-8")
    assert any("Discord does not know that token" in line for line in said)
    assert "Nothing was pasted." in said


def test_three_unusable_tokens_change_nothing(env_file: Path) -> None:
    code = setup.run(
        env_file, ask=replies(), ask_secret=replies("a", "b", ""), check=accepts, say=print
    )
    assert code == 1 and not env_file.exists()


def test_cancelling_changes_nothing(env_file: Path) -> None:
    def interrupted(prompt: str) -> str:
        raise KeyboardInterrupt

    assert setup.run(env_file, ask=replies(), ask_secret=interrupted, check=accepts, say=print) == 1
    assert not env_file.exists()


def test_without_discord_the_token_is_saved_anyway(env_file: Path) -> None:
    def offline(token: str) -> tuple[str, int]:
        raise OSError("Network is unreachable")

    said: list[str] = []
    code = setup.run(
        env_file, ask=replies(""), ask_secret=replies(TOKEN), check=offline, say=said.append
    )

    assert code == 0 and f"\nDISCORD_TOKEN={TOKEN}\n" in env_file.read_text(encoding="utf-8")
    assert any("saving it anyway" in line for line in said)
    assert not any("client_id" in line for line in said)  # no bot id, so no invite link


@pytest.mark.parametrize("known", [True, False])
def test_the_check_asks_discord_who_the_token_belongs_to(
    monkeypatch: pytest.MonkeyPatch, known: bool
) -> None:
    async def static_login(self: Any, token: str) -> dict[str, str]:
        if not known:
            raise discord.LoginFailure("Improper token has been passed.")
        return {"username": "ReminHelper", "id": str(BOT_ID)}

    monkeypatch.setattr(discord.http.HTTPClient, "static_login", static_login)
    if known:
        assert setup.check_token(TOKEN) == ("ReminHelper", BOT_ID)
    else:
        with pytest.raises(setup.TokenRejected):
            setup.check_token("wrong")


def test_the_command_line_runs_it(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path] = []

    def run(env_file: Path) -> int:
        calls.append(env_file)
        return 0

    monkeypatch.setattr(setup, "run", run)
    assert main(["--setup"]) == 0
    assert calls == [ROOT / ".env"]
    with pytest.raises(SystemExit):
        main(["--setup", "--check"])
