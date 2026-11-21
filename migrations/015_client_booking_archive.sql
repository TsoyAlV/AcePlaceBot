ALTER TABLE bookings ADD COLUMN client_deleted_at timestamptz;
COMMENT ON COLUMN bookings.client_deleted_at IS 'Когда клиент удалил завершённую, просроченную или отменённую бронь из личного списка. Запись, бизнес-статус, платежи, уборка и календарь сохраняются.';

-- Personal visibility must not invalidate payment, calendar or notification jobs.
CREATE OR REPLACE FUNCTION update_booking_version() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) - 'admin_notifications' - 'calendar_sync' - 'client_deleted_at') IS NOT DISTINCT FROM
       (to_jsonb(OLD) - 'admin_notifications' - 'calendar_sync' - 'client_deleted_at') THEN
        RETURN NEW;
    END IF;
    NEW.version := OLD.version + 1;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;
COMMENT ON FUNCTION update_booking_version() IS 'Увеличивает бизнес-версию, кроме изменений очередей и клиентского удаления из списка.';
COMMENT ON COLUMN bookings.version IS 'Версия бизнес-данных. Очереди admin_notifications/calendar_sync и client_deleted_at версию не меняют.';
