"""Pricing by local start date and earliest permitted cleaning start."""

from datetime import datetime, timedelta, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.business_config import BusinessConfig
from app.holiday_calendar import is_holiday

SEOUL=ZoneInfo('Asia/Seoul')
DAYS=('MON','TUE','WED','THU','FRI','SAT','SUN')


class RulesError(ValueError):
    pass


def next_cleaning(end: datetime, config: BusinessConfig):
    if end.utcoffset() is None:
        raise RulesError('Timezone required')
    if config.cleaning.policy not in ('next_start_time','next_hour_slot'):
        raise RulesError('Cleaning policy is not configured')
    end=end.astimezone(SEOUL)
    # Include previous-day windows which can extend into the end date.
    candidates=[]
    for offset in range(-1,9):
        day=end.date()+timedelta(days=offset)
        windows=config.cleaning.weekly[DAYS[day.weekday()]]
        if windows is None:
            # An unknown day cannot be silently treated as having no cleaning.
            if offset>=0:
                if candidates:
                    break
                raise RulesError(f'Cleaning schedule is missing for {DAYS[day.weekday()]}')
            raise RulesError(f'Previous-day cleaning schedule is missing for {DAYS[day.weekday()]}')
        for window in windows:
            if isinstance(window,time):
                start=datetime.combine(day,window,SEOUL)
                if start>=end:
                    candidates.append((start,start+timedelta(minutes=config.cleaning.duration_minutes)))
                continue
            start=datetime.combine(day,window.start,SEOUL)
            finish=datetime.combine(day,window.end,SEOUL)
            if finish<=start:
                finish+=timedelta(days=1)
            slot=max(start,end)
            rounded=slot.replace(minute=0,second=0,microsecond=0)
            if slot!=rounded:
                rounded+=timedelta(hours=1)
            slot_end=rounded+timedelta(minutes=config.cleaning.duration_minutes)
            if rounded<finish:
                candidates.append((rounded,slot_end))
        if offset>=0 and candidates:
            break
    if not candidates:
        raise RulesError('No allowed cleaning start time is configured')
    return min(candidates)


def rental_price(start: datetime,end: datetime,config: BusinessConfig) -> Decimal:
    if start.utcoffset() is None or end.utcoffset() is None:
        raise RulesError('Timezone required')
    start,end=start.astimezone(SEOUL),end.astimezone(SEOUL)
    hours=(end-start).total_seconds()/3600
    if hours<config.booking.minimum_hours or not hours.is_integer():
        raise RulesError('Rental must be at least five hours and contain whole hours')
    if config.pricing.date_policy!='start_date':
        raise RulesError('Pricing date policy is not configured')
    day=start.date()
    try:
        special=DAYS[day.weekday()] in config.pricing.weekend_days or is_holiday(day,config.pricing.holiday_dates)
    except Exception as error:
        raise RulesError(f'Holiday calendar unavailable for {day.year} ({type(error).__name__})') from None
    pricing=config.pricing
    base=pricing.weekend_holiday_base_amount if special else pricing.weekday_base_amount
    extra=pricing.weekend_holiday_extra_hour_amount if special else pricing.weekday_extra_hour_amount
    return Decimal(base+max(0,int(hours)-pricing.base_hours)*extra)
