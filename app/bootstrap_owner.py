"""Local-only command to appoint the first registered owner."""

import argparse

from app.config import load_settings
from app.database import connect_database
from app.users import grant_first_owner


def main() -> int:
    parser = argparse.ArgumentParser(description='Appoint the first Ace Place owner locally')
    parser.add_argument('--telegram-id', type=int, required=True)
    parser.add_argument('--google-email')
    args = parser.parse_args()
    try:
        with connect_database(load_settings()) as connection:
            created = grant_first_owner(connection, args.telegram_id, args.google_email)
        print('Owner appointed.' if created else 'This user is already the owner. No changes made.')
        return 0
    except ValueError as error:
        print(str(error))
        return 1
    except Exception as error:
        print(f'Owner bootstrap failed ({type(error).__name__}). No changes committed.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
