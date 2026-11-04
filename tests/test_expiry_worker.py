import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

import test_bookings as fixtures
from app import expiry_worker,bookings,notifications
from app.database import connect_database


class ExpiryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_retries_then_wakes_notifications(self):
        application=SimpleNamespace(bot_data={
            'settings':object(),'delivery_wakeup':asyncio.Event(),'calendar_wakeup':asyncio.Event()})
        with patch('app.expiry_worker.sweep',side_effect=[RuntimeError('unavailable'),1]) as sweep, \
             patch('app.expiry_worker.asyncio.sleep',new=AsyncMock(side_effect=[None,asyncio.CancelledError()])), \
             self.assertLogs('app.expiry_worker',level='WARNING'):
            with self.assertRaises(asyncio.CancelledError):
                await expiry_worker.loop(application)
        self.assertEqual(sweep.call_count,2)
        self.assertTrue(application.bot_data['delivery_wakeup'].is_set())
        self.assertTrue(application.bot_data['calendar_wakeup'].is_set())

    async def test_background_task_stops_cleanly(self):
        application=SimpleNamespace(bot_data={})
        with patch('app.expiry_worker.loop',new=AsyncMock()):
            await expiry_worker.start(application)
            task=application.bot_data['expiry_task']
            await expiry_worker.stop(application)
        self.assertTrue(task.done())
        self.assertNotIn('expiry_task',application.bot_data)


@unittest.skipUnless(os.getenv('RUN_DB_TESTS')=='1','Set RUN_DB_TESTS=1')
class ExpiryDatabaseTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.BookingTests.setUp
    use_schema=fixtures.BookingTests.use_schema
    drop_schema=fixtures.BookingTests.drop_schema

    def connection(self,settings):
        c=connect_database(settings)
        self.use_schema(c)
        return c

    async def test_sweep_expires_once_and_delivers_to_both_sides(self):
        row=fixtures.BookingTests.create(self)
        self.c.execute("UPDATE bookings SET expires_at=clock_timestamp()-interval '1 second' WHERE booking_id=%s",(row['booking_id'],))
        self.c.commit()
        with patch('app.expiry_worker.connect_database',side_effect=self.connection):
            self.assertEqual(expiry_worker.sweep(self.settings),1)
            self.assertEqual(expiry_worker.sweep(self.settings),0)
        current=bookings.get_booking(self.c,101,row['booking_id'])
        self.assertEqual(current['status'],'expired')
        with self.assertRaises(bookings.BookingError):
            bookings.change_booking(self.c,101,row['booking_id'],action='confirm',expected_version=current['version'])
        self.c.rollback()
        bot=SimpleNamespace(send_message=AsyncMock())
        with patch('app.notifications.connect_database',side_effect=self.connection):
            await notifications.deliver_pending(bot,self.settings)
            await notifications.deliver_pending(bot,self.settings)
        self.assertEqual({call.kwargs['chat_id'] for call in bot.send_message.call_args_list},{101,102})
        self.assertEqual(bot.send_message.await_count,2)
