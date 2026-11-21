"""Booking-bound image upload and manual bank verification in private chats."""

from telegram import InlineKeyboardButton as Button,InlineKeyboardMarkup as Markup
from datetime import datetime,timezone

from app import client_ui,admin_ui
from app import ui_messages as ui
from app.bookings import BookingError,get_booking
from app.payments import submit_receipt,review_receipt


def upload_prompt(row,lang):
    return admin_ui.tr(lang,
        f"После перевода отправь подтверждение оплаты прямо сюда для брони #{row['booking_id']} — нажимать отдельную кнопку не нужно. Подойдёт фото или PNG/JPEG/WebP-файл до 10 МБ. /cancel — выйти из загрузки. Отправка файла не подтверждает поступление денег и бронь автоматически.",
        f"송금 후 예약 #{row['booking_id']}의 결제 증빙을 이 채팅에 바로 보내세요. 별도 버튼을 누를 필요가 없습니다. 사진 또는 10MB 이하 PNG/JPEG/WebP 파일을 보낼 수 있습니다. /cancel 로 업로드를 중단하세요. 파일 전송만으로 입금과 예약이 확정되지는 않습니다.",
        f"After transferring the deposit, send payment evidence here for booking #{row['booking_id']} — no extra button is needed. Send a photo or PNG/JPEG/WebP file up to 10 MB. /cancel to exit upload mode. Sending a file does not automatically confirm payment or your booking.")


def expect_receipt_after_booking(context,row):
    """Only arm the exact booking just created/replayed, never guess another one."""
    now=datetime.now(timezone.utc)
    receipt=row.get('payment_receipt') or {}
    active=(row['status']=='pending' and row.get('expires_at') and row['expires_at']>now) or (row['status']=='confirmed' and row['ends_at']>now)
    ready=(row['type']=='booking' and active and row['deposit_paid_amount']==0 and receipt.get('review_status') in (None,'rejected'))
    if ready:
        context.user_data['receipt_upload']=row['booking_id']
    elif context.user_data.get('receipt_upload')==row['booking_id']:
        context.user_data.pop('receipt_upload',None)
    return bool(ready)


def admin_controls(row,lang):
    receipt=row.get('payment_receipt')
    if not receipt:
        return []
    bid,version=row['booking_id'],row['version']
    buttons=[[Button(admin_ui.tr(lang,'Посмотреть скриншот','입금 이미지 보기', 'View payment image'),callback_data=f'pay:view:{bid}')]]
    if receipt['review_status']=='pending':
        if row['status']=='confirmed' or (row['status']=='pending' and row['expires_at']>datetime.now(timezone.utc)):
            buttons.append([Button(admin_ui.tr(lang,'Предоплата получена','선입금 확인', 'Deposit received'),callback_data=f'pay:ask:accept:{bid}:{version}')])
        buttons.append([Button(admin_ui.tr(lang,'Запросить другой скриншот','다른 이미지 요청', 'Request another payment image'),callback_data=f'pay:ask:reject:{bid}:{version}')])
    return buttons


def receipt_caption(row,lang):
    receipt=row['payment_receipt']
    text=admin_ui.tr(lang,'Скриншот НЕ подтверждает поступление денег. Проверь банк.\n','이미지는 입금을 보장하지 않습니다. 은행에서 확인하세요.\n', 'A payment image does NOT prove receipt of money. Check your bank account.\n')
    text+=admin_ui.tr(lang,'Ожидаемая предоплата: ','예상 선입금: ', 'Expected deposit: ')+f"{receipt['expected_amount']} {receipt['unit']}\n"
    if receipt.get('late') or row['status'] in ('expired','cancelled') or (row['status']=='pending' and row['expires_at']<=datetime.now(timezone.utc)):
        text+=admin_ui.tr(lang,'ВНИМАНИЕ: поздняя оплата / заявка не действует. Не восстанавливай бронь без проверки доступности.\n','주의: 지연 결제 또는 만료/취소 신청입니다. 예약 가능 여부를 다시 확인해야 합니다.\n', 'WARNING: late payment / inactive request. Do not restore the booking without checking availability.\n')
    text+=admin_ui.card(row,lang)
    return text


async def send_receipt(bot,chat_id,row,lang):
    receipt=row['payment_receipt']
    buttons=admin_controls(row,lang)
    buttons.append([Button(admin_ui.tr(lang,'Открыть бронь','예약 열기', 'Open booking'),callback_data=f"adm:view:{row['booking_id']}")])
    kwargs=dict(chat_id=chat_id,caption=receipt_caption(row,lang),reply_markup=Markup(buttons))
    if receipt['media_type']=='photo':
        await bot.send_photo(photo=receipt['file_id'],**kwargs)
    else:
        await bot.send_document(document=receipt['file_id'],**kwargs)


