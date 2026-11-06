import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from readiness import diagnose


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / 'private.sqlite3'
        with sqlite3.connect(self.database) as connection:
            connection.executescript('CREATE TABLE users(id TEXT); CREATE TABLE sessions(id TEXT); CREATE TABLE profiles(id TEXT); CREATE TABLE completions(id TEXT);')

    def tearDown(self):
        self.temp.cleanup()

    def test_local_optional_email_does_not_block_lessons_or_start_services(self):
        with patch('mailer.Mailer.from_env', side_effect=AssertionError('Doctor must not start mail services')):
            result = diagnose(database=self.database, environ={})
        self.assertTrue(result['checksPassed'])
        self.assertEqual(next(c for c in result['checks'] if c['name'] == 'mail')['status'], 'optional')
        self.assertTrue(result['unverified'])

    def test_missing_database_is_reported_without_creating_it(self):
        missing = Path(self.temp.name) / 'missing.sqlite3'
        result = diagnose(database=missing, environ={})
        self.assertFalse(result['checksPassed'])
        self.assertFalse(missing.exists())

    def test_production_missing_settings_and_mail_cannot_report_pass(self):
        result = diagnose(database=self.database, production=True, environ={})
        self.assertFalse(result['checksPassed'])
        self.assertEqual(next(c for c in result['checks'] if c['name'] == 'mail')['status'], 'failed')

    def test_report_never_contains_credentials_and_still_requires_external_checks(self):
        env = {'ACADEMY_ENV': 'production', 'ACADEMY_APP_ORIGIN': 'https://academy.example',
               'ACADEMY_SECURE_COOKIES': '1', 'ACADEMY_DB': str(self.database),
               'ACADEMY_MAIL_MODE': 'smtp', 'ACADEMY_SMTP_HOST': 'smtp.example.test',
               'ACADEMY_SMTP_USER': 'private-operator-marker', 'ACADEMY_SMTP_PASSWORD': 'private-password-marker',
               'ACADEMY_SMTP_FROM': 'operator@example.test'}
        with patch('readiness.importlib.metadata.version', return_value='3.0.2'):
            report = diagnose(database=self.database, production=True, environ=env)
        self.assertTrue(report['checksPassed'])
        self.assertNotIn('private-operator-marker', json.dumps(report))
        self.assertNotIn('private-password-marker', json.dumps(report))
        self.assertTrue(report['unverified'])
