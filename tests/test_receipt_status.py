import unittest
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch

from app import client_ui,payment_ui,payments


class ReceiptStatusTests(unittest.IsolatedAsyncioTestCase):
    def row(self,**changes):
        now=datetime.now(timezone.utc)
        return dict(dict(booking_id=17,user_id=4,type='booking',status='pending',version=1,
            starts_at=now+timedelta(days=1),ends_at=now+timedelta(days=1,hours=5),
            expires_at=now+timedelta(hours=24),deposit_paid_amount=0,balance_paid_amount=0,
            payment_receipt=None,client_deleted_at=None),**changes)

    def inactive_rows(self):
        now=datetime.now(timezone.utc)
        return [self.row(status=status) for status in ('cancelled','expired','completed','hold')]+[
            self.row(expires_at=now-timedelta(seconds=1)),
            self.row(status='confirmed',ends_at=now-timedelta(seconds=1)),
            self.row(client_deleted_at=now),self.row(type='block')]

    def test_inactive_bookings_have_no_upload_button_and_cannot_arm_automatic_upload(self):
        for row in self.inactive_rows():
            with self.subTest(row=row):
                callbacks=[button.callback_data for buttons in client_ui.controls(row,'en').inline_keyboard for button in buttons]
                self.assertNotIn('pay:upload:17',callbacks)
                context=SimpleNamespace(user_data={'receipt_upload':17})
                self.assertFalse(payment_ui.expect_receipt_after_booking(context,row))
                self.assertNotIn('receipt_upload',context.user_data)

    def test_active_unpaid_bookings_and_rejected_images_can_still_be_uploaded(self):
        for row in (self.row(),self.row(status='confirmed'),self.row(payment_receipt={'review_status':'rejected'})):
            with self.subTest(row=row):
                callbacks=[button.callback_data for buttons in client_ui.controls(row,'en').inline_keyboard for button in buttons]
                self.assertIn('pay:upload:17',callbacks)
                self.assertTrue(payment_ui.expect_receipt_after_booking(SimpleNamespace(user_data={}),row))

    async def test_old_upload_buttons_are_rejected_and_clear_upload_mode(self):
        for row in self.inactive_rows()+[self.row(deposit_paid_amount=50000),self.row(payment_receipt={'review_status':'pending'})]:
            with self.subTest(row=row):
                context=SimpleNamespace(user_data={'receipt_upload':17,'_language':'en'})
                query=SimpleNamespace(data='pay:upload:17',answer=AsyncMock())
                update=SimpleNamespace(callback_query=query)
                async def call(update,context,operation):
                    return operation(None,42,SimpleNamespace(language='en'))
                with patch('app.payment_ui.client_ui.call',new=AsyncMock(side_effect=call)), \
                     patch('app.payment_ui.client_ui.own_booking',return_value=row), \
                     patch('app.payment_ui.ui.screen',new=AsyncMock()) as screen, \
                     patch('app.payment_ui.ui.error',new=AsyncMock()) as error:
                    await payment_ui.callback(update,context)
                screen.assert_not_awaited()
                self.assertIn('cannot be submitted',error.call_args.args[2])
                self.assertNotIn('receipt_upload',context.user_data)

    def test_storage_rejects_inactive_bookings_before_duplicate_image_or_database_write(self):
        for row in self.inactive_rows():
            row['payment_receipt']={'file_unique_id':'same-image','review_status':'pending'}
            connection=Mock()
            with self.subTest(row=row),patch('app.payments._lock'), \
                 patch('app.payments._get',return_value=row),patch('app.payments._user',return_value=4), \
                 patch('app.payments._now',return_value=datetime.now(timezone.utc)):
                with self.assertRaises(payments.ReceiptUnavailable):
                    payments.submit_receipt(connection,42,17,file_id='image',file_unique_id='same-image',media_type='photo')
                connection.execute.assert_not_called()

    async def test_cancelled_while_waiting_for_photo_does_not_save_or_notify(self):
        context=SimpleNamespace(user_data={'receipt_upload':17,'_language':'ru'})
        image=SimpleNamespace(file_id='image',file_unique_id='image-id',file_size=100)
        update=SimpleNamespace(effective_message=SimpleNamespace(photo=[image],document=None))
        async def call(update,context,operation):
            return operation(None,42,SimpleNamespace(language='ru'))
        with patch('app.payment_ui.client_ui.call',new=AsyncMock(side_effect=call)), \
             patch('app.payments._lock'),patch('app.payments._get',return_value=self.row(status='cancelled')), \
             patch('app.payments._user',return_value=4), \
             patch('app.payments._now',return_value=datetime.now(timezone.utc)), \
             patch('app.payments.enqueue_event') as enqueue, \
             patch('app.notifications.wake_delivery') as wake, \
             patch('app.payment_ui.ui.error',new=AsyncMock()) as error:
            await payment_ui.media_input(update,context)
        enqueue.assert_not_called()
        wake.assert_not_called()
        self.assertNotIn('receipt_upload',context.user_data)
        self.assertIn('недоступна',error.call_args.args[2])

    async def test_removed_booking_from_old_button_exits_upload_with_specific_error(self):
        from app.bookings import BookingError
        context=SimpleNamespace(user_data={'receipt_upload':17})
        update=SimpleNamespace(callback_query=SimpleNamespace(data='pay:upload:17',answer=AsyncMock()))
        with patch('app.payment_ui.client_ui.call',new=AsyncMock(side_effect=BookingError('Booking removed from your list'))), \
             patch('app.payment_ui.ui.error',new=AsyncMock()) as error:
            await payment_ui.callback(update,context)
        self.assertNotIn('receipt_upload',context.user_data)
        self.assertIn('недоступна',error.call_args.args[2])
