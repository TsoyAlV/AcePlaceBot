import os
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from uuid import uuid4

import test_bookings as fixtures
from app import bookings,payments,notifications,payment_ui,client_ui
from app.database import connect_database


@unittest.skipUnless(os.getenv('RUN_DB_TESTS')=='1','Set RUN_DB_TESTS=1')
class PaymentTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.BookingTests.setUp
    use_schema=fixtures.BookingTests.use_schema
    drop_schema=fixtures.BookingTests.drop_schema

    def connection(self,settings):
        c=connect_database(settings)
        self.use_schema(c)
        return c

    def create(self,**kwargs):
        values=dict(user_id=self.client,starts_at=self.start,ends_at=self.end,amount='150000',request_key=uuid4())
        values.update(kwargs)
        with self.connection(self.settings) as c:
            return bookings.create_booking(c,101,**values)

    def submit(self,row,actor=102,unique='receipt-a',media='photo'):
        with self.connection(self.settings) as c:
            return payments.submit_receipt(c,actor,row['booking_id'],file_id='telegram-'+unique,
                file_unique_id=unique,media_type=media)

    def review(self,row,accept=True,actor=101):
        with self.connection(self.settings) as c:
            return payments.review_receipt(c,actor,row['booking_id'],expected_version=row['version'],accept=accept)

    def update(self,*,actor=102,data=None,photo=None,document=None):
        message=SimpleNamespace(reply_text=AsyncMock(),photo=[photo] if photo else [],document=document)
        return SimpleNamespace(effective_user=SimpleNamespace(id=actor,full_name='Test',username=None,language_code='ru'),
            effective_chat=SimpleNamespace(id=actor,type='private'),effective_message=message,
            callback_query=SimpleNamespace(data=data,answer=AsyncMock()) if data else None)

    async def test_upload_does_not_record_money_or_confirm(self):
        row=self.submit(self.create())
        self.assertEqual(row['status'],'pending')
        self.assertEqual(row['deposit_paid_amount'],0)
        self.assertEqual(row['payment_receipt']['review_status'],'pending')

    async def test_accept_records_exact_deposit_but_booking_stays_pending(self):
        row=self.review(self.submit(self.create()))
        self.assertEqual(row['deposit_paid_amount'],50000)
        self.assertEqual(row['status'],'pending')
        self.assertEqual(row['payment_receipt']['review_status'],'accepted')
        with self.assertRaises(bookings.StaleBooking):
            self.review(dict(row,version=row['version']-1))
        self.assertEqual(bookings.get_booking(self.c,102,row['booking_id'])['deposit_paid_amount'],50000)

    async def test_rejected_receipt_can_be_replaced_without_payment(self):
        row=self.review(self.submit(self.create()),accept=False)
        row=self.submit(row,unique='replacement')
        self.assertEqual(row['payment_receipt']['review_status'],'pending')
        self.assertEqual(row['deposit_paid_amount'],0)

    async def test_duplicate_upload_is_idempotent(self):
        row=self.submit(self.create())
        again=self.submit(row)
        self.assertEqual(again['version'],row['version'])
        self.assertEqual(again['admin_notifications'],row['admin_notifications'])

    async def test_foreign_client_and_operator_cannot_review(self):
        row=self.create()
        with self.assertRaises(bookings.AccessDenied):
            self.submit(row,actor=103)
        row=self.submit(row)
        for actor in (102,104):
            with self.subTest(actor=actor),self.assertRaises(bookings.AccessDenied):
                self.review(row,actor=actor)

    async def test_same_image_cannot_be_attached_to_another_booking(self):
        self.submit(self.create())
        row=self.create(starts_at=self.start+timedelta(days=1),ends_at=self.end+timedelta(days=1))
        with self.assertRaises(bookings.BookingError):
            self.submit(row)

    async def test_late_evidence_is_kept_but_does_not_restore_booking(self):
        row=self.create()
        with self.connection(self.settings) as c:
            c.execute("UPDATE bookings SET expires_at=clock_timestamp()-interval '1 second' WHERE booking_id=%s",(row['booking_id'],))
            bookings.expire_bookings(c)
        row=self.submit(row)
        self.assertTrue(row['payment_receipt']['late'])
        with self.assertRaises(bookings.BookingError):
            self.review(row)
        current=bookings.get_booking(self.c,102,row['booking_id'])
        self.assertEqual(current['status'],'expired')
        self.assertEqual(current['deposit_paid_amount'],0)

    async def test_pending_evidence_is_delivered_even_after_expiry(self):
        row=self.submit(self.create())
        with self.connection(self.settings) as c:
            c.execute("UPDATE bookings SET expires_at=clock_timestamp()-interval '1 second' WHERE booking_id=%s",(row['booking_id'],))
            bookings.expire_bookings(c)
        bot=SimpleNamespace(send_message=AsyncMock(),send_photo=AsyncMock())
        with patch('app.notifications.connect_database',side_effect=self.connection):
            await notifications.deliver_pending(bot,self.settings)
        bot.send_photo.assert_awaited_once()
        self.assertEqual(bot.send_photo.call_args.kwargs['photo'],'telegram-receipt-a')
        self.assertIn('поздняя оплата',bot.send_photo.call_args.kwargs['caption'])

    async def test_photo_handler_binds_image_to_selected_booking(self):
        row=self.create()
        context=SimpleNamespace(user_data={},bot_data={'settings':self.settings})
        with patch('app.client_ui.connect_database',side_effect=self.connection):
            await payment_ui.callback(self.update(data=f"pay:upload:{row['booking_id']}"),context)
            image=SimpleNamespace(file_id='photo1',file_unique_id='unique1',file_size=100)
            await payment_ui.media_input(self.update(photo=image),context)
        current=bookings.get_booking(self.c,102,row['booking_id'])
        self.assertEqual(current['payment_receipt']['file_id'],'photo1')
        self.assertNotIn('receipt_upload',context.user_data)

    async def test_non_image_document_is_rejected(self):
        row=self.create()
        context=SimpleNamespace(user_data={'receipt_upload':row['booking_id']},bot_data={'settings':self.settings})
        document=SimpleNamespace(mime_type='application/pdf',file_id='doc',file_unique_id='doc',file_size=100)
        await payment_ui.media_input(self.update(document=document),context)
        self.assertIsNone(bookings.get_booking(self.c,102,row['booking_id'])['payment_receipt'])

    async def test_bank_confirmation_requires_separate_admin_agreement(self):
        row=self.submit(self.create())
        context=SimpleNamespace(user_data={},bot_data={'settings':self.settings})
        with patch('app.admin_ui.connect_database',side_effect=self.connection):
            await payment_ui.callback(self.update(actor=101,data=f"pay:ask:accept:{row['booking_id']}:{row['version']}"),context)
            self.assertEqual(bookings.get_booking(self.c,102,row['booking_id'])['deposit_paid_amount'],0)
            await payment_ui.callback(self.update(actor=101,data=f"pay:yes:accept:{row['booking_id']}:{row['version']}"),context)
        self.assertEqual(bookings.get_booking(self.c,102,row['booking_id'])['deposit_paid_amount'],50000)
