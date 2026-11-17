"""One temporary navigation screen; results and unknown/old messages are preserved."""

from telegram.error import BadRequest,TelegramError
from telegram import InlineKeyboardMarkup,ReplyKeyboardMarkup,ReplyKeyboardRemove

FLOW_KEYS=('admin_draft','client_draft','client_cancel','receipt_upload','staff_draft')


def with_cancel_hint(text,context):
    data=getattr(context,'user_data',None) or {}
    if '/cancel' in text or not any(key in data for key in FLOW_KEYS):
        return text
    hint=('\n\n/cancel — stop and return to the main menu' if data.get('_language')=='en' else
          '\n\n/cancel — прервать операцию и в главное меню / 작업 중단 후 메인 메뉴')
    return text+hint if len((text+hint).encode('utf-16-le'))//2<=4096 else text


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


async def welcome_photo(update,context,path):
    """Separate welcome cover; never treat a photo as the editable text screen."""
    with path.open('rb') as photo:
        message=await update.effective_message.reply_photo(photo=photo)
    ref=_ref(message,update)
    data=getattr(context,'user_data',None)
    if ref and data is not None:
        old=data.get('_ui_welcome_photo')
        data['_ui_welcome_photo']=ref
        if old and old!=ref and old[0]==ref[0]:
            await _remove(context,old)
    return message


async def ensure_navigation(update,context):
    data=getattr(context,'user_data',None)
    if data is None or not data.get('_ui_navigation_keyboard'):
        return
    signature=data.get('_ui_navigation_signature')
    if data.get('_ui_navigation_sent')==signature and not data.get('_ui_reply_keyboard'):
        return
    text={'ru':'Меню действий','ko':'메뉴','en':'Action menu'}.get(data.get('_language'),'Меню действий')
    message=await update.effective_message.reply_text(text,reply_markup=data['_ui_navigation_keyboard'])
    ref=_ref(message,update)
    if ref:
        old=data.get('_ui_navigation_message')
        data['_ui_navigation_message']=ref
        if old and old!=ref and old[0]==ref[0]:
            await _remove(context,old)
    data['_ui_navigation_sent']=signature
    data['_ui_reply_keyboard']=False


async def screen(update,context,text,*,reply_markup=None,keep=False,key=None,parse_mode=None):
    text=with_cancel_hint(text,context)
    formatting={'parse_mode':parse_mode} if parse_mode else {}
    if context is None or getattr(context,'user_data',None) is None:
        return await update.effective_message.reply_text(text,reply_markup=reply_markup,**formatting)
    data=context.user_data
    if key and key in data.get('_ui_results',{}):
        return
    if reply_markup is None and data.get('_ui_navigation_keyboard'):
        reply_markup=data['_ui_navigation_keyboard']
    if isinstance(reply_markup,InlineKeyboardMarkup):
        await ensure_navigation(update,context)
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
            message=await context.bot.edit_message_text(chat_id=old[0],message_id=old[1],text=text,reply_markup=reply_markup,**formatting)
            ref=old
        except BadRequest as error:
            if 'message is not modified' in str(error).lower():
                ref=old
        except TelegramError:
            pass
    if ref is None:
        message=await update.effective_message.reply_text(text,reply_markup=ReplyKeyboardRemove() if remove_keyboard else reply_markup,**formatting)
        ref=_ref(message,update)
        if remove_keyboard and isinstance(reply_markup,InlineKeyboardMarkup) and ref and getattr(context,'bot',None):
            await context.bot.edit_message_reply_markup(chat_id=ref[0],message_id=ref[1],reply_markup=reply_markup)
        is_navigation=reply_markup is not None and reply_markup==data.get('_ui_navigation_keyboard')
        data['_ui_reply_keyboard']=isinstance(reply_markup,ReplyKeyboardMarkup) and not is_navigation
        if is_navigation:
            data['_ui_navigation_sent']=data.get('_ui_navigation_signature')
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


async def result(update,context,text,*,reply_markup=None,key=None,retire_refs=()):
    message=await screen(update,context,text,reply_markup=reply_markup,keep=True,key=key)
    # Explicitly supplied action cards are superseded only after the result exists.
    # Ordinary notifications, receipts and unrelated navigation are never inferred here.
    data=getattr(context,'user_data',None) or {}
    result_ref=data.get('_ui_results',{}).get(key) if key else _ref(message,update)
    if result_ref:
        seen=set()
        for ref in retire_refs:
            if ref and ref!=result_ref and ref[0]==getattr(update.effective_chat,'id',None) and tuple(ref) not in seen:
                seen.add(tuple(ref))
                await _remove(context,ref)
    return message


def action_source(update):
    """For an authenticated, validated action only; media messages stay intact."""
    message=getattr(update,'effective_message',None)
    if not getattr(update,'callback_query',None) or not isinstance(getattr(message,'text',None),str):
        return None
    return _ref(message,update)


async def error(update,context,text):
    data=getattr(context,'user_data',None) or {}
    if data.get('_language')=='en':
        from app.error_texts import english_error
        text=english_error(text)
    text=with_cancel_hint(text,context)
    message=await update.effective_message.reply_text(text)
    if context is not None and getattr(context,'user_data',None) is not None:
        ref=_ref(message,update)
        if ref:
            await _remove(context,context.user_data.get('_ui_error'))
            context.user_data['_ui_error']=ref
    return message
