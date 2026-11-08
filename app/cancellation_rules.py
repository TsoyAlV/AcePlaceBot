"""Cancellation terms and refund calculation; bank transfers remain manual."""

from datetime import timedelta
from decimal import Decimal
from app.business_config import load_business_config


def current_policy(config=None):
    config=config or load_business_config()
    return dict(refund_cutoff_hours=config.cancellation.refund_cutoff_hours,
        deposit_amount=str(config.payment.deposit_amount),unit=config.currency)


def policy_for(row):
    return (row.get('cancellation') or {}).get('policy') or current_policy()


def calculate(row,now):
    from app.bookings import BookingError
    policy=policy_for(row)
    if row['unit']!=policy['unit']:
        raise BookingError('Возврат в этой валюте согласуйте с администратором. / 해당 통화의 환불은 관리자에게 문의하세요.')
    cutoff=row['starts_at']-timedelta(hours=policy['refund_cutoff_hours'])
    early=now<=cutoff
    paid=row['deposit_paid_amount']+row['balance_paid_amount']
    withheld=Decimal('0') if early else min(row['deposit_paid_amount'],Decimal(policy['deposit_amount']))
    return dict(policy=policy,deposit_refundable=early,paid_amount=str(paid),withheld_amount=str(withheld),
        refund_amount=str(paid-withheld),unit=row['unit'],refund_deadline=cutoff.isoformat())


def policy_text(policy,lang):
    hours=policy['refund_cutoff_hours']
    amount=policy['deposit_amount']
    unit=policy['unit']
    if lang=='ko':
        return (f'취소 규정: 시작 {hours}시간 전까지 취소하면 확인된 결제 금액을 환불합니다. '
            f'{hours}시간 미만 남은 경우 수령한 선입금 중 최대 {amount} {unit}은 환불되지 않으며 나머지는 환불 대상입니다. '
            '환불은 관리자가 수동으로 처리합니다.')
    return (f'Условия отмены: за {hours} ч и более до начала возвращается вся подтверждённая оплата. '
        f'Менее чем за {hours} ч удерживается полученная предоплата — до {amount} {unit}; остаток оплаты подлежит возврату. '
        'Возврат выполняет администратор вручную.')


def quote_text(quote,lang):
    if lang=='ko':
        return (f"확인된 결제: {quote['paid_amount']} {quote['unit']}\n"
            f"공제 선입금: {quote['withheld_amount']} {quote['unit']}\n"
            f"수동 환불 대상: {quote['refund_amount']} {quote['unit']}")
    return (f"Подтверждённая оплата: {quote['paid_amount']} {quote['unit']}\n"
        f"Удержание предоплаты: {quote['withheld_amount']} {quote['unit']}\n"
        f"К возврату вручную: {quote['refund_amount']} {quote['unit']}")


def cancellation_text(row,lang):
    data=row.get('cancellation') or {}
    quote=data.get('quote')
    if not quote:
        return ''
    text=quote_text(quote,lang)
    labels={'pending':('Ожидается ручной возврат.','관리자의 수동 환불을 기다리는 중입니다.'),
        'completed':('Администратор подтвердил выполнение возврата.','관리자가 환불 완료를 확인했습니다.'),
        'not_due':('По учтённым платежам возврат не требуется.','확인된 결제 내역 기준 환불 대상 금액이 없습니다.')}
    text+='\n'+labels[data['refund_status']][lang=='ko']
    receipt=row.get('payment_receipt') or {}
    if receipt.get('review_status')=='pending':
        text+='\n'+('결제 증빙이 아직 검토 중입니다. 미확인 송금은 관리자에게 문의하세요.' if lang=='ko' else
            'Скриншот ещё не проверен. Неучтённый перевод необходимо согласовать с администратором.')
    return text
