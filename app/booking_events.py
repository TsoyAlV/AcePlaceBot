"""Append delivery events in the same transaction as a booking mutation."""

from psycopg.types.json import Jsonb
from app.calendar_queue import queue_calendar


def enqueue_event(connection,row,event,*,actor=None,origin='admin',previous=None):
    queue=row['admin_notifications']
    # Supersede unsent notifications; never announce an old confirmation after cancellation.
    for state in queue.values():
        if (state.get('event')=='review' and row['type']=='booking' and row['status'] in ('confirmed','completed')
            and state.get('rental_end')==row['ends_at'].isoformat() and state.get('user_id')==row['user_id']):
            continue
        if state['status'] in ('pending','processing') and state.get('event')!='receipt':
            state['status']='cancelled'
    recipients=[]
    for uid,tg in connection.execute("""SELECT a.user_id,c.telegram_user_id FROM admins a
        JOIN clients c USING(user_id) WHERE active AND notifications_enabled
        AND role IN ('owner','admin')""").fetchall():
        if tg!=actor:
            recipients.append(('admin',uid))
    if row['user_id'] is not None and (origin!='client' or (event=='cancel' and (row.get('cancellation') or {}).get('cancelled_at'))):
        recipients.append(('client',row['user_id']))
    for audience,uid in recipients:
        key=f"{row['version']}:{audience}:{uid}"
        state=dict(status='pending',attempts=0,event=event,origin=origin,audience=audience,
                   user_id=uid,booking_version=row['version'])
        if event=='move' and previous:
            state['previous_start']=previous['starts_at'].isoformat()
            state['previous_end']=previous['ends_at'].isoformat()
        if event=='receipt':
            state['receipt_unique_id']=row['payment_receipt']['file_unique_id']
        if event=='plan_conflict':
            state['conflicts']=row['request_data'].get('conflicts',[])
        queue[key]=state
    connection.execute('UPDATE bookings SET admin_notifications=%s WHERE booking_id=%s',
        (Jsonb(queue),row['booking_id']))
    queue_calendar(connection,row)
