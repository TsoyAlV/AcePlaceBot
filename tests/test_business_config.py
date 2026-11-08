import unittest

from pydantic import ValidationError

from app.business_config import BusinessConfig, load_business_config


class BusinessConfigTests(unittest.TestCase):
    def test_supplied_rules(self):
        config=load_business_config()
        self.assertEqual(config.booking.minimum_hours,5)
        self.assertEqual(config.booking.confirmation_timeout_minutes,1440)
        self.assertEqual(config.pricing.weekday_base_amount,150000)
        self.assertEqual(config.pricing.weekend_holiday_extra_hour_amount,20000)
        self.assertEqual(config.payment.deposit_amount,50000)
        self.assertIsInstance(config.payment.account_number,str)

    def test_cleaning_overlap_is_rejected(self):
        data=load_business_config().model_dump(by_alias=True)
        data['cleaning']['weekly']['MON']=[{'from':'09:00','to':'11:00'},{'from':'10:00','to':'12:00'}]
        with self.assertRaises(ValidationError):
            BusinessConfig.model_validate(data)

    def test_missing_schedule_is_not_a_day_without_cleaning(self):
        config=load_business_config()
        config.cleaning.weekly['TUE']=None
        self.assertIsNone(config.cleaning.weekly['TUE'])
        self.assertTrue(config.cleaning.weekly['MON'])

    def test_cleaning_duration_and_start_values_are_validated(self):
        for duration in (0,-1,1441,True,1.5):
            data=load_business_config().model_dump(by_alias=True)
            data['cleaning']['duration_minutes']=duration
            with self.subTest(duration=duration),self.assertRaises(ValidationError):
                BusinessConfig.model_validate(data)
        for values in (['09:00','09:00'],[540],['24:00'],['09:00:30']):
            data=load_business_config().model_dump(by_alias=True)
            data['cleaning']['weekly']['MON']=values
            with self.subTest(values=values),self.assertRaises(ValidationError):
                BusinessConfig.model_validate(data)
