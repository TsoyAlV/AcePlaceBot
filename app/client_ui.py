"""Customer booking flow; identity, price and status are server-controlled."""

import asyncio
from datetime import datetime,timedelta
from uuid import uuid4

from telegram import InlineKeyboardButton as Button,InlineKeyboardMarkup as Markup

from app.admin_ui import tr,fmt
from app.availability import get_availability_windows
from app import ui_messages as ui
from app.bookings import (BookingError,SlotUnavailable,StaleBooking,quote_rental,
    create_client_booking,change_booking,get_booking,quote_cancellation,CancellationTermsChanged)
from app.cancellation_rules import current_policy,policy_for,policy_text,quote_text,cancellation_text
from app.business_config import load_business_config
from app.database import connect_database
from app.rental_rules import SEOUL
from app.users import register_client
from app.date_picker import month_keyboard,parse_month


def menu(lang):
    return Markup([[Button(tr(lang,'Забронировать','예약하기'),callback_data='cli:new'),
        Button(tr(lang,'Мои брони','내 예약'),callback_data='cli:mine:0')]])


async def call(update,context,operation):
    user,chat=update.effective_user,update.effective_chat
    if not user or not chat or chat.type!='private':
        raise BookingError('Private chat required')
    def execute():
        with connect_database(context.bot_data['settings']) as c:
            client=register_client(c,telegram_user_id=user.id,telegram_chat_id=chat.id,
                name=user.full_name,username=user.username,language_code=user.language_code)
            return operation(c,user.id,client)
    return await asyncio.to_thread(execute)


def own_booking(c,actor,bid):
    row=get_booking(c,actor,bid)
    own=c.execute('SELECT user_id FROM clients WHERE telegram_user_id=%s',(actor,)).fetchone()[0]
    if row['user_id']!=own:
        raise BookingError('Booking access denied')
    return row


def card(row,lang):
    statuses={'pending':tr(lang,'Ожидает подтверждения','승인 대기'),'confirmed':tr(lang,'Подтверждено','확정'),
        'cancelled':tr(lang,'Отменено','취소'),'expired':tr(lang,'Срок истёк','만료'),
        'hold':tr(lang,'Удержание','임시 보류'),'completed':tr(lang,'Завершено','완료')}
    status=row['status']
    if status in ('pending','hold') and row['expires_at']<=datetime.now(SEOUL):
        status='expired'
    text=f"Ace Place · #{row['booking_id']}\n{statuses[status]}\n{fmt(row['starts_at'])} → {fmt(row['ends_at'])}\nAsia/Seoul\n{row['amount']} {row['unit']}"
    text+='\n'+tr(lang,'Предоплата получена: ','확인된 선입금: ')+f"{row['deposit_paid_amount']} {row['unit']}"
    receipt=row.get('payment_receipt')
    if receipt:
        labels={'pending':tr(lang,'Скриншот на проверке','이미지 검토 중'),'accepted':tr(lang,'Предоплата проверена','선입금 확인됨'),'rejected':tr(lang,'Нужен другой скриншот','다른 이미지가 필요합니다')}
        text+='\n'+labels[receipt['review_status']]
    if status in ('pending','hold'):
        text+='\n'+tr(lang,'Ответ ожидается до: ','승인 대기 기한: ')+fmt(row['expires_at'])
    if row['type']=='booking':
        text+='\n\n'+policy_text(policy_for(row),lang)
    details=cancellation_text(row,lang)
    if details:
        text+='\n\n'+details
    return text


