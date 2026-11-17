"""Production bot. Run one polling process per token."""

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
from telegram.error import TelegramError

from app.config import load_settings,PROJECT_ROOT
from app.business_config import load_business_config
from app.database import check_database, connect_database
from app.users import register_client, set_language, admin_role
from app import admin_ui,client_ui,notifications,payment_ui,calendar_sync,expiry_worker,staff_ui,command_menu
from app import ui_messages as ui
from app import navigation,venue_information

logger = logging.getLogger(__name__)

WELCOME = {
    "ru": "Ace Place — бронирование party room.\n"
    "Выбери действие кнопками внизу. /help — помощь.\n"
    "В любой момент /cancel — прервать текущую операцию и вернуться в главное меню.",
    "ko": "Ace Place 파티룸 예약입니다.\n"
    "아래 버튼에서 원하는 기능을 선택하세요. /help — 도움말.\n"
    "언제든 /cancel 로 현재 작업을 중단하고 메인 메뉴로 돌아갈 수 있습니다.",
    "en": "Ace Place — party room bookings.\n"
    "Choose an action using the buttons below. /help — help.\n"
    "Use /cancel at any time to stop the current operation and return to the main menu.",
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
    navigation.configure(context,client.language,role)
    await command_menu.sync_profile(context,chat.id,client.language,role)
    return client


def language_keyboard(lang='ru',website_url=None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Русский", callback_data="language:ru"),
        InlineKeyboardButton("한국어", callback_data="language:ko"),
        InlineKeyboardButton("English", callback_data="language:en"),
    ]])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE, *, cancelled=False) -> None:
    if update.effective_message:
        if getattr(context,'user_data',None) is not None:
            for key in ui.FLOW_KEYS:
                context.user_data.pop(key,None)
        client = await current_client(update, context)
        navigation.configure(context,client.language,(getattr(context,'user_data',None) or {}).get('_role'))
        notice=client_ui.tr(client.language,'Операция прервана. Главное меню.\n\n',
            '작업을 중단했습니다. 메인 메뉴입니다.\n\n', 'Operation stopped. Main menu.\n\n') if cancelled else ''
        welcome=load_business_config().welcome
        if not cancelled and welcome.image_path:
            image=(PROJECT_ROOT/welcome.image_path).resolve()
            try:
                if not image.is_relative_to((PROJECT_ROOT/'assets'/'telegram').resolve()):
                    raise ValueError('Welcome image path escapes assets/telegram')
                await ui.welcome_photo(update,context,image)
            except (OSError,ValueError,TelegramError) as error:
                logger.warning('Welcome photo unavailable (%s); sending text menu',type(error).__name__)
        await ui.screen(update,context,
            notice+WELCOME[client.language]
        )


