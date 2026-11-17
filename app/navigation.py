"""Localized bottom keyboard. Labels are routing intents, never authorization."""

from telegram import KeyboardButton,ReplyKeyboardMarkup

LABELS={
    'book':{'ru':'📅 Забронировать','ko':'📅 예약하기','en':'📅 Book now'},
    'mine':{'ru':'📋 Мои брони','ko':'📋 내 예약','en':'📋 My bookings'},
    'website':{'ru':'🌐 Наш сайт','ko':'🌐 웹사이트','en':'🌐 Our website'},
    'language':{'ru':'🌍 Language','ko':'🌍 Language','en':'🌍 Language'},
    'admin':{'ru':'⚙️ Панель администратора','ko':'⚙️ 관리자 패널','en':'⚙️ Admin panel'},
}


def label(action,lang):
    return LABELS[action].get(lang,LABELS[action]['ru'])


def action(text):
    return next((key for key,values in LABELS.items() if text in values.values()),None)


def keyboard(lang='ru',role=None):
    rows=[[KeyboardButton(label(key,lang)) for key in ('book','mine')],
          [KeyboardButton(label(key,lang)) for key in ('website','language')]]
    if role in ('owner','admin'):
        rows.append([KeyboardButton(label('admin',lang))])
    return ReplyKeyboardMarkup(rows,resize_keyboard=True,is_persistent=True,one_time_keyboard=False)


def configure(context,lang,role=None):
    data=getattr(context,'user_data',None)
    if data is not None:
        data['_language']=lang
        data['_role']=role
        data['_ui_navigation_keyboard']=keyboard(lang,role)
        data['_ui_navigation_signature']=(lang,role in ('owner','admin'))


def clear_flows(context):
    from app.ui_messages import FLOW_KEYS
    if getattr(context,'user_data',None) is None:
        return
    for key in FLOW_KEYS:
        context.user_data.pop(key,None)
