ALTER TABLE bookings ADD COLUMN contact_phone text;
ALTER TABLE bookings ADD COLUMN guests_count integer;
ALTER TABLE bookings ADD COLUMN event_name text;
ALTER TABLE bookings ADD CONSTRAINT booking_details_valid CHECK (
    (contact_phone IS NULL AND guests_count IS NULL AND event_name IS NULL) OR
    (type='booking' AND contact_phone IS NOT NULL AND guests_count IS NOT NULL AND event_name IS NOT NULL
     AND contact_phone ~ '^\+?[0-9]{7,15}$' AND guests_count BETWEEN 1 AND 10000
     AND length(event_name) BETWEEN 1 AND 200 AND event_name=btrim(event_name))
);
COMMENT ON COLUMN bookings.contact_phone IS 'Контактный телефон для конкретной брони: 7–15 цифр, возможен ведущий +. Снимок ответа клиента; старые/ручные записи могут не содержать анкету.';
COMMENT ON COLUMN bookings.guests_count IS 'Заявленное количество гостей. 1–10000 — технический диапазон, не ограничение вместимости площадки.';
COMMENT ON COLUMN bookings.event_name IS 'Тип или название мероприятия, введённое клиентом, до 200 символов. Передаётся администраторам и в описание события Google Calendar.';
