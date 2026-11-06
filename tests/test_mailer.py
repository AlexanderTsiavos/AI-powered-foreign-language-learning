"""Offline mail tests: injected transports and mocked SMTP, never a provider."""

import ssl
import threading
import unittest
from unittest.mock import MagicMock, patch

from app_errors import ServiceError
from mailer import Mailer, MailMessage, MailSettings


def configured_env(**changes):
    values = {"ACADEMY_MAIL_MODE": "smtp", "ACADEMY_ENV": "production",
              "ACADEMY_SMTP_HOST": "smtp.example.test", "ACADEMY_SMTP_PORT": "587",
              "ACADEMY_SMTP_USER": "operator@example.test", "ACADEMY_SMTP_PASSWORD": "test-secret-721",
              "ACADEMY_SMTP_FROM": "academy@example.test", "ACADEMY_APP_ORIGIN": "https://academy.example.test"}
    values.update(changes)
    return values


class MailConfigurationTests(unittest.TestCase):
    def test_default_disabled_and_no_credentials_in_repr(self):
        self.assertEqual(Mailer.from_env({}).status(), {"enabled": False, "configured": False})
        settings = MailSettings.from_env(configured_env())
        self.assertTrue(settings.configured)
        self.assertNotIn("test-secret-721", repr(settings))
        self.assertNotIn("operator@example.test", repr(settings))

    def test_production_requires_explicit_tls_origin_and_credentials(self):
        for changes in ({"ACADEMY_SMTP_PORT": "25"}, {"ACADEMY_SMTP_PORT": "bad"},
                        {"ACADEMY_SMTP_PASSWORD": ""}, {"ACADEMY_SMTP_USER": ""},
                        {"ACADEMY_SMTP_PASSWORD": "${SECRET}"}, {"ACADEMY_SMTP_HOST": "https://smtp.test"},
                        {"ACADEMY_SMTP_FROM": "academy@example.test\r\nBcc:other@example.test"},
                        {"ACADEMY_APP_ORIGIN": ""}, {"ACADEMY_APP_ORIGIN": "http://localhost:8000"},
                        {"ACADEMY_APP_ORIGIN": "https://user:pass@academy.test"},
                        {"ACADEMY_APP_ORIGIN": "https://academy.test/path"},
                        {"ACADEMY_APP_ORIGIN": "https://academy.test/#fragment"}):
            with self.subTest(changes=tuple(changes)):
                self.assertFalse(MailSettings.from_env(configured_env(**changes)).configured)

    def test_development_http_only_loopback_and_link_uses_fragment(self):
        for origin in ("http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000"):
            settings = MailSettings.from_env(configured_env(ACADEMY_ENV="development", ACADEMY_APP_ORIGIN=origin))
            self.assertTrue(settings.configured)
            mailer = Mailer(settings)
            token = "a" * 43
            self.assertEqual(mailer.link("verify-email", token), origin + "/#verify-email/" + token)
            mailer.close()
        self.assertFalse(MailSettings.from_env(configured_env(ACADEMY_ENV="development", ACADEMY_APP_ORIGIN="http://example.test")).configured)

    def test_injected_transport_requires_explicit_test_factory(self):
        with self.assertRaises(ValueError):
            Mailer(transport=lambda message: None)


class MailQueueTests(unittest.TestCase):
    def test_reserved_message_waits_for_commit_signal_and_cancel_does_not_send(self):
        sent = []
        mailer = Mailer.for_testing(sent.append)
        self.addCleanup(mailer.close)
        reservation = mailer.reserve()
        self.assertFalse(mailer.wait_idle(0.01))
        self.assertEqual(sent, [])
        reservation.publish(MailMessage("student@example.test", "Subject", "Body"))
        self.assertTrue(mailer.wait_idle())
        cancelled = mailer.reserve()
        cancelled.cancel()
        self.assertTrue(mailer.wait_idle())
        self.assertEqual(len(sent), 1)

    def test_bounded_queue_has_one_hundred_slots_and_close_cancels_unpublished(self):
        sent = []
        mailer = Mailer.for_testing(sent.append)
        reservations = [mailer.reserve() for _ in range(100)]
        with self.assertRaises(ServiceError) as caught:
            mailer.reserve()
        self.assertEqual(caught.exception.status, 503)
        self.assertTrue(mailer.close())
        self.assertEqual(sent, [])
        with self.assertRaises(ServiceError):
            reservations[-1].publish(MailMessage("a@example.test", "Subject", "Body"))
        with self.assertRaises(ServiceError):
            mailer.reserve()

    def test_provider_error_is_not_retried_logged_or_exposed(self):
        attempts = []

        def failing(message):
            attempts.append(1)
            raise RuntimeError("sensitive-token-body-and-password")

        mailer = Mailer.for_testing(failing)
        self.addCleanup(mailer.close)
        with patch("sys.stderr") as stderr, patch("sys.stdout") as stdout:
            mailer.reserve().publish(MailMessage("student@example.test", "Subject", "Secret token"))
            self.assertTrue(mailer.wait_idle())
        self.assertEqual(attempts, [1])
        stderr.write.assert_not_called()
        stdout.write.assert_not_called()
        self.assertEqual(mailer._counts["failed"], 1)

    def test_dummy_jobs_use_queue_without_sending(self):
        sent = []
        mailer = Mailer.for_testing(sent.append)
        self.addCleanup(mailer.close)
        mailer.reserve().publish(None)
        self.assertTrue(mailer.wait_idle())
        self.assertEqual(mailer._counts["dummy"], 1)
        self.assertEqual(sent, [])

    def test_published_jobs_finish_on_close(self):
        sent = []
        mailer = Mailer.for_testing(sent.append)
        mailer.reserve().publish(MailMessage("student@example.test", "Subject", "Body"))
        self.assertTrue(mailer.close())
        self.assertEqual(len(sent), 1)


class SMTPTLSTests(unittest.TestCase):
    def test_starttls_uses_verified_context_before_login(self):
        settings = MailSettings.from_env(configured_env())
        connection = MagicMock()
        smtp = connection.__enter__.return_value
        with patch("mailer.smtplib.SMTP", return_value=connection) as factory, patch("mailer.ssl.create_default_context", wraps=ssl.create_default_context) as context_factory:
            Mailer(settings)._send_smtp(MailMessage("student@example.test", "Subject", "Body"))
        factory.assert_called_once_with("smtp.example.test", 587, timeout=10)
        context_factory.assert_called_once_with()
        context = smtp.starttls.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        calls = [call[0] for call in smtp.method_calls]
        self.assertEqual(calls, ["ehlo", "starttls", "ehlo", "login", "send_message"])

    def test_implicit_tls_uses_verified_context(self):
        settings = MailSettings.from_env(configured_env(ACADEMY_SMTP_PORT="465"))
        with patch("mailer.smtplib.SMTP_SSL") as factory, patch("mailer.smtplib.SMTP") as plaintext:
            Mailer(settings)._send_smtp(MailMessage("student@example.test", "Subject", "Body"))
        context = factory.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        plaintext.assert_not_called()

    def test_missing_starttls_does_not_login_or_send(self):
        connection = MagicMock()
        smtp = connection.__enter__.return_value
        smtp.starttls.side_effect = RuntimeError("TLS not supported")
        with patch("mailer.smtplib.SMTP", return_value=connection):
            with self.assertRaises(RuntimeError):
                Mailer(MailSettings.from_env(configured_env()))._send_smtp(MailMessage("student@example.test", "Subject", "Body"))
        smtp.login.assert_not_called()
        smtp.send_message.assert_not_called()
