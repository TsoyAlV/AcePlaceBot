"""Persistent Telegram command menu, localized and scoped to private chats."""

import asyncio
import logging

from telegram import BotCommand,BotCommandScopeAllPrivateChats,BotCommandScopeChat,MenuButtonCommands
from telegram.error import TelegramError

from app.database import connect_database

logger=logging.getLogger(__name__)


def commands(lang='ru',role=None):
    ru=(lang!='ko')
    values=[
        ('start','Главное меню' if ru else '메인 메뉴'),
        ('book','Забронировать' if ru else '예약하기'),
        ('help','Помощь' if ru else '도움말'),
        ('get_user_id','Мой Telegram ID' if ru else '내 Telegram ID'),
        ('language','Выбрать язык' if ru else '언어 선택'),
        ('cancel','Прервать ввод' if ru else '입력 중단'),
    ]
    if role in ('owner','admin'):
        values.append(('admin','Управление бронированиями' if ru else '예약 관리'))
    return [BotCommand(name,description) for name,description in values]


async def sync_profile(context,chat_id,lang,role):
    if not getattr(context,'bot',None) or type(chat_id) is not int or chat_id<=0:
        return
    cache=context.bot_data.setdefault('command_menu_cache',{})
    signature=(lang,role in ('owner','admin'))
    if cache.get(chat_id)==signature:
        return
    try:
        # Explicit variants prevent an old language-specific scope from overriding the user's chosen language.
        for language_code in ('','ru','ko'):
            await context.bot.set_my_commands(commands(lang,role),scope=BotCommandScopeChat(chat_id),language_code=language_code)
        await context.bot.set_chat_menu_button(chat_id=chat_id,menu_button=MenuButtonCommands())
        cache[chat_id]=signature
    except TelegramError as error:
        logger.warning('Command menu update failed (%s)',type(error).__name__)


def profiles(settings,telegram_id=None):
    with connect_database(settings) as c:
        sql="""SELECT c.telegram_chat_id,c.language,
            CASE WHEN a.active THEN a.role END FROM clients c
            LEFT JOIN admins a USING(user_id) WHERE c.telegram_chat_id>0"""
        if telegram_id is not None:
            return c.execute(sql+' AND c.telegram_user_id=%s',(telegram_id,)).fetchall()
        return c.execute(sql).fetchall()


async def refresh_user(context,telegram_id):
    if not getattr(context,'bot',None):
        return
    try:
        rows=await asyncio.to_thread(profiles,context.bot_data['settings'],telegram_id)
        for chat,lang,role in rows:
            await sync_profile(context,chat,lang,role)
    except Exception as error:
        logger.warning('Command menu refresh failed (%s)',type(error).__name__)


async def reconcile(application):
    if not application.bot_data.get('command_menu_defaults'):
        for language_code in ('','ru','ko'):
            await application.bot.set_my_commands(commands('ko' if language_code=='ko' else 'ru'),
                scope=BotCommandScopeAllPrivateChats(),language_code=language_code)
        await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        application.bot_data['command_menu_defaults']=True
    rows=await asyncio.to_thread(profiles,application.bot_data['settings'])
    for chat,lang,role in rows:
        await sync_profile(application,chat,lang,role)


async def loop(application):
    while True:
        try:
            await reconcile(application)
        except Exception as error:
            logger.warning('Command menu sync failed (%s)',type(error).__name__)
        await asyncio.sleep(60)


async def start(application):
    application.bot_data['command_menu_task']=asyncio.create_task(loop(application))


async def stop(application):
    task=application.bot_data.pop('command_menu_task',None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
