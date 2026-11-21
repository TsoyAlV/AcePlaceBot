"""Client-only soft deletion; never changes occupancy, payments or calendar jobs."""

from app.bookings import BookingError, _lock, _user


ELIGIBLE_SQL = """type='booking' AND (
    status IN ('completed','expired','cancelled')
    OR (status='confirmed' AND ends_at<=statement_timestamp())
    OR (status IN ('hold','pending') AND expires_at<=statement_timestamp())
)"""


def can_archive(row, now):
    if row['type'] != 'booking' or row.get('client_deleted_at'):
        return False
    return (row['status'] in ('completed','expired','cancelled')
            or (row['status']=='confirmed' and row['ends_at']<=now)
            or (row['status'] in ('hold','pending') and row['expires_at']<=now))


def archive_bookings(connection, actor, booking_id=None):
    """Hide own inactive bookings atomically; explicit active/foreign IDs fail closed."""
    _lock(connection)
    owner=_user(connection,actor)
    if booking_id is not None:
        row=connection.execute(
            'SELECT user_id FROM bookings WHERE booking_id=%s FOR UPDATE',
            (booking_id,)).fetchone()
        if not row or row[0]!=owner:
            raise BookingError('Booking access denied')
    params=(owner,) if booking_id is None else (owner,booking_id)
    rows=connection.execute(
        'UPDATE bookings SET client_deleted_at=statement_timestamp() '
        'WHERE user_id=%s AND client_deleted_at IS NULL AND '+ELIGIBLE_SQL
        +(' AND booking_id=%s' if booking_id is not None else '')
        +' RETURNING booking_id',params).fetchall()
    if booking_id is not None and not rows:
        hidden=connection.execute('SELECT client_deleted_at FROM bookings WHERE booking_id=%s',(booking_id,)).fetchone()[0]
        if hidden is None:
            raise BookingError('Only inactive bookings can be removed')
    return len(rows)
