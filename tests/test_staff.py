import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

import test_bookings as fixtures
from app import staff,staff_ui,admin_ui
from app.bookings import AccessDenied,BookingError,StaleBooking
from app.users import admin_role
from app.database import connect_database


@unittest.skipUnless(os.getenv('RUN_DB_TESTS')=='1','Set RUN_DB_TESTS=1')
class StaffTests(unittest.TestCase):
    setUp=fixtures.BookingTests.setUp
    use_schema=fixtures.BookingTests.use_schema
    drop_schema=fixtures.BookingTests.drop_schema

    def change(self,tg,field,value):
        row=staff.get_member(self.c,101,tg)
        return staff.update_member(self.c,101,tg,expected=staff.snapshot(row),field=field,value=value)

    def test_add_change_role_disable_and_reactivate(self):
        self.change(102,'role','admin')
        self.assertEqual(admin_role(self.c,102),'admin')
        self.change(102,'notifications_enabled',False)
        self.change(102,'role','operator')
        self.assertEqual(admin_role(self.c,102),'operator')
        self.change(102,'active',False)
        self.assertIsNone(admin_role(self.c,102))
        self.change(102,'active',True)
        self.assertEqual(admin_role(self.c,102),'operator')
        self.assertFalse(staff.get_member(self.c,101,102)['notifications_enabled'])

    def test_requires_registered_user(self):
        with self.assertRaises(BookingError):
            staff.get_member(self.c,101,999999)

    def test_non_owner_cannot_read_or_mutate_staff(self):
        self.change(102,'role','admin')
        target=staff.snapshot(staff.get_member(self.c,101,103))
        for actor in (102,103,104):
            with self.subTest(actor=actor):
                with self.assertRaises(AccessDenied):
                    staff.list_members(self.c,actor)
                with self.assertRaises(AccessDenied):
                    staff.update_member(self.c,actor,103,expected=target,field='role',value='admin')

    def test_cannot_disable_demote_or_create_owner(self):
        for tg,field,value in ((101,'active',False),(101,'role','admin'),(102,'role','owner')):
            with self.subTest(field=field,tg=tg),self.assertRaises(BookingError):
                self.change(tg,field,value)
        self.assertEqual(admin_role(self.c,101),'owner')
        self.change(101,'notifications_enabled',False)
        self.assertEqual(admin_role(self.c,101),'owner')

    def test_outdated_confirmation_does_not_overwrite_new_rights(self):
        self.change(102,'role','admin')
        expected=staff.snapshot(staff.get_member(self.c,101,102))
        self.change(102,'active',False)
        with self.assertRaises(StaleBooking):
            staff.update_member(self.c,101,102,expected=expected,field='role',value='operator')
        self.assertIsNone(admin_role(self.c,102))


@unittest.skipUnless(os.getenv('RUN_DB_TESTS')=='1','Set RUN_DB_TESTS=1')
class StaffUITests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.BookingTests.setUp
    use_schema=fixtures.BookingTests.use_schema
    drop_schema=fixtures.BookingTests.drop_schema

    def connection(self,settings):
        c=connect_database(settings)
        self.use_schema(c)
        return c

    def update(self,data=None,text=None,actor=101):
        message=SimpleNamespace(text=text,reply_text=AsyncMock())
        return SimpleNamespace(effective_user=SimpleNamespace(id=actor,full_name='Test',username=None,language_code='ru'),
            effective_chat=SimpleNamespace(id=actor,type='private'),effective_message=message,
            callback_query=SimpleNamespace(data=data,answer=AsyncMock()) if data else None)

    def context(self):
        return SimpleNamespace(bot_data={'settings':self.settings},user_data={})

    async def test_add_requires_final_confirmation_and_can_disable_later(self):
        context=self.context()
        with patch('app.admin_ui.connect_database',side_effect=self.connection):
            await staff_ui.callback(self.update(data='staff:add'),context)
            await staff_ui.text_input(self.update(text='102'),context)
            await staff_ui.callback(self.update(data='staff:ask:role:admin:102'),context)
            self.assertIsNone(admin_role(self.c,102))
            await staff_ui.callback(self.update(data='staff:apply:'+context.user_data['staff_draft']['token']),context)
            self.assertEqual(admin_role(self.c,102),'admin')
            await staff_ui.callback(self.update(data='staff:list:0'),context)
            await staff_ui.callback(self.update(data='staff:ask:active:0:102'),context)
            await staff_ui.callback(self.update(data='staff:apply:'+context.user_data['staff_draft']['token']),context)
            self.assertIsNone(admin_role(self.c,102))

    async def test_owner_rights_rechecked_before_apply(self):
        context=self.context()
        with patch('app.admin_ui.connect_database',side_effect=self.connection):
            await staff_ui.callback(self.update(data='staff:ask:role:admin:102'),context)
            token=context.user_data['staff_draft']['token']
            self.c.execute("UPDATE admins SET active=false WHERE role='owner'")
            self.c.commit()
            update=self.update(data='staff:apply:'+token)
            await staff_ui.callback(update,context)
            self.assertIn('Только для владельца',update.effective_message.reply_text.call_args.args[0])
            self.assertIsNone(admin_role(self.c,102))

    async def test_forged_callback_from_operator_is_denied(self):
        context=self.context()
        with patch('app.admin_ui.connect_database',side_effect=self.connection):
            update=self.update(data='staff:ask:role:admin:102',actor=104)
            await staff_ui.callback(update,context)
        self.assertIn('Только для владельца',update.effective_message.reply_text.call_args.args[0])
        self.assertNotIn('staff_draft',context.user_data)

    async def test_owner_controls_hidden_for_admin(self):
        self.assertIn('staff:list:0',str(admin_ui.menu('ru',owner=True)))
        self.assertNotIn('staff:list:0',str(admin_ui.menu('ru')))
