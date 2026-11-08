"""One temporary navigation screen; results and unknown/old messages are preserved."""

from telegram.error import BadRequest,TelegramError
from telegram import InlineKeyboardMarkup,ReplyKeyboardMarkup,ReplyKeyboardRemove


def _ref(message,update):
    mid=getattr(message,'message_id',None)
    chat=getattr(update,'effective_chat',None)
    cid=getattr(chat,'id',None)
    return [cid,mid] if type(cid) is int and type(mid) is int else None


async def _remove(context,ref):
    if not ref or not getattr(context,'bot',None):
        return
    try:
        await context.bot.delete_message(chat_id=ref[0],message_id=ref[1])
    except TelegramError:
        # Old/already deleted messages must not interrupt a completed business action.
        try:
            await context.bot.edit_message_reply_markup(chat_id=ref[0],message_id=ref[1],reply_markup=None)
        except TelegramError:
            pass


async def screen(update,context,text,*,reply_markup=None,keep=False,key=None):
    if context is None or getattr(context,'user_data',None) is None:
        return await update.effective_message.reply_text(text,reply_markup=reply_markup)
    data=context.user_data
    if key and key in data.get('_ui_results',{}):
        return
    old=data.get('_ui_screen')
    remove_keyboard=data.get('_ui_reply_keyboard',False) and not isinstance(reply_markup,ReplyKeyboardMarkup)
    # Only a screen registered by this running process may be edited or removed.
    # Callback messages may be receipts or durable notifications: never infer their type.
    chat=getattr(update,'effective_chat',None)
    if old and old[0]!=getattr(chat,'id',None):
        old=None
    ref=None
    message=None
    if old and getattr(update,'callback_query',None) and getattr(context,'bot',None) and not remove_keyboard and (reply_markup is None or isinstance(reply_markup,InlineKeyboardMarkup)):
        try:
            message=await context.bot.edit_message_text(chat_id=old[0],message_id=old[1],text=text,reply_markup=reply_markup)
            ref=old
        except BadRequest as error:
            if 'message is not modified' in str(error).lower():
                ref=old
        except TelegramError:
            pass
    if ref is None:
        message=await update.effective_message.reply_text(text,reply_markup=ReplyKeyboardRemove() if remove_keyboard else reply_markup)
        ref=_ref(message,update)
        if remove_keyboard and isinstance(reply_markup,InlineKeyboardMarkup) and ref and getattr(context,'bot',None):
            await context.bot.edit_message_reply_markup(chat_id=ref[0],message_id=ref[1],reply_markup=reply_markup)
        data['_ui_reply_keyboard']=isinstance(reply_markup,ReplyKeyboardMarkup)
        # Send the next prompt before removing the previous one.
        if ref:
            await _remove(context,old)
    if ref:
        await _remove(context,data.pop('_ui_error',None))
        if keep:
            data.pop('_ui_screen',None)
            if key:
                results=data.setdefault('_ui_results',{})
                results[key]=ref
                while len(results)>30:
                    results.pop(next(iter(results)))
            source=_ref(update.effective_message,update) if getattr(update,'callback_query',None) else None
            if source and source!=ref and getattr(context,'bot',None):
                try:
                    await context.bot.edit_message_reply_markup(chat_id=source[0],message_id=source[1],reply_markup=None)
                except TelegramError:
                    pass
        else:
            data['_ui_screen']=ref
    return message


async def result(update,context,text,*,reply_markup=None,key=None):
    return await screen(update,context,text,reply_markup=reply_markup,keep=True,key=key)


async def error(update,context,text):
    message=await update.effective_message.reply_text(text)
    if context is not None and getattr(context,'user_data',None) is not None:
        ref=_ref(message,update)
        if ref:
            await _remove(context,context.user_data.get('_ui_error'))
            context.user_data['_ui_error']=ref
    return message
