"""Durable booking-change delivery to clients and administrators inside the bot."""

import asyncio
import logging
from datetime import datetime,timedelta,timezone
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from telegram.error import Forbidden,BadRequest
from telegram import LinkPreviewOptions

from app.database import connect_database
from app.business_config import load_business_config
from app.admin_ui import card,controls,tr
from app import client_ui
from app import reviews

logger=logging.getLogger(__name__)


def visit_text(config,lang):
    """Use the client's language, falling back to the other configured language."""
    primary=config.visit.ko if lang=='ko' else config.visit.ru
    fallback=config.visit.ru if lang=='ko' else config.visit.ko
    lines=[]
    for field,label in (
        ('address',tr(lang,'Адрес: ','주소: ')),
        ('route_url',tr(lang,'Маршрут: ','오시는 길: ')),
        ('entry_instructions',tr(lang,'Инструкция входа: ','입장 안내: ')),
    ):
        value=getattr(primary,field).strip() or getattr(fallback,field).strip()
        if value:
            lines.append(label+value)
    return '\n'.join(lines)


def prepare_cleaning_reminders(settings):
    """Catch up after restarts while cleaning is still in the future; persist per-slot delivery."""
    rules=load_business_config().notifications.cleaning
    if not rules.telegram_enabled:
        return
    with connect_database(settings) as c:
        recipients=c.execute("SELECT user_id FROM admins WHERE active AND notifications_enabled AND role IN ('owner','admin')").fetchall()
        if not recipients:
            return
        with c.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""SELECT * FROM bookings WHERE type='booking' AND status='confirmed'
                AND cleaning_starts_at>clock_timestamp()
                AND cleaning_starts_at<=clock_timestamp()+%s
                ORDER BY cleaning_starts_at FOR UPDATE SKIP LOCKED""",(timedelta(hours=rules.hours_before),))
            rows=cursor.fetchall()
        for row in rows:
            queue=row['admin_notifications']
            slot=row['cleaning_starts_at'].isoformat()
            changed=False
            for (uid,) in recipients:
                key=f'cleaning:{slot}:{uid}'
                existing=queue.get(key)
                if existing and existing['status']!='cancelled':
                    continue
                queue[key]=dict(status='pending',attempts=0,event='cleaning_reminder',audience='admin',
                    user_id=uid,cleaning_start=slot)
                changed=True
            if changed:
                c.execute('UPDATE bookings SET admin_notifications=%s WHERE booking_id=%s',(Jsonb(queue),row['booking_id']))


def claim(settings):
    config=load_business_config()
    cleaning_rules=config.notifications.cleaning
    with connect_database(settings) as c:
        now=c.execute('SELECT clock_timestamp()').fetchone()[0]
        with c.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""SELECT * FROM bookings b WHERE EXISTS (
                SELECT 1 FROM jsonb_each(b.admin_notifications) e
                WHERE e.value->>'status' IN ('pending','processing')
                AND (e.value->>'retry_at' IS NULL OR
                    (e.value->>'retry_at')::timestamptz<=statement_timestamp()))
                ORDER BY dt_order FOR UPDATE SKIP LOCKED LIMIT 50""")
            rows=cursor.fetchall()
        for row in rows:
            queue=row['admin_notifications']
            for uid,state in queue.items():
                if state['status'] not in ('pending','processing'):
                    continue
                # Legacy entries are initial application notifications.
                receipt_event=state.get('event')=='receipt'
                if state.get('event')=='review':
                    stale=reviews.stale(row,state,now,config.reviews)
                    if not stale and row['ends_at']>now:
                        continue
                elif state.get('event')=='cleaning_reminder':
                    start=row.get('cleaning_starts_at')
                    stale=(not cleaning_rules.telegram_enabled or row['status']!='confirmed' or row['type']!='booking'
                        or start is None or start.isoformat()!=state.get('cleaning_start') or start<=now)
                    if not stale and start-timedelta(hours=cleaning_rules.hours_before)>now:
                        continue
                elif state.get('event')=='plan_conflict':
                    plan=row.get('request_data') or {}
                    stale=not plan.get('active') or not plan.get('conflicts') or plan.get('conflicts')!=state.get('conflicts')
                elif receipt_event:
                    receipt=row.get('payment_receipt') or {}
                    stale=(receipt.get('review_status')!='pending' or receipt.get('file_unique_id')!=state.get('receipt_unique_id'))
                else:
                    stale=(state.get('booking_version',row['version'])!=row['version'])
                if state.get('event','created')=='created' and (
                    row['status']!='pending' or row['expires_at'] is None or row['expires_at']<=now):
                    stale=True
                if stale:
                    state['status']='cancelled'
                    c.execute('UPDATE bookings SET admin_notifications=%s WHERE booking_id=%s',(Jsonb(queue),row['booking_id']))
                    continue
                if state.get('retry_at') and datetime.fromisoformat(state['retry_at'])>now:
                    continue
                target=state.get('user_id',uid)
                if state.get('audience','admin')=='client':
                    recipient=c.execute('SELECT telegram_chat_id,language FROM clients WHERE user_id=%s AND user_id=%s',
                        (target,row['user_id'])).fetchone()
                else:
                    recipient=c.execute("""SELECT u.telegram_chat_id,u.language FROM admins a JOIN clients u USING(user_id)
                        WHERE a.user_id=%s AND a.active AND a.notifications_enabled AND a.role IN ('owner','admin')""",(int(target),)).fetchone()
                if recipient is None:
                    state['status']='cancelled'
                    c.execute('UPDATE bookings SET admin_notifications=%s WHERE booking_id=%s',(Jsonb(queue),row['booking_id']))
                    continue
                token=uuid4().hex
                state.update(status='processing',attempts=state['attempts']+1,token=token,
                    retry_at=(now+timedelta(minutes=2)).isoformat())
                c.execute('UPDATE bookings SET admin_notifications=%s WHERE booking_id=%s',(Jsonb(queue),row['booking_id']))
                return row,uid,token,recipient
    return None


