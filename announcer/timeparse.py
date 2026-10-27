"""Turn what a caller types in the "When" box into a point in time.

One rule: convert only what is unambiguous. ``20:30``, ``8pm``, ``19 utc`` and
``in 45m`` each mean one thing. ``20h30`` is a clock time to a Brazilian and a
duration to an Englishman, so it is kept exactly as typed: an announcement that
shows the caller's own words is fine, one that shows the wrong hour is not.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import Literal

# "20:30" typed at 20:33 still means tonight, not tomorrow.
GRACE = timedelta(minutes=5)


@dataclass(frozen=True)
class When:
    """``now``, a moment (``at``), or ``text`` — something only a human can read."""

    kind: Literal["now", "at", "text"]
    at: datetime | None = None  # aware and in UTC; set only for "at"


_NOW = frozenset({"", "now", "right now", "asap", "ahora", "ahora mismo", "ya", "ya mismo"})

_MINUTE_UNIT = r"(?:minutes?|minutos?|mins?|m)"
_IN = re.compile(r"(?:in|en|dentro de)\s+|\+\s*")
_MINUTES = re.compile(rf"(\d{{1,3}})\s*{_MINUTE_UNIT}", re.ASCII)
_HOURS = re.compile(
    r"(?P<h>\d{1,2}(?:[.,]\d)?)\s*(?P<hu>hours?|hrs?|horas?|h)"
    rf"(?:\s*(?:y|and)?\s*(?P<m>\d{{1,2}})\s*(?P<mu>{_MINUTE_UNIT})?)?",
    re.ASCII,
)
_BARE_MINUTES = re.compile(r"\d{1,3}", re.ASCII)

_AT = re.compile(r"(?:at|a las|a la|@)\s*")
_ZONE = re.compile(r"\s*(?:utc|gmt)$")
_CLOCK = re.compile(
    r"(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\s*(?P<ampm>[ap]\.?m\.?)?(?:\s*(?:hrs?|hs?))?",
    re.ASCII,
)
_MILITARY = re.compile(r"(?P<h>\d{2})(?P<m>\d{2})", re.ASCII)

_WEEKDAYS = (
    ("monday", "mon", "lunes", "lun"),
    ("tuesday", "tue", "tues", "martes"),
    ("wednesday", "wed", "miercoles", "mie"),
    ("thursday", "thu", "thur", "thurs", "jueves", "jue"),
    ("friday", "fri", "viernes", "vie"),
    ("saturday", "sat", "sabado", "sab"),
    ("sunday", "sun", "domingo", "dom"),
)
# word -> ("offset", days from today), ("tonight", 0) or ("weekday", 0=Monday)
_DAYS: dict[str, tuple[str, int]] = {
    "today": ("offset", 0),
    "hoy": ("offset", 0),
    "tonight": ("tonight", 0),
    "esta noche": ("tonight", 0),
    "tomorrow": ("offset", 1),
    "tmrw": ("offset", 1),
    "manana": ("offset", 1),
    **{name: ("weekday", index) for index, names in enumerate(_WEEKDAYS) for name in names},
}


def _normalise(raw: str) -> str:
    """Lower-case, accent-free, single-spaced: ``Mañana, 20:30!`` -> ``manana 20:30``."""
    text = unicodedata.normalize("NFKD", raw.casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r",(?=\s|$)", " ", text)
    return " ".join(text.split()).strip(" .!¡")


def _relative(s: str) -> timedelta | None:
    prefix = _IN.match(s)
    body = s[prefix.end() :].strip() if prefix else s

    if m := _MINUTES.fullmatch(body):
        return timedelta(minutes=int(m[1]))

    if m := _HOURS.fullmatch(body):
        hours, minutes = m["h"], int(m["m"] or 0)
        # "2h" and "20h30" are how half the world writes a clock time. They only count
        # as a duration when something else says so: "in", a minutes unit, a decimal,
        # or the English word.
        is_duration = (
            prefix or m["mu"] or "." in hours or "," in hours or m["hu"].startswith("hour")
        )
        if not is_duration or minutes > 59:
            return None
        return timedelta(hours=float(hours.replace(",", ".")), minutes=minutes)

    if prefix and _BARE_MINUTES.fullmatch(body):
        return timedelta(minutes=int(body))
    return None


def _take_day(s: str) -> tuple[tuple[str, int] | None, str]:
    """Split a leading or trailing day word ("tomorrow", "sábado") off the time."""
    for filler in ("el ", "on ", "this ", "este "):
        if s.startswith(filler):
            s = s[len(filler) :]
    for word, day in _DAYS.items():
        if s == word:
            return day, ""
        if s.startswith(word + " "):
            return day, s[len(word) :].strip()
        if s.endswith(" " + word):
            return day, s[: -len(word)].strip()
    return None, s


def _absolute(s: str, now: datetime, tz: tzinfo) -> datetime | None:
    day, s = _take_day(s)
    at = _AT.match(s)
    if at:
        s = s[at.end() :]
    zone = _ZONE.search(s)
    if zone:
        s = s[: zone.start()]
    s = s.strip()

    ampm = None
    if m := _MILITARY.fullmatch(s):
        hour, minute = int(m["h"]), int(m["m"])
    elif m := _CLOCK.fullmatch(s):
        hour, minute, ampm = int(m["h"]), int(m["m"] or 0), m["ampm"]
        if m["m"] is None and ampm is None:
            # A lone number is only a clock time when something marks it as one: a zone
            # ("19 utc"), or "at"/a day in front of an hour that cannot be a.m.-or-p.m.
            # guesswork ("at 20" yes, "at 8" no).
            marked = zone or ((at or day) and (hour == 0 or hour > 12))
            if not marked:
                return None
    else:
        return None

    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm.startswith("p") else 0)
    if hour > 23 or minute > 59:
        return None

    if zone:
        # "mañana 19 utc", typed late in the evening: tomorrow on whose calendar, the
        # guild's or UTC's? When the two disagree it is not for the bot to pick.
        readings = {_moment(hour, minute, day, now, UTC, calendar) for calendar in (UTC, tz)}
        return readings.pop() if len(readings) == 1 else None
    return _moment(hour, minute, day, now, tz, tz).astimezone(UTC)


def _moment(
    hour: int,
    minute: int,
    day: tuple[str, int] | None,
    now: datetime,
    clock: tzinfo,
    calendar: tzinfo,
) -> datetime:
    """HH:MM on the clock of ``clock``, on the day that ``day`` names in ``calendar``."""
    if day is None:
        # The next time the clock shows HH:MM, or the one just gone if within the grace.
        local_now = now.astimezone(clock)
        moment = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return moment + timedelta(days=1) if moment < local_now - GRACE else moment

    today = now.astimezone(calendar).date()
    kind, value = day
    if kind == "weekday":
        ahead = (value - today.weekday()) % 7
        moment = datetime.combine(today + timedelta(days=ahead), time(hour, minute), clock)
        # "sunday 10:00", said on a Sunday afternoon, is next week's.
        return moment + timedelta(days=7) if ahead == 0 and moment < now - GRACE else moment

    moment = datetime.combine(today + timedelta(days=value), time(hour, minute), clock)
    if kind == "tonight" and moment < now - GRACE:
        # "tonight 00:30" is after midnight: the small hours of tomorrow.
        moment += timedelta(days=1)
    return moment


def parse_when(raw: str, *, now: datetime, tz: tzinfo = UTC) -> When:
    """Read a caller's "when".

    ``now`` must be timezone-aware. ``tz`` is the zone clock times are typed in
    (Albion's server time is UTC); a trailing "utc" in the text overrides it.
    """
    s = _normalise(raw)
    if s in _NOW:
        return When("now")

    delta = _relative(s)
    if delta is not None:
        if delta < timedelta(minutes=1):
            return When("now")
        moment = (now + delta + timedelta(seconds=30)).replace(second=0, microsecond=0)
        return When("at", moment.astimezone(UTC))

    at = _absolute(s, now, tz)
    return When("at", at) if at else When("text")