async def help_command(update,context):
    navigation.clear_flows(context)
    client=await current_client(update,context)
    navigation.configure(context,client.language,(getattr(context,'user_data',None) or {}).get('_role'))
    text=client_ui.tr(client.language,
        'Как пользоваться AcePlace\n\n📅 «Забронировать» — выберите дату, длительность и время, затем ответьте на три вопроса и отправьте заявку. До отправки время не резервируется.\n📋 «Мои брони» — статус, оплата и отмена своих бронирований.\n🌐 «Наш сайт» — ссылка на сайт.\n🌍 Language — выбор языка.\n\nПосле отправки оплатите предоплату по реквизитам и приложите скриншот через карточку брони. Проверка оплаты и подтверждение аренды выполняются администратором. Условия отмены указаны в карточке.\n\n/address — адрес и парковка\n/price — график работы и тарифы\n/get_user_id — ваш Telegram ID\n/cancel — прервать ввод и вернуться в главное меню.',
        'AcePlace 이용 방법\n\n📅 «예약하기» — 날짜, 이용 시간, 시작 시간을 선택하고 세 가지 질문에 답한 후 신청하세요. 신청 전에는 시간이 확보되지 않습니다.\n📋 «내 예약» — 예약 상태, 결제 및 취소를 확인하세요.\n🌐 «웹사이트» — 웹사이트 링크.\n🌍 Language — 언어 선택.\n\n신청 후 안내된 계좌로 선입금하고 예약 화면에서 결제 증빙을 보내 주세요. 입금 확인과 예약 승인은 관리자가 진행합니다. 취소 조건은 예약 화면에 표시됩니다.\n\n/address — 오시는 길 및 주차\n/price — 운영 시간 및 요금\n/get_user_id — 내 Telegram ID\n/cancel — 입력을 중단하고 메인 메뉴로 돌아가기.',
        'How to use AcePlace\n\n📅 Book now — select a date, duration and start time, answer three questions and submit your request. Your time is not reserved before submission.\n📋 My bookings — check status, payment and cancellation options.\n🌐 Our website — website link.\n🌍 Language — choose your language.\n\nAfter submitting, transfer the deposit using the provided bank details and upload your payment screenshot from the booking card. An administrator checks payment and confirms the rental separately. Cancellation terms are shown in the booking card.\n\n/address — address and parking\n/price — opening hours and prices\n/get_user_id — your Telegram ID\n/cancel — stop the current form and return to the main menu.')
    await ui.screen(update,context,text)


async def language_command(update,context):
    navigation.clear_flows(context)
    client=await current_client(update,context)
    navigation.configure(context,client.language,(getattr(context,'user_data',None) or {}).get('_role'))
    await ui.screen(update,context,client_ui.tr(client.language,'Выберите язык','언어를 선택하세요','Choose your language'),reply_markup=language_keyboard())


async def website_command(update,context):
    navigation.clear_flows(context)
    client=await current_client(update,context)
    navigation.configure(context,client.language,(getattr(context,'user_data',None) or {}).get('_role'))
    url=load_business_config().welcome.website_url
    if not url:
        await ui.screen(update,context,client_ui.tr(client.language,'Ссылка на сайт пока не указана.','웹사이트 링크가 아직 등록되지 않았습니다.','The website link has not been configured yet.'))
        return
    await ui.screen(update,context,'AcePlace · '+client_ui.tr(client.language,'Наш сайт','웹사이트','Our website'),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(client_ui.tr(client.language,'Открыть сайт','웹사이트 열기','Open website'),url=str(url))]]))


async def information_command(update,context,kind):
    navigation.clear_flows(context)
    client=await current_client(update,context)
    navigation.configure(context,client.language,(getattr(context,'user_data',None) or {}).get('_role'))
    rules=load_business_config()
    text=venue_information.address_text(rules,client.language) if kind=='address' else venue_information.price_text(rules,client.language)
    await ui.screen(update,context,text,parse_mode='HTML')


async def address_command(update,context):
    await information_command(update,context,'address')


async def price_command(update,context):
    await information_command(update,context,'price')


