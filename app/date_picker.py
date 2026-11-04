"""Localized inline month calendar; all dates are interpreted by callers in Seoul."""

import calendar
from datetime import date
from telegram import InlineKeyboardButton as Button,InlineKeyboardMarkup as Markup

MONTHS_RU=('Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь')


def parse_month(value):
    return date.fromisoformat(value+'-01')


def month_keyboard(prefix,month,minimum,lang):
    month=month.replace(day=1)
    title=f'{month.year}년 {month.month}월' if lang=='ko' else f'{MONTHS_RU[month.month-1]} {month.year}'
    noop=prefix+':noop'
    weekdays=('월','화','수','목','금','토','일') if lang=='ko' else ('Пн','Вт','Ср','Чт','Пт','Сб','Вс')
    rows=[[Button(title,callback_data=noop)],[Button(day,callback_data=noop) for day in weekdays]]
    for week in calendar.Calendar(firstweekday=0).monthdayscalendar(month.year,month.month):
        buttons=[]
        for day in week:
            value=date(month.year,month.month,day) if day else None
            allowed=value is not None and value>=minimum
            label=str(day) if allowed else (f'×{day}' if day else '·')
            buttons.append(Button(label,callback_data=prefix+':day:'+value.isoformat() if allowed else noop))
        rows.append(buttons)
    nav=[]
    index=month.year*12+month.month-1
    for delta,label in ((-1,'←'),(1,'→')):
        year,zero_month=divmod(index+delta,12)
        if 1<=year<=9999:
            other=date(year,zero_month+1,1)
            if other>=minimum.replace(day=1):
                nav.append(Button(label,callback_data=prefix+':month:'+other.strftime('%Y-%m')))
    rows.append(nav)
    return Markup(rows)
