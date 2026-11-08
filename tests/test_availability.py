import os
import unittest
from datetime import timedelta,datetime
from unittest.mock import patch

import test_bookings as fixtures
import app.bookings as bookings
from app.availability import get_availability_windows
from app.bookings import BookingError


@unittest.skipUnless(os.getenv('RUN_DB_TESTS')=='1','Set RUN_DB_TESTS=1')
class AvailabilityTests(unittest.TestCase):
    use_schema=fixtures.BookingTests.use_schema
    drop_schema=fixtures.BookingTests.drop_schema
    create=fixtures.BookingTests.create

    def setUp(self):
        fixtures.BookingTests.setUp(self)
        replacement=patch('app.availability.load_business_config',return_value=bookings.load_business_config())
        replacement.start()
        self.addCleanup(replacement.stop)

    def search(self,start=None,end=None,**kwargs):
        return get_availability_windows(self.c,102,start or self.start,end or self.start+timedelta(hours=12),**kwargs)

    def test_empty_five_hour_range_allows_cleaning_after_boundary(self):
        result=self.search(end=self.end)
        self.assertEqual(len(result['available_starts']),1)
        option=result['available_starts'][0]
        self.assertEqual(option['allowed_hours'],[5])
        self.assertEqual(datetime.fromisoformat(option['latest_cleaning_ends_at']),self.end+timedelta(hours=1))
        self.assertFalse(result['is_reservation'])
        self.assertEqual(self.c.execute('SELECT count(*) FROM bookings').fetchone()[0],0)

    def test_rental_and_cleaning_are_separate_and_next_start_is_cleaning_end(self):
        self.create()
        result=self.search()
        self.assertEqual([item['reason'] for item in result['blocked']],['rental','cleaning'])
        self.assertEqual(datetime.fromisoformat(result['available_starts'][0]['starts_at']),self.end+timedelta(hours=1))
        self.assertNotIn('user_id',str(result))
        self.assertNotIn('booking_id',str(result))

    def test_waiting_time_is_blocked(self):
        config=bookings.load_business_config().model_copy(deep=True)
        from app.business_config import CleaningInterval
        config.cleaning.weekly={key:[CleaningInterval.model_validate({'from':'09:00','to':'10:00'})] for key in config.cleaning.weekly}
        with patch('app.bookings.load_business_config',return_value=config):
            row=self.create()
        result=self.search(end=row['cleaning_ends_at']+timedelta(hours=6))
        reasons={item['reason'] for item in result['blocked']}
        if row['cleaning_starts_at']>row['ends_at']:
            self.assertIn('waiting_for_cleaning',reasons)
        self.assertGreaterEqual(datetime.fromisoformat(result['available_starts'][0]['starts_at']),row['cleaning_ends_at'])

    def test_two_hour_cleaning_is_respected_by_suggested_starts(self):
        config=bookings.load_business_config().model_copy(deep=True)
        config.cleaning.duration_minutes=120
        with patch('app.bookings.load_business_config',return_value=config), patch('app.availability.load_business_config',return_value=config):
            row=self.create()
            result=self.search()
        self.assertEqual(datetime.fromisoformat(result['available_starts'][0]['starts_at']),self.end+timedelta(hours=2))
        self.assertEqual(datetime.fromisoformat(result['blocked'][-1]['ends_at']),row['cleaning_ends_at'])

    def test_outside_range_booking_can_block_proposed_cleaning(self):
        self.create(user_id=None,kind='block',status='confirmed',amount='0',
                    starts_at=self.end,ends_at=self.end+timedelta(hours=1))
        result=self.search(end=self.end)
        self.assertEqual(result['blocked'],[])
        self.assertEqual(len(result['unoccupied']),1)
        self.assertEqual(result['available_starts'],[])

    def test_short_free_gap_is_not_bookable(self):
        self.create(user_id=None,kind='block',status='confirmed',amount='0',
                    starts_at=self.start+timedelta(hours=4),ends_at=self.start+timedelta(hours=9))
        result=self.search(end=self.start+timedelta(hours=9))
        self.assertTrue(result['unoccupied'])
        self.assertEqual(result['available_starts'],[])

    def test_expired_and_cancelled_do_not_block(self):
        row=self.create()
        self.c.execute("UPDATE bookings SET expires_at=clock_timestamp()-interval '1 second' WHERE booking_id=%s",(row['booking_id'],))
        self.assertEqual(self.search()['blocked'],[])
        self.c.execute("UPDATE bookings SET status='cancelled' WHERE booking_id=%s",(row['booking_id'],))
        self.assertEqual(self.search()['blocked'],[])

    def test_requested_duration_and_service_agree(self):
        result=self.search(duration_hours=7)
        for option in result['available_starts']:
            self.assertEqual(option['allowed_hours'],[7])
            self.assertTrue(bookings.is_available(self.c,102,datetime.fromisoformat(option['starts_at']),datetime.fromisoformat(option['latest_ends_at'])))

    def test_unknown_schedule_returns_warning_without_guessed_availability(self):
        config=bookings.load_business_config().model_copy(deep=True)
        config.cleaning.weekly={key:None for key in config.cleaning.weekly}
        with patch('app.availability.load_business_config',return_value=config):
            result=self.search()
        self.assertEqual(result['available_starts'],[])
        self.assertTrue(result['warnings'])

    def test_invalid_inputs(self):
        for kwargs in (dict(start=self.start.replace(tzinfo=None)),dict(end=self.start),
                       dict(end=self.start+timedelta(days=32)),dict(duration_hours=4),dict(duration_hours=5.5)):
            with self.subTest(kwargs=kwargs),self.assertRaises(BookingError):
                self.search(**kwargs)
