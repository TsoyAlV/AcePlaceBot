import ast
import re
import tempfile
import unittest
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

from app import bot,client_ui,admin_ui,command_menu,notifications,reviews,booking_details,payment_ui,staff_ui,ui_messages
from app.business_config import load_business_config
from app.cancellation_rules import policy_text,current_policy,cancellation_text
from app.date_picker import month_keyboard
from app.users import Client


def english_only(test,text):
    test.assertNotRegex(text,r'[А-Яа-яЁё가-힣]')


class EnglishTests(unittest.IsolatedAsyncioTestCase):
    async def test_language_button_persists_english_and_updates_all_menu_scopes(self):
        context=SimpleNamespace(user_data={},bot_data={'settings':object()},bot=SimpleNamespace(set_my_commands=AsyncMock(),set_chat_menu_button=AsyncMock()))
        update=SimpleNamespace(effective_message=SimpleNamespace(),callback_query=SimpleNamespace(data='language:en',answer=AsyncMock(),message=None),effective_chat=SimpleNamespace(id=42,type='private'))
        with patch('app.bot.current_client',new=AsyncMock(return_value=Client(1,42,'ru'))),patch('app.bot.connect_database'),patch('app.bot.set_language') as save,patch('app.bot.admin_role',return_value=None),patch('app.bot.ui.screen',new=AsyncMock()) as screen:
            await bot.select_language(update,context)
        self.assertEqual(save.call_args.args[1:],(42,'en'))
        self.assertEqual(context.user_data['_language'],'en')
        self.assertIn('Language updated',screen.call_args.args[2])
        self.assertEqual({c.kwargs['language_code'] for c in context.bot.set_my_commands.call_args_list},{'','ru','ko','en'})
        for call in context.bot.set_my_commands.call_args_list:
            self.assertEqual(call.args[0][0].description,'Main menu')
            self.assertNotIn('admin',[c.command for c in call.args[0]])
        buttons=bot.language_keyboard('en').inline_keyboard
        self.assertEqual({b.callback_data for row in buttons for b in row if b.callback_data.startswith('language:')},{'language:ru','language:ko','language:en'})

    async def test_questions_receipt_errors_and_cancel_are_in_english(self):
        context=SimpleNamespace(user_data={'_language':'en','client_draft':{}},bot_data={})
        for step in ('phone','guests','event'):
            with patch('app.client_ui.ui.screen',new=AsyncMock()) as screen:
                await client_ui.show_question(None,context,{},'en',step)
            english_only(self,screen.call_args.args[2])
            for row in screen.call_args.kwargs['reply_markup'].keyboard if step=='phone' else []:
                for button in row: english_only(self,button.text)
        update=SimpleNamespace(effective_message=SimpleNamespace(reply_text=AsyncMock()),effective_chat=None)
        from app.bookings import BookingError
        await payment_ui.error_message(update,BookingError('Send an image'),context)
        text=update.effective_message.reply_text.call_args.args[0]
        english_only(self,text)
        self.assertIn('Videos',text)
        self.assertIn('/cancel',text)
        with patch('app.bot.current_client',new=AsyncMock(return_value=Client(1,42,'en'))),patch('app.bot.ui.screen',new=AsyncMock()) as screen:
            await bot.start(update,context,cancelled=True)
        english_only(self,screen.call_args.args[2])
        self.assertIn('Operation stopped',screen.call_args.args[2])

    def test_cards_terms_calendar_and_controls_use_english(self):
        now=datetime.now(timezone.utc)
        row=dict(booking_id=42,status='confirmed',type='booking',starts_at=now+timedelta(days=1),ends_at=now+timedelta(days=1,hours=5),
                 expires_at=now+timedelta(hours=1),amount=Decimal(150000),unit='KRW',deposit_paid_amount=Decimal(50000),balance_paid_amount=Decimal(0),
                 user_id=1,version=2,contact_phone='01012345678',guests_count=20,event_name='Birthday',
                 cleaning_starts_at=now+timedelta(days=1,hours=5),cleaning_ends_at=now+timedelta(days=1,hours=7),
                 cancellation={'policy':current_policy()},payment_receipt={'review_status':'pending','expected_amount':50000,'unit':'KRW'})
        for card in (client_ui.card(row,'en'),admin_ui.card(row,'en'),payment_ui.receipt_caption(row,'en'),booking_details.payment_text(row,'en')):
            english_only(self,card)
        self.assertIn('non-refundable',client_ui.card(row,'en'))
        for markup in (client_ui.controls(row,'en'),admin_ui.controls(row,'en'),admin_ui.menu('en',owner=True),month_keyboard('cli:cal',date(2026,11,1),date(2026,11,1),'en')):
            for buttons in markup.inline_keyboard:
                for button in buttons: english_only(self,button.text)
        self.assertIn('November',month_keyboard('x',date(2026,11,1),date(2026,11,1),'en').inline_keyboard[0][0].text)
        row['cancellation'].update(quote={'paid_amount':'50000','withheld_amount':'50000','refund_amount':'0','unit':'KRW'},refund_status='not_due')
        english_only(self,cancellation_text(row,'en'))

    def test_visit_reviews_and_terms_are_available_without_changing_other_languages(self):
        config=load_business_config()
        self.assertIn('Map and directions: https://kko.to/Hn6onZrkRD',notifications.visit_text(config,'en'))
        self.assertIn('turn right',notifications.visit_text(config,'en'))
        self.assertIn('Leave a review',reviews.render(config.reviews,'en'))
        self.assertIn('non-refundable',policy_text(current_policy(config),'en'))
        self.assertIn('Условия отмены',policy_text(current_policy(config),'ru'))
        self.assertIn('취소 규정',policy_text(current_policy(config),'ko'))

    def test_all_language_helper_calls_have_an_explicit_english_translation(self):
        for path in Path('app').glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
                if isinstance(node,ast.Call) and ((isinstance(node.func,ast.Name) and node.func.id=='tr') or (isinstance(node.func,ast.Attribute) and node.func.attr=='tr')):
                    self.assertEqual(len(node.args),4,f'{path}:{node.lineno}')

    def test_duplicate_config_keys_cannot_erase_map_links(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.yaml'
            path.write_text('visit:\n  en:\n    route_url: https://kko.to/Hn6onZrkRD\n    route_url: ""\n',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Duplicate config key: route_url'):
                load_business_config(path)
