"""Durable Google Calendar pair synchronization; PostgreSQL remains authoritative."""

import argparse
import asyncio
import logging
from datetime import datetime,timedelta
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import load_settings
from app.business_config import load_business_config
from app.database import connect_database
from app.calendar_queue import queue_calendar
from app.google_calendar import CalendarAdapter,RecreateEvent,make_service
from app.rental_rules import SEOUL

logger=logging.getLogger(__name__)


def event_bodies(row,config):
    """Exactly two events for a rental, one for a manual block, none when cancelled."""
    if row['status'] not in ('confirmed','completed'):
        return {}
    bid=str(row['booking_id'])
    def body(summary,start,end,color,description):
        return dict(summary=summary,description=description,colorId=color,status='confirmed',
            start=dict(dateTime=start.astimezone(SEOUL).isoformat(),timeZone='Asia/Seoul'),
            end=dict(dateTime=end.astimezone(SEOUL).isoformat(),timeZone='Asia/Seoul'),
            visibility='private',transparency='opaque',reminders=dict(useDefault=False),
            extendedProperties=dict(private=dict(ace_place_booking_id=bid)))
    description=f"Ace Place #{bid}\nУправление через Telegram / Telegram에서 관리.\n{row['amount']} {row['unit']}"
    if row['type']=='block':
        return {'booking':body('Ace Place · Блокировка / 차단 #'+bid,row['starts_at'],row['ends_at'],config.calendar.block_color_id,description)}
    if not row.get('cleaning_starts_at') or not row.get('cleaning_ends_at'):
        raise ValueError('Confirmed rental needs an assigned cleaning slot')
    description+='\nПлощадка занята до окончания уборки / 청소 종료까지 사용 불가: '+row['cleaning_ends_at'].astimezone(SEOUL).isoformat()
    return {
        'booking':body('Ace Place · Аренда / 예약 #'+bid,row['starts_at'],row['ends_at'],config.calendar.booking_color_id,description),
        'cleaning':body('Ace Place · Уборка / 청소 #'+bid,row['cleaning_starts_at'],row['cleaning_ends_at'],config.calendar.cleaning_color_id,
            'Плановая уборка / 예정 청소.\nСвязана с арендой / 연결 예약: #'+bid),
    }


def prepare_existing(settings,limit=20):
    """Enabling integration can safely include already confirmed bookings."""
    with connect_database(settings) as c:
        with c.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT * FROM bookings WHERE status='confirmed' AND calendar_sync='{}'::jsonb ORDER BY booking_id FOR UPDATE SKIP LOCKED LIMIT %s",(limit,))
            rows=cursor.fetchall()
        for row in rows:
            queue_calendar(c,row)


