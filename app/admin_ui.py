"""Private Telegram admin interface. Drafts are local; mutations are transactional."""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup

from app import ui_messages as ui
from app.bookings import (AccessDenied, BookingError, SlotUnavailable, StaleBooking,
                         create_booking, change_booking, get_booking, list_bookings, parse_amount, quote_rental)
from app.database import connect_database
from app.users import admin_role, register_client
from app.business_config import load_business_config
from app.rental_rules import next_cleaning, RulesError
from app.date_picker import month_keyboard,parse_month

SEOUL = ZoneInfo('Asia/Seoul')


def tr(lang, ru, ko):
    return ko if lang == 'ko' else ru


def menu(lang,owner=False):
    buttons=[
        [Button(tr(lang,'Все брони','전체 예약'),callback_data='adm:list:all:0'),
         Button(tr(lang,'Ожидают решения','승인 대기'),callback_data='adm:list:pending:0')],
        [Button(tr(lang,'Создать заявку','예약 신청 만들기'),callback_data='adm:new:booking'),
         Button(tr(lang,'Блокировать время','시간 차단'),callback_data='adm:new:block')],
        [Button(tr(lang,'Блокировки','시간 차단 목록'),callback_data='adm:list:blocks:0')],
    ]
    if owner:
        buttons.append([Button(tr(lang,'Администраторы','관리자'),callback_data='staff:list:0')])
    return Markup(buttons)


def parse_date(text):
    try:
        return datetime.strptime(text.strip(), '%Y-%m-%d %H:%M').replace(tzinfo=SEOUL)
    except ValueError:
        raise BookingError('Используй формат YYYY-MM-DD HH:MM / YYYY-MM-DD HH:MM 형식을 사용하세요.') from None


def fmt(value):
    return value.astimezone(SEOUL).strftime('%Y-%m-%d %H:%M')


def card(row, lang):
    status = {
        'hold':tr(lang,'Удержание','임시 보류'), 'pending':tr(lang,'Ожидает подтверждения','승인 대기'),
        'confirmed':tr(lang,'Подтверждено','확정'), 'cancelled':tr(lang,'Отменено','취소'),
        'expired':tr(lang,'Истекло','만료'), 'completed':tr(lang,'Завершено','완료'),
    }[row['status']]
    text = (f"#{row['booking_id']} · " + tr(lang,'Блокировка','시간 차단') if row['type']=='block'
            else f"#{row['booking_id']} · " + tr(lang,'Бронь','예약'))
    text += f"\n{status}\n{fmt(row['starts_at'])} → {fmt(row['ends_at'])}\nAsia/Seoul"
    text += f"\n{row['amount']} {row['unit']}"
    text+='\n'+tr(lang,'Предоплата получена: ','확인된 선입금: ')+f"{row['deposit_paid_amount']} {row['unit']}"
    if row.get('cleaning_ends_at'):
        text+='\n'+tr(lang,'Уборка: ','청소: ')+fmt(row['cleaning_starts_at'])+' → '+fmt(row['cleaning_ends_at'])
    text += '\n' + tr(lang,'Клиент ID: ','고객 ID: ') + str(row['user_id'] or '—')
    if row.get('expires_at'):
        text += '\n' + tr(lang,'Срок заявки: ','신청 만료: ') + fmt(row['expires_at'])
    sync=row.get('calendar_sync') or {}
    if sync:
        text+='\nGoogle Calendar: '+sync.get('status','pending')
        if sync.get('last_error'):
            text+=' · '+sync['last_error']
    return text


