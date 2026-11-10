import os
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from uuid import uuid4

from pydantic import ValidationError
from psycopg.types.json import Jsonb
import test_bookings as fixtures
from app import bookings,notifications,reviews
from app.business_config import ReviewsConfig,ReviewText
from app.database import connect_database


class ReviewFormattingTests(unittest.TestCase):
    def test_config_text_and_link_render_as_safe_bold_inline_link(self):
        rules=ReviewsConfig(ru=ReviewText(message='Спасибо <AcePlace>!\n{review_link}',link_text='⭐ Отзыв & фото'))
        text=reviews.render(rules,'ru')
        self.assertIn('Спасибо &lt;AcePlace&gt;!',text)
        self.assertIn('<a href="https://t.me/AcePlaceParty/112"><b>⭐ Отзыв &amp; фото</b></a>',text)
        self.assertNotIn('{review_link}',text)
        self.assertIn('후기',reviews.render(ReviewsConfig(),'ko'))

    def test_invalid_placeholder_and_url_are_rejected(self):
        for message in ('No link','{review_link} {review_link}'):
            with self.assertRaises(ValidationError):
                ReviewText(message=message,link_text='Review')
        with self.assertRaises(ValidationError):
            ReviewsConfig(url='javascript:alert(1)')


@unittest.skipUnless(os.getenv('RUN_DB_TESTS')=='1','Set RUN_DB_TESTS=1')
class ReviewDeliveryTests(unittest.IsolatedAsyncioTestCase):
    use_schema=fixtures.BookingTests.use_schema
    drop_schema=fixtures.BookingTests.drop_schema

    def setUp(self):
        fixtures.BookingTests.setUp(self)
        self.config=bookings.load_business_config().model_copy(deep=True)
        self.config.reviews.enabled=True
        patcher=patch('app.notifications.load_business_config',return_value=self.config)
        patcher.start()
        self.addCleanup(patcher.stop)

    def connection(self,settings):
        c=connect_database(settings)
        self.use_schema(c)
        return c

    def create(self,**kwargs):
        args=dict(user_id=self.client,starts_at=self.start,ends_at=self.end,amount='150000',request_key=uuid4())
        args.update(kwargs)
        with self.connection(self.settings) as c:
            row=bookings.create_booking(c,101,**args)
            if row['type']=='booking':
                row=bookings.change_booking(c,101,row['booking_id'],action='confirm',expected_version=row['version'])
            return row

    def fresh(self,row):
        return bookings.get_booking(self.c,101,row['booking_id'])

    def prepare(self):
        reviews.prepare(self.settings,self.config.reviews,connect=self.connection)

    def end_rental(self,row,hours_ago=0):
        # Simulate elapsed time in this test schema; production clocks are not changed.
        row=self.fresh(row)
        end=self.c.execute('SELECT clock_timestamp()').fetchone()[0]-timedelta(hours=hours_ago,seconds=1)
        queue=row['admin_notifications']
        if 'review' in queue:
            queue['review'].update(rental_end=end.isoformat(),retry_at=end.isoformat())
        self.c.execute('''UPDATE bookings SET starts_at=%s,ends_at=%s,cleaning_starts_at=%s,
            cleaning_ends_at=%s,admin_notifications=%s WHERE booking_id=%s''',
            (end-timedelta(hours=5),end,end,end+timedelta(hours=1),Jsonb(queue),row['booking_id']))
        self.c.commit()

    async def deliver(self,side_effect=None):
        bot=SimpleNamespace(send_message=AsyncMock(side_effect=side_effect))
        with patch('app.notifications.connect_database',side_effect=self.connection):
            await notifications.deliver_pending(bot,self.settings)
        return bot

    async def test_after_end_sends_only_client_once_and_survives_restart(self):
        row=self.create()
        bot=await self.deliver()
        self.assertFalse(any('Спасибо, что выбрали' in c.kwargs['text'] for c in bot.send_message.call_args_list))
        self.assertEqual(self.fresh(row)['version'],row['version'])
        self.end_rental(row,hours_ago=2)
        with self.connection(self.settings) as c:
            bookings.complete_bookings(c)
        self.assertEqual(self.fresh(row)['status'],'completed')
        bot=await self.deliver()
        bot.send_message.assert_awaited_once()
        message=bot.send_message.call_args.kwargs
        self.assertEqual(message['chat_id'],102)
        self.assertIn('Спасибо, что выбрали AcePlace!',message['text'])
        self.assertIn('https://t.me/AcePlaceParty/112',message['text'])
        self.assertIn('<b>⭐ Оставить отзыв</b>',message['text'])
        self.assertEqual(message['parse_mode'],'HTML')
        self.assertNotIn('reply_markup',message)
        self.assertEqual(self.fresh(row)['admin_notifications']['review']['status'],'sent')
        bot=await self.deliver()
        bot.send_message.assert_not_awaited()

    async def test_no_historical_mass_send_or_messages_for_blocks_and_guest_rentals(self):
        old=self.create()
        self.end_rental(old)
        await self.deliver()
        self.assertNotIn('review',self.fresh(old)['admin_notifications'])
        guest=self.create(user_id=None)
        self.prepare()
        self.assertNotIn('review',self.fresh(guest)['admin_notifications'])
        with self.connection(self.settings) as c:
            bookings.change_booking(c,101,guest['booking_id'],action='cancel',expected_version=guest['version'])
        block=self.create(user_id=None,kind='block',status='confirmed',amount='0')
        self.prepare()
        self.assertNotIn('review',self.fresh(block)['admin_notifications'])

    async def test_move_reschedules_and_cancel_disables_review(self):
        row=self.create()
        self.prepare()
        with self.connection(self.settings) as c:
            moved=bookings.change_booking(c,101,row['booking_id'],action='move',expected_version=row['version'],
                starts_at=self.start+timedelta(days=1),ends_at=self.end+timedelta(days=1))
        self.prepare()
        current=self.fresh(row)
        self.assertEqual(current['admin_notifications']['review']['rental_end'],moved['ends_at'].isoformat())
        with self.connection(self.settings) as c:
            bookings.change_booking(c,101,row['booking_id'],action='cancel',expected_version=moved['version'])
        self.assertEqual(self.fresh(row)['admin_notifications']['review']['status'],'cancelled')

    async def test_retry_keeps_one_job_and_uses_client_language(self):
        row=self.create()
        self.prepare()
        self.end_rental(row)
        self.c.execute("UPDATE clients SET language='ko' WHERE telegram_user_id=102")
        self.c.commit()
        await self.deliver(TimeoutError())
        state=self.fresh(row)['admin_notifications']['review']
        self.assertEqual((state['status'],state['attempts']),('pending',1))
        self.c.execute("UPDATE bookings SET admin_notifications=jsonb_set(admin_notifications,'{review,retry_at}',to_jsonb(clock_timestamp()::text)) WHERE booking_id=%s",(row['booking_id'],))
        self.c.commit()
        bot=await self.deliver()
        bot.send_message.assert_awaited_once()
        self.assertIn('AcePlace를 이용해 주셔서 감사합니다!',bot.send_message.call_args.kwargs['text'])

    async def test_disabled_or_too_old_queued_invitation_is_skipped(self):
        row=self.create()
        self.prepare()
        self.end_rental(row,hours_ago=25)
        bot=await self.deliver()
        bot.send_message.assert_not_awaited()
        self.assertEqual(self.fresh(row)['admin_notifications']['review']['status'],'cancelled')
        row=self.create()
        self.prepare()
        self.end_rental(row)
        self.config.reviews.enabled=False
        bot=await self.deliver()
        bot.send_message.assert_not_awaited()

    async def test_cancellation_after_claim_is_checked_again_before_send(self):
        row=self.create()
        self.prepare()
        self.end_rental(row)
        original=notifications.claim
        def claim_then_cancel(settings):
            job=original(settings)
            if job and job[0]['admin_notifications'][job[1]].get('event')=='review':
                with self.connection(settings) as c:
                    current=bookings.get_booking(c,101,row['booking_id'])
                    bookings.change_booking(c,101,row['booking_id'],action='cancel',expected_version=current['version'])
            return job
        with patch('app.notifications.claim',side_effect=claim_then_cancel):
            bot=await self.deliver()
        self.assertFalse(any('Спасибо, что выбрали' in c.kwargs['text'] for c in bot.send_message.call_args_list))

    async def test_payment_change_does_not_supersede_due_review(self):
        from app.booking_events import enqueue_event
        row=self.create()
        self.prepare()
        self.end_rental(row)
        with self.connection(self.settings) as c:
            c.execute('UPDATE bookings SET deposit_paid_amount=50000 WHERE booking_id=%s',(row['booking_id'],))
            current=bookings.get_booking(c,101,row['booking_id'])
            enqueue_event(c,current,'deposit_received',actor=101,origin='admin')
        bot=await self.deliver()
        invitations=[call for call in bot.send_message.call_args_list if 'Спасибо, что выбрали' in call.kwargs['text']]
        self.assertEqual(len(invitations),1)
        self.assertEqual(self.fresh(row)['admin_notifications']['review']['status'],'sent')
