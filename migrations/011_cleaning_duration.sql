ALTER TABLE bookings DROP CONSTRAINT cleaning_slot_valid;
ALTER TABLE bookings ADD CONSTRAINT cleaning_slot_valid CHECK (
    (cleaning_starts_at IS NULL AND cleaning_ends_at IS NULL) OR
    (cleaning_starts_at IS NOT NULL AND cleaning_ends_at IS NOT NULL
     AND isfinite(cleaning_starts_at) AND isfinite(cleaning_ends_at)
     AND cleaning_starts_at >= ends_at
     AND cleaning_ends_at > cleaning_starts_at
     AND cleaning_ends_at <= cleaning_starts_at + interval '24 hours'
     AND type='booking')
);
COMMENT ON COLUMN bookings.cleaning_starts_at IS 'Плановое начало уборки: ближайший разрешённый старт из недельного конфига после окончания аренды. Сохраняется при создании и пересчитывается только при явном переносе.';
COMMENT ON COLUMN bookings.cleaning_ends_at IS 'Сохранённое окончание уборки, рассчитанное по duration_minutes на момент создания/переноса. Изменение конфига не меняет существующие записи; занятость защищена до этого момента.';
COMMENT ON CONSTRAINT cleaning_slot_valid ON bookings IS 'Уборка относится только к аренде, начинается не раньше её окончания и имеет положительную длительность не более 24 часов. У блокировок и старых записей допускаются два NULL.';
COMMENT ON TABLE bookings IS 'Аренда единственной площадки и ручные блокировки. Backend выбирает разрешённый старт уборки и длительность из конфига; PostgreSQL сохраняет интервал и защищает занятость до окончания уборки.';
