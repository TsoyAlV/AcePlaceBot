import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from datetime import timedelta

import test_bookings as fixtures
from app import expiry_worker,bookings,notifications
from app.database import connect_database
from app.rental_rules import SEOUL


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

    def ended_booking(self):
        row=fixtures.BookingTests.create(self)
        row=fixtures.BookingTests.change(self,row,'confirm')
        now=self.c.execute('SELECT clock_timestamp()').fetchone()[0]
        end=now-timedelta(minutes=1)
        cleaning=now.replace(minute=0,second=0,microsecond=0)+timedelta(hours=2)
        self.c.execute('''UPDATE bookings SET starts_at=%s,ends_at=%s,cleaning_starts_at=%s,cleaning_ends_at=%s
            WHERE booking_id=%s''',(end-timedelta(hours=5),end,cleaning,cleaning+timedelta(hours=1),row['booking_id']))
        self.c.commit()
        return bookings.get_booking(self.c,101,row['booking_id'])

    async def test_sweep_completes_ended_rental_once_and_hides_it_only_from_admin(self):
        ended=self.ended_booking()
        future=fixtures.BookingTests.create(self)
        future=fixtures.BookingTests.change(self,future,'confirm')
        self.c.commit()
        # List is clean even before the next background sweep.
        self.assertNotIn(ended['booking_id'],[r['booking_id'] for r in bookings.list_bookings(self.c,101)])
        with patch('app.expiry_worker.connect_database',side_effect=self.connection):
            self.assertEqual(expiry_worker.sweep(self.settings),1)
            self.assertEqual(expiry_worker.sweep(self.settings),0)
        current=bookings.get_booking(self.c,101,ended['booking_id'])
        self.assertEqual(current['status'],'completed')
        self.assertEqual(current['version'],ended['version']+1)
        self.assertEqual(current['cleaning_ends_at'],ended['cleaning_ends_at'])
        self.assertIn(future['booking_id'],[r['booking_id'] for r in bookings.list_bookings(self.c,101)])
        self.assertIn(ended['booking_id'],[r['booking_id'] for r in bookings.list_bookings(self.c,102)])

    async def test_completed_rental_still_blocks_cleanup_in_tool_service_and_database(self):
        row=self.ended_booking()
        bookings.complete_bookings(self.c)
        start=row['cleaning_starts_at']-timedelta(hours=1)
        self.assertFalse(bookings.is_available(self.c,102,start,start+timedelta(hours=5)))
        from app.availability import get_availability_windows
        with patch('app.availability.load_business_config',return_value=bookings.load_business_config()):
            result=get_availability_windows(self.c,102,start,row['cleaning_ends_at']+timedelta(hours=6))
        self.assertIn('cleaning',[r['reason'] for r in result['blocked']])
        self.assertEqual(result['available_starts'][0]['starts_at'],row['cleaning_ends_at'].astimezone(SEOUL).isoformat())
        with self.assertRaises(bookings.SlotUnavailable):
            fixtures.BookingTests.create(self,user_id=None,kind='block',status='confirmed',amount='0',starts_at=start,ends_at=start+timedelta(hours=1))
        following=fixtures.BookingTests.create(self,starts_at=row['cleaning_ends_at'],ends_at=row['cleaning_ends_at']+timedelta(hours=5))
        self.assertEqual(following['starts_at'],row['cleaning_ends_at'])

    async def test_completed_rental_keeps_upcoming_cleaning_reminder(self):
        row=self.ended_booking()
        # Simulate a finish just before a cleanup within the notification window.
        now=self.c.execute('SELECT clock_timestamp()').fetchone()[0]
        self.c.execute('UPDATE bookings SET cleaning_starts_at=%s,cleaning_ends_at=%s WHERE booking_id=%s',
            (now+timedelta(minutes=30),now+timedelta(minutes=90),row['booking_id']))
        bookings.complete_bookings(self.c)
        self.c.commit()
        with patch('app.notifications.connect_database',side_effect=self.connection):
            notifications.prepare_cleaning_reminders(self.settings)
        current=bookings.get_booking(self.c,101,row['booking_id'])
        self.assertTrue(any(s.get('event')=='cleaning_reminder' for s in current['admin_notifications'].values()))
        bot=SimpleNamespace(send_message=AsyncMock())
        with patch('app.notifications.connect_database',side_effect=self.connection):
            await notifications.deliver_pending(bot,self.settings)
        self.assertTrue(any('скоро уборка' in call.kwargs['text'] for call in bot.send_message.call_args_list))

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