async def callback(update,context):
    query=update.callback_query
    if not query or not query.data or not query.data.startswith('pay:'):
        return
    await query.answer()
    try:
        parts=query.data.split(':')
        action=parts[1]
        if action=='upload':
            row,lang=await client_ui.call(update,context,lambda c,a,u:(client_ui.own_booking(c,a,int(parts[2])),u.language))
            if row['status'] not in ('pending','confirmed','expired','cancelled'):
                raise BookingError('Receipt is not allowed')
            context.user_data.pop('admin_draft',None)
            context.user_data.pop('client_draft',None)
            context.user_data.pop('staff_draft',None)
            context.user_data.pop('client_cancel',None)
            context.user_data['receipt_upload']=row['booking_id']
            await ui.screen(update,context,upload_prompt(row,lang))
        elif action=='view':
            row,lang=await admin_ui.call(update,context,lambda c,a,l:(get_booking(c,a,int(parts[2])),l))
            if not row.get('payment_receipt'):
                raise BookingError('Receipt not found')
            await send_receipt(context.bot,update.effective_chat.id,row,lang)
        elif action in ('ask','yes'):
            choice,bid,version=parts[2],int(parts[3]),int(parts[4])
            if choice not in ('accept','reject'):
                raise BookingError('Invalid action')
            row,lang=await admin_ui.call(update,context,lambda c,a,l:(get_booking(c,a,bid),l))
            if action=='ask':
                if row['version']!=version:
                    raise BookingError('Booking changed')
                text=admin_ui.tr(lang,
                    'Подтверждаешь фактическое поступление указанной предоплаты в банке? Бронь будет подтверждаться отдельно.' if choice=='accept' else 'Запросить другой скриншот? Сумма оплаты не изменится.',
                    '은행에서 실제 선입금을 확인했습니까? 예약 승인은 별도입니다.' if choice=='accept' else '다른 이미지를 요청하시겠습니까? 결제 금액은 변경되지 않습니다.', 'Confirm that the deposit has actually reached your bank account? Booking approval is separate.' if choice=='accept' else 'Request another payment image? The recorded payment amount will not change.')
                await ui.screen(update,context,text,reply_markup=Markup([[Button(admin_ui.tr(lang,'Да','네', 'Yes'),callback_data=f'pay:yes:{choice}:{bid}:{version}')]]))
                return
            row=await admin_ui.call(update,context,lambda c,a,l:review_receipt(c,a,bid,expected_version=version,accept=choice=='accept'))
            from app.notifications import wake_delivery
            wake_delivery(context)
            await ui.result(update,context,admin_ui.card(row,lang),reply_markup=admin_ui.controls(row,lang),key=f"payment:{row['booking_id']}:{row['version']}")
    except (BookingError,ValueError,IndexError) as error:
        await error_message(update,error,context)


async def unsupported_input(update,context):
    if context.user_data.get('receipt_upload'):
        await error_message(update,BookingError('Send an image'),context)


async def media_input(update,context):
    bid=context.user_data.get('receipt_upload')
    if not bid:
        await ui.error(update,context,'Сначала выбери бронь → «Отправить подтверждение оплаты». / 먼저 내 예약에서 결제 증빙 보내기를 선택하세요.')
        return
    message=update.effective_message
    try:
        photo=message.photo[-1] if message.photo else None
        document=message.document
        media=photo or document
        if media is None or (not photo and document.mime_type not in ('image/png','image/jpeg','image/webp')):
            raise BookingError('Send an image')
        if media.file_size is not None and media.file_size>10*1024*1024:
            raise BookingError('Image too large')
        row,lang=await client_ui.call(update,context,lambda c,a,u:(submit_receipt(c,a,bid,
            file_id=media.file_id,file_unique_id=media.file_unique_id,media_type='photo' if photo else 'document'),u.language))
        context.user_data.pop('receipt_upload',None)
        from app.notifications import wake_delivery
        wake_delivery(context)
        await ui.result(update,context,admin_ui.tr(lang,'Скриншот отправлен на проверку. Оплата и бронь пока не подтверждены автоматически.','이미지를 검토 요청했습니다. 결제와 예약은 자동 확정되지 않습니다.', 'Payment image sent for review. Payment and booking are not confirmed automatically.'),reply_markup=client_ui.controls(row,lang))
    except (BookingError,ValueError) as error:
        await error_message(update,error,context)


async def error_message(update,error,context=None):
    text=str(error)
    if text=='Send an image':
        message='Видео и кружочки не подходят. Отправь фото или PNG/JPEG/WebP-файл до 10 МБ. Бронь выбрана, повторно нажимать кнопку не нужно. / 동영상과 동영상 메시지는 지원하지 않습니다. 10MB 이하 사진 또는 PNG/JPEG/WebP 파일을 보내세요. 예약을 다시 선택할 필요는 없습니다.'
    elif text=='Image too large':
        message='Изображение больше 10 МБ. Отправь уменьшенное фото или файл до 10 МБ. / 이미지가 10MB를 초과합니다. 크기를 줄여 다시 보내세요.'
    elif 'already linked to another booking' in text:
        message='Это изображение уже прикреплено к другой брони. Отправь отдельное подтверждение оплаты для выбранной брони или свяжись с администратором. / 다른 예약에 이미 첨부된 이미지입니다. 이 예약의 별도 결제 증빙을 보내거나 관리자에게 문의하세요.'
    elif 'Deposit is already recorded' in text:
        message='Предоплата по этой брони уже отмечена. Дополнительное подтверждение не требуется. / 이 예약의 선입금이 이미 기록되어 추가 증빙이 필요하지 않습니다.'
    elif 'Late payment' in text:
        message='Срок заявки истёк или она отменена. Требуется ручное решение по оплате; время не восстановлено. / 만료 또는 취소된 신청입니다. 결제는 수동 확인이 필요하며 시간은 복구되지 않았습니다.'
    elif 'awaiting review or accepted' in text:
        message='Скриншот уже на проверке или принят. Дождись решения администратора. / 이미 검토 중이거나 승인되었습니다.'
    else:
        message='Не удалось выполнить действие. Проверь права, актуальность карточки и формат изображения. / 작업을 수행하지 못했습니다. 권한, 최신 정보와 이미지 형식을 확인하세요.'
    await ui.error(update,context,message)
