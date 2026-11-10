-- A rental can finish before its cleanup. Keep capacity protected for both states.
ALTER TABLE bookings DROP CONSTRAINT bookings_no_overlap;
ALTER TABLE bookings ADD CONSTRAINT bookings_no_overlap EXCLUDE USING gist (
    tstzrange(starts_at,COALESCE(cleaning_ends_at,ends_at),'[)') WITH &&
) WHERE (status IN ('hold','pending','confirmed') OR (type='booking' AND status='completed'));
COMMENT ON CONSTRAINT bookings_no_overlap ON bookings IS 'Защищает аренду до окончания назначенной уборки, в том числе после автоматического перехода в completed. Технические записи серий books_plans (type=block, completed) не занимают время.';
COMMENT ON COLUMN bookings.status IS 'hold — удержание; pending — ожидает решения; confirmed — подтверждено; cancelled — отменено; expired — срок заявки истёк; completed — аренда завершилась по ends_at (уборка может ещё продолжаться). Фоновая проверка каждые 30 секунд.';
