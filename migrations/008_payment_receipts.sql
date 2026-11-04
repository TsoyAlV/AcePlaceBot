ALTER TABLE bookings ADD COLUMN payment_receipt jsonb;
CREATE UNIQUE INDEX bookings_current_receipt_unique ON bookings ((payment_receipt->>'file_unique_id'))
WHERE payment_receipt IS NOT NULL;
COMMENT ON COLUMN bookings.payment_receipt IS 'Предоставленный клиентом скриншот: Telegram file_id/file_unique_id, тип, время, статус ручной проверки и ожидаемая сумма. Изображение хранится в Telegram; загрузка не означает оплату. Отметка получения денег отдельна от подтверждения аренды. Поздние оплаты требуют ручного решения без автоматического восстановления слота.';
