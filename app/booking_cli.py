"""Trusted local console for booking service checks; not a public identity API."""

import argparse
from datetime import datetime
from uuid import UUID
import json

from app.bookings import BookingError, change_booking, create_booking, is_available, list_bookings
from app.config import load_settings
from app.database import connect_database
from app.availability import get_availability_windows


def main() -> int:
    parser = argparse.ArgumentParser(description='Local Ace Place booking administration')
    parser.add_argument('--actor', type=int, required=True, help='Telegram ID of registered caller')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    available = sub.add_parser('available')
    windows = sub.add_parser('windows',help='Blocked segments and bookable starts in a date range')
    windows.add_argument('--hours',type=int,help='Optional exact rental duration, minimum 5')
    create = sub.add_parser('create')
    for item in (available, create, windows):
        item.add_argument('--start', type=datetime.fromisoformat, required=True)
        item.add_argument('--end', type=datetime.fromisoformat, required=True)
    create.add_argument('--client-id', type=int, help='Internal clients.user_id, optional for external bookings')
    create.add_argument('--amount', help='Manual amount; omit for automatic KRW pricing')
    create.add_argument('--unit', default='KRW')
    create.add_argument('--request-key', type=UUID, required=True)
    create.add_argument('--hold', action='store_true')
    create.add_argument('--block', action='store_true')
    for command in ('submit','confirm','cancel','move'):
        item = sub.add_parser(command)
        item.add_argument('--id', type=int, required=True)
        item.add_argument('--version', type=int, required=True)
        if command == 'move':
            item.add_argument('--start', type=datetime.fromisoformat, required=True)
            item.add_argument('--end', type=datetime.fromisoformat, required=True)
    args = parser.parse_args()
    try:
        with connect_database(load_settings()) as connection:
            if args.command == 'list':
                result = list_bookings(connection, args.actor)
            elif args.command == 'available':
                result = {'available': is_available(connection, args.actor, args.start, args.end)}
            elif args.command == 'windows':
                result = get_availability_windows(connection,args.actor,args.start,args.end,duration_hours=args.hours)
            elif args.command == 'create':
                if args.hold and args.block:
                    raise BookingError('Choose either hold or block')
                result = create_booking(connection, args.actor, user_id=args.client_id,
                    starts_at=args.start, ends_at=args.end, amount=args.amount, unit=args.unit,
                    request_key=args.request_key, kind='block' if args.block else 'booking',
                    status='confirmed' if args.block else ('hold' if args.hold else 'pending'),
                    ttl_minutes=None)
            else:
                result = change_booking(connection, args.actor, args.id, action=args.command,
                    expected_version=args.version, starts_at=getattr(args,'start',None), ends_at=getattr(args,'end',None))
        print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
        return 0
    except BookingError as error:
        print(str(error))
        return 1
    except Exception as error:
        print(f'Operation failed ({type(error).__name__}). No changes committed.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
