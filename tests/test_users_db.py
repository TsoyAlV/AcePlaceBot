"""Opt-in integration tests; schema and records are rolled back."""

import os
import unittest
import uuid

from psycopg import sql

from app.config import PROJECT_ROOT, load_settings
from app.database import connect_database
from app.users import admin_role, grant_first_owner, register_client, set_language


@unittest.skipUnless(os.getenv('RUN_DB_TESTS') == '1', 'Set RUN_DB_TESTS=1 for PostgreSQL tests')
class UsersDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.connection = connect_database(load_settings())
        self.addCleanup(self.connection.close)
        self.transaction = self.connection.transaction(force_rollback=True)
        self.transaction.__enter__()
        self.addCleanup(self.transaction.__exit__, None, None, None)
        schema = 'test_users_' + uuid.uuid4().hex
        self.connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        self.connection.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
        self.connection.execute((PROJECT_ROOT / 'migrations/001_initial.sql').read_text(encoding='utf-8'))

    def register(self, telegram_id=101, **changes):
        values = dict(telegram_user_id=telegram_id, telegram_chat_id=telegram_id,
                      name='Test user', username='test', language_code='ru')
        values.update(changes)
        return register_client(self.connection, **values)

    def test_repeat_registration_updates_profile_without_duplicate(self):
        first = self.register()
        again = self.register(name='Changed', username=None, telegram_chat_id=202)
        self.assertEqual(first.user_id, again.user_id)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM clients').fetchone()[0], 1)
        self.assertEqual(self.connection.execute('SELECT name,username,telegram_chat_id FROM clients').fetchone(), ('Changed', None, 202))

    def test_explicit_language_survives_new_registration(self):
        self.register()
        set_language(self.connection, 101, 'ko')
        self.assertEqual(self.register(language_code='ru').language, 'ko')

    def test_initial_language_and_validation(self):
        self.assertEqual(self.register(language_code='ko-KR').language, 'ko')
        self.assertEqual(self.register(102, language_code='en').language, 'ru')
        with self.assertRaises(ValueError):
            set_language(self.connection, 101, 'en')

    def test_normal_client_has_no_access(self):
        self.register(name='owner', username='admin')
        self.assertIsNone(admin_role(self.connection, 101))

    def test_bootstrap_is_idempotent_and_refuses_second_owner(self):
        self.register()
        self.register(102)
        self.assertTrue(grant_first_owner(self.connection, 101, None))
        self.assertEqual(admin_role(self.connection, 101), 'owner')
        self.assertFalse(grant_first_owner(self.connection, 101, None))
        with self.assertRaises(ValueError):
            grant_first_owner(self.connection, 102, None)
        self.assertIsNone(admin_role(self.connection, 102))

    def test_deactivated_admin_loses_access_without_restart(self):
        user = self.register()
        self.connection.execute("INSERT INTO admins(user_id,role) VALUES (%s,'operator')", (user.user_id,))
        self.assertEqual(admin_role(self.connection, 101), 'operator')
        self.connection.execute('UPDATE admins SET active=false WHERE user_id=%s', (user.user_id,))
        self.assertIsNone(admin_role(self.connection, 101))

    def test_owner_must_already_be_registered(self):
        with self.assertRaises(ValueError):
            grant_first_owner(self.connection, 999, None)
