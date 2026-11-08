"""Offline Korean public holidays plus dates set by the venue in config.yaml."""

from datetime import date
from functools import lru_cache

import holidays


@lru_cache(maxsize=64)
def korean_holidays(year: int) -> frozenset[date]:
    calendar=holidays.country_holidays('KR',expand=False,observed=True)
    # holidays otherwise returns an empty calendar for unsupported years.
    if not calendar.start_year<=year<=calendar.end_year:
        raise ValueError(f'Korean holiday calendar supports {calendar.start_year}–{calendar.end_year}')
    return frozenset(holidays.country_holidays('KR',years=[year],expand=False,observed=True))


def is_holiday(day: date,manual_dates) -> bool:
    # Manual entries are evaluated each time, so config edits do not require a restart.
    return day in manual_dates or day in korean_holidays(day.year)
