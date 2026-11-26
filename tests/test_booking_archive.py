import os
import unittest
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

import test_bookings as fixtures
from app import client_ui
from app.bookings import BookingError,get_booking,is_available,list_bookings
from app.booking_archive import archive_bookings,can_archive


class ArchiveUITests(unittest.IsolatedAsyncioTestCase):
    def test_only_inactive_rentals_get_remove_button(self):
        now=datetime.now(timezone.utc)
        row=dict(booking_id=5,type='booking',status='confirmed',version=1,
            starts_at=now-timedelta(hours=5),ends_at=now,expires_at=None,
            deposit_paid_amount=0,balance_paid_amount=0)
        for status in ('completed','expired','cancelled','confirmed','pending','hold'):
            with self.subTest(status=status):
                row.update(status=status,expires_at=now-timedelta(seconds=1))
                self.assertTrue(can_archive(row,now))
                callbacks=[b.callback_data for r in client_ui.controls(row,'en').inline_keyboard for b in r]
                self.assertIn('cli:archive:5',callbacks)
        row.update(status='confirmed',ends_at=now+timedelta(hours=1))
        self.assertFalse(can_archive(row,now))
        row.update(status='pending',expires_at=now+timedelta(hours=1))
        self.assertFalse(can_archive(row,now))
        row.update(status='completed',client_deleted_at=now)
        self.assertFalse(can_archive(row,now))
        row.update(client_deleted_at=None,type='block')
        self.assertFalse(can_archive(row,now))

    async def test_bulk_requires_confirmation_then_refreshes_list(self):
        context=SimpleNamespace(user_data={'receipt_upload':5})
        update=SimpleNamespace(callback_query=SimpleNamespace(data='cli:archive:all',answer=AsyncMock()))
        async def call(update,context,operation):
            return operation(None,102,SimpleNamespace(language='en'))
        with patch('app.client_ui.call',side_effect=call), \
             patch('app.client_ui.ui.screen',new=AsyncMock()) as screen, \
             patch('app.client_ui.archive_bookings',return_value=2) as archive, \
             patch('app.client_ui.show_mine',new=AsyncMock()) as mine:
            await client_ui.callback(update,context)
            archive.assert_not_called()
            self.assertIn('Remove all completed',screen.call_args.args[2])
            self.assertNotIn('receipt_upload',context.user_data)
            update.callback_query.data='cli:archive_yes:all'
            await client_ui.callback(update,context)
            archive.assert_called_once_with(None,102,None)
            mine.assert_awaited_once()


@unittest.skipUnless(os.getenv('RUN_DB_TESTS')=='1','Set RUN_DB_TESTS=1')
class ArchiveDatabaseTests(unittest.TestCase):
    setUp=fixtures.BookingTests.setUp
    use_schema=fixtures.BookingTests.use_schema
    drop_schema=fixtures.BookingTests.drop_schema
    create=fixtures.BookingTests.create

    def test_single_keeps_business_data_and_cleanup_and_is_idempotent(self):
        row=self.create()
        self.c.execute("UPDATE bookings SET status='completed',deposit_paid_amount=50000 WHERE booking_id=%s",(row['booking_id'],))
        before=get_booking(self.c,102,row['booking_id'])
        self.assertEqual(archive_bookings(self.c,102,row['booking_id']),1)
        after=get_booking(self.c,102,row['booking_id'])
        self.assertIsNotNone(after.pop('client_deleted_at'))
        before.pop('client_deleted_at')
        self.assertEqual(after,before)
        self.assertEqual(archive_bookings(self.c,102,row['booking_id']),0)
        self.assertFalse(is_available(self.c,102,row['ends_at'],row['ends_at']+timedelta(hours=5)))
        self.assertEqual(list_bookings(self.c,102),[])

    def test_bulk_only_own_inactive_and_expiry_before_worker(self):
        inactive=self.create()
        active=self.create(starts_at=self.start+timedelta(days=1),ends_at=self.end+timedelta(days=1))
        foreign=self.create(user_id=None,starts_at=self.start+timedelta(days=2),ends_at=self.end+timedelta(days=2))
        self.c.execute("UPDATE bookings SET expires_at=clock_timestamp()-interval '1 second' WHERE booking_id=%s",(inactive['booking_id'],))
        self.c.execute("UPDATE bookings SET status='expired' WHERE booking_id=%s",(foreign['booking_id'],))
        self.assertEqual(archive_bookings(self.c,102),1)
        self.assertIsNone(get_booking(self.c,102,active['booking_id'])['client_deleted_at'])
        self.assertIsNone(get_booking(self.c,101,foreign['booking_id'])['client_deleted_at'])
        self.assertEqual(archive_bookings(self.c,102),0)
        for bid in (active['booking_id'],foreign['booking_id']):
            with self.assertRaises(BookingError):
                archive_bookings(self.c,102,bid)

    def test_deleted_card_cannot_be_reopened_and_other_client_cannot_delete(self):
        row=self.create()
        self.c.execute("UPDATE bookings SET status='expired' WHERE booking_id=%s",(row['booking_id'],))
        with self.assertRaises(BookingError):
            archive_bookings(self.c,103,row['booking_id'])
        archive_bookings(self.c,102,row['booking_id'])
        with self.assertRaises(BookingError):
            client_ui.own_booking(self.c,102,row['booking_id'])
