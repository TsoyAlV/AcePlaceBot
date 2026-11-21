"""Public venue information and price text calculated from the business config."""

from html import escape

ADDRESS_PARKING={
    'ru': 'Мы находимся в здании «The Luce» Wedding Hall по адресу: 경기 안산시 단원구 중앙대로 829, на этаже B1. При выходе из лифта вход направо.\n\nПарковка находится в этом же здании на этажах B2 и B3. Зарегистрировать автомобиль можно до 17:00, пока работает офис управления зданием (관리사무실). После регистрации первые 4 часа бесплатны, далее — 600 ₩ за каждые 30 минут.\n\nВ воскресенье парковка полностью бесплатная, но автомобиль также нужно зарегистрировать. Из-за свадеб свободных мест может быть меньше.',
    'ko': 'The Luce 웨딩홀 건물 지하 1층(B1)에 있습니다. 주소: 경기 안산시 단원구 중앙대로 829. 엘리베이터에서 내리신 후 오른쪽으로 오시면 됩니다.\n\n주차장은 같은 건물 지하 2층(B2)과 지하 3층(B3)에 있습니다. 차량 등록은 관리사무실 운영 시간인 오후 5시까지 가능합니다. 등록 후 최초 4시간은 무료이며, 이후 30분마다 600원이 부과됩니다.\n\n일요일은 주차가 종일 무료이지만 차량 등록은 필요합니다. 결혼식 행사로 인해 주차 공간이 부족할 수 있습니다.',
    'en': 'We are in The Luce Wedding Hall building at 경기 안산시 단원구 중앙대로 829, on level B1. Turn right when leaving the lift.\n\nParking is in the same building on levels B2 and B3. Register your vehicle by 5:00 PM while the building management office (관리사무실) is open. After registration, the first 4 hours are free; additional parking costs ₩600 per 30 minutes.\n\nParking is free all day on Sundays, but vehicle registration is still required. Spaces may be limited due to weddings.',
}


def address_text(config,lang):
    heading={'ru':'📍 Адрес и парковка','ko':'📍 오시는 길 및 주차','en':'📍 Address and parking'}[lang]
    return '<b>'+heading+'</b>\n\n'+escape(getattr(config.information.address_parking,lang))


def price_text(config,lang):
    p=config.pricing
    deposit=escape(f'{config.payment.deposit_amount:,}'.replace(',',' '))
    unit=escape(config.currency)
    hours=config.cancellation.refund_cutoff_hours
    refundable=config.cancellation.deposit_refundable_before_cutoff
    if lang=='ko':
        terms=(f'시작 {hours}시간 전까지 취소하면 예약금이 환불됩니다. 이후 취소 시 예약금은 환불되지 않습니다.' if refundable else
               '예약금은 선택한 시간을 확보하기 위한 금액으로, 취소 시 환불되지 않습니다.')
        deposit_text=f'💳 <b>예약금 — {deposit} {unit}</b>\n예약금은 총 대관 요금에 포함됩니다.\n'+terms
    elif lang=='en':
        terms=(f'The deposit is refundable if you cancel at least {hours} hours before the start. For later cancellations, it is non-refundable.' if refundable else
               'The deposit secures your chosen time and is non-refundable if you cancel.')
        deposit_text=f'💳 <b>Deposit — {deposit} {unit}</b>\nThe deposit is included in the total rental price.\n'+terms
    else:
        terms=(f'При отмене за {hours} ч и более до начала предоплата возвращается. При более поздней отмене предоплата не возвращается.' if refundable else
               'Предоплата закрепляет за вами выбранное время и при отмене брони не возвращается.')
        deposit_text=f'💳 <b>Предоплата — {deposit} {unit}</b>\nВходит в общую стоимость аренды.\n'+terms
    base,weekday,extra,weekend,weekend_extra=(escape(str(v)) for v in (p.base_hours,f'{p.weekday_base_amount:,}'.replace(',',' '),f'{p.weekday_extra_hour_amount:,}'.replace(',',' '),f'{p.weekend_holiday_base_amount:,}'.replace(',',' '),f'{p.weekend_holiday_extra_hour_amount:,}'.replace(',',' ')))
    if lang=='ko':
        return f'🕒 <b>운영 시간</b>\n매일 <b>24시간 연중무휴</b>로 운영합니다!\n📅 원하시는 날짜가 있다면 미리 예약해 주세요.\n\n💰 <b>대관 요금</b>\n📌 <b>평일</b>\n{base}시간 — <b>{weekday} ₩</b>\n추가 1시간 — <b>{extra} ₩</b>\n\n📌 <b>주말 및 공휴일</b>\n{base}시간 — <b>{weekend} ₩</b>\n추가 1시간 — <b>{weekend_extra} ₩</b>\n\n{deposit_text}'
    if lang=='en':
        return f'🕒 <b>OPENING HOURS</b>\nOur space is open <b>24/7 — every day, around the clock!</b>\n📅 We recommend booking in advance, especially for a specific date.\n\n💰 <b>RENTAL PRICES</b>\n📌 <b>Weekdays</b>\n{base} hours — <b>{weekday} ₩</b>\nEach additional hour — <b>{extra} ₩</b>\n\n📌 <b>Weekends and public holidays</b>\n{base} hours — <b>{weekend} ₩</b>\nEach additional hour — <b>{weekend_extra} ₩</b>\n\n{deposit_text}'
    return f'🕒 <b>ГРАФИК РАБОТЫ</b>\nНаше пространство открыто <b>24/7 — круглосуточно, без выходных!</b>\n📅 Рекомендуем бронировать зал заранее, особенно если планируете мероприятие на конкретную дату.\n\n💰 <b>СТОИМОСТЬ АРЕНДЫ</b>\n📌 <b>Будние дни</b>\n{base} часов — <b>{weekday} ₩</b>\nДополнительный час — <b>{extra} ₩</b>\n\n📌 <b>Выходные и праздничные дни</b>\n{base} часов — <b>{weekend} ₩</b>\nДополнительный час — <b>{weekend_extra} ₩</b>\n\n{deposit_text}'
