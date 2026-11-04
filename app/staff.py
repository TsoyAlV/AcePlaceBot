"""Owner-only staff management; role changes are serialized with owner bootstrap."""

from psycopg.rows import dict_row

from app.bookings import AccessDenied,BookingError,StaleBooking
from app.users import admin_role


def require_owner(connection,actor):
    if admin_role(connection,actor)!='owner':
        raise AccessDenied('Owner required')


def get_member(connection,actor,telegram_id):
    require_owner(connection,actor)
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("""SELECT c.user_id,c.telegram_user_id,c.name,a.role,a.active,a.notifications_enabled
            FROM clients c LEFT JOIN admins a USING(user_id) WHERE c.telegram_user_id=%s""",(telegram_id,))
        row=cursor.fetchone()
    if row is None:
        raise BookingError('Сначала пользователь должен отправить боту /start. / 먼저 봇에 /start 를 보내야 합니다.')
    return row


def list_members(connection,actor,page=0):
    require_owner(connection,actor)
    if not 0<=page<=100000:
        raise BookingError('Invalid page')
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("""SELECT c.user_id,c.telegram_user_id,c.name,a.role,a.active,a.notifications_enabled
            FROM admins a JOIN clients c USING(user_id) ORDER BY c.user_id LIMIT 9 OFFSET %s""",(page*8,))
        return cursor.fetchall()


def snapshot(row):
    return {key:row[key] for key in ('user_id','telegram_user_id','role','active','notifications_enabled')}


def update_member(connection,actor,telegram_id,*,expected,field,value):
    connection.execute('SELECT pg_advisory_xact_lock(836021471)')
    require_owner(connection,actor)
    row=get_member(connection,actor,telegram_id)
    if snapshot(row)!=expected:
        raise StaleBooking('Staff member changed')
    if field=='role':
        if value not in ('admin','operator') or row['role']=='owner':
            raise BookingError('Роль владельца изменить нельзя. / 소유자 역할은 변경할 수 없습니다.')
    elif field in ('active','notifications_enabled'):
        if type(value) is not bool or row['role'] is None:
            raise BookingError('Invalid staff update')
        if field=='active' and row['role']=='owner':
            raise BookingError('Отключить владельца нельзя. / 소유자는 비활성화할 수 없습니다.')
    else:
        raise BookingError('Invalid staff update')
    if row['role'] is None:
        connection.execute('INSERT INTO admins(user_id,role) VALUES (%s,%s)',(row['user_id'],value))
    else:
        # Field names are selected exclusively from the validated allowlist above.
        from psycopg import sql
        connection.execute(sql.SQL('UPDATE admins SET {}=%s WHERE user_id=%s').format(sql.Identifier(field)),(value,row['user_id']))
    return get_member(connection,actor,telegram_id)
