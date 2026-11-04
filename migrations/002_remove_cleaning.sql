ALTER TABLE bookings DROP CONSTRAINT bookings_no_overlap;
ALTER TABLE bookings DROP CONSTRAINT booking_times_valid;
ALTER TABLE bookings DROP COLUMN occupied_until;
ALTER TABLE bookings ADD CONSTRAINT booking_times_valid CHECK (
    isfinite(starts_at) AND isfinite(ends_at) AND starts_at < ends_at
);
ALTER TABLE bookings ADD CONSTRAINT bookings_no_overlap EXCLUDE USING gist (
    tstzrange(starts_at, ends_at, '[)') WITH &&
) WHERE (status IN ('hold', 'pending', 'confirmed'));
