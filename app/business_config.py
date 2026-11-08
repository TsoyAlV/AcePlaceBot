"""Validated business configuration, separate from API secrets in .env."""

from datetime import date, time
from pathlib import Path
from typing import Literal
import re

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
    holiday_dates: list[date] = Field(default_factory=list)
    # Accepted for old configs; automatic KR holidays no longer need a year allowlist.
    verified_holiday_years: list[int] = Field(default_factory=list)


class CleaningInterval(Model):
    """Allowed start-time range [from,to), possibly crossing midnight."""
    start: time = Field(alias='from')
    end: time = Field(alias='to')

    @model_validator(mode='after')
    def valid_interval(self):
        if self.start == self.end:
            raise ValueError('Cleaning window cannot have equal start and end')
        return self


class Cleaning(Model):
    policy: Literal['next_start_time','next_hour_slot'] | None
    duration_minutes: int = Field(default=60,ge=1,le=1440,strict=True)
    weekly: dict[Day,list[CleaningInterval | time] | None]

    @model_validator(mode='before')
    @classmethod
    def valid_start_values(cls,data):
        if isinstance(data,dict):
            weekly=data.get('weekly',{})
            for items in weekly.values() if isinstance(weekly,dict) else []:
                for item in items if isinstance(items,(list,tuple)) else []:
                    if isinstance(item,(dict,CleaningInterval,time)):
                        continue
                    if not isinstance(item,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',item):
                        raise ValueError('Use quoted HH:MM cleaning start times or from/to start ranges')
        return data

    @model_validator(mode='after')
    def valid_schedule(self):
        if set(self.weekly) != {'MON','TUE','WED','THU','FRI','SAT','SUN'}:
            raise ValueError('Specify all seven weekdays')
        # Detect ambiguous/duplicated declarations of allowed starts, not overlaps of possible cleanings.
        intervals=[]
        for week in range(2):
            for day,key in enumerate(('MON','TUE','WED','THU','FRI','SAT','SUN')):
                for item in self.weekly[key] or []:
                    if isinstance(item,time):
                        if item.tzinfo is not None or item.second or item.microsecond:
                            raise ValueError('Cleaning start times must be local HH:MM without seconds')
                        start=(week*7+day)*1440+item.hour*60+item.minute
                        end=start+1
                    else:
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


class ReviewText(Model):
    message: str = Field(min_length=1,max_length=3500)
    link_text: str = Field(min_length=1,max_length=100)

    @model_validator(mode='after')
    def valid_template(self):
        if self.message.count('{review_link}')!=1:
            raise ValueError('Review message must contain {review_link} exactly once')
        if len(self.message.replace('{review_link}',self.link_text).encode('utf-16-le'))//2>4096:
            raise ValueError('Review message exceeds Telegram length limit')
        return self


class ReviewsConfig(Model):
    enabled: bool = False
    url: str = Field(default='https://t.me/AcePlaceParty/112',pattern=r'^https://\S+$',max_length=2048)
    catch_up_hours: int = Field(default=24,ge=1,le=168,strict=True)
    ru: ReviewText = Field(default_factory=lambda:ReviewText(
        message='Спасибо, что выбрали AcePlace! 🎉\nНадеемся, вы отлично провели время.\n\nПоделитесь впечатлениями — ваш отзыв поможет нам стать лучше и другим гостям выбрать место для своего мероприятия.\n\n{review_link}\n\nБудем рады видеть вас снова! 💛',
        link_text='⭐ Оставить отзыв'))
    ko: ReviewText = Field(default_factory=lambda:ReviewText(
        message='AcePlace를 이용해 주셔서 감사합니다! 🎉\n즐거운 시간 보내셨기를 바랍니다.\n\n이용 후기를 남겨 주세요. 여러분의 후기는 더 나은 서비스를 만드는 데 도움이 되고, 다른 손님들이 행사 장소를 선택하는 데에도 큰 도움이 됩니다.\n\n{review_link}\n\n다시 뵙기를 기다리겠습니다! 💛',
        link_text='⭐ 후기 남기기'))


class BooksPlanInterval(Model):
    start: time = Field(alias='from')
    end: time = Field(alias='to')

    @model_validator(mode='after')
    def valid_interval(self):
        if self.start == self.end:
            raise ValueError('Planned block must have different start and end times')
        for value in (self.start,self.end):
            if value.tzinfo is not None or value.second or value.microsecond:
                raise ValueError('Planned block times must be local HH:MM')
        return self


class BooksPlansConfig(Model):
    enabled: bool = False
    timezone: Literal['Asia/Seoul'] = 'Asia/Seoul'
    generate_days_ahead: int = Field(default=90,ge=1,le=366,strict=True)
    weekly: dict[Day,list[BooksPlanInterval]] = Field(default_factory=lambda:{
        day:[] for day in ('MON','TUE','WED','THU','FRI','SAT','SUN')})
    excluded_dates: list[date] = Field(default_factory=list)

    @model_validator(mode='after')
    def all_weekdays(self):
        if set(self.weekly) != {'MON','TUE','WED','THU','FRI','SAT','SUN'}:
            raise ValueError('Specify all seven weekdays for books_plans')
        days=('MON','TUE','WED','THU','FRI','SAT','SUN')
        if sum(map(len,self.weekly.values()))>64:
            raise ValueError('Use at most 64 weekly planned ranges')
        spans=[]
        for week in range(2):
            for day,key in enumerate(days):
                for item in self.weekly[key]:
                    a=(week*7+day)*1440+item.start.hour*60+item.start.minute
                    b=(week*7+day)*1440+item.end.hour*60+item.end.minute
                    if b<=a:
                        b+=1440
                    spans.append((a,b))
        spans.sort()
        if any(a[1]>b[0] for a,b in zip(spans,spans[1:])):
            raise ValueError('Weekly books_plans ranges must not overlap')
        return self


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
    reviews: ReviewsConfig = Field(default_factory=ReviewsConfig)
    books_plans: BooksPlansConfig = Field(default_factory=BooksPlansConfig)


def load_business_config(path: Path | None = None) -> BusinessConfig:
    with (path or PROJECT_ROOT / 'config.yaml').open(encoding='utf-8') as stream:
        return BusinessConfig.model_validate(yaml.safe_load(stream))
