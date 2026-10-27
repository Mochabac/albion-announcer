from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from announcer.catalog import (
    CUSTOM_KEY,
    LABEL_LIMIT,
    MAX_ACTIVITIES,
    MAX_LIMIT,
    MAX_ROLES,
    ROLE_LABEL_LIMIT,
    ROLE_LABELS_TOTAL,
    Catalog,
    CatalogError,
    load_catalog,
    parse_catalog,
)
from announcer.config import ROOT

MINIMAL: dict[str, Any] = {
    "roles": {"tank": {"emoji": "🛡️", "en": "Tank", "es": "Tanque"}},
    "custom": {"emoji": "📢", "color": "#5865F2", "style": "secondary", "en": "Custom"},
    "activity": [
        {
            "key": "zvz",
            "emoji": "⚔️",
            "color": "#C0392B",
            "style": "danger",
            "icon": "T8_MOUNT_MAMMOTH_BATTLE@1",
            "roles": ["tank"],
            "en": {"label": "ZvZ", "message": "Mass up."},
        }
    ],
}


def broken(**changes: Any) -> dict[str, Any]:
    """MINIMAL with its first activity altered (a value of ... removes the key)."""
    data = copy.deepcopy(MINIMAL)
    for key, value in changes.items():
        if value is ...:
            del data["activity"][0][key]
        else:
            data["activity"][0][key] = value
    return data


def test_the_guilds_own_file_is_valid() -> None:
    # Whatever the officers put in activities.toml, the bot must be able to start with it.
    catalog = load_catalog(ROOT / "activities.toml")
    keys = [activity.key for activity in catalog.buttons]
    assert keys and len(keys) == len(set(keys)) <= MAX_ACTIVITIES + 1
    for activity in catalog.buttons:
        for lang in ("en", "es"):
            assert 0 < len(activity.label(lang)) <= LABEL_LIMIT
        assert all(role in catalog.roles for role in activity.roles)
        assert activity.icon is None or activity.icon.startswith("https://")


def test_the_tests_catalog_is_valid(catalog: Catalog) -> None:
    keys = [activity.key for activity in catalog.activities]
    assert len(keys) == len(set(keys)) == 13
    assert catalog.custom is not None and catalog.buttons[-1].key == CUSTOM_KEY
    assert len(catalog.buttons) <= 25
    for activity in catalog.buttons:
        for lang in ("en", "es"):
            assert 0 < len(activity.label(lang)) <= LABEL_LIMIT
        assert all(role in catalog.roles for role in activity.roles)
    for activity in catalog.activities:
        assert activity.message("en") and activity.message("es")
        assert activity.icon and activity.icon.startswith("https://render.albiononline.com/")


def test_item_ids_become_render_urls() -> None:
    activity = parse_catalog(MINIMAL).activities[0]
    assert activity.icon == "https://render.albiononline.com/v1/item/T8_MOUNT_MAMMOTH_BATTLE@1.png"
    assert activity.color == 0xC0392B


def test_a_full_url_icon_is_kept() -> None:
    url = "https://example.com/banner.png"
    assert parse_catalog(broken(icon=url)).activities[0].icon == url


def test_a_missing_translation_falls_back_to_english() -> None:
    catalog = parse_catalog(MINIMAL)
    activity = catalog.activities[0]
    assert activity.label("es") == "ZvZ"
    assert activity.message("es") == "Mass up."
    assert catalog.roles["tank"].label("es") == "Tanque"
    assert catalog.roles["tank"].label("fr") == "Tank"


@pytest.mark.parametrize(
    "emoji", ["⚔️", "🛡️", "1️⃣", "🏴‍☠️", "<:guild:123456789012345678>", "<a:spin:123456789012345678>"]
)
def test_real_emoji_and_server_emoji_are_accepted(emoji: str) -> None:
    assert parse_catalog(broken(emoji=emoji)).activities[0].emoji == emoji


def test_the_message_is_optional() -> None:
    # A comp sheet says it all in its title and its roles.
    bare = parse_catalog(broken(en={"label": "Limpieza!"})).activities[0]
    assert bare.message("en") == bare.message("es") == ""
    spanish_only = parse_catalog(
        broken(en={"label": "Skip"}, es={"label": "Skip", "message": "Trae a quien quieras."})
    ).activities[0]
    assert spanish_only.message("es") == "Trae a quien quieras."
    assert spanish_only.message("en") == ""


def test_a_role_can_be_a_position_for_a_set_number_of_players() -> None:
    data = copy.deepcopy(MINIMAL)
    assert parse_catalog(data).roles["tank"].limit is None  # anyone can join
    data["roles"]["tank"]["limit"] = 1
    catalog = parse_catalog(data)
    assert catalog.roles["tank"].limit == 1
    assert catalog.limit("tank") == 1 and catalog.limit("nobody") is None


def test_an_activity_takes_at_most_twenty_roles() -> None:
    data = copy.deepcopy(MINIMAL)
    data["roles"] = {f"r{n}": {"emoji": "⚔️", "en": f"R{n}"} for n in range(MAX_ROLES + 1)}
    data["activity"][0]["roles"] = [f"r{n}" for n in range(MAX_ROLES)]
    assert len(parse_catalog(data).activities[0].roles) == MAX_ROLES == 20
    data["activity"][0]["roles"].append(f"r{MAX_ROLES}")
    with pytest.raises(CatalogError, match="21 roles is too many; the limit is 20"):
        parse_catalog(data)


