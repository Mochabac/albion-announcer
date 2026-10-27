from __future__ import annotations

import string
from datetime import UTC, datetime

import pytest

from announcer.i18n import LANGUAGES, STRINGS, day_label, permission_name, t
from announcer.service import REQUIRED_PERMISSIONS

# The longest name in the tz database, to size the hints that quote the time zone.
LONGEST_ZONE = "America/Argentina/ComodRivadavia"


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_language_has_every_string() -> None:
    assert set(STRINGS) == set(LANGUAGES)
    english = STRINGS["en"]
    for lang, table in STRINGS.items():
        assert set(table) == set(english), lang


@pytest.mark.parametrize("key", sorted(STRINGS["en"]))
def test_translations_take_the_same_placeholders(key: str) -> None:
    expected = placeholders(STRINGS["en"][key])
    for lang, table in STRINGS.items():
        assert placeholders(table[key]) == expected, (lang, key)


def test_unknown_languages_fall_back_to_english() -> None:
    assert t("fr", "button.leave") == t("en", "button.leave")
    assert permission_name("fr", "embed_links") == "Embed Links"


@pytest.mark.parametrize("lang", LANGUAGES)
def test_form_texts_fit_discords_limits(lang: str) -> None:
    for name in ("when", "where", "gear", "notes", "title", "body"):
        assert len(t(lang, f"modal.{name}")) <= 45
        assert len(t(lang, f"modal.{name}.placeholder")) <= 100
    for name in ("where", "gear", "notes", "title", "body"):
        assert len(t(lang, f"modal.{name}.hint")) <= 100
    for key in ("modal.when.hint", "modal.when.hint.custom"):
        assert len(t(lang, key, tz=LONGEST_ZONE)) <= 100


@pytest.mark.parametrize("lang", LANGUAGES)
def test_button_labels_fit(lang: str) -> None:
    keys = ["button.leave", "button.manage", "panel.continue"]
    keys += [f"manage.{action}" for action in ("remind", "start", "edit", "finish", "cancel")]
    for key in keys:
        assert len(t(lang, key)) <= 80


@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_required_permission_has_a_name(lang: str) -> None:
    for flag in (*REQUIRED_PERMISSIONS, "send_messages_in_threads", "mention_everyone"):
        assert permission_name(lang, flag) != flag


def test_day_labels() -> None:
    saturday = datetime(2026, 10, 10, 19, 0, tzinfo=UTC)
    assert day_label("en", saturday) == "Sat 10 Oct"
    assert day_label("es", saturday) == "sáb 10 oct"
