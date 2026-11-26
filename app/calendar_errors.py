"""Safe Google diagnostics: only allowlisted codes, never OAuth response text."""

from google.auth.exceptions import RefreshError


def error_reason(error):
    if isinstance(error,RefreshError):
        for item in error.args:
            if isinstance(item,dict):
                code=item.get('error')
                if code in ('invalid_grant','invalid_client','unauthorized_client','invalid_scope','access_denied'):
                    return 'RefreshError:'+code
        return 'RefreshError:temporary' if getattr(error,'retryable',False) else 'RefreshError'
    status=getattr(getattr(error,'resp',None),'status',None)
    return type(error).__name__+(f':{status}' if isinstance(status,int) else '')


def error_hint(reason,lang='ru'):
    if reason=='RefreshError:temporary':
        texts=('Временный сбой обновления доступа Google. Повторная попытка сохранена в очереди; проверьте связь с Google.',
               'Google 접근 권한 갱신 중 일시적인 오류입니다. 재시도 작업은 보존되었습니다. Google 연결을 확인하세요.',
               'Temporary failure refreshing Google access. A retry is queued; check connectivity to Google.')
    elif reason in ('RefreshError:invalid_client','RefreshError:unauthorized_client'):
        texts=('Google отклонил OAuth-клиент. Проверьте Desktop JSON и повторите авторизацию календаря. Инструкция: DEPLOYMENT.md → восстановление Google Calendar.',
               'Google OAuth 클라이언트가 거부되었습니다. Desktop JSON을 확인하고 캘린더 인증을 다시 진행하세요. DEPLOYMENT.md의 복구 안내를 참고하세요.',
               'Google rejected the OAuth client. Check the Desktop JSON and authorize the calendar again. See Google Calendar recovery in DEPLOYMENT.md.')
    elif reason in ('RefreshError:invalid_grant','CalendarAuthorizationError'):
        texts=('Нужно заново подключить Google-аккаунт календаря. Очередь сохранена; брони и уборки в боте продолжают действовать. Инструкция: DEPLOYMENT.md → восстановление Google Calendar.',
               '캘린더 Google 계정을 다시 인증해야 합니다. 대기 작업은 보존되며 봇의 예약과 청소 시간은 유지됩니다. DEPLOYMENT.md의 복구 안내를 참고하세요.',
               'Authorize the calendar Google account again. Jobs are retained; bookings and cleaning remain active in the bot. See Google Calendar recovery in DEPLOYMENT.md.')
    elif reason.startswith('RefreshError'):
        texts=('Не удалось обновить доступ Google. Выполните проверку календаря с --refresh; повторение синхронизации само по себе может не помочь.',
               'Google 접근 권한을 갱신하지 못했습니다. --refresh 옵션으로 캘린더를 점검하세요. 동기화 재시도만으로 해결되지 않을 수 있습니다.',
               'Google access could not be refreshed. Run the calendar check with --refresh; retrying sync alone may not resolve it.')
    else:
        return ''
    return dict(zip(('ru','ko','en'),texts)).get(lang,texts[0])
