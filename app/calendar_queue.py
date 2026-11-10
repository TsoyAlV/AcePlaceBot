"""Calendar jobs persisted with the booking transaction, independent of API availability."""

from uuid import uuid4
from psycopg.types.json import Jsonb


def queue_calendar(connection,row):
    from app.books_plans import managed
    if managed(row,'occurrence'):
        return
    state=dict(row.get('calendar_sync') or {})
    if row['status'] not in ('confirmed','completed') and not state and not managed(row,'series'):
        return
    state.setdefault('booking_event_id',uuid4().hex)
    state.setdefault('cleaning_event_id',uuid4().hex)
    state['desired_version']=row['version']
    state['attempts']=0
    state.pop('last_error',None)
    # A change during a request must wait for the current lease, then reconcile latest data.
    if state.get('status')!='processing':
        state.update(status='pending')
        state.pop('retry_at',None)
    connection.execute('UPDATE bookings SET calendar_sync=%s WHERE booking_id=%s',
        (Jsonb(state),row['booking_id']))
