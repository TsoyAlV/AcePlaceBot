"""Inspect tables or apply the initial schema atomically."""

import argparse

from app.config import PROJECT_ROOT, load_settings
from app.database import connect_database


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='Create initial tables')
    parser.add_argument('--migration', choices=['001_initial', '002_remove_cleaning', '003_schema_comments', '004_booking_requests', '005_cleaning_slots', '006_client_notifications', '007_change_notifications', '008_payment_receipts', '009_calendar_sync', '010_client_cancellations', '011_cleaning_duration', '012_booking_details', '013_completed_bookings'], default='001_initial')
    args = parser.parse_args()
    try:
        with connect_database(load_settings()) as connection:
            connection.execute("SET LOCAL search_path TO public")
            connection.execute("SELECT pg_advisory_xact_lock(836021470)")
            tables = [row[0] for row in connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
            )]
            print('Current public tables: ' + (', '.join(tables) or '(none)'))
            if args.apply:
                if args.migration == '001_initial' and set(tables) & {'clients', 'admins', 'bookings'}:
                    print('Initial migration not applied: target tables already exist. No data changed.')
                    return 1
                sql = (PROJECT_ROOT / 'migrations' / f'{args.migration}.sql').read_text(encoding='utf-8')
                connection.execute(sql)
        if args.apply:
            print(f'Migration committed: {args.migration}.')
        return 0
    except Exception as error:
        print(f'Migration failed ({type(error).__name__}); transaction rolled back.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
