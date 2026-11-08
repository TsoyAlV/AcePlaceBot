"""Test bot only. Run one polling process per token."""

import logging
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import load_settings
from app.database import check_database, connect_database
from app.users import register_client, set_language, admin_role
from app import admin_ui,client_ui,notifications,payment_ui,calendar_sync,expiry_worker,staff_ui,command_menu
from app import ui_messages as ui

logger = logging.getLogger(__name__)

WELCOME = {
    "ru": "Ace Place — бронирование party room.\n"
    "Выбери язык. Команда /get_user_id покажет твой Telegram ID.",
    "ko": "Ace Place 파티룸 예약입니다.\n"
    "언어를 선택해 주세요. /get_user_id 명령으로 Telegram ID를 확인할 수 있습니다.",
}


async def current_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, chat = update.effective_user, update.effective_chat
    if user is None or chat is None or chat.type != 'private':
        raise ValueError('Private chat required')

    def save():
        with connect_database(context.bot_data['settings']) as connection:
            client=register_client(
                connection, telegram_user_id=user.id, telegram_chat_id=chat.id,
                name=user.full_name, username=user.username, language_code=user.language_code,
            )
            return client,admin_role(connection,user.id)
    client,role=await asyncio.to_thread(save)
    await command_menu.sync_profile(context,chat.id,client.language,role)
    return client


def language_keyboard(lang='ru') -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(list(client_ui.menu(lang).inline_keyboard)+[[
        InlineKeyboardButton("Русский", callback_data="language:ru"),
        InlineKeyboardButton("한국어", callback_data="language:ko"),
    ]])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        client = await current_client(update, context)
        if getattr(context,'user_data',None) is not None:
            context.user_data.pop('admin_draft',None)
            context.user_data.pop('client_draft',None)
            context.user_data.pop('client_cancel',None)
            context.user_data.pop('receipt_upload',None)
            context.user_data.pop('staff_draft',None)
        await ui.screen(update,context,
            WELCOME[client.language], reply_markup=language_keyboard(client.language)
        )


async def select_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data not in ("language:ru", "language:ko"):
        return
    if not update.effective_chat or update.effective_chat.type != 'private':
        await query.answer()
        return
    await query.answer()
    client = await current_client(update, context)
    selected = query.data.split(":", 1)[1]

    def save():
        with connect_database(context.bot_data['settings']) as connection:
            set_language(connection, client.telegram_user_id, selected)
            return admin_role(connection,client.telegram_user_id)
    role=await asyncio.to_thread(save)
    await command_menu.sync_profile(context,getattr(update.effective_chat,'id',None),selected,role)
    if query.message and query.message.text == WELCOME[selected]:
        return
    await ui.screen(update,context,WELCOME[selected], reply_markup=language_keyboard(selected))


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message and update.effective_user:
        await current_client(update, context)
        await update.effective_message.reply_text(f"Telegram ID: {update.effective_user.id}")


async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    client = await current_client(update, context)

    def check_role():
        with connect_database(context.bot_data['settings']) as connection:
            return admin_role(connection, client.telegram_user_id)
    role = await asyncio.to_thread(check_role)
    if getattr(context, 'user_data', None) is not None:
        context.user_data.pop('admin_draft', None)
        context.user_data.pop('client_draft', None)
        context.user_data.pop('client_cancel', None)
        context.user_data.pop('receipt_upload',None)
        context.user_data.pop('staff_draft',None)
    if role is None:
        text = 'Доступ запрещён.' if client.language == 'ru' else '접근 권한이 없습니다.'
    else:
        text = (
            f'Административное меню Ace Place\nРоль: {role}\n'
            'Выбери действие ниже.'
            if client.language == 'ru' else
            f'Ace Place 관리자 메뉴\n역할: {role}\n'
            '아래에서 작업을 선택하세요.'
        )
    if role in ('owner', 'admin'):
        await ui.screen(update,context,text, reply_markup=admin_ui.menu(client.language,owner=role=='owner'))
    elif role == 'operator':
        await ui.screen(update,context,text + '\n' + (
            'Управление бронированиями доступно владельцу и администратору.' if client.language == 'ru'
            else '예약 관리는 소유자와 관리자만 이용할 수 있습니다.'
        ))
    else:
        await ui.error(update,context,text)


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get('receipt_upload'):
        await ui.error(update,context,'Отправь изображение или /cancel для выхода. / 이미지를 보내거나 /cancel 로 종료하세요.')
        return
    if not await staff_ui.text_input(update,context) and not await admin_ui.text_input(update, context) and not await client_ui.text_input(update,context):
        await start(update, context)


