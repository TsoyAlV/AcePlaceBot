"""Private staff-management screens available only to the active owner."""

from uuid import uuid4
from telegram import InlineKeyboardButton as Button,InlineKeyboardMarkup as Markup
from app import admin_ui,staff
from app import ui_messages as ui
from app.bookings import BookingError,AccessDenied,StaleBooking

tr=admin_ui.tr


async def call(update,context,operation):
    def checked(c,a,lang):
        staff.require_owner(c,a)
        return operation(c,a,lang)
    return await admin_ui.call(update,context,checked)


def card(row,lang):
    state=tr(lang,'Активен','활성') if row['active'] else tr(lang,'Отключён','비활성')
    notify=tr(lang,'Включены','켜짐') if row['notifications_enabled'] else tr(lang,'Выключены','꺼짐')
    return (f"{row['name']}\nTelegram ID: {row['telegram_user_id']}\n"
        +tr(lang,'Роль: ','역할: ')+str(row['role'] or '—')+'\n'+state+'\n'
        +tr(lang,'Уведомления: ','알림: ')+notify)


def controls(row,lang):
    tg=row['telegram_user_id']
    buttons=[]
    if row['role']!='owner':
        buttons.append([Button(role,callback_data=f'staff:ask:role:{role}:{tg}') for role in ('admin','operator')])
        buttons.append([Button(tr(lang,'Отключить доступ','접근 비활성화') if row['active'] else tr(lang,'Включить доступ','접근 활성화'),
            callback_data=f"staff:ask:active:{int(not row['active'])}:{tg}")])
    buttons.append([Button(tr(lang,'Выключить уведомления','알림 끄기') if row['notifications_enabled'] else tr(lang,'Включить уведомления','알림 켜기'),
        callback_data=f"staff:ask:notifications_enabled:{int(not row['notifications_enabled'])}:{tg}")])
    buttons.append([Button(tr(lang,'К списку администраторов','관리자 목록'),callback_data='staff:list:0')])
    return Markup(buttons)


async def review(update,context,row,field,value,lang):
    token=uuid4().hex[:12]
    context.user_data['staff_draft']=dict(step='review',token=token,expected=staff.snapshot(row),field=field,value=value)
    labels={'role':tr(lang,'Новая роль','새 역할'),'active':tr(lang,'Доступ','접근'),
        'notifications_enabled':tr(lang,'Уведомления','알림')}
    display=value if type(value) is not bool else (tr(lang,'Включить','활성화') if value else tr(lang,'Выключить','비활성화'))
    await ui.screen(update,context,card(row,lang)+f'\n\n{labels[field]}: {display}\n'
        +tr(lang,'Подтвердить изменение?','변경하시겠습니까?'),reply_markup=Markup([
            [Button(tr(lang,'Да, применить','적용'),callback_data='staff:apply:'+token)],
            [Button(tr(lang,'Назад к списку','목록으로'),callback_data='staff:list:0')]]))


