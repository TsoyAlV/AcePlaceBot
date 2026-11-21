import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

from telegram import InlineKeyboardButton,InlineKeyboardMarkup,KeyboardButton,ReplyKeyboardMarkup

from app import bot,navigation,ui_messages as ui,venue_information
from app.business_config import load_business_config
from app.users import Client


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    def context_update(self,text=''):
        context=SimpleNamespace(user_data={},bot_data={'settings':object()},bot=SimpleNamespace(
            edit_message_text=AsyncMock(),delete_message=AsyncMock(),edit_message_reply_markup=AsyncMock()))
        message=SimpleNamespace(text=text,message_id=1,reply_text=AsyncMock(return_value=SimpleNamespace(message_id=100)))
        update=SimpleNamespace(effective_message=message,effective_chat=SimpleNamespace(id=102,type='private'),
            effective_user=SimpleNamespace(id=102),callback_query=None)
        return context,update

    def test_bottom_keyboard_is_persistent_and_admin_button_requires_role(self):
        for lang in ('ru','ko','en'):
            for role in (None,'operator','admin','owner'):
                with self.subTest(lang=lang,role=role):
                    markup=navigation.keyboard(lang,role)
                    labels=[b.text for row in markup.keyboard for b in row]
                    self.assertTrue(markup.is_persistent)
                    self.assertFalse(markup.one_time_keyboard)
                    self.assertIn(navigation.label('book',lang),labels)
                    self.assertIn('🌍 Language',labels)
                    self.assertEqual(navigation.label('admin',lang) in labels,role in ('owner','admin'))

    async def test_help_is_instructions_and_language_is_only_language_choice(self):
        context,update=self.context_update()
        with patch('app.bot.current_client',new=AsyncMock(return_value=Client(1,102,'ru'))):
            await bot.help_command(update,context)
            text=update.effective_message.reply_text.call_args.args[0]
            self.assertIn('Как пользоваться',text)
            self.assertIn('/address',text)
            await bot.language_command(update,context)
        markup=update.effective_message.reply_text.call_args.kwargs['reply_markup']
        self.assertIsInstance(markup,InlineKeyboardMarkup)
        self.assertEqual({b.callback_data for row in markup.inline_keyboard for b in row},{'language:ru','language:ko','language:en'})

    async def test_navigation_is_routed_before_receipt_upload_and_questionnaire(self):
        context,update=self.context_update(navigation.label('website','ru'))
        context.user_data.update(receipt_upload={'booking_id':1},client_draft={'step':'phone'})
        with patch('app.bot.website_command',new=AsyncMock()) as site,patch('app.client_ui.text_input',new=AsyncMock()) as form:
            await bot.text_message(update,context)
        site.assert_awaited_once_with(update,context)
        form.assert_not_awaited()

    async def test_website_button_returns_configured_clickable_link(self):
        context,update=self.context_update()
        with patch('app.bot.current_client',new=AsyncMock(return_value=Client(1,102,'en'))):
            await bot.website_command(update,context)
        markup=update.effective_message.reply_text.call_args.kwargs['reply_markup']
        self.assertEqual(markup.inline_keyboard[0][0].url,'https://151.243.177.216/')
        self.assertEqual(markup.inline_keyboard[0][0].text,'Open website')

    async def test_contact_keyboard_is_replaced_by_navigation_and_inline_screens_keep_it(self):
        context,update=self.context_update()
        navigation.configure(context,'ru','admin')
        update.effective_message.reply_text.side_effect=[SimpleNamespace(message_id=100),SimpleNamespace(message_id=101),SimpleNamespace(message_id=102)]
        await ui.screen(update,context,'Телефон',reply_markup=ReplyKeyboardMarkup([[KeyboardButton('Контакт',request_contact=True)]],one_time_keyboard=True))
        self.assertTrue(context.user_data['_ui_reply_keyboard'])
        await ui.screen(update,context,'Количество гостей')
        self.assertFalse(context.user_data['_ui_reply_keyboard'])
        self.assertTrue(update.effective_message.reply_text.call_args.kwargs['reply_markup'].is_persistent)
        await ui.screen(update,context,'Дата',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Дата',callback_data='day')]]))
        self.assertEqual(update.effective_message.reply_text.await_count,3)
        self.assertEqual(context.user_data['_ui_navigation_sent'],('ru',True))

    async def test_forged_admin_bottom_button_does_not_grant_access(self):
        context,update=self.context_update(navigation.label('admin','ru'))
        with patch('app.bot.current_client',new=AsyncMock(return_value=Client(1,102,'ru'))),patch('app.bot.connect_database'),patch('app.bot.admin_role',return_value=None),patch('app.bot.ui.error',new=AsyncMock()) as error:
            await bot.text_message(update,context)
        self.assertIn('Доступ запрещён',error.call_args.args[2])

    def test_public_address_and_prices_are_translated_and_use_live_config(self):
        config=load_business_config().model_copy(deep=True)
        for lang in ('ru','ko','en'):
            address=venue_information.address_text(config,lang)
            price=venue_information.price_text(config,lang)
            self.assertIn('B1',address)
            self.assertIn('B2',address)
            self.assertIn('B3',address)
            self.assertIn('150 000',price)
            self.assertIn('200 000',price)
            self.assertIn('24/7',price) if lang!='ko' else self.assertIn('24시간',price)
        self.assertIn('первые 4 часа бесплатны',venue_information.address_text(config,'ru'))
        config.pricing.weekday_base_amount=160000
        self.assertIn('160 000',venue_information.price_text(config,'ru'))
        config.payment.deposit_amount=40000
        config.cancellation.deposit_refundable_before_cutoff=False
        for lang,removed,condition in (
            ('ru','Стоимость может меняться','при отмене брони не возвращается'),
            ('ko','요금은 변경될 수 있습니다','취소 시 환불되지 않습니다'),
            ('en','Prices may change','non-refundable if you cancel'),
        ):
            price=venue_information.price_text(config,lang)
            self.assertIn('40 000',price)
            self.assertIn(condition,price)
            self.assertNotIn(removed,price)
        config.cancellation.deposit_refundable_before_cutoff=True
        config.cancellation.refund_cutoff_hours=72
        price=venue_information.price_text(config,'ru')
        self.assertIn('за 72 ч и более',price)
        self.assertNotIn('при отмене брони не возвращается',price)