def claim(settings):
    if not settings.google_calendar_id:
        return None
    with connect_database(settings) as c:
        now=c.execute('SELECT clock_timestamp()').fetchone()[0]
        with c.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""SELECT * FROM bookings WHERE
                (calendar_sync->>'status' IN ('pending','processing') AND
                    (calendar_sync->>'retry_at' IS NULL OR (calendar_sync->>'retry_at')::timestamptz<=clock_timestamp()))
                OR (calendar_sync->>'status'='synced' AND (calendar_sync->>'next_check_at')::timestamptz<=clock_timestamp())
                ORDER BY booking_id FOR UPDATE SKIP LOCKED LIMIT 1""")
            row=cursor.fetchone()
        if row is None:
            return None
        state=dict(row['calendar_sync'])
        if state.get('calendar_id') and state['calendar_id']!=settings.google_calendar_id:
            state.update(status='pending',last_error='CalendarIdChanged',retry_at=(now+timedelta(minutes=15)).isoformat())
            c.execute('UPDATE bookings SET calendar_sync=%s WHERE booking_id=%s',(Jsonb(state),row['booking_id']))
            return None
        state.update(calendar_id=settings.google_calendar_id,status='processing',token=uuid4().hex,
            target_version=row['version'],attempts=(state.get('attempts',0)+1),retry_at=(now+timedelta(minutes=3)).isoformat())
        row['calendar_sync']=state
        c.execute('UPDATE bookings SET calendar_sync=%s WHERE booking_id=%s',(Jsonb(state),row['booking_id']))
        return row


def still_current(settings,job):
    with connect_database(settings) as c:
        current=c.execute('SELECT version,calendar_sync FROM bookings WHERE booking_id=%s',(job['booking_id'],)).fetchone()
    return bool(current and current[0]==job['version'] and current[1].get('token')==job['calendar_sync']['token'])


def rotate_id(settings,job,key):
    with connect_database(settings) as c:
        row=c.execute('SELECT version,calendar_sync FROM bookings WHERE booking_id=%s FOR UPDATE',(job['booking_id'],)).fetchone()
        if row is None or row[0]!=job['version'] or row[1].get('token')!=job['calendar_sync']['token']:
            return False
        state=row[1]
        state[key]=uuid4().hex
        c.execute('UPDATE bookings SET calendar_sync=%s WHERE booking_id=%s',(Jsonb(state),job['booking_id']))
        job['calendar_sync'][key]=state[key]
    return True


def finish(settings,job,error=None):
    with connect_database(settings) as c:
        row=c.execute('SELECT version,calendar_sync FROM bookings WHERE booking_id=%s FOR UPDATE',(job['booking_id'],)).fetchone()
        if row is None or row[1].get('token')!=job['calendar_sync']['token']:
            return
        state=row[1]
        now=c.execute('SELECT clock_timestamp()').fetchone()[0]
        if row[0]!=job['version']:
            state.update(status='pending',retry_at=now.isoformat(),attempts=0)
        elif error:
            # Keep only class/status, never credentials, request URLs or event contents.
            http_status=getattr(getattr(error,'resp',None),'status',None)
            reason=type(error).__name__+(f':{http_status}' if http_status else '')
            delay=min(3600,30*2**min(state.get('attempts',1),7))
            state.update(status='pending',last_error=reason,retry_at=(now+timedelta(seconds=delay)).isoformat())
        else:
            state.update(status='synced',synced_version=job['version'],attempts=0,
                synced_at=now.isoformat(),next_check_at=(now+timedelta(minutes=15)).isoformat())
            state.pop('last_error',None)
            state.pop('retry_at',None)
        c.execute('UPDATE bookings SET calendar_sync=%s WHERE booking_id=%s',(Jsonb(state),job['booking_id']))


def sync_once(settings,adapter_factory=None,limit=20):
    config=load_business_config()
    if not config.calendar.enabled or not settings.google_calendar_id:
        return 0
    prepare_existing(settings,limit)
    processed=0
    adapter=None
    for _ in range(limit):
        job=claim(settings)
        if job is None:
            break
        try:
            if not still_current(settings,job):
                finish(settings,job)
                continue
            bodies=event_bodies(job,config)
            if adapter is None:
                adapter=adapter_factory() if adapter_factory else CalendarAdapter(make_service(settings))
            state=job['calendar_sync']
            for kind in ('booking','cleaning'):
                if not still_current(settings,job):
                    break
                key=kind+'_event_id'
                if kind not in bodies:
                    adapter.delete(state['calendar_id'],state[key])
                else:
                    try:
                        adapter.upsert(state['calendar_id'],state[key],bodies[kind])
                    except RecreateEvent:
                        if rotate_id(settings,job,key):
                            adapter.upsert(state['calendar_id'],state[key],bodies[kind])
            finish(settings,job)
        except Exception as error:
            finish(settings,job,error)
            logger.warning('Calendar sync failed (%s)',type(error).__name__)
        processed+=1
    return processed


def reconcile(settings):
    with connect_database(settings) as c:
        with c.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT * FROM bookings WHERE calendar_sync<>'{}'::jsonb OR status='confirmed' ORDER BY booking_id FOR UPDATE")
            rows=cursor.fetchall()
        for row in rows:
            queue_calendar(c,row)
    return len(rows)


async def loop(application):
    while True:
        try:
            await asyncio.to_thread(sync_once,application.bot_data['settings'])
        except Exception as error:
            logger.warning('Calendar sweep failed (%s)',type(error).__name__)
        try:
            await asyncio.wait_for(application.bot_data['calendar_wakeup'].wait(),timeout=30)
        except asyncio.TimeoutError:
            pass
        application.bot_data['calendar_wakeup'].clear()


async def start(application):
    application.bot_data['calendar_wakeup']=asyncio.Event()
    application.bot_data['calendar_task']=asyncio.create_task(loop(application))


async def stop(application):
    task=application.bot_data.pop('calendar_task',None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--reconcile',action='store_true')
    args=parser.parse_args()
    settings=load_settings()
    if not load_business_config().calendar.enabled or not settings.google_calendar_id:
        print('Calendar integration disabled or GOOGLE_CALENDAR_ID is missing.')
        return 1
    try:
        if args.reconcile:
            print(f'Queued for reconciliation: {reconcile(settings)}')
        print(f'Processed jobs: {sync_once(settings)}. Check /admin for individual sync states.')
        return 0
    except Exception as error:
        print(f'Calendar sync failed ({type(error).__name__}).')
        return 1


if __name__=='__main__':
    raise SystemExit(main())
