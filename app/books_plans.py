"""Recurring capacity blocks. Config is authoritative; Google is only a mirror.

Occurrences and durable series state use the existing bookings table. A completed
technical block holds each series' metadata and Calendar job, never capacity.
"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4, uuid5, UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.business_config import load_business_config
from app.database import connect_database
from app.rental_rules import SEOUL, DAYS

SOURCE='books_plans'


def managed(row,role=None):
    data=row.get('request_data') or {}
    return data.get('source')==SOURCE and (role is None or data.get('role')==role)


def specifications(config):
    if not config.books_plans.enabled:
        return {}
    result={}
    for day,items in config.books_plans.weekly.items():
        for item in items:
            spec=dict(day=day,start=item.start.strftime('%H:%M'),end=item.end.strftime('%H:%M'))
            key=sha256(f'{day}/{spec["start"]}/{spec["end"]}'.encode()).hexdigest()
            result[key]=spec
    return result


def occurrence(day,spec):
    start=datetime.combine(day,datetime.strptime(spec['start'],'%H:%M').time(),SEOUL)
    end=datetime.combine(day,datetime.strptime(spec['end'],'%H:%M').time(),SEOUL)
    if end<=start:
        end+=timedelta(days=1)
    return start,end


def intervals(config,start,end):
    """Exact virtual schedule, including beyond the materialized/Calendar horizon."""
    specs=specifications(config)
    excluded=set(config.books_plans.excluded_dates)
    day=start.astimezone(SEOUL).date()-timedelta(days=1)
    last=end.astimezone(SEOUL).date()
    while day<=last:
        if day not in excluded:
            for key,spec in specs.items():
                if spec['day']==DAYS[day.weekday()]:
                    a,b=occurrence(day,spec)
                    if a<end and b>start:
                        yield key,a,b
        day+=timedelta(days=1)


def overlaps(config,start,end):
    return next(intervals(config,start,end),None) is not None


def _rows(c,query,params=()):
    with c.cursor(row_factory=dict_row) as cursor:
        cursor.execute(query,params)
        return cursor.fetchall()


def _save_series(c,row,data):
    if row['request_data']==data:
        return row
    from app.calendar_queue import queue_calendar
    c.execute('UPDATE bookings SET request_data=%s WHERE booking_id=%s',(Jsonb(data),row['booking_id']))
    updated=_rows(c,'SELECT * FROM bookings WHERE booking_id=%s',(row['booking_id'],))[0]
    queue_calendar(c,updated)
    return updated


def reconcile(c,config,now=None):
    """Caller owns transaction. Share the single-room mutation lock with bookings."""
    from app.calendar_queue import queue_calendar
    from app.booking_events import enqueue_event
    c.execute('SELECT pg_advisory_xact_lock(836021472)')
    now=now or c.execute('SELECT clock_timestamp()').fetchone()[0]
    local=now.astimezone(SEOUL)
    until=datetime.combine(local.date()+timedelta(days=config.books_plans.generate_days_ahead),datetime.min.time(),SEOUL)
    specs=specifications(config)
    rows=_rows(c,"SELECT * FROM bookings WHERE request_data->>'source'=%s ORDER BY booking_id",(SOURCE,))
    if not specs and not rows:
        return 0
    # Expired holds still participate in the SQL exclusion until their status changes.
    from app.bookings import _expire
    _expire(c)
    series={r['request_data']['key']:r for r in rows if managed(r,'series') and r['request_data']['active']}
    desired={}
    # Includes a currently running occurrence when first enabling the schedule.
    for key,a,b in intervals(config,now,until):
        desired[(key,a.isoformat())]=(a,b)
    ordinary=_rows(c,"""SELECT booking_id,starts_at,COALESCE(cleaning_ends_at,ends_at) AS occupied_end
        FROM bookings WHERE (status IN ('hold','pending','confirmed') OR (type='booking' AND status='completed'))
        AND (status IN ('confirmed','completed') OR expires_at>%s)
        AND request_data->>'source' IS DISTINCT FROM %s
        AND starts_at<%s AND COALESCE(cleaning_ends_at,ends_at)>%s""",(now,SOURCE,until,now))
    running=[r for r in rows if managed(r,'occurrence') and r['status']=='confirmed'
             and r['starts_at']<=now<r['ends_at']]
    conflicts={}
    for slot,(a,b) in desired.items():
        ids=[r['booking_id'] for r in ordinary if r['starts_at']<b and r['occupied_end']>a]
        ids += [r['booking_id'] for r in running if r['starts_at']<b and r['ends_at']>a
                and (r['request_data']['key'],r['starts_at'].astimezone(SEOUL).isoformat())!=slot]
        if ids:
            conflicts[slot]=ids
    # Release obsolete FUTURE capacity first, so moves of the weekly schedule are atomic.
    existing={}
    for row in rows:
        if not managed(row,'occurrence'):
            continue
        data=row['request_data']
        slot=(data['key'],row['starts_at'].astimezone(SEOUL).isoformat())
        existing[slot]=row
        if row['starts_at']>now and row['status']=='confirmed' and (slot not in desired or slot in conflicts):
            c.execute("UPDATE bookings SET status='cancelled' WHERE booking_id=%s",(row['booking_id'],))
            row['status']='cancelled'
    # Retire old series by truncating recurrence; do not delete its history.
    for key,row in series.items():
        if key not in specs:
            data=dict(row['request_data'],active=False,until=now.isoformat())
            _save_series(c,row,data)
    for key,spec in specs.items():
        row=series.get(key)
        if row is None:
            day=local.date()
            day+=timedelta(days=(DAYS.index(spec['day'])-day.weekday())%7)
            anchor,finish=occurrence(day,spec)
            if finish<=now:
                anchor,finish=occurrence(day+timedelta(days=7),spec)
            data=dict(source=SOURCE,role='series',key=key,active=True,spec=spec,
                      until=until.isoformat(),excluded_starts=[],conflicts=[])
            row=_rows(c,"""INSERT INTO bookings(type,status,starts_at,ends_at,amount,unit,request_key,request_data)
                VALUES ('block','completed',%s,%s,0,'KRW',%s,%s) RETURNING *""",
                (anchor,finish,uuid4(),Jsonb(data)))[0]
            queue_calendar(c,row)
        old=row['request_data']
        # Past exceptions are immutable; future exceptions follow the current config/conflicts.
        excluded={s for s in old.get('excluded_starts',[]) if datetime.fromisoformat(s)<=now}
        anchor=row['starts_at'].astimezone(SEOUL)
        day=anchor.date()
        while day<until.date():
            a,b=occurrence(day,spec)
            if a>=anchor and a>now and (day in config.books_plans.excluded_dates or (key,a.isoformat()) in conflicts):
                excluded.add(a.isoformat())
            # Include an excluded/conflicted first, already-started occurrence as well.
            preserved=any(r['request_data']['key']==key and r['starts_at']==a for r in running)
            if a==anchor and b>now and not preserved and (day in config.books_plans.excluded_dates or (key,a.isoformat()) in conflicts):
                excluded.add(a.isoformat())
            day+=timedelta(days=7)
        conflict_data=[dict(start=s,booking_ids=ids) for (k,s),ids in sorted(conflicts.items()) if k==key]
        data=dict(old,until=until.isoformat(),excluded_starts=sorted(excluded),conflicts=conflict_data)
        updated=_save_series(c,row,data)
        if conflict_data and old.get('conflicts')!=conflict_data:
            enqueue_event(c,updated,'plan_conflict',origin='system')
        for (k,stamp),(a,b) in desired.items():
            if k!=key or (k,stamp) in conflicts:
                continue
            prior=existing.get((k,stamp))
            if prior and prior['status']=='confirmed':
                continue
            if prior:
                meta=dict(prior['request_data'],series_id=row['booking_id'])
                c.execute("UPDATE bookings SET status='confirmed',request_data=%s WHERE booking_id=%s",(Jsonb(meta),prior['booking_id']))
            else:
                token=uuid5(UUID(str(row['request_key'])),stamp)
                meta=dict(source=SOURCE,role='occurrence',key=key,series_id=row['booking_id'])
                c.execute("""INSERT INTO bookings(type,status,starts_at,ends_at,amount,unit,request_key,request_data)
                    VALUES ('block','confirmed',%s,%s,0,'KRW',%s,%s)""",(a,b,token,Jsonb(meta)))
    return len(desired)


def prepare(settings,config=None,*,connect=connect_database):
    with connect(settings) as c:
        return reconcile(c,config or load_business_config())


def event_body(row,config):
    """Google recurrence uses one parent per weekly range, UTC UNTIL/EXDATE."""
    data=row['request_data']
    until=datetime.fromisoformat(data['until'])
    if until<row['starts_at']:
        return None
    utc=lambda dt:dt.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    cutoff=until-timedelta(seconds=1) if data['active'] else until
    rule='RRULE:FREQ=WEEKLY;BYDAY='+data['spec']['day'][:2]+';UNTIL='+utc(cutoff)
    recurrence=[rule]
    exceptions=[utc(datetime.fromisoformat(s)) for s in data.get('excluded_starts',[]) if datetime.fromisoformat(s)<=cutoff]
    if exceptions:
        recurrence.append('EXDATE:'+','.join(exceptions))
    return dict(summary='Ace Place · По расписанию / 정기 일정',
        description='Регулярная блокировка books_plans. Управление через config.yaml. / config.yaml에서 관리.',
        start=dict(dateTime=row['starts_at'].astimezone(SEOUL).isoformat(),timeZone='Asia/Seoul'),
        end=dict(dateTime=row['ends_at'].astimezone(SEOUL).isoformat(),timeZone='Asia/Seoul'),
        recurrence=recurrence,colorId=config.calendar.block_color_id,status='confirmed',
        visibility='private',transparency='opaque',reminders=dict(useDefault=False),
        extendedProperties=dict(private=dict(ace_place_books_plan=str(row['booking_id']))))
