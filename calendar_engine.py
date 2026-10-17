"""Personal calendar engine: everything here is computed, never written by hand.

- Personal year, month and day numbers (Pythagorean reduction, master numbers kept).
- Moon phases (Meeus, "Astronomical Algorithms", ch. 49; accurate to a few minutes),
  placed on the local calendar day of a given time zone.
- A few fixed-date holidays.
- The month grid (Monday first, as in Liliana's calendar) with each day's numbers.
"""
from __future__ import annotations

import calendar as _calendar
import datetime as dt
import math
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from numerology import reduce

DEFAULT_TZ = "America/Edmonton"


# --------------------------------------------------------------------------
# Personal numbers
# --------------------------------------------------------------------------
def personal_year(birth: dt.date, year: int) -> int:
    return reduce(reduce(birth.month) + reduce(birth.day) + reduce(year))


def personal_month(birth: dt.date, year: int, month: int) -> int:
    return reduce(personal_year(birth, year) + reduce(month))


def personal_day(birth: dt.date, day: dt.date) -> int:
    return reduce(personal_month(birth, day.year, day.month) + reduce(day.day))


def universal_day(day: dt.date) -> int:
    return reduce(sum(int(c) for c in day.strftime("%Y%m%d")))


# --------------------------------------------------------------------------
# Moon phases
# --------------------------------------------------------------------------
NEW, FIRST_QUARTER, FULL, LAST_QUARTER = "new", "first_quarter", "full", "last_quarter"
_PHASE_OFFSET = {NEW: 0.0, FIRST_QUARTER: 0.25, FULL: 0.5, LAST_QUARTER: 0.75}
_J2000 = dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc)


def _phase_jde(k: float, phase: str) -> float:
    rad = math.radians
    t = k / 1236.85
    jde = (2451550.09766 + 29.530588861 * k + 0.00015437 * t ** 2 - 0.000000150 * t ** 3
           + 0.00000000073 * t ** 4)
    e = 1 - 0.002516 * t - 0.0000074 * t ** 2
    m = rad(2.5534 + 29.10535670 * k - 0.0000014 * t ** 2 - 0.00000011 * t ** 3)
    mp = rad(201.5643 + 385.81693528 * k + 0.0107582 * t ** 2 + 0.00001238 * t ** 3 - 0.000000058 * t ** 4)
    f = rad(160.7108 + 390.67050284 * k - 0.0016118 * t ** 2 - 0.00000227 * t ** 3 + 0.000000011 * t ** 4)
    om = rad(124.7746 - 1.56375588 * k + 0.0020672 * t ** 2 + 0.00000215 * t ** 3)
    sin, cos = math.sin, math.cos
    if phase in (NEW, FULL):
        c = (-0.40720, 0.17241, 0.01608, 0.01039, 0.00739, -0.00514, 0.00208) if phase == NEW else \
            (-0.40614, 0.17302, 0.01614, 0.01043, 0.00734, -0.00515, 0.00209)
        jde += (c[0] * sin(mp) + c[1] * e * sin(m) + c[2] * sin(2 * mp) + c[3] * sin(2 * f)
                + c[4] * e * sin(mp - m) + c[5] * e * sin(mp + m) + c[6] * e * e * sin(2 * m)
                - 0.00111 * sin(mp - 2 * f) - 0.00057 * sin(mp + 2 * f) + 0.00056 * e * sin(2 * mp + m)
                - 0.00042 * sin(3 * mp) + 0.00042 * e * sin(m + 2 * f) + 0.00038 * e * sin(m - 2 * f)
                - 0.00024 * e * sin(2 * mp - m) - 0.00017 * sin(om))
    else:
        jde += (-0.62801 * sin(mp) + 0.17172 * e * sin(m) - 0.01183 * e * sin(mp + m) + 0.00862 * sin(2 * mp)
                + 0.00804 * sin(2 * f) + 0.00454 * e * sin(mp - m) + 0.00204 * e * e * sin(2 * m)
                - 0.00180 * sin(mp - 2 * f) - 0.00070 * sin(mp + 2 * f) - 0.00040 * sin(3 * mp)
                - 0.00034 * e * sin(2 * mp - m) + 0.00032 * e * sin(m + 2 * f) + 0.00032 * e * sin(m - 2 * f)
                - 0.00028 * e * e * sin(mp + 2 * m) + 0.00027 * e * sin(2 * mp + m) - 0.00017 * sin(om))
        w = (0.00306 - 0.00038 * e * cos(m) + 0.00026 * cos(mp) - 0.00002 * cos(mp - m)
             + 0.00002 * cos(mp + m) + 0.00002 * cos(2 * f))
        jde += w if phase == FIRST_QUARTER else -w
    return jde