def controls(row,lang):
    buttons=[[Button(tr(lang,'Обновить статус','상태 새로고침'),callback_data=f"cli:view:{row['booking_id']}")]]
    receipt=row.get('payment_receipt')
    if row['status'] in ('pending','confirmed','expired','cancelled') and row['deposit_paid_amount']==0 and (not receipt or receipt['review_status']=='rejected'):
        buttons.append([Button(tr(lang,'Отправить подтверждение оплаты','결제 증빙 보내기'),callback_data=f"pay:upload:{row['booking_id']}")])
    paid=row['deposit_paid_amount']+row['balance_paid_amount']
    if row['type']=='booking' and row['starts_at']>datetime.now(SEOUL) and (row['status']=='confirmed' or (row['status'] in ('hold','pending') and paid>0 and row['expires_at']>datetime.now(SEOUL))):
        buttons.append([Button(tr(lang,'Отменить бронь','예약 취소'),callback_data=f"cli:cancel:{row['booking_id']}:{row['version']}")])
    elif row['status'] in ('pending','hold') and row['expires_at']>datetime.now(SEOUL):
        buttons.append([Button(tr(lang,'Отозвать заявку','신청 철회'),callback_data=f"cli:withdraw:{row['booking_id']}:{row['version']}")])
    return Markup(buttons)


async def show_starts(update,context,lang,page=0):
    data=context.user_data['client_draft']
    rows=data['options']
    if page<0 or page*8>=max(1,len(rows)):
        raise BookingError('Invalid page')
    buttons=[[Button(datetime.fromisoformat(item['starts_at']).strftime('%H:%M'),
        callback_data=f"cli:pick:{data['token']}:{index}")] for index,item in enumerate(rows[page*8:page*8+8],page*8)]
    nav=[]
    if page:
        nav.append(Button('←',callback_data=f"cli:page:{data['token']}:{page-1}"))
    if (page+1)*8<len(rows):
        nav.append(Button('→',callback_data=f"cli:page:{data['token']}:{page+1}"))
    if nav:
        buttons.append(nav)
    await ui.screen(update,context,tr(lang,'Выбери время начала (Сеул). До отправки заявки время не резервируется.','시작 시간을 선택하세요 (서울). 신청 전에는 시간이 확보되지 않습니다.'),reply_markup=Markup(buttons))


async def show_review(update,context,data,lang):
    quote=data['quote']
    payment=load_business_config().payment
    data['deposit']=payment.deposit_amount
    data['payment']=(payment.bank,payment.account_number,payment.account_holder)
    data['cancellation_policy']=current_policy(load_business_config())
    text=(f"Ace Place\n{fmt(data['start'])} → {fmt(data['end'])}\nAsia/Seoul\n"
          +tr(lang,'Полная стоимость: ','총금액: ')+f"{quote['amount']} {quote['unit']}\n"
          +tr(lang,'Предоплата: ','선입금: ')+f'{payment.deposit_amount} KRW\n'
          +f'{payment.bank}\n{payment.account_number}\n{payment.account_holder}\n'
          +tr(lang,'Предоплата входит в полную стоимость. После отправки дождись подтверждения администратора. Перевод сам по себе не подтверждает бронь.','선입금은 총금액에 포함됩니다. 신청 후 관리자 승인을 기다려 주세요. 송금만으로 예약이 확정되지 않습니다.'))
    text+='\n\n'+policy_text(data['cancellation_policy'],lang)
    data['step']='review'
    await ui.screen(update,context,text,reply_markup=Markup([
        [Button(tr(lang,'Отправить заявку','예약 신청'),callback_data='cli:send:'+data['token'])],
        [Button(tr(lang,'Прервать','중단'),callback_data='cli:abort:'+data['token'])]]))


async def show_dates(update,context,lang,month=None,text=None):
    data=context.user_data['client_draft']
    today=datetime.now(SEOUL).date()
    month=month or today.replace(day=1)
    if month<today.replace(day=1):
        raise BookingError('Past date')
    await ui.screen(update,context,text or tr(lang,
        'Выбери дату в календаре или введи YYYY-MM-DD. Время Сеула. × — прошедшие даты. /cancel — прервать.',
        '달력에서 날짜를 선택하거나 YYYY-MM-DD 형식으로 입력하세요. 서울 시간 기준. ×는 지난 날짜입니다. /cancel 로 중단.'),
        reply_markup=month_keyboard('cli:cal:'+data['token'],month,today,lang))