def controls(row, lang):
    bid, version = row['booking_id'], row['version']
    buttons = []
    from app.payment_ui import admin_controls
    buttons.extend(admin_controls(row,lang))
    if row['status']=='pending':
        buttons.append([Button(tr(lang,'Подтвердить','승인'),callback_data=f'adm:ask:confirm:{bid}:{version}')])
    if row['status']=='hold':
        buttons.append([Button(tr(lang,'Отправить на подтверждение','승인 요청'),callback_data=f'adm:ask:submit:{bid}:{version}')])
    if row['status'] in ('pending','confirmed'):
        buttons.append([Button(tr(lang,'Перенести','일정 변경'),callback_data=f'adm:ask:move:{bid}:{version}')])
    if row['status'] in ('hold','pending','confirmed'):
        label=tr(lang,'Снять блокировку','시간 차단 해제') if row['type']=='block' else tr(lang,'Отменить запись','예약 취소')
        buttons.append([Button(label,callback_data=f'adm:ask:cancel:{bid}:{version}')])
    buttons.append([Button(tr(lang,'Обновить','새로고침'),callback_data=f'adm:view:{bid}')])
    if row.get('calendar_sync') or row['status']=='confirmed':
        buttons.append([Button(tr(lang,'Повторить синхронизацию Calendar','캘린더 다시 동기화'),callback_data=f'adm:calendar:{bid}')])
    buttons.append([Button(tr(lang,'В меню администратора','관리자 메뉴'),callback_data='adm:menu')])
    return Markup(buttons)


async def call(update, context, operation):
    """Recheck identity and role for every input/button, including final confirmation."""
    if not update.effective_user or not update.effective_chat or update.effective_chat.type != 'private':
        raise AccessDenied('Private chat required')
    user = update.effective_user
    def execute():
        with connect_database(context.bot_data['settings']) as connection:
            client = register_client(connection, telegram_user_id=user.id,
                telegram_chat_id=update.effective_chat.id, name=user.full_name,
                username=user.username, language_code=user.language_code)
            if admin_role(connection,user.id) not in ('owner','admin'):
                raise AccessDenied('Доступ запрещён / 접근 권한이 없습니다.')
            return operation(connection,user.id,client.language)
    return await asyncio.to_thread(execute)


def draft(context, **values):
    data = dict(token=uuid4().hex[:12], request_key=uuid4(), **values)
    context.user_data['admin_draft'] = data
    return data


async def prompt(update, data, lang, context):
    if data['step'] in ('start','end'):
        await show_dates(update,context,data,lang)
        return
    steps = {
        'start':tr(lang,'Введи начало: YYYY-MM-DD HH:MM. Время Сеула.','시작 시간을 입력하세요: YYYY-MM-DD HH:MM (서울 시간).'),
        'end':tr(lang,'Введи окончание: YYYY-MM-DD HH:MM. При переходе через полночь укажи следующую дату.','종료: YYYY-MM-DD HH:MM. 자정을 넘으면 다음 날짜를 입력하세요.'),
        'client':tr(lang,'Введи Telegram ID клиента или «-», если у клиента нет Telegram.','고객 Telegram ID를 입력하거나 Telegram이 없는 고객은 - 를 입력하세요.'),
        'price':tr(lang,'Введи «авто» для расчёта по тарифам или согласованную полную стоимость: 100000 KRW','요금 자동 계산은 auto, 수동 입력은 100000 KRW 형식으로 입력하세요.'),
    }
    await ui.screen(update,context,steps[data['step']] + '\n/cancel — ' + tr(lang,'прервать ввод','입력 중단'))


def picker_minimum(data):
    now=datetime.now(SEOUL)
    if data['step']=='end':
        rental=data.get('kind',data.get('row',{}).get('type'))=='booking'
        hours=load_business_config().booking.minimum_hours if rental else 1
        return max(now,data['start']+timedelta(hours=hours))
    return now


async def show_dates(update,context,data,lang,month=None,text=None):
    minimum=picker_minimum(data).date()
    month=month or minimum.replace(day=1)
    if month<minimum.replace(day=1):
        raise BookingError('Past date')
    data.pop('picker_day',None)
    title=tr(lang,'Выбери дату начала','시작 날짜 선택') if data['step']=='start' else tr(lang,'Выбери дату окончания','종료 날짜 선택')
    text=text or title+tr(lang,'. Или введи YYYY-MM-DD HH:MM. Время Сеула. × — недоступные даты. /cancel — прервать.',
        '. 또는 YYYY-MM-DD HH:MM 형식으로 입력하세요. 서울 시간 기준. ×는 선택 불가 날짜입니다. /cancel 로 중단.')
    prefix=f"adm:cal:{data['token']}:{data['step']}"
    await ui.screen(update,context,text,reply_markup=month_keyboard(prefix,month,minimum,lang))


