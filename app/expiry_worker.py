"""Durable expiry sweep: python -m app.expiry_worker [--once]."""

import argparse
import asyncio
import logging
import time

from app.bookings import expire_bookings,complete_bookings
from app.config import load_settings
from app.database import connect_database


logger=logging.getLogger(__name__)


def sweep(settings):
    with connect_database(settings) as connection:
        return expire_bookings(connection)+complete_bookings(connection)


async def loop(application):
    while True:
        try:
            count=await asyncio.to_thread(sweep,application.bot_data['settings'])
            if count:
                from app.notifications import wake_delivery
                wake_delivery(application)
        except Exception as error:
            logger.warning('Expiry sweep failed (%s); retry in 30 seconds',type(error).__name__)
        await asyncio.sleep(30)


async def start(application):
    application.bot_data['expiry_task']=asyncio.create_task(loop(application))


async def stop(application):
    task=application.bot_data.pop('expiry_task',None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    settings = load_settings()
    try:
        while True:
            try:
                count = sweep(settings)
                print(f'Updated bookings (expired/completed): {count}', flush=True)
            except Exception as error:
                print(f'Expiry sweep failed ({type(error).__name__}); retry in 30 seconds.', flush=True)
                if args.once:
                    return 1
            if args.once:
                return 0
            time.sleep(30)
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
