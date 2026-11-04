import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from telegram.error import BadRequest,Forbidden,TimedOut
from app import ui_messages as ui


class UIMessagesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot=SimpleNamespace(edit_message_text=AsyncMock(),delete_message=AsyncMock(),edit_message_reply_markup=AsyncMock())
        self.context=SimpleNamespace(bot=self.bot,user_data={})

    def update(self,mid=5,callback=True):
        message=SimpleNamespace(message_id=mid,reply_text=AsyncMock(return_value=SimpleNamespace(message_id=100)))
        return SimpleNamespace(effective_chat=SimpleNamespace(id=10),effective_message=message,
            callback_query=SimpleNamespace() if callback else None)

    async def test_navigation_updates_one_known_screen(self):
        self.context.user_data['_ui_screen']=[10,5]
        update=self.update()
        await ui.screen(update,self.context,'Next page')
        self.bot.edit_message_text.assert_awaited_once_with(chat_id=10,message_id=5,text='Next page',reply_markup=None)
        update.effective_message.reply_text.assert_not_awaited()
        self.bot.delete_message.assert_not_awaited()

    async def test_typed_answer_removes_prompt_but_keeps_user_message(self):
        self.context.user_data['_ui_screen']=[10,5]
        update=self.update(mid=99,callback=False)
        await ui.screen(update,self.context,'Next question')
        self.bot.delete_message.assert_awaited_once_with(chat_id=10,message_id=5)
        self.assertEqual(self.context.user_data['_ui_screen'],[10,100])

    async def test_notification_and_screenshot_are_never_edited_or_deleted(self):
        for mid in (25,26):
            with self.subTest(mid=mid):
                self.context.user_data.clear()
                update=self.update(mid=mid)
                await ui.screen(update,self.context,'Current booking')
                update.effective_message.reply_text.assert_awaited_once()
        self.bot.edit_message_text.assert_not_awaited()
        self.bot.delete_message.assert_not_awaited()
        self.bot.edit_message_reply_markup.assert_not_awaited()

    async def test_action_result_survives_next_navigation(self):
        self.context.user_data['_ui_screen']=[10,5]
        await ui.result(self.update(),self.context,'Confirmed',key='confirm:42')
        self.assertNotIn('_ui_screen',self.context.user_data)
        update=self.update(mid=5)
        await ui.screen(update,self.context,'Menu')
        update.effective_message.reply_text.assert_awaited_once()
        self.assertEqual(self.bot.edit_message_text.await_count,1)
        self.bot.delete_message.assert_not_awaited()

    async def test_repeated_result_does_not_duplicate_message(self):
        update=self.update()
        await ui.result(update,self.context,'Created',key='create:42')
        await ui.result(update,self.context,'Created',key='create:42')
        update.effective_message.reply_text.assert_awaited_once()

    async def test_deleted_screen_falls_back_to_new_message(self):
        self.context.user_data['_ui_screen']=[10,5]
        self.bot.edit_message_text.side_effect=BadRequest('Message to edit not found')
        self.bot.delete_message.side_effect=BadRequest('Message to delete not found')
        self.bot.edit_message_reply_markup.side_effect=BadRequest('Message not found')
        update=self.update()
        await ui.screen(update,self.context,'Menu')
        update.effective_message.reply_text.assert_awaited_once()
        self.assertEqual(self.context.user_data['_ui_screen'],[10,100])

    async def test_identical_refresh_does_not_send_new_message(self):
        self.context.user_data['_ui_screen']=[10,5]
        self.bot.edit_message_text.side_effect=BadRequest('Message is not modified')
        update=self.update()
        await ui.screen(update,self.context,'Menu')
        update.effective_message.reply_text.assert_not_awaited()

    async def test_old_prompt_that_cannot_be_deleted_does_not_break_result(self):
        self.context.user_data['_ui_screen']=[10,5]
        self.bot.delete_message.side_effect=Forbidden('Cannot delete')
        self.bot.edit_message_reply_markup.side_effect=TimedOut()
        await ui.result(self.update(callback=False),self.context,'Payment recorded',key='pay:42')
        self.assertIn('pay:42',self.context.user_data['_ui_results'])

    async def test_error_removed_after_successful_input(self):
        update=self.update(callback=False)
        update.effective_message.reply_text.side_effect=[SimpleNamespace(message_id=101),SimpleNamespace(message_id=102)]
        await ui.error(update,self.context,'Invalid date')
        await ui.screen(update,self.context,'Choose hours')
        self.bot.delete_message.assert_awaited_once_with(chat_id=10,message_id=101)
        self.assertNotIn('_ui_error',self.context.user_data)

    async def test_after_restart_unknown_confirmation_kept_and_keyboard_retired(self):
        update=self.update(mid=55)
        await ui.result(update,self.context,'Cancelled',key='cancel:42')
        self.bot.delete_message.assert_not_awaited()
        self.bot.edit_message_text.assert_not_awaited()
        self.bot.edit_message_reply_markup.assert_awaited_once_with(chat_id=10,message_id=55,reply_markup=None)
