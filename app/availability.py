"""Read-only availability tool; output contains no client IDs or booking details."""

from datetime import datetime, timedelta

from psycopg import Connection
from psycopg.rows import dict_row

from app.bookings import BookingError, _interval, _user
from app.business_config import load_business_config
from app.rental_rules import SEOUL, RulesError, next_cleaning
from app import books_plans


def get_availability_windows(connection: Connection, actor: int,
                             starts_at: datetime, ends_at: datetime, *,
                             duration_hours: int | None = None) -> dict:
    """Inspect <=31 days. Rentals must fit the range; their cleaning may end later.

    Returns blocked segments, physically unoccupied segments, and valid starts.
    Each valid start has minimum/maximum whole-hour duration (or requested duration).
    This is a snapshot, not a hold. Creation must recheck under its transaction lock.
    actor is trusted effective_user.id, not user-supplied text. No prices are calculated.
    """
    _user(connection,actor)
    start,end=_interval(starts_at,ends_at)
    if end-start>timedelta(days=31):
        raise BookingError('Search range must not exceed 31 days')
    config=load_business_config()
    minimum=config.booking.minimum_hours
    if duration_hours is not None and (type(duration_hours) is not int or duration_hours<minimum):
        raise BookingError(f'Duration must be an integer of at least {minimum} hours')
    # Include bookings AFTER the search boundary: proposed cleaning may extend past it.
    # A single statement provides a consistent snapshot and one clock reference.
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("""SELECT statement_timestamp() AS checked_at,b.type,b.starts_at,b.ends_at,b.request_data,
            b.cleaning_starts_at,b.cleaning_ends_at
            FROM (SELECT 1) seed LEFT JOIN bookings b ON
            b.status IN ('hold','pending','confirmed')
            AND (b.status='confirmed' OR b.expires_at>statement_timestamp())
            AND (b.request_data->>'source'='books_plans' AND b.starts_at>statement_timestamp()) IS NOT TRUE
            AND ((b.starts_at<%s AND COALESCE(b.cleaning_ends_at,b.ends_at)>%s)
                OR (b.type='booking' AND b.cleaning_ends_at IS NULL))
            ORDER BY b.starts_at""",(end+timedelta(days=10),start))
        records=cursor.fetchall()
    now=records[0]['checked_at']
    rows=[row for row in records if row['type'] is not None]
    blocked=[]
    occupied=[]
    warnings=set()

    def segment(a,b,reason):
        a,b=max(a,start),min(b,end)
        if a<b:
            blocked.append(dict(starts_at=a,ends_at=b,reason=reason))

    for row in rows:
        a,b=row['starts_at'],row['ends_at']
        occupied.append((a,row['cleaning_ends_at'] or b))
        segment(a,b,'planned_block' if books_plans.managed(row) else 'manual_block' if row['type']=='block' else 'rental')
        if row['cleaning_ends_at']:
            segment(b,row['cleaning_starts_at'],'waiting_for_cleaning')
            segment(row['cleaning_starts_at'],row['cleaning_ends_at'],'cleaning')
        elif row['type']=='booking':
            warnings.add('Existing active bookings need an explicit cleaning slot before new sales')
    for _,a,b in books_plans.intervals(config,start,end+timedelta(days=10)):
        if (a,b) in occupied:
            continue
        occupied.append((a,b))
        segment(a,b,'planned_block')
    # Physical gaps are deliberately distinguished from valid >=5h rental options.
    gaps=[]
    cursor_time=start
    for a,b in sorted(occupied):
        if b<=cursor_time or a>=end:
            continue
        if a>cursor_time:
            gaps.append(dict(starts_at=cursor_time,ends_at=min(a,end)))
        cursor_time=max(cursor_time,min(b,end))
    if cursor_time<end:
        gaps.append(dict(starts_at=cursor_time,ends_at=end))

    options=[]
    cleaning_cache={}

    def cleaning_at(rental_end):
        if rental_end not in cleaning_cache:
            try:
                cleaning_cache[rental_end]=next_cleaning(rental_end,config)
            except RulesError as error:
                cleaning_cache[rental_end]=None
                warnings.add(str(error))
        return cleaning_cache[rental_end]

    candidate=max(start,now).astimezone(SEOUL)
    rounded=candidate.replace(minute=0,second=0,microsecond=0)
    candidate=rounded if rounded>=candidate and rounded>now else rounded+timedelta(hours=1)
    while candidate+timedelta(hours=duration_hours or minimum)<=end:
        # Occupancy boundary can lie outside the requested range.
        inside=any(a<=candidate<b for a,b in occupied)
        boundary=min((a for a,b in occupied if a>candidate),default=None)
        valid=[]
        hours=duration_hours or minimum
        if not inside and not any(row['type']=='booking' and row['cleaning_ends_at'] is None for row in rows):
            while candidate+timedelta(hours=hours)<=end:
                rental_end=candidate+timedelta(hours=hours)
                if boundary is not None and rental_end>boundary:
                    break
                cleaning=cleaning_at(rental_end)
                if cleaning and (boundary is None or cleaning[1]<=boundary):
                    valid.append((hours,rental_end,cleaning))
                elif cleaning and boundary is not None and cleaning[1]>boundary:
                    break
                if duration_hours is not None:
                    break
                hours+=1
        if valid:
            first,last=valid[0],valid[-1]
            # Missing schedule could create holes: expose exact durations, not an inferred range.
            options.append(dict(starts_at=candidate,min_hours=first[0],max_hours=last[0],
                allowed_hours=[item[0] for item in valid],
                earliest_ends_at=first[1],latest_ends_at=last[1],
                earliest_cleaning_starts_at=first[2][0],earliest_cleaning_ends_at=first[2][1],
                latest_cleaning_starts_at=last[2][0],latest_cleaning_ends_at=last[2][1]))
        candidate+=timedelta(hours=1)

    def serial(value):
        if isinstance(value,datetime):
            return value.astimezone(SEOUL).isoformat()
        if isinstance(value,list):
            return [serial(item) for item in value]
        if isinstance(value,dict):
            return {key:serial(item) for key,item in value.items()}
        return value

    return serial(dict(timezone='Asia/Seoul',checked_at=now,starts_at=start,ends_at=end,
        minimum_hours=minimum,requested_duration_hours=duration_hours,
        blocked=sorted(blocked,key=lambda item:item['starts_at']),unoccupied=gaps,
        available_starts=options,warnings=sorted(warnings),is_reservation=False))