async def show_hours(update,context,data,lang,day):
    minimum=picker_minimum(data)
    if day<minimum.date():
        raise BookingError('Past date')
    values=[hour for hour in range(24) if datetime.combine(day,datetime.min.time(),SEOUL)+timedelta(hours=hour)>=minimum]
    if not values:
        await show_dates(update,context,data,lang,day.replace(day=1),tr(lang,'На эту дату подходящих часов нет. Выбери другую дату.','해당 날짜에 선택 가능한 시간이 없습니다. 다른 날짜를 선택하세요.'))
        return
    data['picker_day']=day
    prefix=f"adm:hour:{data['token']}:{data['step']}:{day.isoformat()}"
    buttons=[Button(f'{hour:02}:00',callback_data=prefix+f':{hour}') for hour in values]
    rows=[buttons[i:i+4] for i in range(0,len(buttons),4)]
    rows.append([Button(tr(lang,'К календарю','달력으로'),callback_data=f"adm:cal:{data['token']}:{data['step']}:month:{day.strftime('%Y-%m')}")])
    title=tr(lang,'Выбери час начала','시작 시간 선택') if data['step']=='start' else tr(lang,'Выбери час окончания','종료 시간 선택')
    await ui.screen(update,context,f'{title} · {day.isoformat()}\nAsia/Seoul\n'+tr(lang,'Можно ввести YYYY-MM-DD HH:MM вручную.','YYYY-MM-DD HH:MM 형식으로 직접 입력할 수도 있습니다.'),reply_markup=Markup(rows))


async def accept_datetime(update,context,data,lang,value):
    if data['step']=='start':
        if value<=datetime.now(SEOUL):
            raise BookingError('Начало должно быть в будущем / 시작은 미래여야 합니다.')
        data.update(start=value,step='end')
    elif data['step']=='end':
        if value<=data['start']:
            raise BookingError('Окончание должно быть позже начала / 종료는 시작 이후여야 합니다.')
        rental=data.get('kind',data.get('row',{}).get('type'))=='booking'
        if rental and value-data['start']<timedelta(hours=load_business_config().booking.minimum_hours):
            raise BookingError(f'Minimum rental duration: {load_business_config().booking.minimum_hours} hours')
        data['end']=value
        if data['action']=='move' or data.get('kind')=='block':
            await review(update,data,lang,context)
            return
        data['step']='client'
    else:
        raise BookingError('Draft expired')
    data.pop('picker_day',None)
    await prompt(update,data,lang,context)


async def review(update, data, lang, context):
    if data.get('start') and (data.get('kind')=='booking' or data.get('row',{}).get('type')=='booking'):
        try:
            data['planned_cleaning']=next_cleaning(data['end'],load_business_config())
        except RulesError as error:
            raise BookingError(str(error)) from None
    data['step']='review'
    labels = {'create':tr(lang,'Создать запись?','새 항목을 만드시겠습니까?'),
              'confirm':tr(lang,'Подтвердить бронь? Проверь поступление оплаты в банке.','예약을 승인하시겠습니까? 은행 입금을 확인하세요.'),
              'submit':tr(lang,'Отправить на подтверждение?','승인 요청을 보내시겠습니까?'),
              'cancel':tr(lang,'Отменить запись и освободить время?','취소하고 시간을 비우시겠습니까?'),
              'move':tr(lang,'Перенести? Согласованная стоимость сохраняется.','일정을 변경하시겠습니까? 합의된 금액은 유지됩니다.')}
    text=labels[data['action']]
    if data['action']=='create' and data.get('kind')=='block':
        text=tr(lang,'Заблокировать это время? Нажми «Да, выполнить», чтобы сохранить блокировку и отправить её в календарь.','이 시간을 차단하시겠습니까? 실행을 누르면 저장 후 캘린더에 반영됩니다.')
    if data.get('row'):
        text+='\n\n'+card(data['row'],lang)
    if data.get('start'):
        text+=f"\n\n{fmt(data['start'])} → {fmt(data['end'])}\nAsia/Seoul"
    if data.get('planned_cleaning'):
        a,b=data['planned_cleaning']
        text+='\n'+tr(lang,'Новая уборка: ','예정 청소: ')+fmt(a)+' → '+fmt(b)
    if data['action']=='create':
        minutes=load_business_config().booking.confirmation_timeout_minutes
        text+= '\n' + tr(lang,'Блокировка','시간 차단') if data['kind']=='block' else '\n'+tr(lang,f'Заявка: ожидание до {minutes} минут',f'신청: 최대 {minutes}분 승인 대기')
        text+=f"\n{data['amount']} {data['unit']}\n"+tr(lang,'Клиент ID: ','고객 ID: ')+str(data.get('client_id') or '—')
        if data.get('quote'):
            text+='\n'+tr(lang,'Уборка до: ','청소 종료: ')+fmt(data['quote']['cleaning_ends_at'])
    await ui.screen(update,context,text,reply_markup=Markup([
        [Button(tr(lang,'Да, выполнить','실행'),callback_data='adm:apply:'+data['token'])],
        [Button(tr(lang,'Прервать','중단'),callback_data='adm:abort:'+data['token'])],
    ]))


