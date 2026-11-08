import asyncio
import os
import unittest
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from unittest.mock import patch,AsyncMock
from uuid import uuid4

from pydantic import ValidationError

import test_bookings as fixtures
from test_calendar_sync import FakeCalendar
from app import books_plans,bookings,calendar_sync,notifications
from app.availability import get_availability_windows
from app.business_config import BusinessConfig,BooksPlanInterval,load_business_config
from app.database import connect_database
from app.rental_rules import SEOUL,DAYS


class PlannedScheduleTests(unittest.TestCase):
    def config(self):
        config=load_business_config().model_copy(deep=True)
        config.books_plans.enabled=True
        config.books_plans.weekly={d:[] for d in DAYS}
        return config

    def test_multiple_ranges_overnight_exceptions_and_half_open_boundaries(self):
        config=self.config()
        config.books_plans.weekly['MON']=[BooksPlanInterval.model_validate(x) for x in (
            {'from':'10:00','to':'12:00'},{'from':'23:00','to':'01:00'})]
        start=datetime(2030,11,4,0,tzinfo=SEOUL) # Monday
        spans=list(books_plans.intervals(config,start,start+timedelta(days=2)))
        self.assertEqual(len(spans),2)
        self.assertEqual(spans[1][2],start+timedelta(days=1,hours=1))
        self.assertFalse(books_plans.overlaps(config,start+timedelta(hours=12),start+timedelta(hours=23)))
        config.books_plans.excluded_dates=[start.date()]
        self.assertEqual(list(books_plans.intervals(config,start,start+timedelta(days=2))),[])

    def test_overlapping_week_and_sunday_midnight_are_rejected(self):
        for a,b in ((('MON','10:00','12:00'),('MON','11:00','13:00')),
                    (('SUN','23:00','02:00'),('MON','01:00','03:00'))):
            config=self.config().model_dump(by_alias=True)
            for day,start,end in (a,b):
                config['books_plans']['weekly'][day].append({'from':start,'to':end})
            with self.assertRaises(ValidationError):
                BusinessConfig.model_validate(config)


