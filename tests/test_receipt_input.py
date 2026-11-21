import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from datetime import datetime,timedelta,timezone

from telegram import Update
from telegram.ext import filters
from app import payment_ui
from app import client_ui
from app.bookings import BookingError


class ReceiptInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_creating_request_arms_upload_and_next_photo_needs_no_upload_button(self):
        now=datetime.now(timezone.utc)
        row=dict(booking_id=6,type='booking',status='pending',expires_at=now+timedelta(hours=1),
            ends_at=now+timedelta(days=1),deposit_paid_amount=0,payment_receipt=None)
        payment=SimpleNamespace(deposit_amount=50000,bank='TEST',account_number='000',account_holder='TEST')
        draft=dict(token='token',key=object(),step='review',start=now+timedelta(days=1),end=now+timedelta(days=1,hours=5),
            deposit=50000,payment=('TEST','000','TEST'),quote={},cancellation_policy={},details={})
        context=SimpleNamespace(user_data={'client_draft':draft},bot_data={})
        message=SimpleNamespace(reply_text=AsyncMock())
        update=SimpleNamespace(callback_query=SimpleNamespace(data='cli:send:token',answer=AsyncMock()),effective_message=message)
        async def call(update,ctx,operation):
            return operation(None,42,SimpleNamespace(language='ru'))
        with patch('app.client_ui.call',new=AsyncMock(side_effect=call)), \
             patch('app.client_ui.load_business_config',return_value=SimpleNamespace(payment=payment)), \
             patch('app.client_ui.create_client_booking',return_value=row), \
             patch('app.client_ui.card',return_value='Booking #6'), \
             patch('app.client_ui.controls',return_value=None), \
             patch('app.notifications.wake_delivery'), \
             patch('app.client_ui.ui.result',new=AsyncMock()) as result, \
             patch('app.payment_ui.submit_receipt',return_value=row) as submit:
            await client_ui.callback(update,context)
            self.assertEqual(context.user_data['receipt_upload'],6)
            self.assertIn('нажимать отдельную кнопку не нужно',result.call_args.args[2])
            photo=self.update({'photo':[{'file_id':'image','file_unique_id':'i','width':597,'height':1280,'file_size':1024}]})
            await payment_ui.media_input(photo,context)
        self.assertEqual(submit.call_args.args,(None,42,6))
        self.assertNotIn('receipt_upload',context.user_data)

    def test_automatic_upload_is_not_armed_for_inactive_paid_or_already_submitted_bookings(self):
        now=datetime.now(timezone.utc)
        row=dict(booking_id=6,type='booking',status='pending',expires_at=now+timedelta(hours=1),
            ends_at=now+timedelta(days=1),deposit_paid_amount=0,payment_receipt=None)
        for changes in (dict(status='cancelled'),dict(status='expired'),dict(status='completed'),
                        dict(expires_at=now-timedelta(seconds=1)),dict(deposit_paid_amount=50000),
                        dict(payment_receipt={'review_status':'pending'}),dict(payment_receipt={'review_status':'accepted'})):
            with self.subTest(changes=changes):
                context=SimpleNamespace(user_data={'receipt_upload':6})
                self.assertFalse(payment_ui.expect_receipt_after_booking(context,dict(row,**changes)))
                self.assertNotIn('receipt_upload',context.user_data)
        context=SimpleNamespace(user_data={})
        self.assertTrue(payment_ui.expect_receipt_after_booking(context,row))
        self.assertEqual(context.user_data['receipt_upload'],6)

    def update(self, media):
        payload={'message_id':1,'date':1,'chat':{'id':42,'type':'private'},
                 'from':{'id':42,'is_bot':False,'first_name':'Test'},**media}
        return Update.de_json({'update_id':1,'message':payload},None)

    async def test_video_then_image_keeps_booking_and_accepts_image(self):
        for field,meta in (
            ('video',{'width':320,'height':240,'duration':1}),
            ('video_note',{'length':240,'duration':1}),
            ('document',{'mime_type':'video/mp4','file_name':'video.mp4'}),
        ):
            with self.subTest(field=field):
                context=SimpleNamespace(user_data={'receipt_upload':6})
                invalid=self.update({field:dict(file_id='video',file_unique_id='v',**meta)})
                handler=payment_ui.media_input if field=='document' else payment_ui.unsupported_input
                self.assertTrue((filters.ChatType.PRIVATE & filters.ALL & ~filters.COMMAND).check_update(invalid))
                with patch('app.payment_ui.ui.error',new=AsyncMock()) as error:
                    await handler(invalid,context)
                self.assertIn('Видео и кружочки',error.call_args.args[2])
                self.assertEqual(context.user_data['receipt_upload'],6)
                photo=self.update({'photo':[{'file_id':'image','file_unique_id':'i','width':597,'height':1280,'file_size':119162}]})
                def call(update,ctx,operation):
                    return operation(None,42,SimpleNamespace(language='ru'))
                with patch('app.payment_ui.client_ui.call',new=AsyncMock(side_effect=call)), \
                     patch('app.payment_ui.submit_receipt',return_value={'booking_id':6}) as submit, \
                     patch('app.notifications.wake_delivery'), \
                     patch('app.payment_ui.client_ui.controls',return_value=None), \
                     patch('app.payment_ui.ui.result',new=AsyncMock()) as result:
                    await payment_ui.media_input(photo,context)
                self.assertEqual(submit.call_args.args,(None,42,6))
                self.assertEqual(submit.call_args.kwargs['file_id'],'image')
                result.assert_awaited_once()
                self.assertNotIn('receipt_upload',context.user_data)

    async def test_specific_rejection_is_explained_and_retry_stays_available(self):
        context=SimpleNamespace(user_data={'receipt_upload':6})
        for reason,expected in [('This image is already linked to another booking','другой брони'),
                                ('Deposit is already recorded','уже отмечена'),
                                ('Image too large','больше 10 МБ')]:
            with self.subTest(reason=reason),patch('app.payment_ui.ui.error',new=AsyncMock()) as error:
                await payment_ui.error_message(None,BookingError(reason),context)
                self.assertIn(expected,error.call_args.args[2])
                self.assertEqual(context.user_data['receipt_upload'],6)

    async def test_unsupported_media_outside_upload_is_ignored(self):
        with patch('app.payment_ui.ui.error',new=AsyncMock()) as error:
            await payment_ui.unsupported_input(None,SimpleNamespace(user_data={}))
        error.assert_not_awaited()
