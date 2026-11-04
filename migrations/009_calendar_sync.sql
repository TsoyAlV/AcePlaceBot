ALTER TABLE bookings ADD COLUMN calendar_sync jsonb NOT NULL DEFAULT '{}';
COMMENT ON COLUMN bookings.calendar_sync IS 'Синхронизация аренды/блокировки и связанной уборки в Google Calendar: календарь, устойчивые ID двух событий, требуемая/синхронизированная версия, состояние, попытки, срок повтора, ошибки и аренда обработки. Секретов не содержит; обновление только этого поля не меняет бизнес-версию.';
CREATE OR REPLACE FUNCTION update_booking_version() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) - 'admin_notifications' - 'calendar_sync') IS NOT DISTINCT FROM
       (to_jsonb(OLD) - 'admin_notifications' - 'calendar_sync') THEN
        RETURN NEW;
    END IF;
    NEW.version := OLD.version + 1;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;
COMMENT ON COLUMN bookings.version IS 'Версия бизнес-данных. Обновления только admin_notifications и calendar_sync версию не меняют.';
COMMENT ON FUNCTION update_booking_version() IS 'Увеличивает version и updated_at при изменении бизнес-полей, исключая чистые изменения очередей admin_notifications/calendar_sync.';
COMMENT ON TRIGGER bookings_updated ON bookings IS 'Обновляет бизнес-версию при изменениях полей брони, кроме технических очередей.';