async def select_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data not in ("language:ru", "language:ko", "language:en"):
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
    navigation.clear_flows(context)
    navigation.configure(context,selected,role)
    await command_menu.sync_profile(context,getattr(update.effective_chat,'id',None),selected,role)
    if getattr(update,'effective_message',None):
        await ui.screen(update,context,client_ui.tr(selected,'Язык изменён. Выберите действие кнопками внизу.','언어가 변경되었습니다. 아래 버튼을 선택하세요.','Language updated. Choose an action using the buttons below.'))


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message and update.effective_user:
        navigation.clear_flows(context)
        client=await current_client(update, context)
        navigation.configure(context,client.language,(getattr(context,'user_data',None) or {}).get('_role'))
        await ui.screen(update,context,f"Telegram ID: {update.effective_user.id}")


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
        text = client_ui.tr(client.language,'Доступ запрещён.','접근 권한이 없습니다.','Access denied.')
    else:
        text = (
            f'Административное меню Ace Place\nРоль: {role}\n'
            'Выбери действие ниже.'
            if client.language == 'ru' else
            f'Ace Place administration\nRole: {role}\nChoose an action below.'
            if client.language == 'en' else
            f'Ace Place 관리자 메뉴\n역할: {role}\n'
            '아래에서 작업을 선택하세요.'
        )
    if role in ('owner', 'admin'):
        await ui.screen(update,context,text, reply_markup=admin_ui.menu(client.language,owner=role=='owner'))
    elif role == 'operator':
        await ui.screen(update,context,text + '\n' + (
            client_ui.tr(client.language,'Управление бронированиями доступно владельцу и администратору.',
            '예약 관리는 소유자와 관리자만 이용할 수 있습니다.','Booking management is available to the owner and administrators.')
        ))
    else:
        await ui.ensure_navigation(update,context)
        await ui.error(update,context,text)


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    action=navigation.action(getattr(update.effective_message,'text',None))
    if action:
        handlers={'book':book_command,'mine':my_bookings_command,'website':website_command,'language':language_command,'admin':admin_menu}
        await handlers[action](update,context)
        return
    if context.user_data.get('receipt_upload'):
        await ui.error(update,context,'Отправь изображение или /cancel для выхода. / 이미지를 보내거나 /cancel 로 종료하세요.')
        return
    if not await staff_ui.text_input(update,context) and not await admin_ui.text_input(update, context) and not await client_ui.text_input(update,context):
        await start(update, context)


async def book_command(update,context):
    # Commands use the same handler, without fabricating a Telegram callback identity.
    navigation.clear_flows(context)
    from uuid import uuid4
    client=await current_client(update,context)
    context.user_data['client_draft']=dict(token=uuid4().hex[:12],key=uuid4(),step='date')
    await client_ui.show_dates(update,context,client.language)


async def my_bookings_command(update,context):
    navigation.clear_flows(context)
    await client_ui.show_mine(update,context)


async def cancel_command(update,context):
    if not update.effective_chat or update.effective_chat.type!='private':
        return
    await start(update,context,cancelled=True)


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
    if not settings.telegram_prod_token:
        print("Add the production bot token to TELEGRAM_PROD in .env.")
        return 1
    try:
        if not check_database(settings):
            print("PostgreSQL returned an unexpected response.")
            return 1
    except Exception as error:
        print(f"PostgreSQL unavailable ({type(error).__name__}). Run python -m app.check_db.")
        return 1
    try:
        application = Application.builder().token(settings.telegram_prod_token).post_init(start_background).post_shutdown(stop_background).build()
        application.bot_data['settings'] = settings
        application.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("help", help_command, filters=filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("language", language_command, filters=filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("address", address_command, filters=filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("price", price_command, filters=filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler(["myid","get_user_id"], my_id, filters=filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("admin", admin_menu, filters=filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("book", book_command, filters=filters.ChatType.PRIVATE))
        application.add_handler(CommandHandler("cancel", cancel_command, filters=filters.ChatType.PRIVATE))
        application.add_handler(CallbackQueryHandler(select_language, pattern=r"^language:(ru|ko|en)$"))
        application.add_handler(CallbackQueryHandler(admin_ui.callback, pattern=r"^adm:"))
        application.add_handler(CallbackQueryHandler(staff_ui.callback, pattern=r"^staff:"))
        application.add_handler(CallbackQueryHandler(client_ui.callback, pattern=r"^cli:"))
        application.add_handler(CallbackQueryHandler(payment_ui.callback, pattern=r"^pay:"))
        application.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.PHOTO | filters.Document.ALL),payment_ui.media_input))
        application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.CONTACT,client_ui.contact_input))
        application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, text_message))
        application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.ALL & ~filters.COMMAND, payment_ui.unsupported_input))
        application.add_error_handler(on_error)
        print("Starting production bot. Stop with Ctrl+C. Only one polling process per token.")
        application.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=False, bootstrap_retries=0)
    except Exception as error:
        print(f"Bot stopped ({type(error).__name__}). Check TELEGRAM_PROD, network and other polling processes.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
