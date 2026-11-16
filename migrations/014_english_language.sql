-- Extend the existing constraint without changing any user's selected language.
ALTER TABLE clients DROP CONSTRAINT IF EXISTS clients_language_check;
ALTER TABLE clients ADD CONSTRAINT clients_language_check CHECK (language IN ('ru', 'ko', 'en'));
COMMENT ON COLUMN clients.language IS 'Preferred language: ru — Russian, ko — Korean, en — English. Defaults to ru.';