@unittest.skipUnless(os.getenv('RUN_DB_TESTS')=='1','Set RUN_DB_TESTS=1')
class PlannedBookingTests(unittest.TestCase):
    use_schema=fixtures.BookingTests.use_schema
    drop_schema=fixtures.BookingTests.drop_schema
    create=fixtures.BookingTests.create
    change=fixtures.BookingTests.change

    def setUp(self):
        fixtures.BookingTests.setUp(self)
        self.rules=bookings.load_business_config().model_copy(deep=True)
        self.rules.books_plans.enabled=True
        self.rules.books_plans.weekly={d:[] for d in DAYS}
        self.rules.calendar.enabled=True
        self.settings=replace(self.settings,google_calendar_id='test-calendar')
        for name in ('app.bookings.load_business_config','app.availability.load_business_config','app.calendar_sync.load_business_config'):
            patcher=patch(name,return_value=self.rules)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.remote=FakeCalendar()

    def plan(self,start,end):
        start,end=start.astimezone(SEOUL),end.astimezone(SEOUL)
        self.rules.books_plans.weekly[DAYS[start.weekday()]].append(
            BooksPlanInterval.model_validate({'from':start.strftime('%H:%M'),'to':end.strftime('%H:%M')}))

    def connection(self,settings):
        c=connect_database(settings)
        self.use_schema(c)
        return c

    def sync(self):
        self.c.commit()
        with patch('app.calendar_sync.connect_database',side_effect=self.connection):
            return calendar_sync.sync_once(self.settings,adapter_factory=lambda:self.remote)

    def records(self,role=None):
        query="SELECT * FROM bookings WHERE request_data->>'source'='books_plans'"
        if role:
            return books_plans._rows(self.c,query+" AND request_data->>'role'=%s",(role,))
        return books_plans._rows(self.c,query)

    def test_creation_availability_and_cleaning_obey_plans_before_first_worker_sweep(self):
        self.plan(self.end,self.end+timedelta(hours=2))
        self.assertFalse(bookings.is_available(self.c,102,self.start,self.end))
        result=get_availability_windows(self.c,102,self.start,self.end)
        self.assertEqual(result['available_starts'],[])
        with self.assertRaises(bookings.SlotUnavailable):
            self.create()
        # Both rental and cleanup can end exactly at the beginning of a planned block.
        self.create(starts_at=self.start-timedelta(hours=1),ends_at=self.end-timedelta(hours=1))

    def test_manual_blocks_and_customer_requests_cannot_overlap(self):
        self.plan(self.start,self.end)
        with self.assertRaises(bookings.SlotUnavailable):
            self.create(user_id=None,kind='block',status='confirmed',amount='0')
        quote=bookings.quote_rental(self.start,self.end)
        with self.assertRaises(bookings.SlotUnavailable):
            bookings.create_client_booking(self.c,102,details=dict(contact_phone='01022636263',guests_count=40,event_name='Level up'),starts_at=self.start,ends_at=self.end,
                request_key=uuid4(),expected_quote=quote)

    def test_virtual_schedule_protects_dates_beyond_generation_horizon(self):
        start=self.start+timedelta(days=400)
        self.plan(start,start+timedelta(hours=2))
        self.assertFalse(bookings.is_available(self.c,102,start,start+timedelta(hours=5)))
        with self.assertRaises(bookings.SlotUnavailable):
            self.create(starts_at=start,ends_at=start+timedelta(hours=5))

    def test_move_into_plan_preserves_original_booking(self):
        row=self.create()
        start=self.start+timedelta(days=1)
        self.plan(start,start+timedelta(hours=2))
        with self.assertRaises(bookings.SlotUnavailable):
            self.change(row,'move',starts_at=start,ends_at=start+timedelta(hours=5))
        self.assertEqual(bookings.get_booking(self.c,101,row['booking_id'])['starts_at'],row['starts_at'])

    def test_reconciliation_is_idempotent_and_series_is_hidden_from_lists(self):
        self.plan(self.start,self.start+timedelta(hours=2))
        books_plans.reconcile(self.c,self.rules)
        first=[(r['booking_id'],r['version']) for r in self.records()]
        books_plans.reconcile(self.c,self.rules)
        self.assertEqual(first,[(r['booking_id'],r['version']) for r in self.records()])
        self.assertTrue(self.records('occurrence'))
        self.assertTrue(all(r['type']=='block' and r['amount']==0 and not r['cleaning_ends_at'] for r in self.records()))
        self.assertFalse(any(books_plans.managed(r,'series') for r in bookings.list_bookings(self.c,101)))
        occurrence=self.records('occurrence')[0]
        with self.assertRaises(bookings.BookingError):
            self.change(occurrence,'cancel')

    def test_disabling_plans_releases_future_blocks_but_preserves_manual_blocks_and_running_plan(self):
        self.plan(self.start,self.start+timedelta(hours=2))
        books_plans.reconcile(self.c,self.rules)
        manual=self.create(user_id=None,kind='block',status='confirmed',amount='0',
            starts_at=self.end+timedelta(hours=2),ends_at=self.end+timedelta(hours=3))
        self.rules.books_plans.enabled=False
        books_plans.reconcile(self.c,self.rules)
        now=self.c.execute('SELECT clock_timestamp()').fetchone()[0]
        self.assertTrue(all(r['status']=='cancelled' for r in self.records('occurrence') if r['starts_at']>now))
        self.assertEqual(bookings.get_booking(self.c,101,manual['booking_id'])['status'],'confirmed')
        self.assertTrue(bookings.is_available(self.c,102,self.start,self.end))

    def test_google_uses_two_recurring_parents_without_individual_event_duplicates(self):
        self.plan(self.start,self.start+timedelta(hours=2))
        self.plan(self.start+timedelta(hours=3),self.start+timedelta(hours=4))
        self.sync()
        self.assertEqual(len(self.remote.events),2)
        ids=set(self.remote.events)
        for body in self.remote.events.values():
            self.assertTrue(body['recurrence'][0].startswith('RRULE:FREQ=WEEKLY;BYDAY='))
            self.assertIn(';UNTIL=',body['recurrence'][0])
            self.assertEqual(body['start']['timeZone'],'Asia/Seoul')
        self.sync()
        self.assertEqual(set(self.remote.events),ids)
        self.assertTrue(all(r['calendar_sync']=={} for r in self.records('occurrence')))

    def test_existing_booking_is_kept_conflict_excluded_from_google_and_admin_notified(self):
        row=self.create()
        self.plan(self.start,self.start+timedelta(hours=2))
        self.sync()
        self.assertEqual(bookings.get_booking(self.c,101,row['booking_id'])['status'],'pending')
        parent=self.records('series')[0]
        self.assertIn(row['booking_id'],parent['request_data']['conflicts'][0]['booking_ids'])
        body=next(iter(self.remote.events.values()))
        self.assertTrue(any(s.startswith('EXDATE:') for s in body['recurrence']))
        self.assertTrue(any(s['event']=='plan_conflict' for s in parent['admin_notifications'].values()))
        self.c.commit()
        bot=type('Bot',(),{'send_message':AsyncMock()})()
        with patch('app.notifications.connect_database',side_effect=self.connection):
            asyncio.run(notifications.deliver_pending(bot,self.settings))
        self.assertTrue(any('books_plans' in call.kwargs['text'] for call in bot.send_message.call_args_list))

    def test_excluded_date_and_removed_range_update_series_and_capacity(self):
        self.plan(self.start,self.start+timedelta(hours=2))
        self.sync()
        parent=self.records('series')[0]
        event_id=parent['calendar_sync']['booking_event_id']
        self.rules.books_plans.excluded_dates=[self.start.astimezone(SEOUL).date()]
        self.sync()
        body=self.remote.events[('test-calendar',event_id)]
        self.assertTrue(any(s.startswith('EXDATE:') for s in body['recurrence']))
        self.assertTrue(bookings.is_available(self.c,102,self.start,self.end))
        self.rules.books_plans.weekly={d:[] for d in DAYS}
        self.sync()
        parent=self.records('series')[0]
        self.assertFalse(parent['request_data']['active'])
        now=self.c.execute('SELECT clock_timestamp()').fetchone()[0]
        self.assertLessEqual(datetime.fromisoformat(parent['request_data']['until']),now)
        self.assertTrue(all(r['status']=='cancelled' for r in self.records('occurrence') if r['starts_at']>now))

    def test_calendar_timeout_retries_same_parent_id(self):
        self.plan(self.start,self.start+timedelta(hours=2))
        self.remote.fail_after_write=True
        self.sync()
        parent=self.records('series')[0]
        self.assertEqual(parent['calendar_sync']['last_error'],'TimeoutError')
        self.c.execute("UPDATE bookings SET calendar_sync=calendar_sync-'retry_at' WHERE booking_id=%s",(parent['booking_id'],))
        self.sync()
        self.assertEqual(len(self.remote.events),1)
        self.assertEqual(self.records('series')[0]['calendar_sync']['status'],'synced')

    def test_capacity_is_updated_even_when_google_is_disabled(self):
        self.plan(self.start,self.start+timedelta(hours=2))
        self.rules.calendar.enabled=False
        self.sync()
        self.assertTrue(self.records('occurrence'))
        self.assertFalse(self.remote.calls)

    def test_removing_schedule_keeps_running_occurrence_and_calendar_history(self):
        self.plan(self.start,self.start+timedelta(hours=2))
        books_plans.reconcile(self.c,self.rules)
        future_now=self.start+timedelta(hours=1)
        self.rules.books_plans.enabled=False
        books_plans.reconcile(self.c,self.rules,now=future_now)
        running=[r for r in self.records('occurrence') if r['starts_at']==self.start][0]
        self.assertEqual(running['status'],'confirmed')
        parent=self.records('series')[0]
        body=books_plans.event_body(parent,self.rules)
        self.assertIsNotNone(body)
        cutoff=datetime.strptime(body['recurrence'][0].split('UNTIL=')[1],'%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
        self.assertEqual(cutoff,future_now)

    def test_excluding_date_does_not_delete_already_running_first_instance(self):
        self.plan(self.start,self.start+timedelta(hours=2))
        books_plans.reconcile(self.c,self.rules)
        parent=self.records('series')[0]
        anchor=parent['starts_at'].astimezone(SEOUL)
        self.rules.books_plans.excluded_dates=[anchor.date()]
        books_plans.reconcile(self.c,self.rules,now=anchor+timedelta(hours=1))
        updated=self.records('series')[0]
        self.assertNotIn(anchor.isoformat(),updated['request_data']['excluded_starts'])
        running=[r for r in self.records('occurrence') if r['starts_at']==anchor][0]
        self.assertEqual(running['status'],'confirmed')

    def test_daily_extension_keeps_same_series_and_does_not_rewrite_existing_occurrences(self):
        self.plan(self.start,self.start+timedelta(hours=2))
        books_plans.reconcile(self.c,self.rules)
        before=self.records('series')[0]
        versions={r['booking_id']:r['version'] for r in self.records('occurrence')}
        now=self.c.execute('SELECT clock_timestamp()').fetchone()[0]
        books_plans.reconcile(self.c,self.rules,now=now+timedelta(days=1))
        after=self.records('series')[0]
        self.assertEqual(after['calendar_sync']['booking_event_id'],before['calendar_sync']['booking_event_id'])
        self.assertEqual(datetime.fromisoformat(after['request_data']['until'])-datetime.fromisoformat(before['request_data']['until']),timedelta(days=1))
        self.assertTrue(all(r['version']==versions[r['booking_id']] for r in self.records('occurrence') if r['booking_id'] in versions))

    def test_expired_pending_booking_does_not_prevent_plan_reconciliation(self):
        row=self.create()
        self.c.execute("UPDATE bookings SET expires_at=clock_timestamp()-interval '1 second' WHERE booking_id=%s",(row['booking_id'],))
        self.plan(self.start,self.start+timedelta(hours=2))
        books_plans.reconcile(self.c,self.rules)
        self.assertEqual(bookings.get_booking(self.c,101,row['booking_id'])['status'],'expired')
        self.assertTrue(any(r['starts_at']==self.start for r in self.records('occurrence')))

    def test_changed_schedule_does_not_overlap_preserved_running_occurrence(self):
        self.plan(self.start,self.start+timedelta(hours=2))
        books_plans.reconcile(self.c,self.rules)
        self.rules.books_plans.weekly={d:[] for d in DAYS}
        self.plan(self.start+timedelta(hours=1),self.start+timedelta(hours=3))
        books_plans.reconcile(self.c,self.rules,now=self.start+timedelta(hours=1))
        active=[r for r in self.records('occurrence') if r['status']=='confirmed' and r['starts_at']<self.end and r['ends_at']>self.start]
        self.assertEqual(len(active),1)
        self.assertEqual(active[0]['starts_at'],self.start)

    def test_changing_range_replaces_future_occurrences_and_reenabling_creates_new_series(self):
        self.plan(self.start,self.start+timedelta(hours=2))
        self.sync()
        first=self.records('series')[0]
        self.rules.books_plans.weekly={d:[] for d in DAYS}
        self.plan(self.start+timedelta(hours=3),self.start+timedelta(hours=4))
        self.sync()
        self.assertEqual(sum(r['request_data']['active'] for r in self.records('series')),1)
        self.assertFalse(next(r for r in self.records('series') if r['booking_id']==first['booking_id'])['request_data']['active'])
        self.assertTrue(bookings.is_available(self.c,102,self.start-timedelta(hours=3),self.start+timedelta(hours=2)))
        self.rules.books_plans.enabled=False
        self.sync()
        self.rules.books_plans.enabled=True
        self.sync()
        self.assertEqual(sum(r['request_data']['active'] for r in self.records('series')),1)
        self.assertEqual(len(self.records('series')),3)
