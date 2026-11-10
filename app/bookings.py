"""Transactional booking service. Caller owns commit/rollback and actor identity.

Telegram handlers must pass effective_user.id, never an ID supplied in user text.
No HTTP booking routes are exposed until authentication is implemented.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from uuid import UUID

from psycopg import Connection
from psycopg.errors import ExclusionViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.users import admin_role
from app.business_config import load_business_config
from app.rental_rules import next_cleaning, rental_price, RulesError
from app.booking_events import enqueue_event
from app.calendar_queue import queue_calendar
from app.cancellation_rules import current_policy,calculate as cancellation_calculation
from app import books_plans
from app.booking_details import validate as validate_details


def quote_rental(start: datetime, end: datetime) -> dict:
    config=load_business_config()
    try:
        amount=rental_price(start,end,config)
        cleaning_start,cleaning_end=next_cleaning(end,config)
    except RulesError as error:
        raise BookingError(str(error)) from None
    return dict(amount=amount,unit=config.currency,cleaning_starts_at=cleaning_start,cleaning_ends_at=cleaning_end)


def _cleaning(start,end,kind):
    if kind=='block':
        return None,None
    config=load_business_config()
    local=start.astimezone(timezone(timedelta(hours=9)))
    if local.minute or local.second or local.microsecond or (end-start).total_seconds()%3600:
        raise BookingError('Rental must start on the hour and contain whole hours')
    try:
        return next_cleaning(end,config)
    except RulesError as error:
        raise BookingError(str(error)) from None


def _legacy_check(connection):
    if connection.execute("SELECT EXISTS(SELECT 1 FROM bookings WHERE type='booking' AND status IN ('hold','pending','confirmed') AND cleaning_ends_at IS NULL AND (status='confirmed' OR expires_at>clock_timestamp()))").fetchone()[0]:
        raise BookingError('Existing active bookings need an explicit cleaning slot before new sales')


class BookingError(Exception):
    """Safe domain error, suitable for translating to a user-facing response."""


class AccessDenied(BookingError):
    pass


class SlotUnavailable(BookingError):
    pass


class StaleBooking(BookingError):
    pass


class CancellationTermsChanged(StaleBooking):
    pass


def _now(connection: Connection) -> datetime:
    return connection.execute('SELECT clock_timestamp()').fetchone()[0]


def _interval(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise BookingError('Dates must be datetime values')
    if start.utcoffset() is None or end.utcoffset() is None:
        raise BookingError('An explicit timezone is required')
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    if end <= start:
        raise BookingError('End must be after start')
    return start, end


def parse_amount(value) -> Decimal:
    if isinstance(value, (float, bool)):
        raise BookingError('Use Decimal or a decimal string for money')
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise BookingError('Invalid amount') from None
    if not amount.is_finite() or amount < 0 or amount >= Decimal('1000000000000'):
        raise BookingError('Amount out of range')
    if amount != amount.quantize(Decimal('.01')):
        raise BookingError('Amount supports at most two decimal places')
    return amount.quantize(Decimal('.01'))


def _admin(connection: Connection, actor: int) -> None:
    if admin_role(connection, actor) not in ('owner', 'admin'):
        raise AccessDenied('Owner or administrator required')


def _user(connection: Connection, actor: int) -> int:
    row = connection.execute('SELECT user_id FROM clients WHERE telegram_user_id=%s', (actor,)).fetchone()
    if row is None:
        raise AccessDenied('Registered user required')
    return row[0]


def _expire(connection: Connection) -> int:
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("""UPDATE bookings SET status='expired'
            WHERE status IN ('hold','pending') AND expires_at <= clock_timestamp() RETURNING *""")
        rows=cursor.fetchall()
    for row in rows:
        enqueue_event(connection,row,'expired',origin='system')
    return len(rows)


def _lock(connection: Connection) -> None:
    # One party room: serialize mutations and expiry with a transaction-level lock.
    connection.execute('SELECT pg_advisory_xact_lock(836021472)')


def expire_bookings(connection: Connection) -> int:
    _lock(connection)
    return _expire(connection)


def complete_bookings(connection: Connection) -> int:
    """Finish rentals at ends_at, preserving cleanup, payment and review jobs."""
    _lock(connection)
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("""UPDATE bookings SET status='completed'
            WHERE type='booking' AND status='confirmed' AND ends_at<=clock_timestamp()
            RETURNING *""")
        rows=cursor.fetchall()
    for row in rows:
        queue_calendar(connection,row)
    return len(rows)


def _get(connection: Connection, booking_id: int, *, lock=False) -> dict:
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute('SELECT * FROM bookings WHERE booking_id=%s' + (' FOR UPDATE' if lock else ''), (booking_id,))
        row = cursor.fetchone()
    if row is None:
        raise BookingError('Booking not found')
    return row


def get_booking(connection: Connection, actor: int, booking_id: int) -> dict:
    row = _get(connection, booking_id)
    if admin_role(connection, actor) not in ('owner', 'admin') and row['user_id'] != _user(connection, actor):
        raise AccessDenied('Booking access denied')
    return row


def list_bookings(connection: Connection, actor: int, *, limit: int | None = None, offset: int = 0, pending_only: bool = False, blocks_only: bool = False, refunds_only: bool = False) -> list[dict]:
    params = ()
    condition = " WHERE status='cancelled' AND cancellation->>'refund_status'='pending'" if refunds_only else " WHERE status <> 'cancelled'"
    condition += " AND (request_data->>'source'='books_plans' AND request_data->>'role'='series') IS NOT TRUE"
    condition += " AND (request_data->>'source'='books_plans' AND request_data->>'role'='occurrence' AND ends_at<=clock_timestamp()) IS NOT TRUE"
    if admin_role(connection, actor) not in ('owner', 'admin'):
        condition += ' AND user_id=%s'
        params = (_user(connection, actor),)
    elif not refunds_only:
        condition += " AND status<>'completed' AND ends_at>statement_timestamp()"
    if pending_only:
        condition += (' AND ' if condition else ' WHERE ') + "status='pending'"
    if blocks_only:
        condition += " AND type='block'"
    pagination = ''
    if limit is not None:
        if not 1 <= limit <= 100 or offset < 0:
            raise BookingError('Invalid pagination')
        pagination = ' LIMIT %s OFFSET %s'
        params += (limit, offset)
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute('SELECT * FROM bookings' + condition + ' ORDER BY starts_at,booking_id' + pagination, params)
        return cursor.fetchall()


def is_available(connection: Connection, actor: int, start: datetime, end: datetime) -> bool:
    _user(connection, actor)
    start, end = _interval(start, end)
    if start <= _now(connection):
        raise BookingError('Start must be in the future')
    if end-start<timedelta(hours=load_business_config().booking.minimum_hours):
        raise BookingError('Minimum rental duration: 5 hours')
    _,cleaning_end=_cleaning(start,end,'booking')
    if books_plans.overlaps(load_business_config(),start,cleaning_end):
        return False
    _legacy_check(connection)
    return not connection.execute("""SELECT EXISTS(SELECT 1 FROM bookings
        WHERE (status IN ('hold','pending','confirmed') OR (type='booking' AND status='completed'))
        AND (status IN ('confirmed','completed') OR expires_at > clock_timestamp())
        AND (request_data->>'source'='books_plans' AND starts_at>clock_timestamp()) IS NOT TRUE
        AND tstzrange(starts_at,COALESCE(cleaning_ends_at,ends_at),'[)') && tstzrange(%s,%s,'[)'))""", (start, cleaning_end)).fetchone()[0]


def create_booking(connection: Connection, actor: int, **kwargs) -> dict:
    _admin(connection,actor)
    row=_create_booking(connection,actor,**kwargs)
    queue_calendar(connection,row)
    return _get(connection,row['booking_id'])


def _create_booking(connection: Connection, actor: int, *, user_id: int | None,
                   starts_at: datetime, ends_at: datetime, amount=None, request_key: UUID,
                   unit='KRW', kind='booking', status='pending', ttl_minutes=None,cancellation_policy=None,details=None) -> dict:
    # Manual pricing is trusted only from administrators until server tariffs exist.
    start, end = _interval(starts_at, ends_at)
    rules = load_business_config().booking
    if kind == 'booking' and end-start < timedelta(hours=rules.minimum_hours):
        raise BookingError(f'Minimum rental duration: {rules.minimum_hours} hours')
    if ttl_minutes is None:
        ttl_minutes = rules.hold_timeout_minutes if status == 'hold' else rules.confirmation_timeout_minutes
    automatic=amount is None
    if not automatic:
        amount = parse_amount(amount)
    if not isinstance(request_key, UUID):
        raise BookingError('A UUID request key is required')
    if not isinstance(unit, str) or not re.fullmatch('[A-Z]{3}', unit):
        raise BookingError('Currency must be a three-letter uppercase code')
    if (kind, status) not in (('booking','hold'), ('booking','pending'), ('block','confirmed')):
        raise BookingError('Invalid creation type or status')
    if not isinstance(ttl_minutes, int) or isinstance(ttl_minutes, bool) or not 1 <= ttl_minutes <= 1440:
        raise BookingError('TTL must be 1 to 1440 minutes')
    if kind == 'block' and (amount != 0 or user_id is not None):
        raise BookingError('A block must have zero price and no client')
    if user_id is not None and connection.execute('SELECT 1 FROM clients WHERE user_id=%s', (user_id,)).fetchone() is None:
        raise BookingError('Client not found')
    data = dict(actor=actor, user_id=user_id, starts_at=start.isoformat(), ends_at=end.isoformat(),
                amount='auto' if automatic else str(amount), unit=unit, kind=kind, status=status, ttl_minutes=ttl_minutes)
    if details is not None:
        try:
            details=validate_details(details)
        except ValueError as error:
            raise BookingError(str(error)) from None
        if kind!='booking':
            raise BookingError('Blocks cannot contain customer details')
        data['details']=details
    _lock(connection)
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute('SELECT * FROM bookings WHERE request_key=%s', (request_key,))
        existing = cursor.fetchone()
    if existing:
        if existing['request_data'] != data:
            raise BookingError('Request key already used with different parameters')
        return existing
    _expire(connection)
    _legacy_check(connection)
    cleaning_start,cleaning_end=_cleaning(start,end,kind)
    config=load_business_config()
    books_plans.reconcile(connection,config)
    if books_plans.overlaps(config,start,cleaning_end or end):
        raise SlotUnavailable('Requested interval is reserved by books_plans')
    if automatic:
        if kind!='booking' or unit!=load_business_config().currency:
            raise BookingError('Automatic pricing supports KRW rental bookings only')
        amount=quote_rental(start,end)['amount']
    now = _now(connection)
    if start <= now:
        raise BookingError('Start must be in the future')
    expires = min(now + timedelta(minutes=ttl_minutes), start) if status in ('hold','pending') else None
    try:
        with connection.transaction():
            booking_id = connection.execute("""INSERT INTO bookings
                (user_id,type,status,starts_at,ends_at,expires_at,amount,unit,request_key,request_data,cleaning_starts_at,cleaning_ends_at,cancellation,contact_phone,guests_count,event_name)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING booking_id""",
                (user_id,kind,status,start,end,expires,amount,unit,request_key,Jsonb(data),cleaning_start,cleaning_end,
                 Jsonb(dict(policy=cancellation_policy or current_policy(load_business_config())) if kind=='booking' else {}),
                 (details or {}).get('contact_phone'),(details or {}).get('guests_count'),(details or {}).get('event_name'))).fetchone()[0]
    except ExclusionViolation:
        raise SlotUnavailable('Requested interval is occupied') from None
    return _get(connection, booking_id)


def create_client_booking(connection: Connection, actor: int, *, starts_at: datetime,
                          ends_at: datetime, request_key: UUID, expected_quote: dict, expected_policy: dict | None=None,details=None) -> dict:
    """Client may only book for themselves using a server quote; never accepts price/status."""
    user_id=_user(connection,actor)
    _lock(connection)
    existing=connection.execute('SELECT booking_id,user_id FROM bookings WHERE request_key=%s',(request_key,)).fetchone()
    if existing:
        if existing[1]!=user_id:
            raise AccessDenied('Request access denied')
        row=_get(connection,existing[0])
        if row['starts_at']!=starts_at or row['ends_at']!=ends_at:
            raise BookingError('Request key already used with different parameters')
        if details is not None:
            try:
                answers=validate_details(details)
            except ValueError as error:
                raise BookingError(str(error)) from None
            if any(row[k]!=v for k,v in answers.items()):
                raise BookingError('Request key already used with different answers')
        return row
    if quote_rental(starts_at,ends_at)!=expected_quote:
        raise BookingError('Quote changed; request a new quote')
    if expected_policy is not None and current_policy(load_business_config())!=expected_policy:
        raise BookingError('Quote changed; request a new quote')
    _expire(connection)
    if connection.execute("SELECT 1 FROM bookings WHERE user_id=%s AND status IN ('hold','pending') LIMIT 1",(user_id,)).fetchone():
        raise BookingError('You already have a pending request')
    try:
        details=validate_details(details)
    except ValueError as error:
        raise BookingError(str(error)) from None
    row=_create_booking(connection,actor,user_id=user_id,starts_at=starts_at,ends_at=ends_at,
        request_key=request_key,amount=None,status='pending',cancellation_policy=expected_policy,details=details)
    enqueue_event(connection,row,'created',actor=actor,origin='client')
    return _get(connection,row['booking_id'])


def quote_cancellation(connection,actor,booking_id,*,expected_version=None):
    row=_get(connection,booking_id)
    if row['user_id']!=_user(connection,actor) or row['type']!='booking':
        raise AccessDenied('Booking access denied')
    if expected_version is not None and row['version']!=expected_version:
        raise StaleBooking('Booking changed')
    now=_now(connection)
    if row['status'] not in ('hold','pending','confirmed') or row['starts_at']<=now:
        raise BookingError('Отмена после начала аренды — через администратора. / 시작 후 취소는 관리자에게 문의하세요.')
    if row['status'] in ('hold','pending') and row['expires_at']<=now:
        raise BookingError('Request expired; confirmation or transfer is not permitted')
    return cancellation_calculation(row,now)


def change_booking(connection: Connection, actor: int, booking_id: int, *, action: str,
                   expected_version: int, starts_at=None, ends_at=None, ttl_minutes=None,origin: str | None=None,
                   expected_cancellation: dict | None=None) -> dict:
    if action not in ('submit','confirm','cancel','move'):
        raise BookingError('Unsupported action')
    _lock(connection)
    row = _get(connection, booking_id, lock=True)
    if books_plans.managed(row):
        raise BookingError('Регулярные блокировки изменяются в config.yaml / 정기 일정은 config.yaml에서 변경하세요.')
    privileged = admin_role(connection, actor) in ('owner', 'admin')
    if origin not in (None,'admin','client'):
        raise BookingError('Invalid operation origin')
    if not privileged or origin=='client':
        if row['user_id'] != _user(connection, actor) or action != 'cancel':
            raise AccessDenied('Action not permitted')
        if row['status'] not in ('hold', 'pending', 'confirmed', 'cancelled'):
            raise AccessDenied('Action not permitted')
        if (row['status']=='confirmed' or row['deposit_paid_amount']+row['balance_paid_amount']>0) and expected_cancellation is None:
            raise AccessDenied('Cancellation confirmation is required')
    if row['version'] != expected_version:
        raise StaleBooking('Booking changed; reload its current version')
    now = _now(connection)
    if row['status'] in ('hold','pending') and row['expires_at'] <= now:
        raise BookingError('Request expired; confirmation or transfer is not permitted')
    _expire(connection)
    if action == 'cancel':
        if row['status'] == 'cancelled':
            return row
        if row['status'] not in ('hold','pending','confirmed'):
            raise BookingError('Booking cannot be cancelled')
        if not privileged or origin=='client':
            if row['starts_at']<=now:
                raise BookingError('Отмена после начала аренды — через администратора. / 시작 후 취소는 관리자에게 문의하세요.')
            if expected_cancellation is not None:
                quote=cancellation_calculation(row,now)
                if quote!=expected_cancellation:
                    raise CancellationTermsChanged('Cancellation terms changed; confirm again')
                data=dict(policy=quote['policy'],cancelled_at=now.isoformat(),cancelled_by=_user(connection,actor),quote=quote,
                    refund_status='pending' if Decimal(quote['refund_amount'])>0 else 'not_due')
                connection.execute("UPDATE bookings SET status='cancelled',expires_at=NULL,cancellation=%s WHERE booking_id=%s",(Jsonb(data),booking_id))
            else:
                connection.execute("UPDATE bookings SET status='cancelled',expires_at=NULL WHERE booking_id=%s", (booking_id,))
        else:
            connection.execute("UPDATE bookings SET status='cancelled',expires_at=NULL WHERE booking_id=%s", (booking_id,))
    elif action == 'confirm':
        if row['status'] == 'confirmed':
            return row
        if row['type'] != 'booking' or row['status'] != 'pending' or row['starts_at'] <= now:
            raise BookingError('Only a future pending booking can be confirmed')
        connection.execute("UPDATE bookings SET status='confirmed',expires_at=NULL WHERE booking_id=%s", (booking_id,))
    elif action == 'submit':
        if row['status'] != 'hold' or row['starts_at'] <= now:
            raise BookingError('Only a current hold can be submitted')
        if ttl_minutes is None:
            ttl_minutes = load_business_config().booking.confirmation_timeout_minutes
        if not isinstance(ttl_minutes, int) or isinstance(ttl_minutes, bool) or not 1 <= ttl_minutes <= 1440:
            raise BookingError('TTL must be 1 to 1440 minutes')
        deadline = min(now + timedelta(minutes=ttl_minutes), row['starts_at'])
        connection.execute("UPDATE bookings SET status='pending',expires_at=%s WHERE booking_id=%s", (deadline,booking_id))
    else:
        start, end = _interval(starts_at, ends_at)
        rules = load_business_config().booking
        if row['type']=='booking' and end-start < timedelta(hours=rules.minimum_hours):
            raise BookingError(f'Minimum rental duration: {rules.minimum_hours} hours')
        if row['status'] not in ('pending','confirmed') or row['starts_at'] <= now or start <= now:
            raise BookingError('Only future pending or confirmed bookings can be moved')
        expiry = min(row['expires_at'], start) if row['status'] == 'pending' else None
        cleaning_start,cleaning_end=_cleaning(start,end,row['type'])
        config=load_business_config()
        books_plans.reconcile(connection,config)
        if books_plans.overlaps(config,start,cleaning_end or end):
            raise SlotUnavailable('New interval is reserved by books_plans; original booking preserved')
        # Explicit move is allowed to recalculate cleaning, never a config edit alone.
        try:
            with connection.transaction():
                connection.execute('UPDATE bookings SET starts_at=%s,ends_at=%s,expires_at=%s,cleaning_starts_at=%s,cleaning_ends_at=%s WHERE booking_id=%s', (start,end,expiry,cleaning_start,cleaning_end,booking_id))
        except ExclusionViolation:
            raise SlotUnavailable('New interval is occupied; original booking preserved') from None
    updated=_get(connection,booking_id)
    enqueue_event(connection,updated,action,actor=actor,
                  origin='client' if origin=='client' or not privileged else 'admin',previous=row)
    return _get(connection, booking_id)


def complete_refund(connection,actor,booking_id,*,expected_version):
    _admin(connection,actor)
    _lock(connection)
    row=_get(connection,booking_id,lock=True)
    if row['version']!=expected_version:
        raise StaleBooking('Booking changed')
    data=dict(row.get('cancellation') or {})
    if row['status']!='cancelled' or data.get('refund_status') not in ('pending','completed'):
        raise BookingError('Нет ожидающего возврата. / 대기 중인 환불이 없습니다.')
    if data['refund_status']=='completed':
        return row
    data.update(refund_status='completed',refunded_at=_now(connection).isoformat(),refunded_by=_user(connection,actor))
    connection.execute('UPDATE bookings SET cancellation=%s WHERE booking_id=%s',(Jsonb(data),booking_id))
    updated=_get(connection,booking_id)
    enqueue_event(connection,updated,'refund_completed',actor=actor,origin='admin')
    return _get(connection,booking_id)
