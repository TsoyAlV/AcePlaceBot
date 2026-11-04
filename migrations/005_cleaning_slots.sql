ALTER TABLE bookings ADD COLUMN cleaning_starts_at timestamptz;
ALTER TABLE bookings ADD COLUMN cleaning_ends_at timestamptz;
ALTER TABLE bookings ADD CONSTRAINT cleaning_slot_valid CHECK (
    (cleaning_starts_at IS NULL AND cleaning_ends_at IS NULL) OR
    (cleaning_starts_at IS NOT NULL AND cleaning_ends_at IS NOT NULL
     AND isfinite(cleaning_starts_at) AND isfinite(cleaning_ends_at)
     AND cleaning_starts_at >= ends_at
     AND cleaning_ends_at = cleaning_starts_at + interval '1 hour'
     AND type='booking')
);
ALTER TABLE bookings DROP CONSTRAINT bookings_no_overlap;
ALTER TABLE bookings ADD CONSTRAINT bookings_no_overlap EXCLUDE USING gist (
    tstzrange(starts_at,COALESCE(cleaning_ends_at,ends_at),'[)') WITH &&
) WHERE (status IN ('hold','pending','confirmed'));
COMMENT ON COLUMN bookings.cleaning_starts_at IS 'Плановое начало выбранного backend часового слота уборки. Фиксируется при создании и пересчитывается при явном переносе, без автоматического изменения из-за конфига.';
COMMENT ON COLUMN bookings.cleaning_ends_at IS 'Плановое окончание уборки. Весь период от начала аренды до этого момента защищён от пересечений, включая ожидание уборки. NULL у блокировок и старых записей без назначенной уборки.';
COMMENT ON CONSTRAINT bookings_no_overlap ON bookings IS 'Запрещает пересечения активной аренды вместе с ожиданием и назначенной уборкой; блокировки занимают только свой интервал.';
COMMENT ON TABLE bookings IS 'Аренда единственной площадки и ручные блокировки. Backend выбирает часовую уборку из конфига; база сохраняет выбранный слот и защищает занятость до его окончания.';