async def callback(update, context):
    query=update.callback_query
    if not query or not query.data or not query.data.startswith('adm:'):
        return
    await query.answer()
    try:
        lang=await call(update,context,lambda c,a,l:l)
        context.user_data.pop('staff_draft',None)
        parts=query.data.split(':')
        command=parts[1]
        if command=='menu':
            owner=await call(update,context,lambda c,a,l:admin_role(c,a)=='owner')
            await ui.screen(update,context,tr(lang,'Меню администратора','관리자 메뉴'),reply_markup=menu(lang,owner=owner))
        elif command=='list':
            mode,page=parts[2],int(parts[3])
            if mode not in ('all','pending','blocks') or not 0<=page<=100000:
                raise BookingError('Invalid page')
            rows=await call(update,context,lambda c,a,l:list_bookings(c,a,limit=9,offset=page*8,pending_only=mode=='pending',blocks_only=mode=='blocks'))
            buttons=[[Button(f"#{r['booking_id']} · "+(tr(lang,'Блокировка','시간 차단') if r['type']=='block' else tr(lang,'Бронь','예약'))+f" · {fmt(r['starts_at'])}",callback_data=f"adm:view:{r['booking_id']}")] for r in rows[:8]]
            nav=[]
            if page:
                nav.append(Button('←',callback_data=f'adm:list:{mode}:{page-1}'))
            if len(rows)>8:
                nav.append(Button('→',callback_data=f'adm:list:{mode}:{page+1}'))
            if nav:
                buttons.append(nav)
            buttons.append([Button(tr(lang,'В меню администратора','관리자 메뉴'),callback_data='adm:menu')])
            await ui.screen(update,context,tr(lang,'Выбери запись:','항목을 선택하세요:') if rows else tr(lang,'Записей нет.','항목이 없습니다.'),reply_markup=Markup(buttons) if buttons else menu(lang))
        elif command=='view':
            row=await call(update,context,lambda c,a,l:get_booking(c,a,int(parts[2])))
            await ui.screen(update,context,card(row,lang),reply_markup=controls(row,lang))
        elif command=='calendar':
            def retry(c,a,l):
                from app.calendar_queue import queue_calendar
                row=get_booking(c,a,int(parts[2]))
                queue_calendar(c,row)
                return get_booking(c,a,row['booking_id'])
            row=await call(update,context,retry)
            from app.notifications import wake_delivery
            wake_delivery(context)
            await ui.screen(update,context,card(row,lang),reply_markup=controls(row,lang))
        elif command=='new':
            context.user_data.pop('receipt_upload',None)
            context.user_data.pop('client_draft',None)
            if parts[2] not in ('booking','block'):
                raise BookingError('Invalid type')
            data=draft(context,action='create',kind=parts[2],step='start',amount=Decimal('0'),unit='KRW',client_id=None)
            await prompt(update,data,lang,context)
        elif command in ('cal','hour'):
            data=context.user_data.get('admin_draft')
            if not data or data['token']!=parts[2] or data['step'] not in ('start','end') or data['step']!=parts[3]:
                raise BookingError('Черновик устарел. Открой /admin. / 입력이 만료되었습니다. /admin 을 여세요.')
            if command=='hour':
                day=datetime.strptime(parts[4],'%Y-%m-%d').date()
                hour=int(parts[5])
                if data.get('picker_day')!=day or not 0<=hour<24:
                    raise BookingError('Invalid hour selection')
                value=datetime.combine(day,datetime.min.time(),SEOUL)+timedelta(hours=hour)
                await accept_datetime(update,context,data,lang,value)
            elif parts[4]=='month':
                await show_dates(update,context,data,lang,parse_month(parts[5]))
            elif parts[4]=='day':
                await show_hours(update,context,data,lang,datetime.strptime(parts[5],'%Y-%m-%d').date())
        elif command=='ask':
            context.user_data.pop('receipt_upload',None)
            context.user_data.pop('client_draft',None)
            action,bid,version=parts[2],int(parts[3]),int(parts[4])
            if action not in ('confirm','cancel','submit','move'):
                raise BookingError('Invalid action')
            row=await call(update,context,lambda c,a,l:get_booking(c,a,bid))
            if row['version']!=version:
                raise StaleBooking('Reload booking')
            data=draft(context,action=action,row=row,step='start' if action=='move' else 'review')
            if action=='move':
                await prompt(update,data,lang,context)
            else:
                await review(update,data,lang,context)
        elif command in ('apply','abort'):
            data=context.user_data.get('admin_draft')
            if not data or data['token']!=parts[2]:
                raise BookingError('Черновик устарел. Открой /admin. / 임시 입력이 만료되었습니다. /admin 을 여세요.')
            if command=='abort':
                context.user_data.pop('admin_draft',None)
                await ui.screen(update,context,tr(lang,'Ввод прерван.','입력이 중단되었습니다.'),reply_markup=menu(lang))
                return
            if data['step']=='done':
                current=await call(update,context,lambda c,a,l:get_booking(c,a,data['result']['booking_id']))
                await ui.result(update,context,card(current,lang),reply_markup=controls(current,lang),key=f"admin:{current['booking_id']}:{current['version']}")
                return
            if data['step']!='review':
                raise BookingError('Complete the form first')
            if data['action']=='create':
                def create_reviewed(c,a,l):
                    if data.get('automatic_price') and quote_rental(data['start'],data['end'])!=data['quote']:
                        raise BookingError('Условия изменились. Открой /admin заново. / 조건이 변경되었습니다. /admin 에서 다시 시작하세요.')
                    return create_booking(c,a,user_id=data['client_id'],
                        starts_at=data['start'],ends_at=data['end'],amount=None if data.get('automatic_price') else data['amount'],unit=data['unit'],
                        request_key=data['request_key'],kind=data['kind'],status='confirmed' if data['kind']=='block' else 'pending')
                result=await call(update,context,create_reviewed)
            else:
                result=await call(update,context,lambda c,a,l:change_booking(c,a,data['row']['booking_id'],action=data['action'],
                    expected_version=data['row']['version'],starts_at=data.get('start'),ends_at=data.get('end')))
            data.update(step='done',result=result)
            from app.notifications import wake_delivery
            wake_delivery(context)
            await ui.result(update,context,card(result,lang),reply_markup=controls(result,lang),key=f"admin:{result['booking_id']}:{result['version']}")
    except (BookingError,ValueError,IndexError) as error:
        await show_error(update,error,context)


