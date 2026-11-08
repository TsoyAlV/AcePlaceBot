"""Validated business configuration, separate from API secrets in .env."""

from datetime import date, time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import PROJECT_ROOT

Day = Literal['MON','TUE','WED','THU','FRI','SAT','SUN']


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class BookingRules(Model):
    minimum_hours: int = Field(gt=0, strict=True)
    start_step_minutes: int = Field(gt=0, le=60, strict=True)
    confirmation_timeout_minutes: int = Field(gt=0, le=1440, strict=True)
    hold_timeout_minutes: int = Field(gt=0, le=1440, strict=True)


class Pricing(Model):
    base_hours: int = Field(gt=0, strict=True)
    weekday_base_amount: int = Field(ge=0, strict=True)
    weekday_extra_hour_amount: int = Field(ge=0, strict=True)
    weekend_holiday_base_amount: int = Field(ge=0, strict=True)
    weekend_holiday_extra_hour_amount: int = Field(ge=0, strict=True)
    weekend_days: list[Day]
    date_policy: Literal['start_date','per_day'] | None
    holiday_dates: list[date]
    verified_holiday_years: list[int]


class CleaningInterval(Model):
    start: time = Field(alias='from')
    end: time = Field(alias='to')

    @model_validator(mode='after')
    def valid_interval(self):
        if self.start == self.end:
            raise ValueError('Cleaning window cannot have equal start and end')
        return self


class Cleaning(Model):
    policy: Literal['next_hour_slot'] | None
    duration_minutes: Literal[60] = 60
    weekly: dict[Day,list[CleaningInterval] | None]

    @model_validator(mode='after')
    def valid_schedule(self):
        if set(self.weekly) != {'MON','TUE','WED','THU','FRI','SAT','SUN'}:
            raise ValueError('Specify all seven weekdays')
        # Expand two weeks to detect overlaps at midnight and Sunday/Monday.
        intervals=[]
        for week in range(2):
            for day,key in enumerate(('MON','TUE','WED','THU','FRI','SAT','SUN')):
                for item in self.weekly[key] or []:
                    start=(week*7+day)*1440+item.start.hour*60+item.start.minute
                    end=(week*7+day)*1440+item.end.hour*60+item.end.minute
                    if end<=start:
                        end+=1440
                    intervals.append((start,end))
        intervals.sort()
        if any(a[1]>b[0] for a,b in zip(intervals,intervals[1:])):
            raise ValueError('Cleaning intervals overlap')
        return self


class Payment(Model):
    deposit_amount: int = Field(ge=0, strict=True)
    bank: str
    account_number: str = Field(repr=False)
    account_holder: str = Field(repr=False)


class CalendarConfig(Model):
    json_dir: str = Field(default='secrets/calendars',min_length=1)
    enabled: bool = False
    booking_color_id: str = Field(default='9',pattern=r'^(?:[1-9]|10|11)$')
    cleaning_color_id: str = Field(default='6',pattern=r'^(?:[1-9]|10|11)$')
    block_color_id: str = Field(default='8',pattern=r'^(?:[1-9]|10|11)$')

    @model_validator(mode='after')
    def different_colors(self):
        if self.booking_color_id==self.cleaning_color_id:
            raise ValueError('Rental and cleaning must use different colors')
        return self


class CleaningNotification(Model):
    hours_before: int = Field(default=2,ge=1,le=168,strict=True)
    telegram_enabled: bool = True


class NotificationsConfig(Model):
    cleaning: CleaningNotification = Field(default_factory=CleaningNotification)


class VisitText(Model):
    address: str = ''
    route_url: str = ''
    entry_instructions: str = ''


class VisitConfig(Model):
    ru: VisitText = Field(default_factory=VisitText)
    ko: VisitText = Field(default_factory=VisitText)


class CancellationRules(Model):
    refund_cutoff_hours: int = Field(default=72,ge=1,le=8760,strict=True)


class BusinessConfig(Model):
    timezone: Literal['Asia/Seoul']
    currency: Literal['KRW']
    booking: BookingRules
    pricing: Pricing
    cleaning: Cleaning
    payment: Payment
    calendar: CalendarConfig = Field(default_factory=CalendarConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    visit: VisitConfig = Field(default_factory=VisitConfig)
    cancellation: CancellationRules = Field(default_factory=CancellationRules)


def load_business_config(path: Path | None = None) -> BusinessConfig:
    with (path or PROJECT_ROOT / 'config.yaml').open(encoding='utf-8') as stream:
        return BusinessConfig.model_validate(yaml.safe_load(stream))
