import unittest
from unittest.mock import MagicMock,patch
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import httplib2
from googleapiclient.errors import HttpError

from app.google_calendar import CalendarAdapter,RecreateEvent
from app.google_calendar import find_client_file,token_path,make_service
from app.calendar_errors import error_reason,error_hint
from google.auth.exceptions import RefreshError


def http_error(code):
    return HttpError(httplib2.Response({'status':str(code)}),b'{}')


class CalendarFileTests(unittest.TestCase):
    def setUp(self):
        directory=TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root=Path(directory.name)
        self.folder=self.root/'secrets'/'calendars'
        self.folder.mkdir(parents=True)
        for target,value in (
            ('app.google_calendar.PROJECT_ROOT',self.root),
            ('app.google_calendar.load_business_config',None),
        ):
            patcher=patch(target,value) if value is not None else patch(target,return_value=SimpleNamespace(calendar=SimpleNamespace(json_dir='secrets/calendars')))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_discovers_desktop_json_with_arbitrary_filename_and_ignores_token(self):
        client=self.folder/'ace-place.json'
        client.write_text('{"installed": {}}',encoding='utf-8')
        (self.folder/'google_token.json').write_text('{"token": "fake"}',encoding='utf-8')
        self.assertEqual(find_client_file(),client)

    def test_multiple_clients_require_explicit_choice(self):
        for name in ('one.json','two.json'):
            (self.folder/name).write_text('{"installed": {}}',encoding='utf-8')
        with self.assertRaises(ValueError):
            find_client_file()
        self.assertEqual(find_client_file('two.json'),self.folder/'two.json')

    def test_root_client_still_supported(self):
        client=self.root/'client_secret_old.json'
        client.write_text('{"installed": {}}',encoding='utf-8')
        self.assertEqual(find_client_file(),client)

    def test_token_uses_directory_unless_environment_overrides_it(self):
        self.assertEqual(token_path(SimpleNamespace(google_token_file='')),self.folder/'google_token.json')
        self.assertEqual(token_path(SimpleNamespace(google_token_file='other.json')),self.root/'other.json')

    def test_missing_token_explains_authorization_step(self):
        with self.assertRaisesRegex(ValueError,'app.google_calendar auth'):
            make_service(SimpleNamespace(google_token_file=''))

    def test_explicit_missing_client_reports_its_path(self):
        with self.assertRaisesRegex(ValueError,'missing.json'):
            find_client_file('missing.json')

    def test_forced_check_validates_refresh_even_with_valid_access_token(self):
        (self.folder/'google_token.json').write_text('{}',encoding='utf-8')
        credentials=MagicMock(valid=True,refresh_token='fake')
        credentials.refresh.side_effect=RefreshError('private response',{'error':'invalid_grant'})
        with patch('google.oauth2.credentials.Credentials.from_authorized_user_file',return_value=credentials), \
             patch('app.google_calendar.save_token') as save:
            with self.assertRaises(RefreshError):
                make_service(SimpleNamespace(google_token_file=''),force_refresh=True)
        credentials.refresh.assert_called_once()
        save.assert_not_called()

    def test_refresh_diagnostics_never_expose_response_or_credentials(self):
        for code,expected in (('invalid_grant','RefreshError:invalid_grant'),
                              ('invalid_client','RefreshError:invalid_client'),
                              ('secret-injected-code','RefreshError')):
            error=RefreshError('private-token-url',{'error':code,'error_description':'secret'})
            reason=error_reason(error)
            self.assertEqual(reason,expected)
            for lang in ('ru','ko','en'):
                hint=error_hint(reason,lang)
                self.assertTrue(hint)
                self.assertNotIn('secret',hint)
                self.assertNotIn('private-token',hint)

    def test_check_cli_reports_auth_failure_with_safe_reason(self):
        from app.google_calendar import main
        from contextlib import redirect_stdout
        from io import StringIO
        output=StringIO()
        with patch('sys.argv',['google_calendar','check','--refresh']), \
             patch('app.google_calendar.load_settings',return_value=SimpleNamespace(google_calendar_id='calendar')), \
             patch('app.google_calendar.make_service',side_effect=RefreshError('private-token-url',{'error':'invalid_grant'})), \
             redirect_stdout(output):
            self.assertEqual(main(),1)
        self.assertIn('RefreshError:invalid_grant',output.getvalue())
        self.assertIn('Authorize',output.getvalue())
        self.assertNotIn('private-token',output.getvalue())


class GoogleAdapterTests(unittest.TestCase):
    def setUp(self):
        self.service=MagicMock()
        self.events=self.service.events.return_value
        self.adapter=CalendarAdapter(self.service)

    def test_missing_event_is_inserted_with_known_id(self):
        self.events.update.return_value.execute.side_effect=http_error(404)
        self.adapter.upsert('calendar','event123',{'summary':'Test'})
        self.assertEqual(self.events.insert.call_args.kwargs['body']['id'],'event123')

    def test_existing_event_is_updated_without_inserting(self):
        self.adapter.upsert('calendar','event123',{'summary':'Test'})
        self.events.insert.assert_not_called()

    def test_lost_insert_response_conflict_reuses_existing_event(self):
        self.events.update.return_value.execute.side_effect=[http_error(404),{}]
        self.events.insert.return_value.execute.side_effect=http_error(409)
        self.events.get.return_value.execute.return_value={'status':'confirmed'}
        self.adapter.upsert('calendar','event123',{'summary':'Test'})
        self.assertEqual(self.events.update.call_count,2)

    def test_tombstone_requires_new_durable_id(self):
        self.events.update.return_value.execute.side_effect=http_error(410)
        with self.assertRaises(RecreateEvent):
            self.adapter.upsert('calendar','event123',{})

    def test_delete_already_absent_is_success(self):
        for code in (404,410):
            self.events.delete.return_value.execute.side_effect=http_error(code)
            self.adapter.delete('calendar','event123')

    def test_permission_failure_is_not_treated_as_missing_event(self):
        self.events.update.return_value.execute.side_effect=http_error(403)
        with self.assertRaises(HttpError):
            self.adapter.upsert('calendar','event123',{})
        self.events.insert.assert_not_called()

    def test_inaccessible_calendar_does_not_report_successful_deletion(self):
        self.events.delete.return_value.execute.side_effect=http_error(404)
        self.events.list.return_value.execute.side_effect=http_error(404)
        with self.assertRaises(HttpError):
            self.adapter.delete('calendar','event123')
