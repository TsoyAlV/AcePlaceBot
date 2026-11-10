"""Contact and event answers captured separately for every booking."""

import re


class DetailsError(ValueError):
    pass


def phone(value):
    if not isinstance(value,str):
        raise DetailsError('Укажи свой телефон для связи цифрами. / 연락 가능한 본인의 전화번호를 숫자로 입력하세요.')
    result=re.sub(r'[ ()\-.]','',value.strip())
    if not re.fullmatch(r'\+?[0-9]{7,15}',result):
        raise DetailsError('Укажи свой телефон для связи: от 7 до 15 цифр. Для международного формата можно добавить «+» в начале. / 연락 가능한 본인의 전화번호를 7~15자리 숫자로 입력하세요. 국제번호는 맨 앞에 «+»를 붙일 수 있습니다.')
    return result


def guests(value):
    if isinstance(value,bool) or not isinstance(value,(str,int)) or not re.fullmatch(r'[0-9]{1,5}',str(value).strip()):
        raise DetailsError('Количество гостей — целое число от 1 до 10000. / 인원수는 1~10000 사이의 정수로 입력하세요.')
    result=int(value)
    if not 1<=result<=10000:
        raise DetailsError('Количество гостей — целое число от 1 до 10000. / 인원수는 1~10000 사이의 정수로 입력하세요.')
    return result


def event(value):
    if not isinstance(value,str):
        raise DetailsError('Укажи мероприятие текстом. / 행사 내용을 입력하세요.')
    result=' '.join(value.split())
    if not 1<=len(result)<=200 or any(ord(c)<32 for c in result):
        raise DetailsError('Опиши мероприятие: от 1 до 200 символов. / 행사 내용을 1~200자로 입력하세요.')
    return result


def validate(details):
    if not isinstance(details,dict) or set(details)!={'contact_phone','guests_count','event_name'}:
        raise DetailsError('Ответь на три вопроса перед отправкой заявки. / 예약 신청 전에 세 가지 질문에 답해 주세요.')
    return dict(contact_phone=phone(details['contact_phone']),guests_count=guests(details['guests_count']),event_name=event(details['event_name']))


def text(row,lang='ru'):
    if not any(row.get(k) is not None for k in ('contact_phone','guests_count','event_name')):
        return ''
    labels=('Телефон','Количество гостей','Мероприятие') if lang!='ko' else ('연락처','인원수','행사')
    return '\n'.join(label+': '+str(row.get(key) or '—') for label,key in zip(labels,('contact_phone','guests_count','event_name')))


def payment_text(row,lang='ru'):
    def money(value):
        return f'{value:,.2f}'.rstrip('0').rstrip('.').replace(',',' ')+f" {row['unit']}"
    paid=row['deposit_paid_amount']+row['balance_paid_amount']
    labels=('Общая стоимость','Аванс получен','К доплате') if lang!='ko' else ('총금액','확인된 선입금','잔금')
    return '\n'.join(label+': '+money(value) for label,value in zip(labels,(row['amount'],row['deposit_paid_amount'],max(0,row['amount']-paid))))
