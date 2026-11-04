import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.main import liveness, readiness


class HealthTests(unittest.TestCase):
    def test_liveness_does_not_need_database(self):
        self.assertEqual(liveness(), {"status": "ok"})

    def test_ready_when_database_answers(self):
        with patch("app.main.load_settings"), patch("app.main.check_database", return_value=True):
            self.assertEqual(readiness(), {"status": "ok", "database": "ok"})

    def test_database_failure_is_503_without_error_details(self):
        with patch("app.main.load_settings"), patch(
            "app.main.check_database", side_effect=RuntimeError("private-password")
        ):
            with self.assertRaises(HTTPException) as caught:
                readiness()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertNotIn("private-password", caught.exception.detail)