async def show_cancellation(update,context,row,quote,lang,changed=False):
    token=uuid4().hex[:12]
    context.user_data['client_cancel']=dict(token=token,booking_id=row['booking_id'],version=row['version'],quote=quote)
    text=(tr(lang,'Условия изменились — проверь расчёт и подтверди заново.\n','조건이 변경되었습니다. 확인 후 다시 승인해 주세요.\n') if changed else '')
    text+=card(row,lang)+'\n\n'+quote_text(quote,lang)+'\n'+tr(lang,'Отменить бронь и освободить время?','예약을 취소하고 시간을 비우시겠습니까?')
    if (row.get('payment_receipt') or {}).get('review_status')=='pending':
        text+='\n'+tr(lang,'Скриншот ещё не проверен: неучтённую оплату согласуй с администратором.','증빙이 아직 검토 중입니다. 미확인 결제는 관리자에게 문의하세요.')
    await ui.screen(update,context,text,reply_markup=Markup([
        [Button(tr(lang,'Да, отменить бронь','예약 취소 확인'),callback_data='cli:cancel_yes:'+token)],
        [Button(tr(lang,'Сохранить бронь','예약 유지'),callback_data=f"cli:view:{row['booking_id']}")]]))


async def callback(update,context):
    query=update.callback_query
    if not query or not query.data or not query.data.startswith('cli:'):
        return
    await query.answer()
    try:
        lang=await call(update,context,lambda c,a,u:u.language)
        parts=query.data.split(':')
        command=parts[1]
        if command in ('new','mine','view'):
            context.user_data.pop('client_cancel',None)
        if command=='new':
            context.user_data.pop('staff_draft',None)
            context.user_data.pop('receipt_upload',None)
            context.user_data.pop('admin_draft',None)
            context.user_data['client_draft']=dict(token=uuid4().hex[:12],key=uuid4(),step='date')
            await show_dates(update,context,lang)
        elif command in ('cal','hours'):
            data=context.user_data.get('client_draft')
            if not data or data['token']!=parts[2] or data['step']!=('date' if command=='cal' else 'hours'):
                raise BookingError('Draft expired')
            if command=='hours':
                await accept_hours(update,context,int(parts[3]),lang)
            elif parts[3]=='month':
                await show_dates(update,context,lang,parse_month(parts[4]))
            elif parts[3]=='day':
                await accept_date(update,context,parts[4],lang)
        elif command=='date':
            await accept_date(update,context,parts[2],lang)
        elif command=='mine':
            page=int(parts[2])
            if not 0<=page<=100000:
                raise BookingError('Invalid page')
            def fetch(c,a,u):
                return c.execute("SELECT booking_id,starts_at FROM bookings WHERE user_id=%s AND status <> 'cancelled' ORDER BY starts_at DESC LIMIT 9 OFFSET %s",(u.user_id,page*8)).fetchall()
            rows=await call(update,context,fetch)
            buttons=[[Button(f'#{bid} · {fmt(start)}',callback_data=f'cli:view:{bid}')] for bid,start in rows[:8]]
            if page:
                buttons.append([Button('←',callback_data=f'cli:mine:{page-1}')])
            if len(rows)>8:
                buttons.append([Button('→',callback_data=f'cli:mine:{page+1}')])
            await ui.screen(update,context,tr(lang,'Мои брони','내 예약') if rows else tr(lang,'Броней пока нет.','아직 예약이 없습니다.'),reply_markup=Markup(buttons) if buttons else menu(lang))
        elif command in ('cancel','cancel_yes'):
            if command=='cancel':
                bid=int(parts[2])
                row=await call(update,context,lambda c,a,u:own_booking(c,a,bid))
                quote=await call(update,context,lambda c,a,u:quote_cancellation(c,a,bid,expected_version=int(parts[3])))
                await show_cancellation(update,context,row,quote,lang)
                return
            data=context.user_data.get('client_cancel')
            if not data or data['token']!=parts[2]:
                raise BookingError('Draft expired')
            try:
                row=await call(update,context,lambda c,a,u:change_booking(c,a,data['booking_id'],action='cancel',
                    expected_version=data['version'],origin='client',expected_cancellation=data['quote']))
            except CancellationTermsChanged:
                row=await call(update,context,lambda c,a,u:own_booking(c,a,data['booking_id']))
                quote=await call(update,context,lambda c,a,u:quote_cancellation(c,a,data['booking_id'],expected_version=data['version']))
                await show_cancellation(update,context,row,quote,lang,changed=True)
                return
            context.user_data.pop('client_cancel',None)
            from app.notifications import wake_delivery
            wake_delivery(context)
            await ui.result(update,context,card(row,lang),reply_markup=controls(row,lang),key=f"cancel:{row['booking_id']}:{row['version']}")
        elif command in ('view','withdraw','withdraw_yes'):
            bid=int(parts[2])
            row=await call(update,context,lambda c,a,u:own_booking(c,a,bid))
            if command=='withdraw':
                if row['status'] not in ('hold','pending') or row['version']!=int(parts[3]):
                    raise StaleBooking('Reload booking')
                await ui.screen(update,context,tr(lang,'Отозвать эту заявку?','이 신청을 철회하시겠습니까?'),reply_markup=Markup([[Button(tr(lang,'Да, отозвать','네, 철회'),callback_data=f"cli:withdraw_yes:{bid}:{row['version']}")]]))
                return
            if command=='withdraw_yes':
                def withdraw(c,a,u):
                    current=own_booking(c,a,bid)
                    if current['status'] not in ('hold','pending'):
                        raise BookingError('Only pending requests can be withdrawn here')
                    return change_booking(c,a,bid,action='cancel',expected_version=int(parts[3]),origin='client')
                row=await call(update,context,withdraw)
                from app.notifications import wake_delivery
                wake_delivery(context)
            if command=='withdraw_yes':
                await ui.result(update,context,card(row,lang),reply_markup=controls(row,lang),key=f"withdraw:{row['booking_id']}:{row['version']}")
            else:
                await ui.screen(update,context,card(row,lang),reply_markup=controls(row,lang))
        elif command in ('pick','page','send','abort'):
            data=context.user_data.get('client_draft')
            if not data or data['token']!=parts[2]:
                raise BookingError('Draft expired')
            if command=='abort':
                context.user_data.pop('client_draft',None)
                await ui.screen(update,context,tr(lang,'Ввод прерван.','입력이 중단되었습니다.'),reply_markup=menu(lang))
            elif command=='page':
                if data['step']!='options':
                    raise BookingError('Draft expired')
                await show_starts(update,context,lang,int(parts[3]))
            elif command=='pick':
                if data['step']!='options':
                    raise BookingError('Draft expired')
                index=int(parts[3])
                if not 0<=index<len(data['options']):
                    raise BookingError('Invalid selection')
                data['start']=datetime.fromisoformat(data['options'][index]['starts_at'])
                data['end']=data['start']+timedelta(hours=data['hours'])
                data['quote']=await asyncio.to_thread(quote_rental,data['start'],data['end'])
                await show_review(update,context,data,lang)
            elif command=='send':
                if data['step']=='done':
                    row=await call(update,context,lambda c,a,u:own_booking(c,a,data['result_id']))
                elif data['step']=='review':
                    payment=load_business_config().payment
                    if (data['deposit'],data['payment'])!=(payment.deposit_amount,(payment.bank,payment.account_number,payment.account_holder)):
                        raise BookingError('Quote changed; request a new quote')
                    row=await call(update,context,lambda c,a,u:create_client_booking(c,a,starts_at=data['start'],ends_at=data['end'],request_key=data['key'],expected_quote=data['quote'],expected_policy=data['cancellation_policy']))
                    data.update(step='done',result_id=row['booking_id'])
                    from app.notifications import wake_delivery
                    wake_delivery(context)
                else:
                    raise BookingError('Draft expired')
                bank,account,holder=data['payment']
                text=card(row,lang)+'\n\n'+tr(lang,'Предоплата: ','선입금: ')+f"{data['deposit']} KRW\n{bank}\n{account}\n{holder}"
                await ui.result(update,context,text,reply_markup=controls(row,lang),key=f"client:create:{row['booking_id']}")
    except (BookingError,ValueError,IndexError) as error:
        await show_error(update,error,context)