def finish(settings,bid,uid,token,success,permanent=False):
    with connect_database(settings) as c:
        row=c.execute('SELECT admin_notifications FROM bookings WHERE booking_id=%s FOR UPDATE',(bid,)).fetchone()
        if row is None:
            return
        queue=row[0]
        state=queue.get(uid)
        if not state or state.get('token')!=token or state['status']!='processing':
            return
        state['status']='sent' if success else ('failed' if permanent or state['attempts']>=5 else 'pending')
        c.execute('UPDATE bookings SET admin_notifications=%s WHERE booking_id=%s',(Jsonb(queue),bid))


async def deliver_pending(bot,settings,limit=20):
    await asyncio.to_thread(prepare_cleaning_reminders,settings)
    await asyncio.to_thread(reviews.prepare,settings,load_business_config().reviews,connect=connect_database)
    for _ in range(limit):
        job=await asyncio.to_thread(claim,settings)
        if job is None:
            break
        row,uid,token,(chat,lang)=job
        success,permanent=False,False
        try:
            state=row['admin_notifications'][uid]
            event=state.get('event','created')
            if event=='review':
                rules=load_business_config().reviews
                if not await asyncio.to_thread(reviews.is_current,settings,row['booking_id'],token,rules,connect=connect_database):
                    await asyncio.to_thread(finish,settings,row['booking_id'],uid,token,False,True)
                    continue
                await bot.send_message(chat_id=chat,text=reviews.render(rules,lang),parse_mode='HTML',
                    link_preview_options=LinkPreviewOptions(is_disabled=True))
                await asyncio.to_thread(finish,settings,row['booking_id'],uid,token,True)
                continue
            if event=='plan_conflict':
                from app.admin_ui import fmt
                text=tr(lang,'⚠️ Конфликт books_plans: существующие брони сохранены. Указанные занятия исключены из серии Google Calendar. Исправьте расписание или перенесите бронь.','⚠️ books_plans 일정 충돌: 기존 예약은 유지되며 해당 정기 일정은 Google Calendar에서 제외됩니다. 일정 또는 예약 시간을 변경하세요.')
                for conflict in row['request_data'].get('conflicts',[])[:20]:
                    text+='\n'+fmt(datetime.fromisoformat(conflict['start']))+' · #'+', #'.join(map(str,conflict['booking_ids'][:10]))
                await bot.send_message(chat_id=chat,text=text)
                await asyncio.to_thread(finish,settings,row['booking_id'],uid,token,True)
                continue
            client=state.get('audience','admin')=='client'
            client_cancel=state.get('origin')=='client'
            cancelled_booking=bool((row.get('cancellation') or {}).get('cancelled_at'))
            cancel_ru='Бронь отменена' if client or not client_cancel else ('Клиент отменил бронь' if cancelled_booking else 'Клиент отозвал заявку')
            cancel_ko='예약이 취소되었습니다' if client or not client_cancel else ('고객이 예약을 취소했습니다' if cancelled_booking else '고객이 신청을 철회했습니다')
            titles={
                'created':tr(lang,'Новая заявка','새 예약 신청'),
                'confirm':tr(lang,'Бронь подтверждена','예약이 확정되었습니다'),
                'cancel':tr(lang,cancel_ru,cancel_ko),
                'move':tr(lang,'Время брони изменено','예약 시간이 변경되었습니다'),
                'submit':tr(lang,'Заявка отправлена на подтверждение','예약 승인 요청'),
                'expired':tr(lang,'Срок заявки истёк','예약 신청이 만료되었습니다'),
                'deposit_received':tr(lang,'Предоплата получена. Подтверждение брони — отдельно.','선입금이 확인되었습니다. 예약 승인은 별도입니다.'),
                'receipt_rejected':tr(lang,'Нужен другой скриншот оплаты','다른 결제 증빙 이미지가 필요합니다'),
                'cleaning_reminder':tr(lang,'Напоминание: скоро уборка','알림: 청소 예정'),
                'refund_completed':tr(lang,'Бронь отменена. Возврат выполнен','예약 취소 및 환불 완료'),
            }
            text=titles.get(event,tr(lang,'Бронь изменена','예약 변경'))+'\n'
            if event=='move' and state.get('previous_start'):
                from app.admin_ui import fmt
                text+=tr(lang,'Было: ','이전: ')+fmt(datetime.fromisoformat(state['previous_start']))+' → '+fmt(datetime.fromisoformat(state['previous_end']))+'\n'
            text+=(client_ui.card(row,lang) if client else card(row,lang))
            if event=='confirm' and client:
                instructions=visit_text(load_business_config(),lang)
                if instructions:
                    text+='\n\n'+instructions
            markup=client_ui.controls(row,lang) if client else controls(row,lang)
            if event=='receipt' and not client:
                from app.payment_ui import send_receipt
                await send_receipt(bot,chat,row,lang)
            else:
                await bot.send_message(chat_id=chat,text=text,reply_markup=markup)
            success=True
        except (Forbidden,BadRequest):
            permanent=True
        except Exception as error:
            logger.warning('Booking delivery failed (%s)',type(error).__name__)
        await asyncio.to_thread(finish,settings,row['booking_id'],uid,token,success,permanent)


async def delivery_loop(application):
    while True:
        try:
            await deliver_pending(application.bot,application.bot_data['settings'])
        except Exception as error:
            logger.warning('Notification sweep failed (%s)',type(error).__name__)
        try:
            await asyncio.wait_for(application.bot_data['delivery_wakeup'].wait(),timeout=30)
        except asyncio.TimeoutError:
            pass
        application.bot_data['delivery_wakeup'].clear()


async def start_delivery(application):
    application.bot_data['delivery_wakeup']=asyncio.Event()
    application.bot_data['delivery_task']=asyncio.create_task(delivery_loop(application))


def wake_delivery(context):
    event=context.bot_data.get('delivery_wakeup')
    if event:
        event.set()
    calendar_event=context.bot_data.get('calendar_wakeup')
    if calendar_event:
        calendar_event.set()


async def stop_delivery(application):
    task=application.bot_data.pop('delivery_task',None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
