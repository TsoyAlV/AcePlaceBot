import os
import unittest
from unittest.mock import patch

from app.config import load_settings


class SettingsTests(unittest.TestCase):
    def environment(self, **extra):
        return {
            "POSTGRES_DB": "test_db",
            "POSTGRES_USER": "test_user",
            "POSTGRES_PASSWORD": "private-db-password",
            "TELEGRAM_TEST": "private-test-token",
            "TELEGRAM_PROD": "must-never-be-used",
            **extra,
        }

    def load(self, environment):
        with patch.dict(os.environ, environment, clear=True), patch("app.config.load_dotenv"):
            return load_settings()

    def test_secrets_are_not_in_repr(self):
        settings = self.load(self.environment())
        self.assertNotIn("private-db-password", repr(settings))
        self.assertNotIn("private-test-token", repr(settings))

    def test_production_token_is_not_a_fallback(self):
        settings = self.load(self.environment(TELEGRAM_TEST=""))
        self.assertEqual(settings.telegram_test_token, "")

    def test_invalid_port_is_rejected(self):
        for port in ("abc", "0", "65536"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                self.load(self.environment(POSTGRES_PORT=port))

    def test_missing_password_names_setting_without_leaking_values(self):
        with self.assertRaisesRegex(ValueError, "POSTGRES_PASSWORD"):
            self.load(self.environment(POSTGRES_PASSWORD=""))


if __name__ == "__main__":
    unittest.main()
