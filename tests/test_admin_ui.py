"""Telegram handlers exercised against isolated PostgreSQL, without Telegram requests."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from datetime import timedelta

import test_bookings as fixtures
from app import admin_ui
from app.database import connect_database


@unittest.skipUnless(os.getenv('RUN_DB_TESTS')=='1','Set RUN_DB_TESTS=1')
class AdminUITests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.BookingTests.setUp
    use_schema=fixtures.BookingTests.use_schema
    drop_schema=fixtures.BookingTests.drop_schema

    def context(self):
        return SimpleNamespace(bot_data={'settings':self.settings},user_data={})

    def update(self, *, actor=101, text=None, data=None):
        message=SimpleNamespace(text=text,reply_text=AsyncMock())
        return SimpleNamespace(effective_user=SimpleNamespace(id=actor,full_name='Admin',username=None,language_code='ru'),
            effective_chat=SimpleNamespace(id=actor,type='private'),effective_message=message,
            callback_query=SimpleNamespace(data=data,answer=AsyncMock()) if data else None)

    def connection(self,settings):
        connection=connect_database(settings)
        self.use_schema(connection)
        return connection

    async def build_draft(self,context,kind='booking'):
        await admin_ui.callback(self.update(data=f'adm:new:{kind}'),context)
        for value in (admin_ui.fmt(self.start),admin_ui.fmt(self.end)):
            await admin_ui.text_input(self.update(text=value),context)
        if kind=='booking':
            await admin_ui.text_input(self.update(text='102'),context)
            await admin_ui.text_input(self.update(text='100000 KRW'),context)
        return context.user_data['admin_draft']

    async def test_full_creation_confirmation_move_and_cancel(self):
        context=self.context()
        with patch('app.admin_ui.connect_database',side_effect=self.connection):
            data=await self.build_draft(context)
            self.assertEqual(data['step'],'review')
            await admin_ui.callback(self.update(data='adm:apply:'+data['token']),context)
            row=data['result']
            self.assertEqual(row['status'],'pending')
            self.assertEqual(row['user_id'],self.client)
            await admin_ui.callback(self.update(data=f"adm:ask:confirm:{row['booking_id']}:{row['version']}"),context)
            data=context.user_data['admin_draft']
            await admin_ui.callback(self.update(data='adm:apply:'+data['token']),context)
            row=data['result']
            self.assertEqual(row['status'],'confirmed')
            await admin_ui.callback(self.update(data=f"adm:ask:move:{row['booking_id']}:{row['version']}"),context)
            await admin_ui.text_input(self.update(text=admin_ui.fmt(self.start+timedelta(days=1))),context)
            await admin_ui.text_input(self.update(text=admin_ui.fmt(self.end+timedelta(days=1))),context)
            data=context.user_data['admin_draft']
            await admin_ui.callback(self.update(data='adm:apply:'+data['token']),context)
            moved=data['result']
            self.assertEqual(moved['starts_at'].date(),(row['starts_at']+timedelta(days=1)).date())
            await admin_ui.callback(self.update(data=f"adm:ask:cancel:{moved['booking_id']}:{moved['version']}"),context)
            data=context.user_data['admin_draft']
            await admin_ui.callback(self.update(data='adm:apply:'+data['token']),context)
            self.assertEqual(data['result']['status'],'cancelled')

    async def test_repeated_apply_does_not_duplicate_booking(self):
        context=self.context()
        with patch('app.admin_ui.connect_database',side_effect=self.connection):
            data=await self.build_draft(context)
            for _ in range(2):
                await admin_ui.callback(self.update(data='adm:apply:'+data['token']),context)
        self.assertEqual(self.c.execute('SELECT count(*) FROM bookings').fetchone()[0],1)

    async def pick_datetime(self,context,data,stage,value):
        day=value.astimezone(admin_ui.SEOUL).date().isoformat()
        hour=value.astimezone(admin_ui.SEOUL).hour
        await admin_ui.callback(self.update(data=f"adm:cal:{data['token']}:{stage}:day:{day}"),context)
        await admin_ui.callback(self.update(data=f"adm:hour:{data['token']}:{stage}:{day}:{hour}"),context)

    async def test_calendar_creation_move_and_block_across_midnight(self):
        context=self.context()
        start=self.start.astimezone(admin_ui.SEOUL).replace(hour=23)
        end=start+timedelta(hours=5)
        with patch('app.admin_ui.connect_database',side_effect=self.connection):
            await admin_ui.callback(self.update(data='adm:new:booking'),context)
            draft=context.user_data['admin_draft']
            await self.pick_datetime(context,draft,'start',start)
            await self.pick_datetime(context,draft,'end',end)
            self.assertEqual(draft['step'],'client')
            await admin_ui.text_input(self.update(text='102'),context)
            await admin_ui.text_input(self.update(text='100000 KRW'),context)
            await admin_ui.callback(self.update(data='adm:apply:'+draft['token']),context)
            row=draft['result']
            self.assertEqual(row['ends_at'],end)
            await admin_ui.callback(self.update(data=f"adm:ask:move:{row['booking_id']}:{row['version']}"),context)
            draft=context.user_data['admin_draft']
            await self.pick_datetime(context,draft,'start',start+timedelta(days=1))
            await self.pick_datetime(context,draft,'end',end+timedelta(days=1))
            await admin_ui.callback(self.update(data='adm:apply:'+draft['token']),context)
            self.assertEqual(draft['result']['starts_at'],start+timedelta(days=1))
            await admin_ui.callback(self.update(data='adm:new:block'),context)
            draft=context.user_data['admin_draft']
            await self.pick_datetime(context,draft,'start',start+timedelta(days=2))
            await self.pick_datetime(context,draft,'end',start+timedelta(days=2,hours=1))
            self.assertEqual(draft['step'],'review')
            await admin_ui.callback(self.update(data='adm:apply:'+draft['token']),context)
            self.assertEqual(draft['result']['type'],'block')

    async def test_calendar_rejects_old_steps_short_rental_and_old_draft(self):
        context=self.context()
        with patch('app.admin_ui.connect_database',side_effect=self.connection):
            await admin_ui.callback(self.update(data='adm:new:booking'),context)
            draft=context.user_data['admin_draft']
            await self.pick_datetime(context,draft,'start',self.start)
            await self.pick_datetime(context,draft,'start',self.start+timedelta(hours=1))
            self.assertEqual(draft['start'],self.start)
            await self.pick_datetime(context,draft,'end',self.start+timedelta(hours=1))
            self.assertEqual(draft['step'],'end')
            await admin_ui.callback(self.update(data='adm:new:block'),context)
            await self.pick_datetime(context,draft,'start',self.start)
            self.assertNotIn('start',context.user_data['admin_draft'])

    async def test_block_can_be_found_and_removed_after_other_actions(self):
        context=self.context()
        with patch('app.admin_ui.connect_database',side_effect=self.connection):
            data=await self.build_draft(context,kind='block')
            await admin_ui.callback(self.update(data='adm:apply:'+data['token']),context)
            row=data['result']
            self.assertEqual(row['calendar_sync']['status'],'pending')
            await admin_ui.callback(self.update(data='adm:new:booking'),context)
            update=self.update(data='adm:list:blocks:0')
            await admin_ui.callback(update,context)
            markup=update.effective_message.reply_text.call_args.kwargs['reply_markup']
            self.assertIn(f"adm:view:{row['booking_id']}",[b.callback_data for line in markup.inline_keyboard for b in line])
            await admin_ui.callback(self.update(data=f"adm:ask:cancel:{row['booking_id']}:{row['version']}"),context)
            await admin_ui.callback(self.update(data='adm:apply:'+context.user_data['admin_draft']['token']),context)
            update=self.update(data='adm:list:blocks:0')
            await admin_ui.callback(update,context)
            markup=update.effective_message.reply_text.call_args.kwargs['reply_markup']
            self.assertNotIn(f"adm:view:{row['booking_id']}",[b.callback_data for line in markup.inline_keyboard for b in line])
        self.assertEqual(self.c.execute('SELECT status FROM bookings WHERE booking_id=%s',(row['booking_id'],)).fetchone()[0],'cancelled')

    async def test_revoked_rights_block_final_apply(self):
        context=self.context()
        with patch('app.admin_ui.connect_database',side_effect=self.connection):
            data=await self.build_draft(context)
            self.c.execute('UPDATE admins SET active=false')
            self.c.commit()
            update=self.update(data='adm:apply:'+data['token'])
            await admin_ui.callback(update,context)
        self.assertIn('Доступ запрещён',update.effective_message.reply_text.call_args.args[0])
        self.assertEqual(self.c.execute('SELECT count(*) FROM bookings').fetchone()[0],0)

    async def test_client_and_operator_cannot_forge_buttons(self):
        with patch('app.admin_ui.connect_database',side_effect=self.connection):
            for actor in (102,104):
                context=self.context()
                update=self.update(actor=actor,data='adm:new:block')
                await admin_ui.callback(update,context)
                self.assertNotIn('admin_draft',context.user_data)
                self.assertIn('Доступ запрещён',update.effective_message.reply_text.call_args.args[0])

    async def test_block_invalid_end_retry_and_stale_button(self):
        context=self.context()
        with patch('app.admin_ui.connect_database',side_effect=self.connection):
            await admin_ui.callback(self.update(data='adm:new:block'),context)
            await admin_ui.text_input(self.update(text=admin_ui.fmt(self.start)),context)
            await admin_ui.text_input(self.update(text=admin_ui.fmt(self.start-timedelta(hours=1))),context)
            self.assertEqual(context.user_data['admin_draft']['step'],'end')
            await admin_ui.text_input(self.update(text=admin_ui.fmt(self.end)),context)
            data=context.user_data['admin_draft']
            await admin_ui.callback(self.update(data='adm:apply:'+data['token']),context)
            row=data['result']
            self.assertEqual((row['type'],row['status'],row['amount']),('block','confirmed',0))
            self.c.execute("UPDATE bookings SET unit='USD' WHERE booking_id=%s",(row['booking_id'],))
            self.c.commit()
            update=self.update(data=f"adm:ask:cancel:{row['booking_id']}:{row['version']}")
            await admin_ui.callback(update,context)
            self.assertIn('Запись изменилась',update.effective_message.reply_text.call_args.args[0])

    async def test_restart_or_replaced_draft_rejects_old_apply(self):
        context=self.context()
        with patch('app.admin_ui.connect_database',side_effect=self.connection):
            update=self.update(data='adm:apply:oldtoken')
            await admin_ui.callback(update,context)
        self.assertIn('Черновик устарел',update.effective_message.reply_text.call_args.args[0])

    def test_seoul_parsing_and_card(self):
        value=admin_ui.parse_date('2030-11-08 01:00')
        self.assertEqual(value.utcoffset(),timedelta(hours=9))
        self.assertEqual(admin_ui.fmt(value),'2030-11-08 01:00')
