ALTER TABLE bookings ADD COLUMN cancellation jsonb NOT NULL DEFAULT '{}';
COMMENT ON COLUMN bookings.cancellation IS 'Снимок условий отмены, дата и инициатор отмены клиентом, расчёт удержания предоплаты и ручного возврата, состояние и подтверждение возврата. Фактически полученные суммы платежей не обнуляются.';
