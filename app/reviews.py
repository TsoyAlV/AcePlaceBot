"""Schedule one review invitation per booking, without touching payment or rental status."""

from datetime import timedelta
from html import escape
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def render(config,lang):
    content=config.ko if lang=='ko' else config.ru
    link=f'<a href="{escape(config.url,quote=True)}"><b>{escape(content.link_text)}</b></a>'
    return escape(content.message).replace('{review_link}',link)


def prepare(settings,rules,*,connect):
    if not rules.enabled:
        return
    with connect(settings) as c:
        with c.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""SELECT * FROM bookings WHERE type='booking' AND user_id IS NOT NULL
                AND status IN ('confirmed','completed') AND ends_at>clock_timestamp()
                AND (admin_notifications->'review' IS NULL OR admin_notifications->'review'->>'status'='cancelled')
                ORDER BY ends_at FOR UPDATE SKIP LOCKED""")
            rows=cursor.fetchall()
        for row in rows:
            queue=row['admin_notifications']
            queue['review']=dict(event='review',audience='client',user_id=row['user_id'],status='pending',attempts=0,
                rental_end=row['ends_at'].isoformat(),retry_at=row['ends_at'].isoformat())
            c.execute('UPDATE bookings SET admin_notifications=%s WHERE booking_id=%s',(Jsonb(queue),row['booking_id']))


def stale(row,state,now,rules):
    return (not rules.enabled or row['type']!='booking' or row['status'] not in ('confirmed','completed')
        or row['user_id'] is None or state.get('user_id')!=row['user_id']
        or state.get('rental_end')!=row['ends_at'].isoformat()
        or now>row['ends_at']+timedelta(hours=rules.catch_up_hours))


def is_current(settings,bid,token,rules,*,connect):
    with connect(settings) as c:
        with c.cursor(row_factory=dict_row) as cursor:
            cursor.execute('SELECT *,clock_timestamp() AS review_now FROM bookings WHERE booking_id=%s',(bid,))
            row=cursor.fetchone()
    if row is None:
        return False
    state=row['admin_notifications'].get('review') or {}
    return (state.get('status')=='processing' and state.get('token')==token
        and row['ends_at']<=row['review_now'] and not stale(row,state,row['review_now'],rules))
