import unittest
from app_origin import canonical_origin
from mailer import MailSettings


class OriginTests(unittest.TestCase):
    def test_browser_equivalent_https_origins_are_canonical(self):
        self.assertEqual(canonical_origin('https://ACADEMY.EXAMPLE:443'), 'https://academy.example')
        self.assertEqual(canonical_origin('https://academy.example:8443'), 'https://academy.example:8443')
        self.assertEqual(canonical_origin('https://[::1]:443'), 'https://[::1]')

    def test_unsafe_or_nonorigin_urls_are_rejected(self):
        for value in ('https://good.example\nevil.test', 'https://user:secret@good.example',
                      'https://good.example/path', 'https://good.example/', 'https://good.example?',
                      'https://good.example#', 'https://bad host', 'https://-bad.test',
                      'https://good.example:0', 'https://good.example:99999', 'http://good.example'):
            with self.subTest(origin=value), self.assertRaises(ValueError):
                canonical_origin(value)

    def test_only_loopback_plain_http_is_allowed_for_development_mail(self):
        self.assertEqual(canonical_origin('http://LOCALHOST:80/', allow_loopback_http=True, allow_trailing_slash=True), 'http://localhost')
        self.assertEqual(canonical_origin('http://127.0.0.1:8000', allow_loopback_http=True), 'http://127.0.0.1:8000')
        with self.assertRaises(ValueError):
            canonical_origin('http://192.168.1.1', allow_loopback_http=True)

    def test_mail_settings_use_the_same_browser_canonical_origin(self):
        env = {'ACADEMY_ENV': 'production', 'ACADEMY_MAIL_MODE': 'smtp',
               'ACADEMY_SMTP_HOST': 'smtp.example.test', 'ACADEMY_SMTP_PORT': '587',
               'ACADEMY_SMTP_USER': 'operator', 'ACADEMY_SMTP_PASSWORD': 'unit-test-only-password',
               'ACADEMY_SMTP_FROM': 'academy@example.test', 'ACADEMY_APP_ORIGIN': 'https://ACADEMY.EXAMPLE:443'}
        settings = MailSettings.from_env(env)
        self.assertTrue(settings.configured)
        self.assertEqual(settings.origin, 'https://academy.example')

    def test_valid_smtp_secret_characters_are_not_guessed_as_placeholders(self):
        from test_mailer import configured_env
        settings = MailSettings.from_env(configured_env(ACADEMY_SMTP_USER='your_company', ACADEMY_SMTP_PASSWORD='abc<defYour_secret!'))
        self.assertTrue(settings.configured)
