import unittest
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo

from app.business_config import load_business_config,BusinessConfig
from app.rental_rules import next_cleaning,rental_price,RulesError

SEOUL=ZoneInfo('Asia/Seoul')


class RentalRulesTests(unittest.TestCase):
    def test_known_2026_calendar_prices(self):
        config=load_business_config()
        for month,day,expected in ((11,10,150000),(12,25,200000),(3,2,200000),
                                    (5,1,200000),(7,17,200000),(6,3,200000),(8,17,200000)):
            with self.subTest(month=month,day=day):
                start=datetime(2026,month,day,18,tzinfo=SEOUL)
                self.assertEqual(rental_price(start,start+timedelta(hours=5),config),expected)

    def config(self,windows=None):
        data=load_business_config().model_dump(by_alias=True)
        data['cleaning']['duration_minutes']=60
        data['cleaning']['weekly']={key:(windows or []) for key in data['cleaning']['weekly']}
        return BusinessConfig.model_validate(data)

    def test_evening_window_and_midnight(self):
        config=self.config([{'from':'20:00','to':'01:00'}])
        end=datetime(2030,11,8,22,tzinfo=SEOUL)
        start,finish=next_cleaning(end,config)
        self.assertEqual((start,finish),(end,end+timedelta(hours=1)))
        end=end.replace(day=9,hour=0)
        self.assertEqual(next_cleaning(end,config),(end,end+timedelta(hours=1)))

    def test_waiting_and_rounding(self):
        config=self.config([{'from':'09:00','to':'10:00'},{'from':'14:00','to':'15:00'}])
        end=datetime(2030,11,8,11,tzinfo=SEOUL)
        self.assertEqual(next_cleaning(end,config)[0].hour,14)
        self.assertEqual(next_cleaning(end.replace(hour=9,minute=30),config)[0].hour,14)

    def test_friday_to_saturday_uses_friday_for_all_hours(self):
        config=self.config()
        start=datetime(2030,11,8,22,tzinfo=SEOUL) # Friday
        self.assertEqual(rental_price(start,start+timedelta(hours=5),config),150000)
        self.assertEqual(rental_price(start,start+timedelta(hours=7),config),170000)
        start+=timedelta(days=1)
        self.assertEqual(rental_price(start,start+timedelta(hours=7),config),240000)

    def test_manual_holiday_and_automatic_calendar_without_verified_years(self):
        config=self.config()
        start=datetime(2030,11,8,22,tzinfo=SEOUL)
        config.pricing.holiday_dates=[start.date()]
        self.assertEqual(rental_price(start,start+timedelta(hours=5),config),200000)
        config.pricing.holiday_dates=[]
        config.pricing.verified_holiday_years=[]
        self.assertEqual(rental_price(start,start+timedelta(hours=5),config),150000)

    def test_missing_schedule_and_no_slot_fail_safely(self):
        config=self.config()
        end=datetime(2030,11,8,22,tzinfo=SEOUL)
        with self.assertRaises(RulesError):
            next_cleaning(end,config)
        config.cleaning.weekly['THU']=None
        with self.assertRaises(RulesError):
            next_cleaning(end,config)

    def test_exact_start_times_and_two_hour_cleaning_across_midnight(self):
        config=self.config(['09:00','14:00','23:00'])
        config.cleaning.policy='next_start_time'
        config.cleaning.duration_minutes=120
        end=datetime(2030,11,8,22,tzinfo=SEOUL)
        start,finish=next_cleaning(end,config)
        self.assertEqual(start,end.replace(hour=23))
        self.assertEqual(finish,datetime(2030,11,9,1,tzinfo=SEOUL))
        self.assertEqual(next_cleaning(end.replace(hour=9,minute=30),config)[0].hour,14)

    def test_start_range_does_not_limit_cleaning_finish(self):
        config=self.config([{'from':'09:00','to':'10:00'}])
        config.cleaning.duration_minutes=120
        end=datetime(2030,11,8,9,tzinfo=SEOUL)
        self.assertEqual(next_cleaning(end,config),(end,end+timedelta(hours=2)))
        self.assertEqual(next_cleaning(end+timedelta(hours=1),config)[0].date(),end.date()+timedelta(days=1))

    def test_non_hour_duration_and_exact_start_at_rental_end(self):
        config=self.config(['10:30','14:00'])
        config.cleaning.duration_minutes=90
        end=datetime(2030,11,8,10,30,tzinfo=SEOUL)
        self.assertEqual(next_cleaning(end,config),(end,end+timedelta(minutes=90)))
