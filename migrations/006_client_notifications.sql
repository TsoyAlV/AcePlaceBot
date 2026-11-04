ALTER TABLE bookings ADD COLUMN admin_notifications jsonb NOT NULL DEFAULT '{}';
COMMENT ON COLUMN bookings.admin_notifications IS 'Доставка уведомлений о клиентской заявке администраторам: внутренний ID получателя, статус, попытки, срок повтора и идентификатор захвата. Записывается вместе с заявкой, переживает перезапуск. Не подтверждает прочтение.';
CREATE OR REPLACE FUNCTION update_booking_version() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) - 'admin_notifications') IS NOT DISTINCT FROM (to_jsonb(OLD) - 'admin_notifications') THEN
        RETURN NEW;
    END IF;
    NEW.version := OLD.version + 1;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;
-- Metadata descriptions
COMMENT ON COLUMN bookings.version IS 'Версия бизнес-данных записи; увеличивается при их изменении. Обновление только состояния доставки admin_notifications не изменяет версию.';
COMMENT ON FUNCTION update_booking_version() IS 'Обновляет version и updated_at при изменении бизнес-данных. Изменение только admin_notifications не меняет версию и время изменения брони.';
COMMENT ON TRIGGER bookings_updated ON bookings IS 'Обновляет версию и время изменения при изменении полей брони, кроме чистых обновлений доставки admin_notifications.';
