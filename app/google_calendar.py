"""Google adapter and local Desktop OAuth setup. No client calendars are accessed."""

import argparse
import json
from pathlib import Path
from uuid import uuid4

from app.config import PROJECT_ROOT,load_settings
from app.business_config import load_business_config

SCOPES=['https://www.googleapis.com/auth/calendar.events.owned']


class RecreateEvent(Exception):
    """Google retains a tombstone for an event; allocate another ID durably."""


def local_path(value):
    path=Path(value)
    return path if path.is_absolute() else PROJECT_ROOT/path


def calendar_json_dir():
    return local_path(load_business_config().calendar.json_dir)


def token_path(settings):
    if settings.google_token_file:
        return local_path(settings.google_token_file)
    return calendar_json_dir()/'google_token.json'


def find_client_file(client_file=None):
    directory=calendar_json_dir()
    if client_file:
        path=local_path(client_file)
        if not path.exists() and len(Path(client_file).parts)==1:
            path=directory/client_file
        if not path.is_file():
            raise ValueError(f'OAuth client JSON not found: {path}')
        return path
    for candidates in (directory.glob('*.json'),PROJECT_ROOT.glob('client_secret*.json')):
        files=[]
        for candidate in candidates:
            try:
                data=json.loads(candidate.read_text(encoding='utf-8-sig'))
            except (OSError,ValueError):
                continue
            if isinstance(data,dict) and 'installed' in data:
                files.append(candidate)
        if len(files)==1:
            return files[0]
        if len(files)>1:
            raise ValueError('Several Desktop JSON files found; specify --client-file')
    raise ValueError(f'Desktop OAuth JSON not found in {directory}. Download a Desktop app client from Google Auth Platform, then run auth.')


def save_token(path,credentials):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    temporary.write_text(credentials.to_json(),encoding='utf-8')
    temporary.replace(path)


def make_service(settings):
    import httplib2
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build
    path=token_path(settings)
    if not path.is_file():
        raise ValueError(f'Google authorization token not found: {path}\nRun: python -m app.google_calendar auth\nThe downloaded OAuth client JSON is not an authorization token.')
    credentials=Credentials.from_authorized_user_file(str(path),SCOPES)
    if not credentials.has_scopes(SCOPES):
        raise ValueError('Google authorization needs calendar.events.owned')
    if not credentials.valid:
        if not credentials.refresh_token:
            raise ValueError('Authorize Google again')
        request=Request()
        credentials.refresh(lambda *args,**kwargs:request(*args,**{**kwargs,'timeout':20}))
        save_token(path,credentials)
    return build('calendar','v3',http=AuthorizedHttp(credentials,http=httplib2.Http(timeout=20)),cache_discovery=False)


def _status(error):
    return getattr(getattr(error,'resp',None),'status',None)


class CalendarAdapter:
    def __init__(self,service):
        self.service=service

    def upsert(self,calendar_id,event_id,body):
        from googleapiclient.errors import HttpError
        events=self.service.events()
        try:
            events.update(calendarId=calendar_id,eventId=event_id,body=body,sendUpdates='none').execute(num_retries=0)
            return
        except HttpError as error:
            if _status(error)==410:
                raise RecreateEvent() from None
            if _status(error)!=404:
                raise
        try:
            events.insert(calendarId=calendar_id,body={**body,'id':event_id},sendUpdates='none').execute(num_retries=0)
        except HttpError as error:
            if _status(error)==410:
                raise RecreateEvent() from None
            if _status(error)!=409:
                raise
            # Response to a previous insert may have been lost. Reuse the same event.
            existing=events.get(calendarId=calendar_id,eventId=event_id).execute(num_retries=0)
            if existing.get('status')=='cancelled':
                raise RecreateEvent() from None
            events.update(calendarId=calendar_id,eventId=event_id,body=body,sendUpdates='none').execute(num_retries=0)

    def delete(self,calendar_id,event_id):
        from googleapiclient.errors import HttpError
        try:
            self.service.events().delete(calendarId=calendar_id,eventId=event_id,sendUpdates='none').execute(num_retries=0)
        except HttpError as error:
            if _status(error) not in (404,410):
                raise
            if _status(error)==404:
                # Do not confuse an inaccessible calendar with an absent event.
                self.service.events().list(calendarId=calendar_id,maxResults=1).execute(num_retries=0)


def authorize(settings,client_file):
    from google_auth_oauthlib.flow import InstalledAppFlow
    path=find_client_file(client_file)
    if 'installed' not in json.loads(path.read_text(encoding='utf-8-sig')):
        raise ValueError('This JSON is a Web client. Create a Desktop app client in Google Auth Platform and download its JSON.')
    flow=InstalledAppFlow.from_client_secrets_file(str(path),SCOPES)
    credentials=flow.run_local_server(host='localhost',port=0,timeout_seconds=180,
        access_type='offline',prompt='consent')
    save_token(token_path(settings),credentials)
    print('Google authorization saved. Tokens were not printed.')


def main():
    parser=argparse.ArgumentParser(description='Google Calendar local setup')
    sub=parser.add_subparsers(dest='command',required=True)
    auth=sub.add_parser('auth')
    auth.add_argument('--client-file')
    sub.add_parser('check')
    args=parser.parse_args()
    try:
        settings=load_settings()
        if args.command=='auth':
            authorize(settings,args.client_file or settings.google_client_file)
        else:
            if not settings.google_calendar_id:
                raise ValueError('Set GOOGLE_CALENDAR_ID in .env')
            service=make_service(settings)
            service.events().list(calendarId=settings.google_calendar_id,maxResults=1).execute(num_retries=0)
            colors=service.colors().get().execute(num_retries=0)
            print('Calendar readable. Event color IDs: '+', '.join(sorted(colors.get('event',{}))))
        return 0
    except ValueError as error:
        print(str(error))
        return 1
    except Exception as error:
        print(f'Google setup failed ({type(error).__name__}). Check credentials, Calendar ID and account permissions.')
        return 1


if __name__=='__main__':
    raise SystemExit(main())
