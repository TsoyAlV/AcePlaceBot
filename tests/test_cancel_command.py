import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

from telegram import ReplyKeyboardRemove
from app import bot,ui_messages as ui,command_menu


class CancelCommandTests(unittest.IsolatedAsyncioTestCase):
    def context(self,flows):
        data={key:{'token':'old','step':'review'} for key in flows}
        data.update(_ui_results={'created:42':[10,20]},_ui_reply_keyboard=True)
        return SimpleNamespace(user_data=data,bot_data={},bot=SimpleNamespace(
            edit_message_reply_markup=AsyncMock(),delete_message=AsyncMock()))

    def update(self,chat_type='private'):
        return SimpleNamespace(effective_chat=SimpleNamespace(id=10,type=chat_type),
            effective_message=SimpleNamespace(reply_text=AsyncMock(return_value=SimpleNamespace(message_id=100))),
            callback_query=None)

    async def test_every_flow_and_mixed_flows_restart_start_menu_in_both_languages(self):
        for lang in ('ru','ko'):
            for flows in [(key,) for key in ui.FLOW_KEYS]+[ui.FLOW_KEYS,()]:
                with self.subTest(lang=lang,flows=flows):
                    context=self.context(flows)
                    update=self.update()
                    with patch('app.bot.current_client',new=AsyncMock(return_value=SimpleNamespace(language=lang))) as client:
                        await bot.cancel_command(update,context)
                    client.assert_awaited_once_with(update,context)
                    self.assertTrue(all(key not in context.user_data for key in ui.FLOW_KEYS))
                    self.assertEqual(context.user_data['_ui_results'],{'created:42':[10,20]})
                    text=update.effective_message.reply_text.call_args.args[0]
                    self.assertIn(bot.WELCOME[lang],text)
                    self.assertIn('/cancel',text)
                    self.assertIsInstance(update.effective_message.reply_text.call_args.kwargs['reply_markup'],ReplyKeyboardRemove)
                    markup=context.bot.edit_message_reply_markup.call_args.kwargs['reply_markup']
                    actions=[b.callback_data for row in markup.inline_keyboard for b in row]
                    self.assertIn('cli:new',actions)
                    self.assertIn('language:ru',actions)
                    self.assertFalse(context.user_data['_ui_reply_keyboard'])

    async def test_cancel_calls_start_and_ignores_group_chats(self):
        context=self.context(ui.FLOW_KEYS)
        with patch('app.bot.start',new=AsyncMock()) as start:
            update=self.update()
            await bot.cancel_command(update,context)
            start.assert_awaited_once_with(update,context,cancelled=True)
            start.reset_mock()
            await bot.cancel_command(self.update('group'),context)
            start.assert_not_awaited()

    async def test_reset_happens_even_if_start_cannot_load_client(self):
        context=self.context(ui.FLOW_KEYS)
        with patch('app.bot.current_client',new=AsyncMock(side_effect=RuntimeError('unavailable'))):
            with self.assertRaises(RuntimeError):
                await bot.cancel_command(self.update(),context)
        self.assertTrue(all(key not in context.user_data for key in ui.FLOW_KEYS))

    def test_active_screens_explain_cancel_without_duplicate_hints_or_overflow(self):
        for flow in ui.FLOW_KEYS:
            context=self.context([flow])
            text=ui.with_cancel_hint('Подтвердите действие',context)
            self.assertIn('/cancel',text)
            self.assertIn('메인 메뉴',text)
            self.assertEqual(ui.with_cancel_hint(text,context),text)
            self.assertEqual(ui.with_cancel_hint('x'*4096,context),'x'*4096)
        self.assertEqual(ui.with_cancel_hint('Главное меню',self.context([])),'Главное меню')
        for lang in ('ru','ko'):
            commands=command_menu.commands(lang)
            self.assertTrue(any(c.command=='cancel' for c in commands))

    async def test_active_input_errors_also_explain_cancel(self):
        context=self.context(['receipt_upload'])
        update=self.update()
        await ui.error(update,context,'Неверный формат файла')
        self.assertIn('/cancel',update.effective_message.reply_text.call_args.args[0])