async def accept_date(update,context,text,lang):
    data=context.user_data.get('client_draft')
    if not data or data['step']!='date':
        raise BookingError('Draft expired')
    day=datetime.strptime(text,'%Y-%m-%d').date()
    if day<datetime.now(SEOUL).date():
        raise BookingError('Past date')
    data.update(day=day,step='hours')
    minimum=load_business_config().booking.minimum_hours
    choices=sorted({minimum,minimum+1,minimum+2,minimum+3,minimum+5,minimum+7})
    buttons=[Button(tr(lang,f'{hours} ч',f'{hours}시간'),callback_data=f"cli:hours:{data['token']}:{hours}") for hours in choices]
    await ui.screen(update,context,tr(lang,f'Сколько часов? Минимум {minimum}. Выбери кнопкой или введи целое число.','몇 시간인가요? 최소 '+str(minimum)+'시간입니다. 버튼을 선택하거나 정수로 입력하세요.'),
        reply_markup=Markup([buttons[:3],buttons[3:]]))


async def accept_hours(update,context,hours,lang):
    data=context.user_data.get('client_draft')
    if not data or data['step']!='hours':
        raise BookingError('Draft expired')
    if not load_business_config().booking.minimum_hours<=hours<=720:
        raise BookingError('Invalid duration')
    start=datetime.combine(data['day'],datetime.min.time(),SEOUL)
    result=await call(update,context,lambda c,a,u:get_availability_windows(c,a,start,start+timedelta(days=1,hours=hours),duration_hours=hours))
    options=[item for item in result['available_starts'] if datetime.fromisoformat(item['starts_at']).date()==data['day']]
    if not options:
        message=tr(lang,'Не удалось проверить расписание. Свяжись с администратором.','일정을 확인할 수 없습니다. 관리자에게 문의하세요.') if result['warnings'] else tr(lang,'Для этой даты свободных вариантов нет. Выбери другую дату.','해당 날짜에 가능한 시간이 없습니다. 다른 날짜를 선택하세요.')
        data['step']='date'
        await show_dates(update,context,lang,month=data['day'].replace(day=1),text=message)
    else:
        data.update(step='options',hours=hours,options=options)
        await show_starts(update,context,lang)


