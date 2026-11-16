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
            "TELEGRAM_PROD": "private-prod-token",
            **extra,
        }

    def load(self, environment):
        with patch.dict(os.environ, environment, clear=True), patch("app.config.load_dotenv"):
            return load_settings()

    def test_secrets_are_not_in_repr(self):
        settings = self.load(self.environment())
        self.assertNotIn("private-db-password", repr(settings))
        self.assertNotIn("private-prod-token", repr(settings))

    def test_only_production_token_is_used(self):
        settings = self.load(self.environment(TELEGRAM_PROD=' private-prod-token ',TELEGRAM_TEST='legacy-token'))
        self.assertEqual(settings.telegram_prod_token,'private-prod-token')

    def test_missing_production_token_never_uses_legacy_token(self):
        settings=self.load(self.environment(TELEGRAM_PROD='',TELEGRAM_TEST='legacy-token'))
        self.assertEqual(settings.telegram_prod_token,'')
        from app import bot
        with patch('app.bot.load_settings',return_value=settings),patch('app.bot.check_database') as check,patch('builtins.print') as output:
            self.assertEqual(bot.main(),1)
        check.assert_not_called()
        self.assertIn('TELEGRAM_PROD',output.call_args.args[0])
        self.assertNotIn('legacy-token',output.call_args.args[0])

    def test_invalid_port_is_rejected(self):
        for port in ("abc", "0", "65536"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                self.load(self.environment(POSTGRES_PORT=port))

    def test_missing_password_names_setting_without_leaking_values(self):
        with self.assertRaisesRegex(ValueError, "POSTGRES_PASSWORD"):
            self.load(self.environment(POSTGRES_PASSWORD=""))


if __name__ == "__main__":
    unittest.main()
