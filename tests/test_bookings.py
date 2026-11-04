import os
import unittest
import uuid
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from psycopg import sql

from app.config import PROJECT_ROOT, load_settings
from app.database import connect_database
from app.users import register_client
from app.bookings import (AccessDenied, BookingError, SlotUnavailable, StaleBooking,
    create_booking, change_booking, expire_bookings, get_booking, is_available, list_bookings)
from app.business_config import load_business_config, BusinessConfig


@unittest.skipUnless(os.getenv('RUN_DB_TESTS') == '1', 'Set RUN_DB_TESTS=1')
class BookingTests(unittest.TestCase):
    def setUp(self):
        data=load_business_config().model_dump(by_alias=True)
        data['cleaning']['weekly']={key:[{'from':'00:00','to':'23:00'},{'from':'23:00','to':'00:00'}] for key in data['cleaning']['weekly']}
        data['pricing']['verified_holiday_years']=[2026,2027,2030]
        rules_patch=patch('app.bookings.load_business_config',return_value=BusinessConfig.model_validate(data))
        rules_patch.start()
        self.addCleanup(rules_patch.stop)
        ui_patch=patch('app.admin_ui.load_business_config',return_value=BusinessConfig.model_validate(data))
        ui_patch.start()
        self.addCleanup(ui_patch.stop)
        self.settings = load_settings()
        self.schema = 'test_bookings_' + uuid.uuid4().hex
        # Committed isolated schema lets independent connections test actual concurrency.
        self.addCleanup(self.drop_schema)
        with connect_database(self.settings) as connection:
            connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
            self.use_schema(connection)
            for name in ('001_initial','002_remove_cleaning','004_booking_requests','005_cleaning_slots','006_client_notifications','008_payment_receipts','009_calendar_sync'):
                connection.execute((PROJECT_ROOT / 'migrations' / f'{name}.sql').read_text(encoding='utf-8'))
            for tg in (101,102,103,104):
                user = register_client(connection, telegram_user_id=tg,telegram_chat_id=tg,
                    name='Test',username=None,language_code='ru')
                if tg == 101:
                    connection.execute("INSERT INTO admins(user_id,role) VALUES (%s,'owner')",(user.user_id,))
                if tg == 104:
                    connection.execute("INSERT INTO admins(user_id,role) VALUES (%s,'operator')",(user.user_id,))
                if tg == 102:
                    self.client = user.user_id
        self.c = connect_database(self.settings)
        self.addCleanup(self.c.close)
        self.use_schema(self.c)
        self.start = (datetime.now(timezone.utc) + timedelta(days=7)).replace(minute=0,second=0,microsecond=0)
        self.end = self.start + timedelta(hours=5)

    def use_schema(self, connection):
        connection.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))

    def drop_schema(self):
        with connect_database(self.settings) as connection:
            connection.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(self.schema)))

    def create(self, **changes):
        values = dict(user_id=self.client, starts_at=self.start, ends_at=self.end,
                      amount='100000',request_key=uuid.uuid4())
        values.update(changes)
        return create_booking(self.c,101,**values)

    def change(self, row, action, **extra):
        return change_booking(self.c,101,row['booking_id'],action=action,expected_version=row['version'],**extra)

    def test_creation_replay_and_changed_parameters(self):
        key=uuid.uuid4()
        first=self.create(request_key=key)
        self.assertEqual(self.create(request_key=key)['booking_id'],first['booking_id'])
        with self.assertRaises(BookingError):
            self.create(request_key=key,amount='200000')

    def test_default_timeout_is_three_hours_and_minimum_is_five(self):
        # Expiry uses PostgreSQL's clock, which can differ from Windows/WSL time.
        before=self.c.execute('SELECT clock_timestamp()').fetchone()[0]
        row=self.create()
        after=self.c.execute('SELECT clock_timestamp()').fetchone()[0]
        self.assertTrue(before+timedelta(hours=3)<=row['expires_at']<=after+timedelta(hours=3))
        with self.assertRaises(BookingError):
            self.create(starts_at=self.end,ends_at=self.end+timedelta(hours=4))

    def test_overlap_and_adjacent(self):
        self.create()
        with self.assertRaises(SlotUnavailable):
            self.create()
        self.assertFalse(is_available(self.c,102,self.start,self.end))
        with self.assertRaises(SlotUnavailable):
            self.create(starts_at=self.end,ends_at=self.end+timedelta(hours=5))
        self.create(starts_at=self.end+timedelta(hours=1),ends_at=self.end+timedelta(hours=6))

    def test_hold_submit_confirm(self):
        row=self.create(status='hold',ttl_minutes=10)
        row=self.change(row,'submit')
        self.assertEqual(row['status'],'pending')
        row=self.change(row,'confirm')
        self.assertEqual(row['status'],'confirmed')
        self.assertIsNone(row['expires_at'])

    def test_saved_cleaning_survives_config_changes(self):
        row=self.create()
        self.assertEqual(row['cleaning_starts_at'],row['ends_at'])
        self.assertEqual(row['cleaning_ends_at'],row['ends_at']+timedelta(hours=1))
        changed=load_business_config().model_copy(deep=True)
        changed.cleaning.weekly={key:[] for key in changed.cleaning.weekly}
        with patch('app.bookings.load_business_config',return_value=changed):
            confirmed=self.change(row,'confirm')
        self.assertEqual(confirmed['cleaning_ends_at'],row['cleaning_ends_at'])

    def test_expiry_and_replacement(self):
        row=self.create()
        self.c.execute("UPDATE bookings SET expires_at=clock_timestamp()-interval '1 second' WHERE booking_id=%s",(row['booking_id'],))
        self.assertTrue(is_available(self.c,102,self.start,self.end))
        self.assertEqual(expire_bookings(self.c),1)
        self.assertEqual(get_booking(self.c,102,row['booking_id'])['status'],'expired')
        self.create()

    def test_expired_request_cannot_be_confirmed(self):
        row=self.create()
        self.c.execute("UPDATE bookings SET expires_at=clock_timestamp()-interval '1 second' WHERE booking_id=%s",(row['booking_id'],))
        row=get_booking(self.c,101,row['booking_id'])
        with self.assertRaises(BookingError):
            self.change(row,'confirm')

    def test_cancel_releases_interval_and_stale_version_rejected(self):
        row=self.create()
        cancelled=self.change(row,'cancel')
        self.assertEqual(cancelled['status'],'cancelled')
        with self.assertRaises(StaleBooking):
            self.change(row,'confirm')
        self.create()

    def test_failed_move_preserves_original_and_success_updates_version(self):
        first=self.create()
        second=self.create(starts_at=self.end+timedelta(hours=1),ends_at=self.end+timedelta(hours=6))
        with self.assertRaises(SlotUnavailable):
            self.change(first,'move',starts_at=second['starts_at'],ends_at=second['ends_at'])
        unchanged=get_booking(self.c,101,first['booking_id'])
        self.assertEqual(unchanged['starts_at'],first['starts_at'])
        self.assertEqual(unchanged['version'],first['version'])
        moved=self.change(first,'move',starts_at=self.start-timedelta(days=1),ends_at=self.end-timedelta(days=1))
        self.assertEqual(moved['version'],first['version']+1)

    def test_access_rights(self):
        row=self.create()
        with self.assertRaises(AccessDenied):
            get_booking(self.c,103,row['booking_id'])
        self.assertEqual(list_bookings(self.c,103),[])
        for actor in (102,103,104):
            with self.assertRaises(AccessDenied):
                change_booking(self.c,actor,row['booking_id'],action='confirm',expected_version=row['version'])
        result=change_booking(self.c,102,row['booking_id'],action='cancel',expected_version=row['version'])
        self.assertEqual(result['status'],'cancelled')

    def test_confirmed_cancellation_requires_admin(self):
        row=self.change(self.create(),'confirm')
        with self.assertRaises(AccessDenied):
            change_booking(self.c,102,row['booking_id'],action='cancel',expected_version=row['version'])

    def test_manual_block(self):
        self.create(user_id=None,kind='block',status='confirmed',amount='0')
        self.assertFalse(is_available(self.c,102,self.start,self.end))

    def test_invalid_dates_money_and_currency(self):
        for extra in (dict(starts_at=self.start.replace(tzinfo=None)),dict(ends_at=self.start),
                      dict(amount='-1'),dict(amount='NaN'),dict(amount='1.001'),dict(amount=1.5),dict(unit='krw')):
            with self.subTest(extra=extra),self.assertRaises(BookingError):
                self.create(**extra)

    def test_two_concurrent_requests_only_one_succeeds(self):
        # Release the fixture connection; the parallel requests use separate transactions.
        self.c.commit()
        barrier=Barrier(2)
        def request():
            try:
                with connect_database(self.settings) as connection:
                    self.use_schema(connection)
                    barrier.wait(timeout=5)
                    row=create_booking(connection,101,user_id=self.client,starts_at=self.start,
                        ends_at=self.end,amount='100000',request_key=uuid.uuid4())
                return row['booking_id']
            except SlotUnavailable:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _: request(),range(2)))
        self.assertEqual(sum(item is not None for item in results),1)