async def book_command(update,context):
    # Commands use the same handler, without fabricating a Telegram callback identity.
    context.user_data.pop('admin_draft',None)
    context.user_data.pop('receipt_upload',None)
    context.user_data.pop('staff_draft',None)
    context.user_data.pop('client_cancel',None)
    from uuid import uuid4
    client=await current_client(update,context)
    context.user_data['client_draft']=dict(token=uuid4().hex[:12],key=uuid4(),step='date')
    await client_ui.show_dates(update,context,client.language)


async def cancel_command(update,context):
    admin_flow=bool(context.user_data.get('admin_draft') or context.user_data.get('staff_draft'))
    context.user_data.pop('staff_draft',None)
    context.user_data.pop('admin_draft',None)
    context.user_data.pop('client_draft',None)
    context.user_data.pop('client_cancel',None)
    context.user_data.pop('receipt_upload',None)
    lang,role=await client_ui.call(update,context,lambda c,a,u:(u.language,admin_role(c,a)))
    markup=admin_ui.menu(lang,owner=role=='owner') if admin_flow and role in ('owner','admin') else client_ui.menu(lang)
    await ui.screen(update,context,client_ui.tr(lang,'Ввод прерван.','입력이 중단되었습니다.'),reply_markup=markup)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Do not log incoming messages, tokens or exception bodies containing URLs.
    logger.error("Telegram handler failed (%s)", type(context.error).__name__)
    if isinstance(update, Update) and update.effective_chat and update.effective_chat.type == 'private' and update.effective_message:
        try:
            await ui.error(update,context,
                'Не удалось выполнить действие. Попробуйте позже.\n'
                '요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.'
            )
        except Exception:
            logger.error('Could not deliver error notification')


async def start_background(application):
    await notifications.start_delivery(application)
    await calendar_sync.start(application)
    await expiry_worker.start(application)
    await command_menu.start(application)


async def stop_background(application):
    await command_menu.stop(application)
    await expiry_worker.stop(application)
    await calendar_sync.stop(application)
    await notifications.stop_delivery(application)


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)
    try:
        settings = load_settings()
    except ValueError as error:
        print(str(error))
        return 1
    if not settings.telegram_test_token:
        print("Add the test bot token to TELEGRAM_TEST in .env.")
        return 1
    try:
        if not check_database(settings):
            print("PostgreSQL returned an unexpected response.")
            return 1
    except Exception as error:
        print(f"PostgreSQL unavailable ({type(error).__name__}). Run python -m app.check_db.")
        return 1
    try:
        application = Application.builder().token(settings.telegram_test_token).post_init(start_background).post_shutdown(stop_background).build()
        application.bot_data['settings'] = settings
        application.add_handler(CommandHandler(["start", "help", "language"], start, filters=filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler(["myid","get_user_id"], my_id, filters=filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("admin", admin_menu, filters=filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("book", book_command, filters=filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("cancel", cancel_command, filters=filters.ChatType.PRIVATE))
        application.add_handler(CallbackQueryHandler(select_language, pattern=r"^language:(ru|ko)$"))
        application.add_handler(CallbackQueryHandler(admin_ui.callback, pattern=r"^adm:"))
        application.add_handler(CallbackQueryHandler(staff_ui.callback, pattern=r"^staff:"))
        application.add_handler(CallbackQueryHandler(client_ui.callback, pattern=r"^cli:"))
        application.add_handler(CallbackQueryHandler(payment_ui.callback, pattern=r"^pay:"))
        application.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.PHOTO | filters.Document.ALL),payment_ui.media_input))
        application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, text_message))
        application.add_error_handler(on_error)
        print("Starting TEST bot. Stop with Ctrl+C. Only one polling process per token.")
        application.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=False, bootstrap_retries=0)
    except Exception as error:
        print(f"Bot stopped ({type(error).__name__}). Check the test token, network and other polling processes.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
