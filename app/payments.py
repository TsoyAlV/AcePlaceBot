"""Screenshot evidence is untrusted; only an administrator records bank receipt."""

from decimal import Decimal

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from app.bookings import BookingError,AccessDenied,StaleBooking,_get,_lock,_now,_admin,_user
from app.business_config import load_business_config
from app.booking_events import enqueue_event


def submit_receipt(connection,actor,booking_id,*,file_id,file_unique_id,media_type):
    _lock(connection)
    row=_get(connection,booking_id,lock=True)
    if row['user_id']!=_user(connection,actor):
        raise AccessDenied('Booking access denied')
    if row['type']!='booking' or row['status'] not in ('pending','confirmed','expired','cancelled'):
        raise BookingError('Receipt not allowed for this booking')
    if media_type not in ('photo','document') or not file_id or not file_unique_id:
        raise BookingError('An image is required')
    previous=row.get('payment_receipt')
    if previous and previous['file_unique_id']==file_unique_id:
        return row
    if row['deposit_paid_amount']!=0:
        raise BookingError('Deposit is already recorded')
    if previous and previous['review_status']!='rejected':
        raise BookingError('Receipt is already awaiting review or accepted')
    now=_now(connection)
    late=row['status'] in ('expired','cancelled') or (row['status']=='pending' and row['expires_at']<=now)
    receipt=dict(file_id=file_id,file_unique_id=file_unique_id,media_type=media_type,
        submitted_at=now.isoformat(),review_status='pending',late=late,
        expected_amount=load_business_config().payment.deposit_amount,unit='KRW')
    if previous:
        receipt['previous_rejected']=dict(file_unique_id=previous['file_unique_id'],reviewed_at=previous.get('reviewed_at'))
    try:
        with connection.transaction():
            connection.execute('UPDATE bookings SET payment_receipt=%s WHERE booking_id=%s',(Jsonb(receipt),booking_id))
    except UniqueViolation:
        raise BookingError('This image is already linked to another booking') from None
    updated=_get(connection,booking_id)
    enqueue_event(connection,updated,'receipt',actor=actor,origin='client')
    return _get(connection,booking_id)


def review_receipt(connection,actor,booking_id,*,expected_version,accept):
    _admin(connection,actor)
    _lock(connection)
    row=_get(connection,booking_id,lock=True)
    if row['version']!=expected_version:
        raise StaleBooking('Booking changed; reload its current version')
    receipt=row.get('payment_receipt')
    if not receipt or receipt['review_status']!='pending':
        raise BookingError('No receipt is awaiting review')
    now=_now(connection)
    if accept:
        if row['status'] not in ('pending','confirmed') or (row['status']=='pending' and row['expires_at']<=now):
            raise BookingError('Late payment needs manual resolution; no slot was restored')
        if row['unit']!=receipt['unit'] or row['deposit_paid_amount']!=0:
            raise BookingError('Currency or previously recorded payment requires manual review')
        amount=Decimal(receipt['expected_amount'])
        if amount>row['amount']:
            raise BookingError('Expected deposit exceeds booking amount')
    receipt.update(review_status='accepted' if accept else 'rejected',
        reviewed_at=now.isoformat(),reviewed_by=_user(connection,actor))
    connection.execute('UPDATE bookings SET payment_receipt=%s,deposit_paid_amount=%s WHERE booking_id=%s',
        (Jsonb(receipt),amount if accept else row['deposit_paid_amount'],booking_id))
    updated=_get(connection,booking_id)
    enqueue_event(connection,updated,'deposit_received' if accept else 'receipt_rejected',actor=actor,origin='admin')
    return _get(connection,booking_id)
