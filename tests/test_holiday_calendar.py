import unittest
from datetime import date,datetime,timedelta,timezone
from unittest.mock import patch

from app.business_config import BusinessConfig,load_business_config
from app.holiday_calendar import is_holiday,korean_holidays
from app.rental_rules import rental_price,RulesError,SEOUL


class HolidayCalendarTests(unittest.TestCase):
    def setUp(self):
        self.rules=load_business_config().model_copy(deep=True)
        self.rules.pricing.holiday_dates=[]
        self.rules.pricing.verified_holiday_years=[]

    def price(self,start):
        return rental_price(start,start+timedelta(hours=5),self.rules)

    def test_automatic_2027_lunar_observed_and_public_holidays_without_manual_dates(self):
        for day in (date(2027,1,1),date(2027,2,8),date(2027,2,9),date(2027,5,13),
                    date(2027,7,19),date(2027,9,15),date(2027,10,4)):
            with self.subTest(day=day):
                self.assertEqual(self.price(datetime.combine(day,datetime.min.time(),SEOUL)),200000)
        self.assertEqual(self.price(datetime(2027,11,4,18,tzinfo=SEOUL)),150000)

    def test_manual_dates_extend_automatic_calendar_and_edits_take_effect_without_cache_reset(self):
        day=date(2027,11,4)
        start=datetime(2027,11,4,18,tzinfo=SEOUL)
        self.assertEqual(self.price(start),150000)
        self.rules.pricing.holiday_dates=[day,day]
        self.assertEqual(self.price(start),200000)
        self.assertTrue(is_holiday(date(2027,2,9),self.rules.pricing.holiday_dates))
        self.rules.pricing.holiday_dates=[]
        self.assertEqual(self.price(start),150000)

    def test_holiday_uses_seoul_start_date_even_if_utc_date_is_previous_day(self):
        start=datetime(2026,12,31,16,tzinfo=timezone.utc)
        self.assertEqual(start.astimezone(SEOUL).date(),date(2027,1,1))
        self.assertEqual(self.price(start),200000)

    def test_unsupported_calendar_year_is_not_silently_treated_as_a_workday(self):
        with self.assertRaises(ValueError):
            korean_holidays(2101)
        start=datetime(2101,1,3,18,tzinfo=SEOUL)
        while start.weekday()>=5:
            start+=timedelta(days=1)
        with self.assertRaisesRegex(RulesError,'Holiday calendar unavailable'):
            self.price(start)

    def test_library_failure_does_not_silently_select_cheaper_weekday_rate(self):
        with patch('app.holiday_calendar.korean_holidays',side_effect=RuntimeError('test')):
            with self.assertRaisesRegex(RulesError,'Holiday calendar unavailable'):
                self.price(datetime(2027,11,4,18,tzinfo=SEOUL))

    def test_manual_dates_and_legacy_year_allowlist_are_optional(self):
        data=self.rules.model_dump(by_alias=True)
        data['pricing'].pop('holiday_dates')
        data['pricing'].pop('verified_holiday_years')
        config=BusinessConfig.model_validate(data)
        start=datetime(2027,1,1,18,tzinfo=SEOUL)
        self.assertEqual(rental_price(start,start+timedelta(hours=5),config),200000)
