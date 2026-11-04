ALTER TABLE bookings ADD COLUMN request_key uuid UNIQUE;
ALTER TABLE bookings ADD COLUMN request_data jsonb;
COMMENT ON COLUMN bookings.request_key IS 'Уникальный ключ создания записи для защиты от повторных запросов; повтор с тем же ключом возвращает ту же бронь.';
COMMENT ON COLUMN bookings.request_data IS 'Исходные параметры создания для проверки повторного запроса; не изменяются при переносе или смене статуса. Без секретов.';