def _jde_to_utc(jde: float) -> dt.datetime:
    return _J2000 + dt.timedelta(days=jde - 2451545.0) - dt.timedelta(seconds=69)  # TT -> UTC (ΔT ≈ 69 s)


def moon_phases(start: dt.date, end: dt.date, tz: str = DEFAULT_TZ) -> dict[dt.date, str]:
    """Principal moon phases falling on each local calendar day between start and end (inclusive)."""
    zone = ZoneInfo(tz)
    k0 = math.floor((start.year + (start.timetuple().tm_yday - 1) / 365.25 - 2000) * 12.3685) - 1
    found: dict[dt.date, str] = {}
    for k in range(k0, k0 + 16):
        for phase, offset in _PHASE_OFFSET.items():
            moment = _jde_to_utc(_phase_jde(k + offset, phase)).astimezone(zone)
            if start <= moment.date() <= end:
                found[moment.date()] = phase
    return found


# --------------------------------------------------------------------------
# Holidays (fixed dates; practitioners can add their own notes to any day)
# --------------------------------------------------------------------------
HOLIDAYS = {
    (1, 1): {"en": "New Year's Day", "es": "Año Nuevo"},
    (2, 14): {"en": "Valentine's Day", "es": "San Valentín"},
    (12, 24): {"en": "Christmas Eve", "es": "Nochebuena"},
    (12, 25): {"en": "Christmas", "es": "Navidad"},
    (12, 31): {"en": "New Year's Eve", "es": "Fin de año"},
}


def _merge_holiday_names():
    import calendar_languages
    for key, names in calendar_languages.HOLIDAYS.items():
        HOLIDAYS.setdefault(key, {}).update(names)


_merge_holiday_names()


# --------------------------------------------------------------------------
# Month grid
# --------------------------------------------------------------------------
@dataclass
class Day:
    date: dt.date
    personal_day: int
    universal_day: int
    moon: str | None = None
    holiday: str | None = None


@dataclass
class Month:
    year: int
    month: int
    personal_year: int
    personal_month: int
    days: list[Day] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    def weeks(self) -> list[list[Day | None]]:
        """Weeks Monday to Sunday, padded with None."""
        lead = self.days[0].date.weekday()
        cells: list[Day | None] = [None] * lead + list(self.days)
        cells += [None] * (-len(cells) % 7)
        return [cells[i:i + 7] for i in range(0, len(cells), 7)]


def build_month(birth: dt.date, year: int, month: int, lang: str = "en", tz: str = DEFAULT_TZ) -> Month:
    last = _calendar.monthrange(year, month)[1]
    start, end = dt.date(year, month, 1), dt.date(year, month, last)
    phases = moon_phases(start, end, tz)
    result = Month(year, month, personal_year(birth, year), personal_month(birth, year, month))
    for number in range(1, last + 1):
        day = dt.date(year, month, number)
        holiday = HOLIDAYS.get((month, number))
        result.days.append(Day(day, personal_day(birth, day), universal_day(day), phases.get(day),
                               holiday.get(lang, holiday["en"]) if holiday else None))
    return result


def local_today(tz: str = DEFAULT_TZ, now: dt.datetime | None = None) -> dt.date:
    """Today's date where the calendar is used (not UTC), so month boundaries are right."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(ZoneInfo(tz)).date()


def parse_month(value: str | None, today: dt.date | None = None) -> tuple[int, int]:
    """'YYYY-MM' -> (year, month); anything invalid falls back to the current month."""
    today = today or dt.date.today()
    try:
        year, month = (int(part) for part in (value or "").split("-"))
        if 1900 <= year <= 2200 and 1 <= month <= 12:
            return year, month
    except ValueError:
        pass
    return today.year, today.month


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1
