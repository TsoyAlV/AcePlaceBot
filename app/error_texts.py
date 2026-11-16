"""English versions of shared validation errors; business exceptions stay unchanged."""

import re

ENGLISH = {
    'Доступ запрещён': 'Access denied.',
    'Время занято.': 'This time is unavailable. Choose another interval; the original booking has been kept.',
    'Используй формат YYYY-MM-DD HH:MM': 'Use YYYY-MM-DD HH:MM. All times are in Seoul time.',
    'Начало должно быть в будущем': 'The start must be in the future.',
    'Запись изменилась.': 'This entry has changed. Open it again via /admin.',
    'Окончание должно быть позже начала': 'The end must be after the start.',
    'Проверь формат ввода.': 'Check the input format.',
    'Срок заявки истёк. Создай новую': 'This request has expired. Create a new one via /admin.',
    'Можно подтвердить только будущую': 'Only future requests awaiting approval can be confirmed.',
    'Перенос доступен только будущим': 'Only future pending or confirmed entries can be rescheduled.',
    'Запись не найдена.': 'Entry not found.',
    'Эту запись уже нельзя отменить.': 'This entry can no longer be cancelled.',
    'Стоимость должна быть': 'The price must be non-negative and less than 1000000000000.',
    'Укажи сумму с максимум': 'Enter an amount with at most two decimal places.',
    'Неверная сумма.': 'Invalid amount. Example: 100000 KRW.',
    'Клиент больше не найден.': 'Customer not found. Start again via /admin.',
    'Пример: 100000 KRW': 'Example: 100000 KRW.',
    'Клиент не найден:': 'Customer not found. They must send /start to the bot first.',
    'Черновик устарел.': 'This draft has expired. Open /admin again.',
    'Условия изменились. Открой /admin': 'The terms have changed. Start again via /admin.',
    'Отмена после начала аренды': 'Contact the administrator to cancel after the rental has started.',
    'Регулярные блокировки изменяются': 'Edit recurring time blocks in config.yaml.',
    'Нет ожидающего возврата.': 'There is no pending refund.',
    'Укажи свой телефон для связи цифрами.': 'Enter your contact phone number using digits.',
    'Укажи свой телефон для связи:': 'Enter your contact phone number: 7 to 15 digits. For international numbers, you may add «+» at the beginning.',
    'Количество гостей': 'The number of guests must be an integer from 1 to 10000.',
    'Укажи мероприятие текстом.': 'Describe your event in text.',
    'Опиши мероприятие:': 'Describe your event using 1 to 200 characters.',
    'Ответь на три вопроса': 'Answer all three questions before submitting your request.',
    'Отправь изображение или /cancel': 'Send an image, or use /cancel to return to the main menu.',
    'Не удалось выполнить действие. Попробуйте позже.': 'Unable to complete the action. Please try again later.',
    'Возврат в этой валюте': 'Contact the administrator to arrange a refund in this currency.',
    'Это время уже заняли.': 'This time has already been booked. Start again with /book.',
    'Отправьте свой контакт': 'Share your own contact using the button, or enter your phone number manually.',
    'Статус изменился.': 'The status has changed. Refresh «My bookings».',
    'Не удалось рассчитать праздничный тариф': 'Unable to calculate the holiday rate for this date. Contact the administrator.',
    'У тебя уже есть ожидающая заявка.': 'You already have a pending request. Open «My bookings».',
    'Проверь ввод. Если карточка': 'Check your input. If the card has expired or the terms have changed, start again with /book.',
    'Видео и кружочки не подходят.': 'Videos and video messages are not supported. Send a photo or PNG/JPEG/WebP file up to 10 MB. Your booking is still selected; you do not need to select it again.',
    'Изображение больше 10 МБ.': 'This image exceeds 10 MB. Send a smaller photo or file up to 10 MB.',
    'Сначала выбери бронь': 'First open your booking and choose «Send payment evidence».',
    'Это изображение уже прикреплено': 'This image is already attached to another booking. Send separate payment evidence for this booking or contact the administrator.',
    'Предоплата по этой брони уже отмечена.': 'The deposit for this booking is already recorded. No additional evidence is needed.',
    'Срок заявки истёк или она отменена.': 'This request has expired or been cancelled. The payment requires manual review; the time slot has not been restored.',
    'Скриншот уже на проверке или принят.': 'This payment image is already under review or accepted. Wait for the administrator’s decision.',
    'Не удалось выполнить действие. Проверь права': 'Unable to complete the action. Check your access, the current booking card and the image format.',
    'Сначала пользователь должен': 'The user must send /start to the bot first.',
    'Роль владельца изменить нельзя.': 'The owner’s role cannot be changed.',
    'Отключить владельца нельзя.': 'The owner cannot be disabled.',
    'Введи положительный числовой Telegram ID.': 'Enter a positive numeric Telegram ID.',
    'Только для владельца.': 'Only the owner can do this.',
    'Права изменились.': 'Permissions have changed. Open the card again via /admin.',
    'Администратор не найден.': 'Administrator not found.',
}


def english_error(text):
    if text.startswith('Минимум '):
        hours=re.search(r'\d+',text)
        if hours:
            return f'Minimum rental duration: {hours[0]} hours.'
    for prefix,translation in ENGLISH.items():
        if text.startswith(prefix):
            return translation
    if re.search(r'[А-Яа-яЁё가-힣]',text):
        return 'Unable to complete the action. Check your input or start again with /cancel.'
    return text