async def callback(update,context):
    query=update.callback_query
    if not query or not query.data or not query.data.startswith('staff:'):
        return
    await query.answer()
    try:
        lang=await call(update,context,lambda c,a,l:l)
        for key in ('admin_draft','client_draft','receipt_upload'):
            context.user_data.pop(key,None)
        parts=query.data.split(':')
        command=parts[1]
        if command=='list':
            context.user_data.pop('staff_draft',None)
            page=int(parts[2])
            rows=await call(update,context,lambda c,a,l:staff.list_members(c,a,page))
            buttons=[[Button(f"{r['name'][:32]} · {r['role']} · "+(tr(lang,'активен','활성') if r['active'] else tr(lang,'отключён','비활성')),
                callback_data=f"staff:view:{r['telegram_user_id']}")] for r in rows[:8]]
            nav=[]
            if page:
                nav.append(Button('←',callback_data=f'staff:list:{page-1}'))
            if len(rows)>8:
                nav.append(Button('→',callback_data=f'staff:list:{page+1}'))
            if nav:
                buttons.append(nav)
            buttons.extend([[Button(tr(lang,'Добавить администратора','관리자 추가'),callback_data='staff:add')],
                [Button(tr(lang,'В меню администратора','관리자 메뉴'),callback_data='adm:menu')]])
            await ui.screen(update,context,tr(lang,'Администраторы','관리자'),reply_markup=Markup(buttons))
        elif command=='add':
            context.user_data['staff_draft']=dict(step='telegram_id')
            await ui.screen(update,context,tr(lang,'Введи числовой Telegram ID. Пользователь должен сначала отправить боту /start. /cancel — прервать.',
                'Telegram 숫자 ID를 입력하세요. 사용자가 먼저 봇에 /start 를 보내야 합니다. /cancel 로 중단.'))
        elif command in ('view','ask'):
            context.user_data.pop('staff_draft',None)
            tg=int(parts[-1])
            row=await call(update,context,lambda c,a,l:staff.get_member(c,a,tg))
            if command=='view':
                if row['role'] is None:
                    raise BookingError('Администратор не найден. / 관리자를 찾을 수 없습니다.')
                await ui.screen(update,context,card(row,lang),reply_markup=controls(row,lang))
            else:
                field,value=parts[2:4]
                if field=='role':
                    if value not in ('admin','operator'):
                        raise BookingError('Invalid role')
                elif field in ('active','notifications_enabled') and value in ('0','1'):
                    value=value=='1'
                else:
                    raise BookingError('Invalid staff update')
                await review(update,context,row,field,value,lang)
        elif command=='apply':
            data=context.user_data.get('staff_draft')
            if not data or data.get('token')!=parts[2] or data.get('step')!='review':
                raise BookingError('Черновик устарел. Открой /admin. / 입력이 만료되었습니다. /admin 을 여세요.')
            row=await call(update,context,lambda c,a,l:staff.update_member(c,a,data['expected']['telegram_user_id'],
                expected=data['expected'],field=data['field'],value=data['value']))
            context.user_data.pop('staff_draft',None)
            await ui.result(update,context,tr(lang,'Сохранено.','저장되었습니다.')+'\n'+card(row,lang),reply_markup=controls(row,lang))
    except (BookingError,ValueError,IndexError) as error:
        if isinstance(error,AccessDenied):
            context.user_data.pop('staff_draft',None)
            await ui.error(update,context,'Только для владельца. / 소유자만 이용할 수 있습니다.')
        elif isinstance(error,StaleBooking):
            context.user_data.pop('staff_draft',None)
            await ui.error(update,context,'Права изменились. Открой карточку заново через /admin. / 권한이 변경되었습니다. /admin 에서 다시 여세요.')
        else:
            await admin_ui.show_error(update,error,context)


async def text_input(update,context):
    data=context.user_data.get('staff_draft')
    if not data or data.get('step')!='telegram_id':
        return False
    try:
        lang=await call(update,context,lambda c,a,l:l)
        text=update.effective_message.text.strip()
        if not text.isascii() or not text.isdigit() or not 0<int(text)<2**63:
            raise BookingError('Введи положительный числовой Telegram ID. / 올바른 Telegram 숫자 ID를 입력하세요.')
        row=await call(update,context,lambda c,a,l:staff.get_member(c,a,int(text)))
        context.user_data.pop('staff_draft',None)
        if row['role'] is not None:
            await ui.screen(update,context,card(row,lang),reply_markup=controls(row,lang))
        else:
            await ui.screen(update,context,card(row,lang)+'\n'+tr(lang,'Выбери роль:','역할 선택:'),reply_markup=Markup([
                [Button(role,callback_data=f"staff:ask:role:{role}:{row['telegram_user_id']}") for role in ('admin','operator')],
                [Button(tr(lang,'К списку','목록으로'),callback_data='staff:list:0')]]))
    except (BookingError,ValueError) as error:
        if isinstance(error,AccessDenied):
            context.user_data.pop('staff_draft',None)
        await admin_ui.show_error(update,error,context)
    return True
