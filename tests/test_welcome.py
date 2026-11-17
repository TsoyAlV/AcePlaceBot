import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

from pydantic import ValidationError
from telegram.error import TimedOut

from app import bot
from app.business_config import load_business_config,WelcomeConfig
from app.users import Client


class WelcomeTests(unittest.IsolatedAsyncioTestCase):
    def context_update(self):
        context=SimpleNamespace(bot_data={},user_data={},bot=SimpleNamespace(
            delete_message=AsyncMock(),edit_message_text=AsyncMock(),edit_message_reply_markup=AsyncMock()))
        message=SimpleNamespace(text='/start',message_id=1,
            reply_photo=AsyncMock(return_value=SimpleNamespace(message_id=100)),
            reply_text=AsyncMock(return_value=SimpleNamespace(message_id=101)))
        update=SimpleNamespace(effective_message=message,effective_chat=SimpleNamespace(id=102,type='private'),callback_query=None)
        return context,update

    async def test_start_sends_cover_and_localized_bottom_keyboard(self):
        for lang,label in (('ru','🌐 Наш сайт'),('ko','🌐 웹사이트'),('en','🌐 Our website')):
            with self.subTest(lang=lang):
                context,update=self.context_update()
                with patch('app.bot.current_client',new=AsyncMock(return_value=Client(1,102,lang))):
                    await bot.start(update,context)
                update.effective_message.reply_photo.assert_awaited_once()
                kwargs=update.effective_message.reply_text.call_args.kwargs
                labels=[b.text for row in kwargs['reply_markup'].keyboard for b in row]
                self.assertIn(label,labels)
                self.assertIn('🌍 Language',labels)
                self.assertTrue(kwargs['reply_markup'].is_persistent)
                self.assertIn(bot.WELCOME[lang],update.effective_message.reply_text.call_args.args[0])
                self.assertEqual(context.user_data['_ui_welcome_photo'],[102,100])
                self.assertEqual(context.user_data['_ui_screen'],[102,101])

    async def test_photo_failure_still_shows_menu_and_website(self):
        context,update=self.context_update()
        update.effective_message.reply_photo.side_effect=TimedOut()
        with patch('app.bot.current_client',new=AsyncMock(return_value=Client(1,102,'ru'))),self.assertLogs('app.bot',level='WARNING'):
            await bot.start(update,context)
        update.effective_message.reply_text.assert_awaited_once()
        self.assertNotIn('_ui_welcome_photo',context.user_data)

    async def test_missing_image_still_shows_menu(self):
        context,update=self.context_update()
        config=load_business_config().model_copy(deep=True)
        config.welcome.image_path='assets/telegram/missing-cover.png'
        with patch('app.bot.current_client',new=AsyncMock(return_value=Client(1,102,'ru'))),patch('app.bot.load_business_config',return_value=config),self.assertLogs('app.bot',level='WARNING'):
            await bot.start(update,context)
        update.effective_message.reply_photo.assert_not_awaited()
        update.effective_message.reply_text.assert_awaited_once()

    async def test_repeated_start_replaces_only_previous_known_cover_and_menu(self):
        context,update=self.context_update()
        update.effective_message.reply_photo.side_effect=[SimpleNamespace(message_id=100),SimpleNamespace(message_id=200)]
        update.effective_message.reply_text.side_effect=[SimpleNamespace(message_id=101),SimpleNamespace(message_id=201)]
        with patch('app.bot.current_client',new=AsyncMock(return_value=Client(1,102,'ru'))):
            await bot.start(update,context)
            await bot.start(update,context)
        self.assertEqual({c.kwargs['message_id'] for c in context.bot.delete_message.call_args_list},{100,101})
        self.assertEqual(context.user_data['_ui_welcome_photo'],[102,200])

    async def test_cancel_does_not_resend_photo(self):
        context,update=self.context_update()
        with patch('app.bot.current_client',new=AsyncMock(return_value=Client(1,102,'ru'))):
            await bot.start(update,context,cancelled=True)
        update.effective_message.reply_photo.assert_not_awaited()
        update.effective_message.reply_text.assert_awaited_once()

    def test_welcome_config_rejects_non_web_urls_and_non_image_paths(self):
        with self.assertRaises(ValidationError):
            WelcomeConfig(website_url='javascript:alert(1)')
        for path in ('../.env','.env','/tmp/cover.png','assets/telegram/../../.env','assets/telegram/token.json'):
            with self.subTest(path=path),self.assertRaises(ValidationError):
                WelcomeConfig(image_path=path)
        self.assertIsNone(WelcomeConfig(image_path=None,website_url=None).image_path)
