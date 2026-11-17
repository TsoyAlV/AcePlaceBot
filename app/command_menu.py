"""Persistent Telegram command menu, localized and scoped to private chats."""

import asyncio
import logging
import re

from telegram import BotCommand,BotCommandScopeAllPrivateChats,BotCommandScopeChat,MenuButtonCommands
from telegram.error import TelegramError,BadRequest,Forbidden

from app.database import connect_database
from app.admin_ui import tr

logger=logging.getLogger(__name__)


def safe_error_reason(error):
    """Retain Telegram's error description without URLs, tokens or long IDs."""
    reason=re.sub(r'https?://\S+','[URL]',str(error))
    reason=re.sub(r'\b[0-9]{5,}:[A-Za-z0-9_-]{10,}\b','[TOKEN]',reason)
    reason=re.sub(r'\b[0-9]{7,}\b','[ID]',reason)
    return ' '.join(reason.split())[:180]


def commands(lang='ru',role=None):
    values=[
        ('start',tr(lang,'Главное меню','메인 메뉴','Main menu')),
        ('help',tr(lang,'Помощь','도움말','Help')),
        ('get_user_id',tr(lang,'Мой Telegram ID','내 Telegram ID','My Telegram ID')),
        ('cancel',tr(lang,'Прервать и в главное меню','작업 중단 · 메인 메뉴','Stop and return to main menu')),
        ('address',tr(lang,'Адрес и парковка','오시는 길 및 주차','Address and parking')),
        ('price',tr(lang,'Прайс','운영 시간 및 요금','Opening hours and prices')),
    ]
    return [BotCommand(name,description) for name,description in values]


async def sync_profile(context,chat_id,lang,role,*,background=False):
    if not getattr(context,'bot',None) or type(chat_id) is not int or chat_id<=0:
        return
    cache=context.bot_data.setdefault('command_menu_cache',{})
    unavailable=context.bot_data.setdefault('command_menu_unavailable',set())
    if background and chat_id in unavailable:
        return
    signature=(lang,role in ('owner','admin'))
    if cache.get(chat_id)==signature:
        return
    try:
        # Explicit variants prevent an old language-specific scope from overriding the user's chosen language.
        for language_code in ('','ru','ko','en'):
            await context.bot.set_my_commands(commands(lang,role),scope=BotCommandScopeChat(chat_id),language_code=language_code)
        await context.bot.set_chat_menu_button(chat_id=chat_id,menu_button=MenuButtonCommands())
        cache[chat_id]=signature
        unavailable.discard(chat_id)
    except (BadRequest,Forbidden) as error:
        reason=str(error).lower().replace('_',' ')
        if isinstance(error,Forbidden) or 'chat not found' in reason or 'user not found' in reason:
            # A different bot cannot reach all users inherited from the shared DB.
            # Retry on their next interaction, not every background sweep.
            unavailable.add(chat_id)
            cache.pop(chat_id,None)
            logger.info('Command menu deferred for an unavailable chat; retry on next user interaction')
        else:
            logger.warning('Command menu update failed (BadRequest: %s)',safe_error_reason(error))
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
        for language_code in ('','ru','ko','en'):
            await application.bot.set_my_commands(commands(language_code or 'ru'),
                scope=BotCommandScopeAllPrivateChats(),language_code=language_code)
        await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        application.bot_data['command_menu_defaults']=True
    rows=await asyncio.to_thread(profiles,application.bot_data['settings'])
    for chat,lang,role in rows:
        await sync_profile(application,chat,lang,role,background=True)


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
