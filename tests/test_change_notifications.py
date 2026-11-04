import os
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from uuid import uuid4

import test_bookings as fixtures
from app import bookings,notifications
from app.database import connect_database


@unittest.skipUnless(os.getenv('RUN_DB_TESTS')=='1','Set RUN_DB_TESTS=1')
class ChangeNotificationTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.BookingTests.setUp
    use_schema=fixtures.BookingTests.use_schema
    drop_schema=fixtures.BookingTests.drop_schema

    def connection(self,settings):
        c=connect_database(settings)
        self.use_schema(c)
        return c

    def create(self):
        with self.connection(self.settings) as c:
            return bookings.create_client_booking(c,102,starts_at=self.start,ends_at=self.end,
                request_key=uuid4(),expected_quote=bookings.quote_rental(self.start,self.end))

    def change(self,row,action,actor=101,**kwargs):
        with self.connection(self.settings) as c:
            return bookings.change_booking(c,actor,row['booking_id'],action=action,
                expected_version=row['version'],**kwargs)

    async def deliver(self,side_effect=None):
        bot=SimpleNamespace(send_message=AsyncMock(side_effect=side_effect))
        with patch('app.notifications.connect_database',side_effect=self.connection):
            await notifications.deliver_pending(bot,self.settings)
        return bot

    async def test_confirm_notifies_client_with_private_card(self):
        row=self.change(self.create(),'confirm')
        bot=await self.deliver()
        bot.send_message.assert_awaited_once()
        message=bot.send_message.call_args.kwargs
        self.assertEqual(message['chat_id'],102)
        self.assertIn('Бронь подтверждена',message['text'])
        self.assertNotIn('Клиент ID',message['text'])
        self.assertNotIn('adm:',str(message['reply_markup']))
        self.assertEqual(bookings.get_booking(self.c,102,row['booking_id'])['version'],row['version'])

    async def test_confirmation_includes_visit_instructions_immediately(self):
        config=bookings.load_business_config().model_copy(deep=True)
        config.visit.ru.address='Test address'
        config.visit.ru.route_url='https://example.com/route'
        config.visit.ru.entry_instructions='Test entry instructions'
        self.change(self.create(),'confirm')
        with patch('app.notifications.load_business_config',return_value=config):
            bot=await self.deliver()
        message=bot.send_message.call_args.kwargs
        self.assertEqual(message['chat_id'],102)
        self.assertIn('Бронь подтверждена',message['text'])
        self.assertIn('Test address',message['text'])
        self.assertIn('https://example.com/route',message['text'])
        self.assertIn('Test entry instructions',message['text'])

    async def test_cleaning_reminder_survives_restart_and_sends_once(self):
        self.start-=timedelta(days=6)
        self.end-=timedelta(days=6)
        row=self.change(self.create(),'confirm')
        config=bookings.load_business_config().model_copy(deep=True)
        config.notifications.cleaning.hours_before=48
        with patch('app.notifications.load_business_config',return_value=config):
            bot=await self.deliver()
            reminders=[call.kwargs for call in bot.send_message.call_args_list if 'скоро уборка' in call.kwargs['text']]
            self.assertEqual(len(reminders),1)
            self.assertEqual(reminders[0]['chat_id'],101)
            from app.admin_ui import fmt
            self.assertIn(fmt(row['cleaning_starts_at']),reminders[0]['text'])
            bot=await self.deliver()
            bot.send_message.assert_not_awaited()
        self.assertEqual(bookings.get_booking(self.c,101,row['booking_id'])['version'],row['version'])

    async def test_cleaning_flag_and_admin_preference_disable_reminders(self):
        self.start-=timedelta(days=6)
        self.end-=timedelta(days=6)
        self.change(self.create(),'confirm')
        config=bookings.load_business_config().model_copy(deep=True)
        config.notifications.cleaning.hours_before=48
        config.notifications.cleaning.telegram_enabled=False
        with patch('app.notifications.load_business_config',return_value=config):
            bot=await self.deliver()
            self.assertFalse(any('скоро уборка' in call.kwargs['text'] for call in bot.send_message.call_args_list))
            config.notifications.cleaning.telegram_enabled=True
            self.c.execute('UPDATE admins SET notifications_enabled=false')
            self.c.commit()
            bot=await self.deliver()
            bot.send_message.assert_not_awaited()

    async def test_move_cancels_queued_cleaning_and_reschedules_new_slot(self):
        self.start-=timedelta(days=6)
        self.end-=timedelta(days=6)
        row=self.change(self.create(),'confirm')
        config=bookings.load_business_config().model_copy(deep=True)
        config.notifications.cleaning.hours_before=48
        with patch('app.notifications.load_business_config',return_value=config),patch('app.notifications.connect_database',side_effect=self.connection):
            notifications.prepare_cleaning_reminders(self.settings)
            moved=self.change(row,'move',starts_at=self.start+timedelta(days=3),ends_at=self.end+timedelta(days=3))
            bot=await self.deliver()
            self.assertFalse(any('скоро уборка' in call.kwargs['text'] for call in bot.send_message.call_args_list))
            config.notifications.cleaning.hours_before=168
            bot=await self.deliver()
            bot.send_message.assert_awaited_once()
            from app.admin_ui import fmt
            self.assertIn(fmt(moved['cleaning_starts_at']),bot.send_message.call_args.kwargs['text'])

    async def test_cancel_prevents_queued_cleaning_reminder(self):
        self.start-=timedelta(days=6)
        self.end-=timedelta(days=6)
        row=self.change(self.create(),'confirm')
        config=bookings.load_business_config().model_copy(deep=True)
        config.notifications.cleaning.hours_before=48
        with patch('app.notifications.load_business_config',return_value=config),patch('app.notifications.connect_database',side_effect=self.connection):
            notifications.prepare_cleaning_reminders(self.settings)
            self.change(row,'cancel')
            bot=await self.deliver()
            self.assertFalse(any('скоро уборка' in call.kwargs['text'] for call in bot.send_message.call_args_list))

    async def test_repeated_confirm_does_not_send_second_notification(self):
        row=self.change(self.create(),'confirm')
        await self.deliver()
        self.change(row,'confirm')
        bot=await self.deliver()
        bot.send_message.assert_not_awaited()

    async def test_notification_uses_client_language(self):
        row=self.create()
        self.c.execute("UPDATE clients SET language='ko' WHERE telegram_user_id=102")
        self.c.commit()
        self.change(row,'confirm')
        bot=await self.deliver()
        self.assertIn('예약이 확정되었습니다',bot.send_message.call_args.kwargs['text'])

    async def test_customer_withdrawal_notifies_admin_even_after_cancellation(self):
        row=self.change(self.create(),'cancel',actor=102,origin='client')
        self.assertEqual(row['status'],'cancelled')
        bot=await self.deliver()
        self.assertEqual(bot.send_message.call_args.kwargs['chat_id'],101)
        self.assertIn('Клиент отозвал',bot.send_message.call_args.kwargs['text'])

    async def test_admin_cancellation_notifies_customer(self):
        row=self.change(self.create(),'cancel')
        bot=await self.deliver()
        self.assertEqual(bot.send_message.call_args.kwargs['chat_id'],102)
        self.assertIn('Бронь отменена',bot.send_message.call_args.kwargs['text'])

    async def test_move_includes_old_and_new_times(self):
        row=self.change(self.create(),'confirm')
        moved=self.change(row,'move',starts_at=self.start+timedelta(days=1),ends_at=self.end+timedelta(days=1))
        bot=await self.deliver()
        text=bot.send_message.call_args.kwargs['text']
        self.assertIn('Время брони изменено',text)
        self.assertIn('Было:',text)
        from app.admin_ui import fmt
        self.assertIn(fmt(row['starts_at']),text)
        self.assertIn(fmt(moved['starts_at']),text)

    async def test_fast_cancel_supersedes_unsent_confirmation(self):
        row=self.change(self.create(),'confirm')
        self.change(row,'cancel')
        bot=await self.deliver()
        bot.send_message.assert_awaited_once()
        self.assertIn('Бронь отменена',bot.send_message.call_args.kwargs['text'])
        bot=await self.deliver()
        bot.send_message.assert_not_awaited()

    async def test_retry_survives_new_worker_and_preserves_version(self):
        row=self.change(self.create(),'cancel')
        await self.deliver(TimeoutError())
        current=bookings.get_booking(self.c,102,row['booking_id'])
        self.assertEqual(current['version'],row['version'])
        pending=[v for v in current['admin_notifications'].values() if v['status']=='pending']
        self.assertEqual(len(pending),1)
        self.assertEqual(pending[0]['attempts'],1)
        self.c.execute("""UPDATE bookings SET admin_notifications=(SELECT jsonb_object_agg(key,
            CASE WHEN value->>'status'='pending' THEN value-'retry_at' ELSE value END)
            FROM jsonb_each(admin_notifications)) WHERE booking_id=%s""",(row['booking_id'],))
        self.c.commit()
        bot=await self.deliver()
        self.assertEqual(bot.send_message.call_args.kwargs['chat_id'],102)

    async def test_failed_move_does_not_queue_notification(self):
        row=self.change(self.create(),'confirm')
        await self.deliver()
        with self.connection(self.settings) as c:
            bookings.create_booking(c,101,user_id=None,starts_at=self.start+timedelta(days=1),
                ends_at=self.end+timedelta(days=1),amount='150000',request_key=uuid4())
        with self.assertRaises(bookings.SlotUnavailable):
            self.change(row,'move',starts_at=self.start+timedelta(days=1),ends_at=self.end+timedelta(days=1))
        bot=await self.deliver()
        bot.send_message.assert_not_awaited()

    async def test_expiry_notifies_both_sides(self):
        row=self.create()
        with self.connection(self.settings) as c:
            c.execute("UPDATE bookings SET expires_at=clock_timestamp()-interval '1 second' WHERE booking_id=%s",(row['booking_id'],))
            bookings.expire_bookings(c)
        bot=await self.deliver()
        self.assertEqual({call.kwargs['chat_id'] for call in bot.send_message.call_args_list},{101,102})
        self.assertTrue(all('Срок заявки истёк' in call.kwargs['text'] for call in bot.send_message.call_args_list))

    async def test_other_admin_receives_changes_but_actor_does_not(self):
        row=self.create()
        with self.connection(self.settings) as c:
            uid=c.execute('SELECT user_id FROM clients WHERE telegram_user_id=103').fetchone()[0]
            c.execute("INSERT INTO admins(user_id,role) VALUES (%s,'admin')",(uid,))
        self.change(row,'confirm')
        bot=await self.deliver()
        self.assertEqual({call.kwargs['chat_id'] for call in bot.send_message.call_args_list},{102,103})

    async def test_owner_in_client_flow_cannot_use_admin_privilege(self):
        row=self.create()
        with self.connection(self.settings) as c:
            c.execute("INSERT INTO admins(user_id,role) VALUES (%s,'owner')",(self.client,))
        row=self.change(row,'confirm')
        with self.assertRaises(bookings.AccessDenied):
            self.change(row,'cancel',actor=102,origin='client')

    async def test_disabled_admin_is_not_notified(self):
        row=self.create()
        self.c.execute('UPDATE admins SET active=false')
        self.c.commit()
        self.change(row,'cancel',actor=102,origin='client')
        bot=await self.deliver()
        bot.send_message.assert_not_awaited()