async def text_input(update, context):
    data=context.user_data.get('admin_draft')
    if not data or data['step'] in ('review','done'):
        return False
    try:
        lang=await call(update,context,lambda c,a,l:l)
        text=update.effective_message.text.strip()
        if data['step'] in ('start','end'):
            await accept_datetime(update,context,data,lang,parse_date(text))
            return True
        elif data['step']=='client':
            if text=='-':
                data['client_id']=None
            else:
                telegram_id=int(text)
                def find(c,a,l):
                    row=c.execute('SELECT user_id FROM clients WHERE telegram_user_id=%s',(telegram_id,)).fetchone()
                    if row is None:
                        raise BookingError('Клиент не найден: сначала /start / 고객이 먼저 /start 를 보내야 합니다.')
                    return row[0]
                data['client_id']=await call(update,context,find)
            data['step']='price'
        elif data['step']=='price':
            if text.lower() in ('авто','auto'):
                quote=quote_rental(data['start'],data['end'])
                data.update(amount=quote['amount'],unit=quote['unit'],quote=quote,automatic_price=True)
                await review(update,data,lang,context)
                return True
            amount,unit=text.split()
            unit=unit.upper()
            if len(unit)!=3 or not unit.isascii() or not unit.isalpha():
                raise BookingError('Пример: 100000 KRW / 예: 100000 KRW')
            data.update(amount=parse_amount(amount),unit=unit,automatic_price=False)
            await review(update,data,lang,context)
            return True
        await prompt(update,data,lang,context)
    except (BookingError,ValueError,InvalidOperation) as error:
        await show_error(update,error,context)
    return True