def test_a_role_name_can_be_longer_than_an_activity_name() -> None:
    # It is only ever a button's name, never a form's title.
    data = copy.deepcopy(MINIMAL)
    data["roles"]["tank"]["en"] = "x" * ROLE_LABEL_LIMIT
    assert parse_catalog(data).roles["tank"].label("en") == "x" * ROLE_LABEL_LIMIT
    assert ROLE_LABEL_LIMIT > LABEL_LIMIT


def test_an_activitys_role_names_share_one_budget() -> None:
    # Twenty long names would not fit in one announcement; seven of 80 easily do.
    data = copy.deepcopy(MINIMAL)
    data["roles"] = {
        f"r{n}": {"emoji": "⚔️", "en": "x" * (ROLE_LABELS_TOTAL // MAX_ROLES)}
        for n in range(MAX_ROLES)
    }
    data["activity"][0]["roles"] = list(data["roles"])
    assert len(parse_catalog(data).activities[0].roles) == MAX_ROLES
    data["roles"]["r0"]["es"] = "x" * (ROLE_LABELS_TOTAL // MAX_ROLES + 1)
    with pytest.raises(CatalogError, match=f"add up to {ROLE_LABELS_TOTAL + 1} characters"):
        parse_catalog(data)


@pytest.mark.parametrize("limit", [0, -1, MAX_LIMIT + 1, "1", 1.5, True])
def test_a_limit_must_be_a_sensible_number(limit: Any) -> None:
    data = copy.deepcopy(MINIMAL)
    data["roles"]["tank"]["limit"] = limit
    with pytest.raises(CatalogError, match=r"\[roles\.tank\]: `limit` must be a whole number"):
        parse_catalog(data)


def test_the_custom_button_is_optional() -> None:
    data = copy.deepcopy(MINIMAL)
    del data["custom"]
    catalog = parse_catalog(data)
    assert catalog.custom is None
    assert catalog.get(CUSTOM_KEY) is None
    assert [activity.key for activity in catalog.buttons] == ["zvz"]


def test_get_finds_activities_and_the_custom_button() -> None:
    catalog = parse_catalog(MINIMAL)
    assert catalog.get("zvz") is catalog.activities[0]
    custom = catalog.get(CUSTOM_KEY)
    assert custom is not None and custom.custom and custom.message("en") == ""
    assert catalog.get("nope") is None


@pytest.mark.parametrize(
    ("data", "complaint"),
    [
        (broken(color="red"), "color"),
        (broken(style="purple"), "style"),
        (broken(roles=["healer"]), "role `healer` is not defined"),
        (broken(roles=["tank", "tank"]), "twice"),
        (broken(roles="tank"), "must be a list"),
        (broken(key="ZvZ"), "key"),
        (broken(key=CUSTOM_KEY), "reserved"),
        (broken(emoji=...), "`emoji` is missing"),
        (broken(emoji=":crossed_swords:"), "not a :shortcode:"),
        (broken(emoji="x"), "must be one emoji"),
        (broken(emoji="⚔️ ZvZ"), "must be one emoji"),
        (broken(icon="https://not a url"), "not a usable URL"),
        (broken(icon="https://"), "not a usable URL"),
        (broken(en=...), "English"),
        (broken(en={"label": "ZvZ", "message": " "}), "`message` must be a non-empty text"),
        (broken(en={"label": "x" * (LABEL_LIMIT + 1), "message": "m"}), "limit"),
        (
            {**MINIMAL, "roles": {"tank": {"emoji": "🛡️", "en": "x" * (ROLE_LABEL_LIMIT + 1)}}},
            "limit",
        ),
        (broken(icon="not an item!"), "icon"),
        (broken(icon="http://insecure.example/x.png"), "icon"),
        (broken(colour="#FFFFFF"), "unknown setting `colour`"),
        ({**MINIMAL, "activities": []}, "unknown setting `activities`"),
        ({**MINIMAL, "activity": MINIMAL["activity"] * 2}, "share the key"),
        ({"roles": {}}, "nothing to announce"),
        ({**MINIMAL, "roles": {"Tank!": {"emoji": "🛡️", "en": "Tank"}}}, "role key"),
        (
            {
                **MINIMAL,
                "activity": [
                    {**MINIMAL["activity"][0], "key": f"a{n}"} for n in range(MAX_ACTIVITIES + 1)
                ],
            },
            "at most",
        ),
    ],
)
def test_mistakes_are_reported_in_plain_words(data: dict[str, Any], complaint: str) -> None:
    with pytest.raises(CatalogError, match=complaint):
        parse_catalog(data)


def test_a_missing_or_malformed_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="does not exist"):
        load_catalog(tmp_path / "nope.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("[[activity]\nkey = ", encoding="utf-8")
    with pytest.raises(CatalogError, match="not valid TOML"):
        load_catalog(bad)
