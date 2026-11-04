import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.bot import admin_menu, select_language
from app.users import Client


class BotUserTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_denies_normal_client(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_message=message)
        context = SimpleNamespace(bot_data={'settings': object()})
        with patch('app.bot.current_client', AsyncMock(return_value=Client(1, 101, 'ru'))), patch('app.bot.connect_database'), patch('app.bot.admin_role', return_value=None):
            await admin_menu(update, context)
        message.reply_text.assert_awaited_once_with('Доступ запрещён.')

    async def test_operator_gets_role_without_booking_actions(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_message=message)
        context = SimpleNamespace(bot_data={'settings': object()})
        with patch('app.bot.current_client', AsyncMock(return_value=Client(1, 101, 'ko'))), patch('app.bot.connect_database'), patch('app.bot.admin_role', return_value='operator'):
            await admin_menu(update, context)
        self.assertIn('operator', message.reply_text.call_args.args[0])

    async def test_language_button_persists_even_when_text_already_matches(self):
        from app.bot import WELCOME
        query = SimpleNamespace(data='language:ko', answer=AsyncMock(), edit_message_text=AsyncMock(), message=SimpleNamespace(text=WELCOME['ko']))
        update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(type='private'))
        context = SimpleNamespace(bot_data={'settings': object()})
        with patch('app.bot.current_client', AsyncMock(return_value=Client(1, 101, 'ru'))), patch('app.bot.connect_database'), patch('app.bot.set_language') as save:
            await select_language(update, context)
        self.assertEqual(save.call_args.args[1:], (101, 'ko'))
        query.edit_message_text.assert_not_awaited()
