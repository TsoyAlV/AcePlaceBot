import os
import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import test_bookings as fixtures
from app import bookings,calendar_sync
from app.google_calendar import RecreateEvent
from app.database import connect_database


class FakeCalendar:
    def __init__(self):
        self.events={}
        self.calls=[]
        self.fail_after_write=False
        self.tombstones=set()

    def upsert(self,calendar,event_id,body):
        if event_id in self.tombstones:
            raise RecreateEvent()
        self.events[(calendar,event_id)]=deepcopy(body)
        self.calls.append(('upsert',event_id))
        if self.fail_after_write:
            self.fail_after_write=False
            raise TimeoutError('reply lost')

    def delete(self,calendar,event_id):
        self.events.pop((calendar,event_id),None)
        self.calls.append(('delete',event_id))


@unittest.skipUnless(os.getenv('RUN_DB_TESTS')=='1','Set RUN_DB_TESTS=1')
class CalendarSyncTests(unittest.TestCase):
    use_schema=fixtures.BookingTests.use_schema
    drop_schema=fixtures.BookingTests.drop_schema

    def setUp(self):
        fixtures.BookingTests.setUp(self)
        self.settings=replace(self.settings,google_calendar_id='test-calendar')
        self.config=bookings.load_business_config().model_copy(deep=True)
        self.config.calendar.enabled=True
        patcher=patch('app.calendar_sync.load_business_config',return_value=self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.remote=FakeCalendar()

    def connection(self,settings):
        c=connect_database(settings)
        self.use_schema(c)
        return c

    def create(self,**extra):
        args=dict(user_id=self.client,starts_at=self.start,ends_at=self.end,amount='150000',request_key=uuid4())
        args.update(extra)
        with self.connection(self.settings) as c:
            return bookings.create_booking(c,101,**args)

    def change(self,row,action,**kwargs):
        with self.connection(self.settings) as c:
            return bookings.change_booking(c,101,row['booking_id'],action=action,expected_version=row['version'],**kwargs)

    def sync(self):
        with patch('app.calendar_sync.connect_database',side_effect=self.connection):
            return calendar_sync.sync_once(self.settings,adapter_factory=lambda:self.remote)

    def fresh(self,row):
        return bookings.get_booking(self.c,101,row['booking_id'])

    def due(self,row):
        self.c.execute("UPDATE bookings SET calendar_sync=calendar_sync-'retry_at' WHERE booking_id=%s",(row['booking_id'],))
        self.c.commit()

    def test_pending_does_not_create_calendar_events(self):
        row=self.create()
        self.assertEqual(row['calendar_sync'],{})
        self.sync()
        self.assertEqual(self.remote.events,{})

    def test_confirmation_creates_pair_in_seoul_and_different_colors(self):
        row=self.change(self.create(),'confirm')
        self.sync()
        self.assertEqual(len(self.remote.events),2)
        bodies=list(self.remote.events.values())
        self.assertEqual({item['colorId'] for item in bodies},{'9','6'})
        self.assertTrue(all(item['start']['timeZone']=='Asia/Seoul' for item in bodies))
        self.assertTrue(all('attendees' not in item and 'file_id' not in str(item) for item in bodies))
        current=self.fresh(row)
        self.assertEqual(current['version'],row['version'])
        self.assertEqual(current['calendar_sync']['synced_version'],row['version'])

    def test_move_updates_both_existing_ids(self):
        row=self.change(self.create(),'confirm')
        self.sync()
        ids=set(self.remote.events)
        row=self.change(row,'move',starts_at=self.start+timedelta(days=1),ends_at=self.end+timedelta(days=1))
        self.sync()
        self.assertEqual(set(self.remote.events),ids)
        state=self.fresh(row)['calendar_sync']
        self.assertEqual(self.remote.events[('test-calendar',state['cleaning_event_id'])]['start']['dateTime'],row['cleaning_starts_at'].astimezone(calendar_sync.SEOUL).isoformat())

    def test_cancellation_deletes_both_and_is_idempotent(self):
        row=self.change(self.create(),'confirm')
        self.sync()
        row=self.change(row,'cancel')
        self.sync()
        self.assertEqual(self.remote.events,{})
        self.sync()
        self.assertEqual(self.remote.events,{})

    def test_manual_block_has_no_cleaning_event(self):
        row=self.create(user_id=None,kind='block',status='confirmed',amount='0')
        self.sync()
        self.assertEqual(len(self.remote.events),1)
        self.assertEqual(next(iter(self.remote.events.values()))['colorId'],'8')
        self.change(row,'cancel')
        self.sync()
        self.assertEqual(self.remote.events,{})

    def test_timeout_after_remote_write_retries_without_duplicates(self):
        row=self.change(self.create(),'confirm')
        self.remote.fail_after_write=True
        self.sync()
        failed=self.fresh(row)
        self.assertEqual(failed['status'],'confirmed')
        self.assertEqual(failed['calendar_sync']['last_error'],'TimeoutError')
        ids=(failed['calendar_sync']['booking_event_id'],failed['calendar_sync']['cleaning_event_id'])
        self.due(row)
        self.sync()
        self.assertEqual({key[1] for key in self.remote.events},set(ids))
        self.assertEqual(self.fresh(row)['calendar_sync']['status'],'synced')

    def test_change_during_sync_is_reconciled_to_latest_version(self):
        row=self.change(self.create(),'confirm')
        original=self.remote.upsert
        called=False
        def update_then_cancel(*args):
            nonlocal called
            original(*args)
            if not called:
                called=True
                self.change(row,'cancel')
        self.remote.upsert=update_then_cancel
        self.sync()
        self.assertEqual(self.remote.events,{})
        self.assertEqual(self.fresh(row)['status'],'cancelled')
        self.assertEqual(self.fresh(row)['calendar_sync']['status'],'synced')

    def test_manually_deleted_event_is_recreated_with_persisted_id(self):
        row=self.change(self.create(),'confirm')
        self.sync()
        state=self.fresh(row)['calendar_sync']
        old_id=state['cleaning_event_id']
        self.remote.events.pop(('test-calendar',old_id))
        self.remote.tombstones.add(old_id)
        with self.connection(self.settings) as c:
            from app.calendar_queue import queue_calendar
            queue_calendar(c,self.fresh(row))
        self.sync()
        updated=self.fresh(row)['calendar_sync']
        self.assertNotEqual(updated['cleaning_event_id'],old_id)
        self.assertEqual(len(self.remote.events),2)

    def test_stale_finish_does_not_mark_newer_version_synced(self):
        row=self.change(self.create(),'confirm')
        with patch('app.calendar_sync.connect_database',side_effect=self.connection):
            job=calendar_sync.claim(self.settings)
            moved=self.change(row,'move',starts_at=self.start+timedelta(days=1),ends_at=self.end+timedelta(days=1))
            calendar_sync.finish(self.settings,job)
        state=self.fresh(moved)['calendar_sync']
        self.assertEqual(state['status'],'pending')
        self.assertNotEqual(state.get('synced_version'),moved['version'])
        self.sync()
        self.assertEqual(self.fresh(moved)['calendar_sync']['synced_version'],moved['version'])

    def test_disabled_integration_does_not_call_api(self):
        row=self.change(self.create(),'confirm')
        self.config.calendar.enabled=False
        self.assertEqual(self.sync(),0)
        self.assertEqual(self.remote.calls,[])

    def test_changed_calendar_id_cannot_silently_duplicate_events(self):
        row=self.change(self.create(),'confirm')
        self.sync()
        row=self.change(row,'move',starts_at=self.start+timedelta(days=1),ends_at=self.end+timedelta(days=1))
        self.settings=replace(self.settings,google_calendar_id='another-calendar')
        self.sync()
        self.assertEqual(self.fresh(row)['calendar_sync']['last_error'],'CalendarIdChanged')
        self.assertEqual(len(self.remote.events),2)
