import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

from app import client_ui,admin_ui
from app.bookings import BookingError


class FlowRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def update(self,data=None,text=None):
        return SimpleNamespace(callback_query=SimpleNamespace(data=data,answer=AsyncMock()) if data else None,
                               effective_message=SimpleNamespace(text=text,reply_text=AsyncMock()))

    async def test_invalid_duration_explains_reason_and_keeps_current_step(self):
        for text,expected in (('4','Минимальная аренда'),('5ч','целым числом')):
            context=SimpleNamespace(user_data={'_language':'ru','client_draft':{'step':'hours'}})
            with patch('app.client_ui.call',new=AsyncMock(return_value='ru')), \
                 patch('app.client_ui.ui.error',new=AsyncMock()) as error:
                await client_ui.text_input(self.update(text=text),context)
            self.assertIn(expected,error.call_args.args[2])
            self.assertEqual(context.user_data['client_draft']['step'],'hours')

    async def test_old_calendar_button_preserves_newer_draft(self):
        draft=dict(token='current',step='date')
        context=SimpleNamespace(user_data={'_language':'ru','client_draft':draft})
        with patch('app.client_ui.call',new=AsyncMock(return_value='ru')), \
             patch('app.client_ui.ui.error',new=AsyncMock()) as error:
            await client_ui.callback(self.update('cli:cal:old:day:2027-01-15'),context)
        self.assertIn('предыдущему шагу',error.call_args.args[2])
        self.assertEqual(draft,dict(token='current',step='date'))

    async def test_removed_booking_error_is_specific_in_all_languages(self):
        for lang,expected in (('ru','уже удалена'),('ko','삭제되었습니다'),('en','already removed')):
            context=SimpleNamespace(user_data={'_language':lang})
            with patch('app.client_ui.ui.error',new=AsyncMock()) as error:
                await client_ui.show_error(None,BookingError('Booking removed from your list'),context)
            self.assertIn(expected,error.call_args.args[2])

    async def test_stale_admin_notification_opens_current_card_without_mutating(self):
        row=dict(booking_id=6,version=2)
        context=SimpleNamespace(user_data={'admin_draft':{'token':'old'}})
        async def call(update,context,operation):
            return operation(None,101,'ru')
        with patch('app.admin_ui.call',new=AsyncMock(side_effect=call)), \
             patch('app.admin_ui.get_booking',return_value=row), \
             patch('app.admin_ui.card',return_value='Current booking'), \
             patch('app.admin_ui.controls',return_value=None), \
             patch('app.admin_ui.change_booking') as change, \
             patch('app.admin_ui.ui.screen',new=AsyncMock()) as screen:
            await admin_ui.callback(self.update('adm:ask:confirm:6:1'),context)
        change.assert_not_called()
        self.assertNotIn('admin_draft',context.user_data)
        self.assertIn('Запись изменилась',screen.call_args.args[2])
        self.assertIn('Current booking',screen.call_args.args[2])
