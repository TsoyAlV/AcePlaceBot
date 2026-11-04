COMMENT ON TABLE clients IS 'Пользователи бота Ace Place: клиенты и сотрудники. Внутренний user_id отделён от идентификаторов Telegram. Права сотрудников задаются в admins.';
COMMENT ON COLUMN clients.user_id IS 'Внутренний уникальный идентификатор пользователя; первичный ключ, генерируется автоматически.';
COMMENT ON COLUMN clients.telegram_user_id IS 'Уникальный числовой ID аккаунта Telegram; используется для идентификации пользователя, не зависит от username.';
COMMENT ON COLUMN clients.telegram_chat_id IS 'ID Telegram-чата для отправки сообщений пользователю. Не является подтверждением доставки.';
COMMENT ON COLUMN clients.name IS 'Отображаемое имя пользователя; не является уникальным идентификатором.';
COMMENT ON COLUMN clients.username IS 'Необязательный username Telegram. Может изменяться; не используется для проверки прав.';
COMMENT ON COLUMN clients.language IS 'Предпочтительный язык общения: ru — русский, ko — корейский. По умолчанию ru.';
COMMENT ON COLUMN clients.created_at IS 'Момент создания записи пользователя. timestamptz хранит однозначный момент времени; бизнес-отображение — Asia/Seoul.';

COMMENT ON TABLE admins IS 'Сотрудники Ace Place, их роли и настройки уведомлений. Каждая запись связана с пользователем clients; имя и Telegram-контакты берутся оттуда.';
COMMENT ON COLUMN admins.user_id IS 'Первичный ключ и ссылка на clients.user_id. Для сотрудника сначала должна существовать запись пользователя.';
COMMENT ON COLUMN admins.google_email IS 'Необязательный адрес Google-аккаунта для настройки доступа к календарю. Сам по себе не предоставляет доступ и не включает отправку писем.';
COMMENT ON COLUMN admins.role IS 'Роль сотрудника: owner — владелец, admin — администратор, operator — оператор. Разрешённые действия проверяет backend.';
COMMENT ON COLUMN admins.notifications_enabled IS 'Признак получения оперативных уведомлений в Telegram. По умолчанию true; доставка сообщений не гарантируется.';
COMMENT ON COLUMN admins.active IS 'Признак действующего доступа сотрудника. Backend должен проверять active и роль при каждой административной операции.';

COMMENT ON TABLE bookings IS 'Бронирования единственного party room Ace Place целиком и ручные блокировки расписания. База запрещает пересечение активных интервалов аренды; уборка хранением и ограничениями этой таблицы не учитывается.';
COMMENT ON COLUMN bookings.booking_id IS 'Уникальный идентификатор брони или блокировки; первичный ключ, генерируется автоматически.';
COMMENT ON COLUMN bookings.user_id IS 'Необязательная ссылка на клиента clients.user_id. Может отсутствовать у ручной блокировки или внешней брони.';
COMMENT ON COLUMN bookings.type IS 'Тип записи: booking — бронирование всей площадки; block — ручная блокировка. Для block допустимы confirmed, cancelled, completed.';
COMMENT ON COLUMN bookings.status IS 'Статус: hold — удержание при оформлении; pending — заявка на подтверждение; confirmed — подтверждено; cancelled — отменено; expired — срок истёк; completed — завершено. hold, pending, confirmed блокируют расписание.';
COMMENT ON COLUMN bookings.dt_order IS 'Момент создания заявки или блокировки; по умолчанию время создания записи. Не дата начала аренды.';
COMMENT ON COLUMN bookings.starts_at IS 'Начало аренды или блокировки, включительно. timestamptz; бизнес-правила и отображение используют Asia/Seoul.';
COMMENT ON COLUMN bookings.ends_at IS 'Окончание аренды или блокировки, не включается в интервал. Должно быть позже starts_at. Может приходиться на следующую дату; время уборки не включено.';
COMMENT ON COLUMN bookings.expires_at IS 'Срок удержания или ожидания подтверждения; обязателен для hold и pending. Наступление этого времени само по себе не освобождает слот: backend должен перевести запись в expired.';
COMMENT ON COLUMN bookings.amount IS 'Полная согласованная стоимость аренды, включая предоплату, в валюте unit. Точное неотрицательное число numeric(14,2). Для бесплатной блокировки указывается 0.';
COMMENT ON COLUMN bookings.deposit_paid_amount IS 'Фактически внесённая предоплата в валюте unit. Неотрицательная сумма, по умолчанию 0; не размер требуемой предоплаты.';
COMMENT ON COLUMN bookings.balance_paid_amount IS 'Фактически внесённая доплата сверх предоплаты в валюте unit. Неотрицательная сумма, по умолчанию 0. Всего внесено: deposit_paid_amount + balance_paid_amount.';
COMMENT ON COLUMN bookings.unit IS 'Трёхбуквенный код валюты всех сумм записи, например KRW или USD; по умолчанию KRW. База проверяет формат из трёх заглавных латинских букв, но не наличие кода в справочнике валют.';
COMMENT ON COLUMN bookings.version IS 'Версия записи, начинается с 1 и автоматически увеличивается триггером при каждом UPDATE. Backend должен проверять ожидаемую версию для защиты от одновременных изменений.';
COMMENT ON COLUMN bookings.updated_at IS 'Момент последнего изменения записи; автоматически обновляется триггером при UPDATE.';

COMMENT ON CONSTRAINT bookings_no_overlap ON bookings IS 'Запрещает пересечение диапазонов [starts_at, ends_at) у записей со статусами hold, pending, confirmed. Применяется ко всей площадке, включая блокировки; уборку не учитывает.';
COMMENT ON CONSTRAINT booking_times_valid ON bookings IS 'Начало и окончание должны быть конечными моментами времени; starts_at строго раньше ends_at.';
COMMENT ON CONSTRAINT booking_expiry_required ON bookings IS 'Для удержания hold и ожидающей заявки pending требуется expires_at.';
COMMENT ON CONSTRAINT blocks_use_explicit_status ON bookings IS 'Ручная блокировка block имеет статус confirmed, cancelled или completed.';
COMMENT ON FUNCTION update_booking_version() IS 'Перед обновлением брони увеличивает version и устанавливает updated_at в текущее время.';
COMMENT ON TRIGGER bookings_updated ON bookings IS 'Автоматическое обновление версии и времени изменения брони при каждом UPDATE.';
