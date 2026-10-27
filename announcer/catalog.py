"""Loads and validates ``activities.toml``: what the guild can announce."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .i18n import DEFAULT_LANGUAGE, LANGUAGES

CUSTOM_KEY = "custom"
STYLES = ("primary", "secondary", "success", "danger")
# With the custom button that is 25: the most buttons one Discord message can hold.
MAX_ACTIVITIES = 24
# Per activity: two rows of buttons, with "Leave" and "Manage" on a third.
MAX_ROLES = 8
# The most players a sign-up role can be limited to.
MAX_LIMIT = 50
# The form's title is "<emoji> <label>" and Discord caps it at 45 characters.
LABEL_LIMIT = 40
MESSAGE_LIMIT = 1000
RENDER_URL = "https://render.albiononline.com/v1/item/{item}.png"

_KEY = re.compile(r"[a-z0-9_]{1,32}")
_COLOR = re.compile(r"#?([0-9a-fA-F]{6})")
_ITEM = re.compile(r"[A-Za-z0-9_]+(?:@[0-9])?")
_CUSTOM_EMOJI = re.compile(r"<a?:[A-Za-z0-9_]{2,32}:[0-9]{15,21}>")


class CatalogError(Exception):
    """activities.toml is missing, or says something the bot cannot use."""


def _pick(texts: Mapping[str, str], lang: str) -> str:
    return texts.get(lang) or texts[DEFAULT_LANGUAGE]


@dataclass(frozen=True)
class Role:
    key: str
    emoji: str
    labels: Mapping[str, str]
    # How many players it takes: a position in a comp. None for as many as sign up.
    limit: int | None = None

    def label(self, lang: str) -> str:
        return _pick(self.labels, lang)


@dataclass(frozen=True)
class Activity:
    key: str
    emoji: str
    color: int
    style: str
    icon: str | None
    roles: tuple[str, ...]
    labels: Mapping[str, str]
    messages: Mapping[str, str] = field(default_factory=dict)
    # The free-form announcement: the caller types the title and the message.
    custom: bool = False

    def label(self, lang: str) -> str:
        return _pick(self.labels, lang)

    def message(self, lang: str) -> str:
        return self.messages.get(lang) or self.messages.get(DEFAULT_LANGUAGE, "")


@dataclass(frozen=True)
class Catalog:
    roles: Mapping[str, Role]
    activities: tuple[Activity, ...]
    custom: Activity | None = None

    @property
    def buttons(self) -> tuple[Activity, ...]:
        """Everything that gets a panel button, in panel order."""
        return (*self.activities, self.custom) if self.custom else self.activities

    def get(self, key: str) -> Activity | None:
        return next((activity for activity in self.buttons if activity.key == key), None)

    def limit(self, role: str) -> int | None:
        """How many players the sign-up role ``role`` takes, if it has a limit."""
        found = self.roles.get(role)
        return found.limit if found else None


def load_catalog(path: Path) -> Catalog:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        raise CatalogError(f"{path} does not exist.") from None
    except tomllib.TOMLDecodeError as exc:
        raise CatalogError(f"{path.name} is not valid TOML: {exc}") from None
    return parse_catalog(data)


def parse_catalog(data: Mapping[str, Any]) -> Catalog:
    _known(data, {"roles", "custom", "activity"}, "the file")

    raw_roles = _table(data.get("roles", {}), "[roles]")
    roles = {key: _role(key, value) for key, value in raw_roles.items()}

    raw_activities = data.get("activity", [])
    if not isinstance(raw_activities, list):
        raise CatalogError("`activity` must be written as [[activity]] blocks.")
    if len(raw_activities) > MAX_ACTIVITIES:
        raise CatalogError(
            f"There are {len(raw_activities)} activities; a panel holds at most {MAX_ACTIVITIES}."
        )
    activities: list[Activity] = []
    for index, raw in enumerate(raw_activities, start=1):
        activity = _activity(raw, index, roles)
        if any(other.key == activity.key for other in activities):
            raise CatalogError(f"Two activities share the key `{activity.key}`.")
        activities.append(activity)

    custom = _custom(data["custom"], roles) if "custom" in data else None
    if not activities and custom is None:
        raise CatalogError("There is nothing to announce: add at least one [[activity]].")
    return Catalog(roles=roles, activities=tuple(activities), custom=custom)


def _role(key: str, raw: Any) -> Role:
    where = f"[roles.{key}]"
    if not _KEY.fullmatch(key):
        raise CatalogError(f"{where}: a role key may only use a-z, 0-9 and _ (32 at most).")
    table = _table(raw, where)
    _known(table, {"emoji", "limit", *LANGUAGES}, where)
    return Role(
        key=key,
        emoji=_emoji(table, where),
        labels=_languages(table, where, limit=LABEL_LIMIT),
        limit=_limit(table, where),
    )


def _limit(table: Mapping[str, Any], where: str) -> int | None:
    if "limit" not in table:
        return None
    value = table["limit"]
    # `limit = true` is a mistake, not 1: Python counts a bool as an int.
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LIMIT:
        raise CatalogError(f"{where}: `limit` must be a whole number from 1 to {MAX_LIMIT}.")
    return value


def _activity(raw: Any, index: int, roles: Mapping[str, Role]) -> Activity:
    table = _table(raw, f"activity #{index}")
    key = table.get("key")
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise CatalogError(
            f"activity #{index}: `key` must be a short id using only a-z, 0-9 and _ (32 at most)."
        )
    if key == CUSTOM_KEY:
        raise CatalogError(
            f"activity #{index}: `{CUSTOM_KEY}` is reserved for the [custom] button."
        )
    where = f"activity `{key}`"
    _known(table, {"key", "emoji", "color", "style", "icon", "roles", *LANGUAGES}, where)

    labels: dict[str, str] = {}
    messages: dict[str, str] = {}
    for lang in LANGUAGES:
        if lang not in table:
            continue
        texts = _table(table[lang], f"{where} [{lang}]")
        _known(texts, {"label", "message"}, f"{where} [{lang}]")
        labels[lang] = _text(texts, "label", f"{where} [{lang}]", limit=LABEL_LIMIT)
        if "message" in texts:
            messages[lang] = _text(texts, "message", f"{where} [{lang}]", limit=MESSAGE_LIMIT)
    if DEFAULT_LANGUAGE not in labels:
        raise CatalogError(f"{where}: the English texts ([activity.en]) are required.")

    return Activity(
        key=key,
        emoji=_emoji(table, where),
        color=_color(table, where),
        style=_style(table, where),
        icon=_icon(table, where),
        roles=_roles(table, where, roles),
        labels=labels,
        messages=messages,
    )


def _custom(raw: Any, roles: Mapping[str, Role]) -> Activity:
    where = "[custom]"
    table = _table(raw, where)
    _known(table, {"emoji", "color", "style", "icon", "roles", *LANGUAGES}, where)
    return Activity(
        key=CUSTOM_KEY,
        emoji=_emoji(table, where),
        color=_color(table, where),
        style=_style(table, where),
        icon=_icon(table, where),
        roles=_roles(table, where, roles),
        labels=_languages(table, where, limit=LABEL_LIMIT),
        custom=True,
    )


def _table(raw: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise CatalogError(f"{where} must be a table.")
    return raw


def _known(table: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise CatalogError(
            f"{where}: unknown setting `{unknown[0]}` (known: {', '.join(sorted(allowed))})."
        )


def _text(table: Mapping[str, Any], name: str, where: str, *, limit: int) -> str:
    value = table.get(name)
    if value is None:
        raise CatalogError(f"{where}: `{name}` is missing.")
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{where}: `{name}` must be a non-empty text.")
    value = value.strip()
    if len(value) > limit:
        raise CatalogError(
            f"{where}: `{name}` is {len(value)} characters long; the limit is {limit}."
        )
    return value


def _languages(table: Mapping[str, Any], where: str, *, limit: int) -> dict[str, str]:
    texts = {lang: _text(table, lang, where, limit=limit) for lang in LANGUAGES if lang in table}
    if DEFAULT_LANGUAGE not in texts:
        raise CatalogError(f"{where}: the English text (`{DEFAULT_LANGUAGE}`) is required.")
    return texts


def _emoji(table: Mapping[str, Any], where: str) -> str:
    """An emoji Discord will take on a button: a real one, or a server emoji as <:name:id>.

    Not a full check (that takes the Unicode tables), but it catches what people
    actually type by mistake: a :shortcode:, a word, a stray letter.
    """
    emoji = _text(table, "emoji", where, limit=64)
    if _CUSTOM_EMOJI.fullmatch(emoji):
        return emoji
    if emoji.isascii() or len(emoji) > 16 or any(ch.isspace() for ch in emoji):
        raise CatalogError(
            f"{where}: `emoji` must be one emoji such as ⚔️ (not a :shortcode:), "
            "or a server emoji written as <:name:id>."
        )
    return emoji


def _color(table: Mapping[str, Any], where: str) -> int:
    match = _COLOR.fullmatch(_text(table, "color", where, limit=7))
    if not match:
        raise CatalogError(f"{where}: `color` must look like #C0392B.")
    return int(match[1], 16)


def _style(table: Mapping[str, Any], where: str) -> str:
    style = _text(table, "style", where, limit=16)
    if style not in STYLES:
        raise CatalogError(f"{where}: `style` must be one of: {', '.join(STYLES)}.")
    return style


def _icon(table: Mapping[str, Any], where: str) -> str | None:
    if "icon" not in table:
        return None
    icon = _text(table, "icon", where, limit=300)
    if icon.startswith("https://"):
        if not urlsplit(icon).netloc or any(ch.isspace() for ch in icon):
            raise CatalogError(f"{where}: `icon` is not a usable URL.")
        return icon
    if _ITEM.fullmatch(icon):
        return RENDER_URL.format(item=icon)
    raise CatalogError(
        f"{where}: `icon` must be an Albion item id (like T8_2H_TOOL_PICK) or an https:// URL."
    )


def _roles(table: Mapping[str, Any], where: str, roles: Mapping[str, Role]) -> tuple[str, ...]:
    value = table.get("roles", [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CatalogError(f'{where}: `roles` must be a list such as ["tank", "healer"].')
    if len(value) > MAX_ROLES:
        raise CatalogError(f"{where}: {len(value)} roles is too many; the limit is {MAX_ROLES}.")
    if len(set(value)) != len(value):
        raise CatalogError(f"{where}: `roles` lists the same role twice.")
    for key in value:
        if key not in roles:
            raise CatalogError(f"{where}: role `{key}` is not defined under [roles].")
    return tuple(value)