async def text_input(update,context):
    data=context.user_data.get('client_draft')
    if not data or data['step'] not in ('date','hours'):
        return False
    try:
        lang=await call(update,context,lambda c,a,u:u.language)
        text=update.effective_message.text.strip()
        if data['step']=='date':
            await accept_date(update,context,text,lang)
        else:
            await accept_hours(update,context,int(text),lang)
    except (BookingError,ValueError) as error:
        await show_error(update,error,context)
    return True


async def show_error(update,error,context=None):
    if isinstance(error,SlotUnavailable):
        text='Это время уже заняли. Начни заново: /book. / 이미 예약된 시간입니다. /book 으로 다시 시작하세요.'
    elif isinstance(error,StaleBooking):
        text='Статус изменился. Обнови «Мои брони». / 상태가 변경되었습니다. 내 예약을 새로고침하세요.'
    elif 'Holiday calendar' in str(error):
        text='Для этой даты ещё не проверен праздничный тариф. Обратись к администратору. / 해당 날짜의 공휴일 요금이 확인되지 않았습니다. 관리자에게 문의하세요.'
    elif 'already have a pending' in str(error):
        text='У тебя уже есть ожидающая заявка. Открой «Мои брони». / 이미 대기 중인 신청이 있습니다. 내 예약을 확인하세요.'
    else:
        text='Проверь ввод. Если карточка устарела или условия изменились, начни заново: /book. / 입력을 확인하세요. 정보가 만료되거나 조건이 변경되었다면 /book 으로 다시 시작하세요.'
    await ui.error(update,context,text)
