import os
import unittest
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from uuid import uuid4

import test_bookings as fixtures
from app import bookings,client_ui,notifications,admin_ui
from app.cancellation_rules import calculate,policy_text
from app.database import connect_database


class CancellationRuleTests(unittest.TestCase):
    def row(self,deposit='50000',balance='100000'):
        return dict(starts_at=datetime(2030,1,5,tzinfo=timezone.utc),unit='KRW',
            deposit_paid_amount=Decimal(deposit),balance_paid_amount=Decimal(balance),
            cancellation=dict(policy=dict(refund_cutoff_hours=72,deposit_amount='50000',unit='KRW')))

    def test_exact_72_hours_refunds_all_but_late_only_withholds_deposit(self):
        row=self.row()
        cutoff=row['starts_at']-timedelta(hours=72)
        self.assertEqual(Decimal(calculate(row,cutoff)['refund_amount']),150000)
        quote=calculate(row,cutoff+timedelta(microseconds=1))
        self.assertEqual(Decimal(quote['refund_amount']),100000)
        self.assertEqual(Decimal(quote['withheld_amount']),50000)

    def test_withholding_never_exceeds_received_deposit_or_agreed_cap(self):
        for deposit,withheld,refund in (('20000',20000,100000),('70000',50000,120000),('0',0,100000)):
            row=self.row(deposit)
            quote=calculate(row,row['starts_at']-timedelta(hours=1))
            self.assertEqual(Decimal(quote['withheld_amount']),withheld)
            self.assertEqual(Decimal(quote['refund_amount']),refund)

    def test_snapshot_and_ru_ko_terms_do_not_change_with_current_config(self):
        row=self.row()
        with patch('app.cancellation_rules.load_business_config',side_effect=AssertionError('must use snapshot')):
            self.assertEqual(calculate(row,row['starts_at']-timedelta(hours=73))['policy'],row['cancellation']['policy'])
        for lang in ('ru','ko'):
            text=policy_text(row['cancellation']['policy'],lang)
            self.assertIn('72',text)
            self.assertIn('50000',text)


