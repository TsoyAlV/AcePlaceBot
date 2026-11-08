import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

from telegram import BotCommandScopeChat,MenuButtonCommands
from telegram.error import TimedOut
from app import command_menu
from app.bot import my_id,select_language,WELCOME
from app.users import Client


class CommandMenuTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        return SimpleNamespace(bot_data={'settings':object()},bot=SimpleNamespace(set_my_commands=AsyncMock(),set_chat_menu_button=AsyncMock()))

    async def test_menu_is_private_localized_and_admin_only_when_allowed(self):
        context=self.context()
        for role,admin in ((None,False),('operator',False),('admin',True),('owner',True)):
            with self.subTest(role=role):
                context.bot_data.clear()
                await command_menu.sync_profile(context,102,'ko',role)
                call=context.bot.set_my_commands.call_args
                values=call.args[0]
                self.assertEqual('admin' in [v.command for v in values],admin)
                self.assertIn('get_user_id',[v.command for v in values])
                self.assertEqual(values[0].description,'메인 메뉴')
                self.assertEqual(call.kwargs['scope'],BotCommandScopeChat(102))
                context.bot.set_chat_menu_button.assert_awaited_with(chat_id=102,menu_button=MenuButtonCommands())

    async def test_revoked_role_updates_commands_and_unchanged_profile_is_cached(self):
        context=self.context()
        await command_menu.sync_profile(context,102,'ru','admin')
        calls=context.bot.set_my_commands.await_count
        await command_menu.sync_profile(context,102,'ru','admin')
        self.assertEqual(context.bot.set_my_commands.await_count,calls)
        await command_menu.sync_profile(context,102,'ru',None)
        self.assertNotIn('admin',[c.command for c in context.bot.set_my_commands.call_args.args[0]])
        self.assertEqual(context.bot.set_my_commands.await_count,calls+3)

    async def test_network_failure_does_not_fail_action_and_is_retried(self):
        context=self.context()
        context.bot.set_my_commands.side_effect=TimedOut()
        with self.assertLogs('app.command_menu',level='WARNING'):
            await command_menu.sync_profile(context,102,'ru',None)
        self.assertNotIn(102,context.bot_data['command_menu_cache'])
        context.bot.set_my_commands.side_effect=None
        await command_menu.sync_profile(context,102,'ru',None)
        self.assertIn(102,context.bot_data['command_menu_cache'])

    async def test_startup_refreshes_existing_users_and_external_role_changes(self):
        context=self.context()
        with patch('app.command_menu.profiles',return_value=[(101,'ru','owner'),(102,'ko',None)]):
            await command_menu.reconcile(context)
        self.assertTrue(context.bot_data['command_menu_defaults'])
        with patch('app.command_menu.profiles',return_value=[(101,'ru',None),(102,'ko',None)]):
            await command_menu.reconcile(context)
        call=context.bot.set_my_commands.call_args
        self.assertEqual(call.kwargs['scope'],BotCommandScopeChat(101))
        self.assertNotIn('admin',[v.command for v in call.args[0]])

    async def test_language_change_refreshes_menu_even_if_welcome_already_matches(self):
        context=self.context()
        query=SimpleNamespace(data='language:ko',answer=AsyncMock(),message=SimpleNamespace(text=WELCOME['ko']))
        update=SimpleNamespace(callback_query=query,effective_chat=SimpleNamespace(id=102,type='private'))
        with patch('app.bot.current_client',new=AsyncMock(return_value=Client(1,102,'ru'))),patch('app.bot.connect_database'),patch('app.bot.set_language'),patch('app.bot.admin_role',return_value='admin'):
            await select_language(update,context)
        self.assertEqual(context.bot.set_my_commands.call_args.args[0][0].description,'메인 메뉴')
        self.assertIn('admin',[v.command for v in context.bot.set_my_commands.call_args.args[0]])

    async def test_get_user_id_outputs_callers_telegram_id(self):
        context=self.context()
        message=SimpleNamespace(reply_text=AsyncMock())
        update=SimpleNamespace(effective_message=message,effective_user=SimpleNamespace(id=102))
        with patch('app.bot.current_client',new=AsyncMock(return_value=Client(1,102,'ru'))):
            await my_id(update,context)
        message.reply_text.assert_awaited_once_with('Telegram ID: 102')
