from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from announcer.timeparse import parse_when

from .conftest import NOW  # Sunday 2026-10-04, 18:00 UTC

BOGOTA = ZoneInfo("America/Bogota")  # UTC-5, no daylight saving


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize(
    "text", ["", "   ", "now", "NOW", "Now!", "asap", "ahora", "Ya", "ya mismo"]
)
def test_nothing_or_now_means_now(text: str) -> None:
    assert parse_when(text, now=NOW).kind == "now"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # clock times, read as today when still ahead
        ("20:30", at(4, 20, 30)),
        ("20.30", at(4, 20, 30)),
        ("2030", at(4, 20, 30)),
        ("8pm", at(4, 20)),
        ("8 PM", at(4, 20)),
        ("8:30pm", at(4, 20, 30)),
        ("8:30 p.m.", at(4, 20, 30)),
        ("20:30 hs", at(4, 20, 30)),
        ("20:30h", at(4, 20, 30)),
        ("19 utc", at(4, 19)),
        ("19utc", at(4, 19)),
        ("20:30 UTC", at(4, 20, 30)),
        ("at 20", at(4, 20)),
        ("a las 20", at(4, 20)),
        ("a las 20:30", at(4, 20, 30)),
        ("@ 21:15", at(4, 21, 15)),
        # already gone today, so tomorrow
        ("17:00", at(5, 17)),
        ("12pm", at(5, 12)),
        ("12am", at(5, 0)),
        ("0030", at(5, 0, 30)),
        ("8 utc", at(5, 8)),
        # a few minutes late still means today
        ("17:58", at(4, 17, 58)),
        # durations
        ("in 45m", at(4, 18, 45)),
        ("45m", at(4, 18, 45)),
        ("45 min", at(4, 18, 45)),
        ("en 45 min", at(4, 18, 45)),
        ("in 30", at(4, 18, 30)),
        ("+30", at(4, 18, 30)),
        ("in 2h", at(4, 20)),
        ("in 1h30", at(4, 19, 30)),
        ("1h30m", at(4, 19, 30)),
        ("1h 30m", at(4, 19, 30)),
        ("1.5h", at(4, 19, 30)),
        ("1,5h", at(4, 19, 30)),
        ("2 hours", at(4, 20)),
        ("en 1 hora", at(4, 19)),
        ("dentro de 2 horas", at(4, 20)),
        ("1 hora y 30 minutos", at(4, 19, 30)),
        ("in 26h", at(5, 20)),
        # days
        ("tomorrow 20:30", at(5, 20, 30)),
        ("20:30 tomorrow", at(5, 20, 30)),
        ("tomorrow at 9:00", at(5, 9)),
        ("mañana 20:30", at(5, 20, 30)),
        ("Mañana a las 20:30", at(5, 20, 30)),
        ("manana 20", at(5, 20)),
        ("today 17:00", at(4, 17)),
        ("hoy 21:00", at(4, 21)),
        # "tonight" runs past midnight
        ("tonight 22:00", at(4, 22)),
        ("esta noche 23:00", at(4, 23)),
        ("tonight 00:30", at(5, 0, 30)),
        ("tonight 1am", at(5, 1)),
        ("tonight 12am", at(5, 0)),
        ("esta noche 1am", at(5, 1)),
        ("tonight 17:58", at(4, 17, 58)),
        ("saturday 19 utc", at(10, 19)),
        ("sábado 20:00", at(10, 20)),
        ("el viernes 21:00", at(9, 21)),
        ("Friday, 21:00", at(9, 21)),
        ("sunday 20:00", at(4, 20)),
        ("sunday 10:00", at(11, 10)),
    ],
)
def test_reads_unambiguous_times(text: str, expected: datetime) -> None:
    when = parse_when(text, now=NOW)
    assert (when.kind, when.at) == ("at", expected)


@pytest.mark.parametrize(
    "text",
    [
        # a clock time to some, a duration to others
        "20h",
        "20h30",
        "2h",
        "1h30",
        "20 horas",
        "2 hrs",
        # a.m. or p.m.?
        "at 8",
        "a las 8",
        "tomorrow 8",
        # minutes, or an hour?
        "8",
        "20",
        "30",
        # not times at all
        "soon",
        "after reset",
        "tomorrow",
        "when the castle timer pops",
        # impossible
        "25:00",
        "20:75",
        "13pm",
        "0pm",
        "2575",
        "in 1h75",
    ],
)
def test_keeps_ambiguous_text_as_typed(text: str) -> None:
    when = parse_when(text, now=NOW)
    assert (when.kind, when.at) == ("text", None)


def test_a_zero_duration_is_now() -> None:
    assert parse_when("in 0m", now=NOW).kind == "now"


def test_durations_round_to_the_minute() -> None:
    late = NOW + timedelta(seconds=40)
    assert parse_when("in 30m", now=late).at == at(4, 18, 31)
    early = NOW + timedelta(seconds=20)
    assert parse_when("in 30m", now=early).at == at(4, 18, 30)


def test_clock_times_are_read_in_the_guild_zone() -> None:
    # 18:00 UTC is 13:00 in Bogotá.
    assert parse_when("20:30", now=NOW, tz=BOGOTA).at == at(5, 1, 30)
    assert parse_when("8pm", now=NOW, tz=BOGOTA).at == at(5, 1)
    assert parse_when("12:00", now=NOW, tz=BOGOTA).at == at(5, 17)  # noon already passed there


def test_an_explicit_utc_beats_the_guild_zone() -> None:
    assert parse_when("20:30 utc", now=NOW, tz=BOGOTA).at == at(4, 20, 30)
    assert parse_when("mañana 19 UTC", now=NOW, tz=BOGOTA).at == at(5, 19)


def test_day_words_follow_the_guild_calendar() -> None:
    # 03:00 UTC on Monday is still Sunday evening in Bogotá.
    night = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)
    assert parse_when("tomorrow 20:00", now=night, tz=BOGOTA).at == at(6, 1)
    assert parse_when("tomorrow 20:00", now=night).at == at(6, 20)


def test_a_day_word_with_utc_is_only_read_when_both_calendars_agree() -> None:
    # Sunday 22:00 in Bogotá is already Monday 03:00 UTC. "Tomorrow, 19 UTC": the
    # caller's tomorrow is Monday, UTC's is Tuesday. That is a day apart, so the bot
    # shows the words instead of picking one.
    night = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)
    assert parse_when("mañana 19 utc", now=night, tz=BOGOTA).kind == "text"
    assert parse_when("tomorrow 19 utc", now=night, tz=BOGOTA).kind == "text"
    # A guild that lives on UTC has one calendar...
    assert parse_when("mañana 19 utc", now=night).at == at(6, 19)
    # ...a weekday is the same day on both...
    assert parse_when("monday 19 utc", now=night, tz=BOGOTA).at == at(5, 19)
    assert parse_when("sunday 23 utc", now=night, tz=BOGOTA).at == at(11, 23)
    # ...and at an hour when both calendars show the same date, there is nothing to pick.
    assert parse_when("mañana 19 utc", now=NOW, tz=BOGOTA).at == at(5, 19)
    # Without "utc" the day word simply follows the guild's own calendar.
    assert parse_when("mañana 19:00", now=night, tz=BOGOTA).at == at(6, 0)


def test_durations_ignore_the_zone() -> None:
    assert parse_when("in 45m", now=NOW, tz=BOGOTA).at == at(4, 18, 45)


def test_the_result_is_always_utc() -> None:
    when = parse_when("20:30", now=NOW, tz=BOGOTA)
    assert when.at is not None and when.at.utcoffset() == timedelta(0)
