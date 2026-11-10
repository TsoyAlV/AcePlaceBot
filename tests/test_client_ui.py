import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from datetime import timedelta

import test_bookings as fixtures
import app.bookings as bookings
from app import client_ui,notifications
from app.database import connect_database


@unittest.skipUnless(os.getenv('RUN_DB_TESTS')=='1','Set RUN_DB_TESTS=1')
class ClientUITests(unittest.IsolatedAsyncioTestCase):
    use_schema=fixtures.BookingTests.use_schema
    drop_schema=fixtures.BookingTests.drop_schema

    def setUp(self):
        fixtures.BookingTests.setUp(self)
        patcher=patch('app.availability.load_business_config',return_value=bookings.load_business_config())
        patcher.start()
        self.addCleanup(patcher.stop)

    def connection(self,settings):
        c=connect_database(settings)
        self.use_schema(c)
        return c

    def context(self):
        return SimpleNamespace(bot_data={'settings':self.settings},user_data={})

    def update(self,*,actor=102,text=None,data=None):
        message=SimpleNamespace(text=text,reply_text=AsyncMock())
        return SimpleNamespace(effective_user=SimpleNamespace(id=actor,full_name='Customer',username=None,language_code='ru'),
            effective_chat=SimpleNamespace(id=actor,type='private'),effective_message=message,
            callback_query=SimpleNamespace(data=data,answer=AsyncMock()) if data else None)

    async def review(self,context,answers=True):
        await client_ui.callback(self.update(data='cli:new'),context)
        await client_ui.text_input(self.update(text=self.start.astimezone(client_ui.SEOUL).strftime('%Y-%m-%d')),context)
        await client_ui.text_input(self.update(text='5'),context)
        draft=context.user_data['client_draft']
        index=next(i for i,row in enumerate(draft['options']) if row['starts_at']==self.start.astimezone(client_ui.SEOUL).isoformat())
        await client_ui.callback(self.update(data=f"cli:pick:{draft['token']}:{index}"),context)
        if not answers:
            return draft
        await client_ui.text_input(self.update(text='00000000000'),context)
        await client_ui.text_input(self.update(text='40'),context)
        await client_ui.text_input(self.update(text='Level up'),context)
        return draft

    async def test_complete_flow_idempotent_creation_and_withdrawal(self):
        context=self.context()
        with patch('app.client_ui.connect_database',side_effect=self.connection):
            draft=await self.review(context)
            self.assertEqual(draft['step'],'review')
            self.assertEqual(self.c.execute('SELECT count(*) FROM bookings').fetchone()[0],0)
            for _ in range(2):
                await client_ui.callback(self.update(data='cli:send:'+draft['token']),context)
            row=bookings.get_booking(self.c,102,draft['result_id'])
            self.assertEqual(row['user_id'],self.client)
            self.assertEqual(row['status'],'pending')
            self.assertEqual(row['amount'],draft['quote']['amount'])
            self.assertEqual((row['contact_phone'],row['guests_count'],row['event_name']),('00000000000',40,'Level up'))
            self.assertEqual(len(row['admin_notifications']),1)
            await client_ui.callback(self.update(data=f"cli:withdraw_yes:{row['booking_id']}:{row['version']}"),context)
            self.assertEqual(bookings.get_booking(self.c,102,row['booking_id'])['status'],'cancelled')

    async def test_questions_are_required_and_invalid_answers_do_not_advance(self):
        context=self.context()
        with patch('app.client_ui.connect_database',side_effect=self.connection):
            draft=await self.review(context,answers=False)
            self.assertEqual(draft['step'],'phone')
            self.assertTrue(context.user_data['_ui_reply_keyboard'])
            await client_ui.callback(self.update(data='cli:send:'+draft['token']),context)
            self.assertEqual(self.c.execute('SELECT count(*) FROM bookings').fetchone()[0],0)
            await client_ui.text_input(self.update(text='not a phone'),context)
            self.assertEqual(draft['step'],'phone')
            contact=self.update()
            contact.effective_message.contact=SimpleNamespace(user_id=103,phone_number='00000000000')
            await client_ui.contact_input(contact,context)
            self.assertEqual(draft['step'],'phone')
            contact.effective_message.contact.user_id=102
            await client_ui.contact_input(contact,context)
            self.assertEqual(draft['step'],'guests')
            self.assertFalse(context.user_data['_ui_reply_keyboard'])
            await client_ui.text_input(self.update(text='0'),context)
            self.assertEqual(draft['step'],'guests')
            await client_ui.text_input(self.update(text='40'),context)
            await client_ui.text_input(self.update(text='   '),context)
            self.assertEqual(draft['step'],'event')

    async def test_edit_answers_invalidates_old_send_button_and_saves_new_answers(self):
        context=self.context()
        with patch('app.client_ui.connect_database',side_effect=self.connection):
            draft=await self.review(context)
            old=draft['token']
            await client_ui.callback(self.update(data='cli:edit:'+old),context)
            self.assertEqual(draft['step'],'phone')
            self.assertNotEqual(draft['token'],old)
            for answer in ('+00 00 0000 0000','25','День рождения'):
                await client_ui.text_input(self.update(text=answer),context)
            await client_ui.callback(self.update(data='cli:send:'+old),context)
            self.assertEqual(self.c.execute('SELECT count(*) FROM bookings').fetchone()[0],0)
            await client_ui.callback(self.update(data='cli:send:'+draft['token']),context)
            row=bookings.get_booking(self.c,102,draft['result_id'])
            self.assertEqual((row['contact_phone'],row['guests_count'],row['event_name']),('+000000000000',25,'День рождения'))

    async def test_cancel_questionnaire_removes_contact_keyboard(self):
        context=self.context()
        with patch('app.client_ui.connect_database',side_effect=self.connection):
            draft=await self.review(context,answers=False)
            await client_ui.callback(self.update(data='cli:abort:'+draft['token']),context)
            self.assertNotIn('client_draft',context.user_data)
            self.assertFalse(context.user_data['_ui_reply_keyboard'])

    async def test_navigation_preserves_created_booking_and_bank_details(self):
        context=self.context()
        context.bot=SimpleNamespace(edit_message_text=AsyncMock(),delete_message=AsyncMock(),edit_message_reply_markup=AsyncMock())
        original_update=self.update
        sent=[]
        def tracked_update(**kwargs):
            update=original_update(**kwargs)
            update.effective_message.message_id=42
            update.effective_message.reply_text.return_value=SimpleNamespace(message_id=200+len(sent))
            sent.append(update)
            return update
        with patch.object(self,'update',side_effect=tracked_update),patch('app.client_ui.connect_database',side_effect=self.connection):
            draft=await self.review(context)
            summary_ref=context.user_data['_ui_screen'][:]
            await client_ui.callback(self.update(data='cli:send:'+draft['token']),context)
            summary=context.bot.edit_message_text.call_args.kwargs['text']
            self.assertIn(str(draft['result_id']),summary)
            self.assertIn(draft['payment'][1],summary)
            self.assertNotIn('_ui_screen',context.user_data)
            edits=context.bot.edit_message_text.await_count
            await client_ui.callback(self.update(data='cli:mine:0'),context)
            await client_ui.callback(self.update(data='cli:send:'+draft['token']),context)
            self.assertEqual(context.bot.edit_message_text.await_count,edits)
            self.assertNotIn(summary_ref[1],[call.kwargs['message_id'] for call in context.bot.delete_message.call_args_list])
            self.assertEqual(self.c.execute('SELECT count(*) FROM bookings').fetchone()[0],1)

    async def test_competing_booking_wins_before_send(self):
        context=self.context()
        with patch('app.client_ui.connect_database',side_effect=self.connection):
            draft=await self.review(context)
            with self.connection(self.settings) as c:
                bookings.create_booking(c,101,user_id=None,starts_at=draft['start'],ends_at=draft['end'],amount='150000',request_key=__import__('uuid').uuid4())
            update=self.update(data='cli:send:'+draft['token'])
            await client_ui.callback(update,context)
            self.assertIn('уже заняли',update.effective_message.reply_text.call_args.args[0])
            self.assertEqual(self.c.execute('SELECT count(*) FROM bookings WHERE user_id=%s',(self.client,)).fetchone()[0],0)

    async def test_calendar_and_duration_buttons_use_existing_availability(self):
        context=self.context()
        with patch('app.client_ui.connect_database',side_effect=self.connection):
            await client_ui.callback(self.update(data='cli:new'),context)
            draft=context.user_data['client_draft']
            day=self.start.astimezone(client_ui.SEOUL).date().isoformat()
            await client_ui.callback(self.update(data=f"cli:cal:{draft['token']}:month:{day[:7]}"),context)
            await client_ui.callback(self.update(data=f"cli:cal:{draft['token']}:day:{day}"),context)
            self.assertEqual(draft['step'],'hours')
            await client_ui.callback(self.update(data=f"cli:hours:{draft['token']}:5"),context)
            self.assertEqual(draft['step'],'options')
            index=next(i for i,row in enumerate(draft['options']) if row['starts_at']==self.start.astimezone(client_ui.SEOUL).isoformat())
            await client_ui.callback(self.update(data=f"cli:pick:{draft['token']}:{index}"),context)
            self.assertEqual(draft['step'],'phone')
            for answer in ('00000000000','40','Level up'):
                await client_ui.text_input(self.update(text=answer),context)
            self.assertEqual(draft['step'],'review')
            self.assertEqual(self.c.execute('SELECT count(*) FROM bookings').fetchone()[0],0)
            await client_ui.callback(self.update(data='cli:send:'+draft['token']),context)
            self.assertEqual(self.c.execute('SELECT count(*) FROM bookings').fetchone()[0],1)

    async def test_calendar_rejects_old_token_and_past_date(self):
        context=self.context()
        with patch('app.client_ui.connect_database',side_effect=self.connection):
            await client_ui.callback(self.update(data='cli:new'),context)
            old=context.user_data['client_draft']['token']
            await client_ui.callback(self.update(data='cli:new'),context)
            draft=context.user_data['client_draft']
            day=self.start.astimezone(client_ui.SEOUL).date().isoformat()
            await client_ui.callback(self.update(data=f'cli:cal:{old}:day:{day}'),context)
            self.assertEqual(draft['step'],'date')
            await client_ui.callback(self.update(data=f"cli:cal:{draft['token']}:day:2020-01-01"),context)
            self.assertEqual(draft['step'],'date')

    async def test_other_user_and_admin_client_menu_cannot_access_foreign_booking(self):
        context=self.context()
        with patch('app.client_ui.connect_database',side_effect=self.connection):
            draft=await self.review(context)
            await client_ui.callback(self.update(data='cli:send:'+draft['token']),context)
            for actor in (103,101):
                update=self.update(actor=actor,data=f"cli:view:{draft['result_id']}")
                await client_ui.callback(update,self.context())
                self.assertNotIn('Ace Place · #',update.effective_message.reply_text.call_args.args[0])

    async def test_client_cannot_cancel_confirmed_booking_even_if_owner(self):
        context=self.context()
        with patch('app.client_ui.connect_database',side_effect=self.connection):
            draft=await self.review(context)
            await client_ui.callback(self.update(data='cli:send:'+draft['token']),context)
            with self.connection(self.settings) as c:
                row=bookings.get_booking(c,101,draft['result_id'])
                row=bookings.change_booking(c,101,row['booking_id'],action='confirm',expected_version=row['version'])
                c.execute("INSERT INTO admins(user_id,role) VALUES (%s,'owner')",(self.client,))
            await client_ui.callback(self.update(data=f"cli:withdraw_yes:{row['booking_id']}:{row['version']}"),context)
            self.assertEqual(bookings.get_booking(self.c,102,row['booking_id'])['status'],'confirmed')

    async def test_quote_change_requires_new_consent(self):
        context=self.context()
        with patch('app.client_ui.connect_database',side_effect=self.connection):
            draft=await self.review(context)
            config=bookings.load_business_config().model_copy(deep=True)
            config.pricing.weekday_base_amount+=10000
            config.pricing.weekend_holiday_base_amount+=10000
            with patch('app.bookings.load_business_config',return_value=config):
                await client_ui.callback(self.update(data='cli:send:'+draft['token']),context)
            self.assertEqual(self.c.execute('SELECT count(*) FROM bookings').fetchone()[0],0)

    async def test_second_pending_request_is_refused(self):
        context=self.context()
        with patch('app.client_ui.connect_database',side_effect=self.connection):
            draft=await self.review(context)
            await client_ui.callback(self.update(data='cli:send:'+draft['token']),context)
        with self.connection(self.settings) as c:
            start=self.start+timedelta(days=1)
            end=start+timedelta(hours=5)
            with self.assertRaises(bookings.BookingError):
                bookings.create_client_booking(c,102,starts_at=start,ends_at=end,
                    request_key=__import__('uuid').uuid4(),expected_quote=bookings.quote_rental(start,end))

    async def test_notification_survives_context_loss_and_does_not_invalidate_version(self):
        context=self.context()
        with patch('app.client_ui.connect_database',side_effect=self.connection):
            draft=await self.review(context)
            await client_ui.callback(self.update(data='cli:send:'+draft['token']),context)
        row=bookings.get_booking(self.c,102,draft['result_id'])
        with patch('app.notifications.connect_database',side_effect=self.connection):
            bot=SimpleNamespace(send_message=AsyncMock())
            await notifications.deliver_pending(bot,self.settings)
            await notifications.deliver_pending(bot,self.settings)
        bot.send_message.assert_awaited_once()
        updated=bookings.get_booking(self.c,102,row['booking_id'])
        self.assertEqual(updated['version'],row['version'])
        self.assertEqual(next(iter(updated['admin_notifications'].values()))['status'],'sent')

    async def test_failed_notification_is_saved_for_retry(self):
        context=self.context()
        with patch('app.client_ui.connect_database',side_effect=self.connection):
            draft=await self.review(context)
            await client_ui.callback(self.update(data='cli:send:'+draft['token']),context)
        with patch('app.notifications.connect_database',side_effect=self.connection):
            await notifications.deliver_pending(SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError())),self.settings)
        row=bookings.get_booking(self.c,102,draft['result_id'])
        state=next(iter(row['admin_notifications'].values()))
        self.assertEqual((state['status'],state['attempts']),('pending',1))

    async def test_cannot_forge_unsupported_status_or_price(self):
        context=self.context()
        with patch('app.client_ui.connect_database',side_effect=self.connection):
            draft=await self.review(context)
            await client_ui.callback(self.update(data='cli:send:'+draft['token']),context)
            update=self.update(data=f"cli:confirm:{draft['result_id']}")
            await client_ui.callback(update,context)
        self.assertEqual(bookings.get_booking(self.c,102,draft['result_id'])['status'],'pending')
