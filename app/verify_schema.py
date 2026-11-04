"""Check database constraints using temporary, rolled-back records."""

import secrets

from psycopg.errors import CheckViolation, ExclusionViolation, ForeignKeyViolation

from app.config import load_settings
from app.database import connect_database


def main() -> None:
    base = -secrets.randbelow(10**15) - 100
    with connect_database(load_settings()) as connection:
        with connection.transaction(force_rollback=True):
            connection.execute(
                "INSERT INTO clients(user_id, telegram_user_id, telegram_chat_id, name) VALUES (%s,%s,%s,'Schema test')",
                (base, -base, -base),
            )
            connection.execute("INSERT INTO admins(user_id,role) VALUES (%s,'owner')", (base,))
            insert = """INSERT INTO bookings
                (booking_id,user_id,status,starts_at,ends_at,amount)
                VALUES (%s,%s,'confirmed',%s,%s,100000)"""
            connection.execute(insert, (base, base, '2099-11-07 20:00+09', '2099-11-08 01:00+09'))

            def rejected(sql, params, expected):
                try:
                    with connection.transaction():
                        connection.execute(sql, params)
                except expected:
                    return
                raise AssertionError(f'Expected {expected.__name__}')

            rejected(insert, (base-1, base, '2099-11-08 00:30+09', '2099-11-08 03:00+09'), ExclusionViolation)
            print('OK: rental overlaps prevented across midnight')
            connection.execute(insert, (base-2, base, '2099-11-08 01:00+09', '2099-11-08 07:00+09'))
            print('OK: adjacent intervals allowed')
            rejected("UPDATE bookings SET starts_at='2099-11-08 00:30+09' WHERE booking_id=%s", (base-2,), ExclusionViolation)
            assert connection.execute("SELECT extract(hour FROM starts_at AT TIME ZONE 'Asia/Seoul') FROM bookings WHERE booking_id=%s", (base-2,)).fetchone()[0] == 1
            print('OK: failed move preserves original booking')
            rejected("UPDATE bookings SET amount=-1 WHERE booking_id=%s", (base,), CheckViolation)
            rejected("UPDATE bookings SET status='hold',expires_at=NULL WHERE booking_id=%s", (base,), CheckViolation)
            rejected("INSERT INTO admins(user_id) VALUES (%s)", (base-10,), ForeignKeyViolation)
            print('OK: payment, expiry and foreign-key checks')
            connection.execute("UPDATE bookings SET status='cancelled' WHERE booking_id=%s", (base,))
            assert connection.execute("SELECT version FROM bookings WHERE booking_id=%s", (base,)).fetchone()[0] == 2
            connection.execute(insert, (base-3, base, '2099-11-07 20:00+09', '2099-11-08 01:00+09'))
            print('OK: cancellation releases interval; version increments')
    print('All checks passed; temporary records rolled back.')


if __name__ == '__main__':
    main()