async def cancel_input(update,context):
    if not update.effective_chat or update.effective_chat.type!='private':
        return
    context.user_data.pop('admin_draft',None)
    await ui.screen(update,context,'Ввод прерван. / 입력이 중단되었습니다.\n/admin')


async def show_error(update,error,context=None):
    if isinstance(error,AccessDenied):
        text='Доступ запрещён. / 접근 권한이 없습니다.'
    elif isinstance(error,SlotUnavailable):
        text='Время занято. Выбери другой интервал; исходная бронь при переносе сохранена. / 이미 예약된 시간입니다. 기존 예약은 유지됩니다.'
    elif isinstance(error,StaleBooking):
        text='Запись изменилась. Открой её заново через /admin. / 항목이 변경되었습니다. /admin 에서 다시 여세요.'
    elif isinstance(error,BookingError):
        if str(error).startswith('Minimum rental duration:'):
            hours=load_business_config().booking.minimum_hours
            await ui.error(update,context,f'Минимум {hours} часов. / 최소 {hours}시간입니다.')
            return
        translations={
            'Request expired; confirmation or transfer is not permitted': 'Срок заявки истёк. Создай новую через /admin. / 신청이 만료되었습니다. /admin 에서 새로 만드세요.',
            'Only a future pending booking can be confirmed': 'Можно подтвердить только будущую заявку в ожидании. / 미래의 승인 대기 예약만 확정할 수 있습니다.',
            'Only future pending or confirmed bookings can be moved': 'Перенос доступен только будущим ожидающим или подтверждённым записям. / 미래의 대기 또는 확정 항목만 변경할 수 있습니다.',
            'Booking not found': 'Запись не найдена. / 항목을 찾을 수 없습니다.',
            'Start must be in the future': 'Начало должно быть в будущем. / 시작은 미래여야 합니다.',
            'Booking cannot be cancelled': 'Эту запись уже нельзя отменить. / 이 항목은 취소할 수 없습니다.',
            'Amount out of range': 'Стоимость должна быть неотрицательной и меньше 1000000000000. / 금액은 0 이상, 1000000000000 미만이어야 합니다.',
            'Amount supports at most two decimal places': 'Укажи сумму с максимум двумя знаками после точки. / 소수점 이하 두 자리까지 입력하세요.',
            'Invalid amount': 'Неверная сумма. Пример: 100000 KRW / 잘못된 금액입니다. 예: 100000 KRW',
            'Client not found': 'Клиент больше не найден. Начни заново через /admin. / 고객을 찾을 수 없습니다. /admin 에서 다시 시작하세요.',
        }
        text=translations.get(str(error),str(error))
    else:
        text='Проверь формат ввода. / 입력 형식을 확인하세요.'
    await ui.error(update,context,text)