@unittest.skipUnless(os.getenv('RUN_DB_TESTS')=='1','Set RUN_DB_TESTS=1')
class CancellationDatabaseTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.BookingTests.setUp
    use_schema=fixtures.BookingTests.use_schema
    drop_schema=fixtures.BookingTests.drop_schema

    def connection(self,settings):
        c=connect_database(settings)
        self.use_schema(c)
        return c

    def create(self,late=False):
        start=self.start if not late else (datetime.now(timezone.utc)+timedelta(days=2)).replace(minute=0,second=0,microsecond=0)
        with self.connection(self.settings) as c:
            row=bookings.create_booking(c,101,user_id=self.client,starts_at=start,ends_at=start+timedelta(hours=5),amount='150000',request_key=uuid4())
            row=bookings.change_booking(c,101,row['booking_id'],action='confirm',expected_version=row['version'])
            c.execute('UPDATE bookings SET deposit_paid_amount=50000,balance_paid_amount=100000 WHERE booking_id=%s',(row['booking_id'],))
            return bookings.get_booking(c,101,row['booking_id'])

    def cancel(self,row):
        with self.connection(self.settings) as c:
            quote=bookings.quote_cancellation(c,102,row['booking_id'],expected_version=row['version'])
            return bookings.change_booking(c,102,row['booking_id'],action='cancel',origin='client',expected_version=row['version'],expected_cancellation=quote)

    async def deliver(self):
        bot=SimpleNamespace(send_message=AsyncMock())
        with patch('app.notifications.connect_database',side_effect=self.connection):
            await notifications.deliver_pending(bot,self.settings)
        return bot

    def update(self,data):
        return SimpleNamespace(effective_user=SimpleNamespace(id=102,full_name='Client',username=None,language_code='ru'),
            effective_chat=SimpleNamespace(id=102,type='private'),effective_message=SimpleNamespace(reply_text=AsyncMock()),
            callback_query=SimpleNamespace(data=data,answer=AsyncMock()))

    async def test_client_cancel_frees_slot_keeps_payments_and_notifies_both(self):
        row=self.cancel(self.create(late=True))
        self.assertEqual(row['status'],'cancelled')
        self.assertEqual(row['deposit_paid_amount'],50000)
        self.assertEqual(row['balance_paid_amount'],100000)
        self.assertEqual(Decimal(row['cancellation']['quote']['refund_amount']),100000)
        self.assertTrue(bookings.is_available(self.c,102,row['starts_at'],row['ends_at']))
        bot=await self.deliver()
        self.assertEqual({c.kwargs['chat_id'] for c in bot.send_message.call_args_list},{101,102})
        self.assertTrue(all('100000' in c.kwargs['text'] and '50000' in c.kwargs['text'] for c in bot.send_message.call_args_list))

    async def test_refund_list_and_admin_completion_are_separate_and_durable(self):
        row=self.cancel(self.create())
        await self.deliver()
        self.assertEqual(Decimal(row['cancellation']['quote']['refund_amount']),150000)
        self.assertEqual(len(bookings.list_bookings(self.c,101,refunds_only=True)),1)
        self.assertEqual(bookings.list_bookings(self.c,101),[])
        with self.connection(self.settings) as c:
            with self.assertRaises(bookings.AccessDenied):
                bookings.complete_refund(c,102,row['booking_id'],expected_version=row['version'])
            done=bookings.complete_refund(c,101,row['booking_id'],expected_version=row['version'])
            again=bookings.complete_refund(c,101,row['booking_id'],expected_version=done['version'])
            self.assertEqual(done['version'],again['version'])
        self.assertEqual(done['cancellation']['refund_status'],'completed')
        bot=await self.deliver()
        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.call_args.kwargs['chat_id'],102)
        self.assertIn('Возврат выполнен',bot.send_message.call_args.kwargs['text'])

    async def test_preview_requires_confirmation_and_rechecks_72_hour_boundary(self):
        row=self.create()
        cutoff=row['starts_at']-timedelta(hours=72)
        context=SimpleNamespace(bot_data={'settings':self.settings},user_data={})
        with patch('app.client_ui.connect_database',side_effect=self.connection):
            with patch('app.bookings._now',return_value=cutoff-timedelta(seconds=1)):
                await client_ui.callback(self.update(f"cli:cancel:{row['booking_id']}:{row['version']}"),context)
            token=context.user_data['client_cancel']['token']
            self.assertEqual(bookings.get_booking(self.c,101,row['booking_id'])['status'],'confirmed')
            with patch('app.bookings._now',return_value=cutoff+timedelta(seconds=1)):
                update=self.update('cli:cancel_yes:'+token)
                await client_ui.callback(update,context)
                self.assertIn('Условия изменились',update.effective_message.reply_text.call_args.args[0])
                self.assertNotEqual(context.user_data['client_cancel']['token'],token)
                self.assertEqual(bookings.get_booking(self.c,101,row['booking_id'])['status'],'confirmed')
                await client_ui.callback(self.update('cli:cancel_yes:'+context.user_data['client_cancel']['token']),context)
        current=bookings.get_booking(self.c,101,row['booking_id'])
        self.assertEqual(current['status'],'cancelled')
        self.assertEqual(Decimal(current['cancellation']['quote']['refund_amount']),100000)

    async def test_foreign_booking_missing_consent_and_started_rental_are_rejected(self):
        row=self.create()
        with self.connection(self.settings) as c:
            for actor in (101,103,104):
                with self.assertRaises(bookings.AccessDenied):
                    bookings.quote_cancellation(c,actor,row['booking_id'])
            with self.assertRaises(bookings.AccessDenied):
                bookings.change_booking(c,102,row['booking_id'],action='cancel',origin='client',expected_version=row['version'])
            with patch('app.bookings._now',return_value=row['starts_at']):
                with self.assertRaises(bookings.BookingError):
                    bookings.quote_cancellation(c,102,row['booking_id'])

    async def test_new_booking_displays_and_saves_cancellation_terms(self):
        row=self.create()
        for lang in ('ru','ko'):
            text=client_ui.card(row,lang)
            self.assertIn('72',text)
            self.assertIn('50000',text)
        self.assertTrue(any(b.callback_data.startswith('cli:cancel:') for line in client_ui.controls(row,'ru').inline_keyboard for b in line))

    async def test_policy_change_requires_new_booking_consent(self):
        config=bookings.load_business_config().model_copy(deep=True)
        policy=dict(refund_cutoff_hours=72,deposit_amount='50000',unit='KRW')
        quote=bookings.quote_rental(self.start,self.end)
        config.cancellation.refund_cutoff_hours=48
        with self.connection(self.settings) as c,patch('app.bookings.load_business_config',return_value=config):
            with self.assertRaises(bookings.BookingError):
                bookings.create_client_booking(c,102,details=dict(contact_phone='01022636263',guests_count=40,event_name='Level up'),starts_at=self.start,ends_at=self.end,
                    request_key=uuid4(),expected_quote=quote,expected_policy=policy)
        self.assertEqual(self.c.execute('SELECT count(*) FROM bookings').fetchone()[0],0)

    async def test_admin_refund_button_requires_final_confirmation(self):
        row=self.cancel(self.create())
        context=SimpleNamespace(bot_data={'settings':self.settings},user_data={})
        def update(data):
            u=self.update(data)
            u.effective_user.id=101
            u.effective_chat.id=101
            return u
        with patch('app.admin_ui.connect_database',side_effect=self.connection):
            await admin_ui.callback(update('adm:list:refunds:0'),context)
            await admin_ui.callback(update(f"adm:ask:refund:{row['booking_id']}:{row['version']}"),context)
            self.assertEqual(bookings.get_booking(self.c,101,row['booking_id'])['cancellation']['refund_status'],'pending')
            await admin_ui.callback(update('adm:apply:'+context.user_data['admin_draft']['token']),context)
        self.assertEqual(bookings.get_booking(self.c,101,row['booking_id'])['cancellation']['refund_status'],'completed')
