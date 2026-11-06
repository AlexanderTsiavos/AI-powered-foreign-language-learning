"""Socket-free WSGI integration and production configuration checks."""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote_to_bytes, urlsplit
from wsgiref.util import setup_testing_defaults
from wsgiref.validate import validator

from server import MAX_BODY
from wsgi import ProductionConfigError, create_application, validate_production_config


class WSGIClient:
    def __init__(self, application, host="localhost", scheme="http"):
        self.raw_application = application
        self.application = validator(application)
        self.host, self.scheme = host, scheme
        self.cookie, self.csrf = "", ""

    def request(self, method, path, body=None, headers=None, raw=None, length=None):
        environ = {}
        setup_testing_defaults(environ)
        parsed = urlsplit(path)
        environ.update({"REQUEST_METHOD": method, "PATH_INFO": unquote_to_bytes(parsed.path).decode("latin-1"),
                        "QUERY_STRING": parsed.query, "HTTP_HOST": self.host,
                        "wsgi.url_scheme": self.scheme, "REMOTE_ADDR": "127.0.0.1",
                        "HTTP_COOKIE": self.cookie})
        if body is not None:
            raw = json.dumps(body).encode()
        if raw is not None:
            environ.update({"wsgi.input": io.BytesIO(raw), "CONTENT_TYPE": "application/json",
                            "CONTENT_LENGTH": str(len(raw) if length is None else length)})
        for key, value in (headers or {}).items():
            name = key.upper().replace("-", "_")
            environ[name if name in ("CONTENT_TYPE", "CONTENT_LENGTH") else "HTTP_" + name] = value
        response = {}

        def start_response(status, response_headers, exc_info=None):
            response.update(status=int(status.split()[0]), headers=dict(response_headers))

        # Broken CONTENT_LENGTH values are rejected by compliant WSGI servers;
        # exercise the application's defensive check directly for those cases.
        application = self.raw_application if length == "invalid" or (isinstance(length, int) and length < 0) else self.application
        chunks = application(environ, start_response)
        try:
            content = b"".join(chunks)
        finally:
            if hasattr(chunks, "close"):
                chunks.close()
        headers_out = response["headers"]
        if "Set-Cookie" in headers_out:
            self.cookie = headers_out["Set-Cookie"].split(";", 1)[0]
        if content and headers_out.get("Content-Type", "").startswith("application/json"):
            content = json.loads(content)
            if isinstance(content, dict) and "csrfToken" in content:
                self.csrf = content["csrfToken"]
        return response["status"], content, headers_out

    def mutate(self, method, path, body=None, headers=None):
        return self.request(method, path, {**(body or {}), "csrfToken": self.csrf}, headers)

    def register(self, email="test@example.com"):
        self.request("GET", "/api/session")
        return self.mutate("POST", "/api/register", {"name": "Δοκιμή", "email": email,
                                                     "password": "a strong password 42", "ageConfirmed": True})


class WSGITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "checkout"
        (self.root / "web").mkdir(parents=True)
        (self.root / "web" / "index.html").write_text("<html lang='el'>Δοκιμή Academy</html>", encoding="utf-8")
        (self.root / "web" / "app.js").write_text("'use strict';", encoding="utf-8")
        (self.root / "data").mkdir()
        self.curriculum = {"lessons": [{"id": "first", "title": "First", "xp": 30,
                                        "cards": [{"word": "car"}],
                                        "exercises": [{"id": "car", "answer": "car"}]}]}
        (self.root / "data" / "curriculum.json").write_text(json.dumps(self.curriculum))
        self.db_path = Path(self.temp.name) / "private" / "academy.sqlite3"
        with patch.dict(os.environ, {"ACADEMY_MAIL_MODE": "disabled"}):
            self.app = create_application(self.db_path, self.root, environ={})
        self.client = WSGIClient(self.app)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def test_constructs_application_without_opening_network_listener(self):
        with patch("socket.socket", side_effect=AssertionError("WSGI must not allocate sockets")):
            app = create_application(Path(self.temp.name) / "other.sqlite3", self.root, environ={})
            try:
                self.assertEqual(WSGIClient(app).request("GET", "/api/health")[0], 200)
            finally:
                app.close()

    def test_auth_learning_and_logout_share_handlers_and_rotate_sessions(self):
        status, data, headers = self.client.register()
        self.assertEqual(status, 200)
        self.assertFalse(data["user"]["emailVerified"])
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", headers["Set-Cookie"])
        self.assertNotIn("Secure", headers["Set-Cookie"])
        self.assertEqual(self.client.mutate("POST", "/api/progress", {"lessonId": "first", "answers": ["wrong"]})[0], 422)
        complete = self.client.mutate("POST", "/api/progress", {"lessonId": "first", "answers": ["CAR!"]})
        self.assertEqual(complete[0], 200)
        self.assertEqual(complete[1]["progress"]["xp"], 30)
        cookie, csrf = self.client.cookie, self.client.csrf
        self.assertEqual(self.client.mutate("POST", "/api/logout")[0], 200)
        self.assertNotEqual(self.client.cookie, cookie)
        self.assertNotEqual(self.client.csrf, csrf)
        self.assertIsNone(self.client.request("GET", "/api/session")[1]["user"])

    def test_head_static_json_security_and_error_headers(self):
        for path in ("/", "/app.js", "/api/health", "/api/curriculum", "/data/curriculum.json", "/missing"):
            get = self.client.request("GET", path)
            head = self.client.request("HEAD", path)
            self.assertEqual(head[0], get[0], path)
            self.assertEqual(head[1], b"", path)
            self.assertEqual(head[2]["Content-Length"], get[2]["Content-Length"], path)
            for name in ("Content-Security-Policy", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy"):
                self.assertIn(name, head[2], path)
        self.assertEqual(self.client.request("GET", "/api/health")[2]["Cache-Control"], "no-store")
        self.assertEqual(self.client.request("GET", "/")[2]["Cache-Control"], "no-cache")
        self.assertEqual(self.client.request("GET", "/app.js")[2]["Content-Type"], "text/javascript; charset=utf-8")
        self.assertEqual(self.client.request("OPTIONS", "/api/session")[0], 405)
        self.assertEqual(self.client.request("PATCH", "/api/session")[0], 405)

    def test_private_paths_and_encoded_traversal_never_escape_web(self):
        for path in ("/.env", "/%2e%2e/data/curriculum.json", "/%2f..%2fdata/curriculum.json",
                     "/%5c..%5cserver.py", "/%00", "/server.py", "/data/private.sqlite3"):
            self.assertEqual(self.client.request("GET", path)[0], 404, path)

    def test_malformed_bounded_bodies_and_content_types(self):
        self.client.request("GET", "/api/session")
        cases = [(b"{", None, {}, 400), (b"[]", None, {}, 400), (b"", None, {}, 413),
                 (b"{}", -1, {}, 413), (b"{}", MAX_BODY + 1, {}, 413),
                 (b"{}", "invalid", {}, 400), (b"{}", None, {"Content-Type": "text/plain"}, 415),
                 (b"{}", None, {"Content-Type": "application/json; charset=iso-8859-1"}, 415),
                 (b"{}", None, {"Transfer-Encoding": "chunked"}, 400)]
        for raw, length, headers, status in cases:
            self.assertEqual(self.client.request("POST", "/api/logout", raw=raw, length=length, headers=headers)[0], status)

    def test_origin_csrf_and_forwarded_host_are_not_trusted(self):
        self.client.register()
        self.assertEqual(self.client.request("POST", "/api/logout", {})[0], 403)
        for origin in ("null", "https://attacker.example", "http://localhost/path", "http://localhost#fragment"):
            self.assertEqual(self.client.mutate("POST", "/api/logout", headers={"Origin": origin})[0], 403)
        self.assertEqual(self.client.mutate("POST", "/api/logout", headers={"Origin": "https://attacker.example", "X-Forwarded-Host": "attacker.example"})[0], 403)
        self.assertEqual(self.client.mutate("POST", "/api/logout", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.client.mutate("POST", "/api/logout", headers={"Origin": "http://localhost"})[0], 200)

    def test_disabled_email_status_is_public_and_requests_require_csrf(self):
        self.assertEqual(self.client.request("GET", "/api/account/email/status")[1], {"available": False})
        self.assertEqual(self.client.request("POST", "/api/account/password/reset/request", {"email": "test@example.com"})[0], 403)
        self.client.request("GET", "/api/session")
        self.assertEqual(self.client.mutate("POST", "/api/account/password/reset/request", {"email": "test@example.com"})[0], 503)
        self.assertEqual(self.client.mutate("POST", "/api/account/email/verification")[0], 401)
        self.client.register()
        self.assertEqual(self.client.mutate("POST", "/api/account/email/verification")[0], 503)

    def test_close_stops_services_once(self):
        with patch.object(self.app.state.mailer, "close", wraps=self.app.state.mailer.close) as close:
            self.app.close()
            self.app.close()
            close.assert_called_once()


class ProductionConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "checkout"
        self.root.mkdir()
        self.env = {"ACADEMY_ENV": "production", "ACADEMY_APP_ORIGIN": "https://academy.example",
                    "ACADEMY_SECURE_COOKIES": "1", "ACADEMY_DB": str(Path(self.temp.name) / "private" / "academy.sqlite3")}

    def tearDown(self):
        self.temp.cleanup()

    def test_validates_without_creating_database_and_defaults_to_loopback(self):
        config = validate_production_config(self.env, self.root)
        self.assertEqual((config.host, config.port), ("127.0.0.1", 8000))
        self.assertTrue(config.secure_cookies)
        self.assertFalse(config.db_path.exists())

    def test_production_requires_every_explicit_security_setting(self):
        for name in ("ACADEMY_ENV", "ACADEMY_APP_ORIGIN", "ACADEMY_SECURE_COOKIES", "ACADEMY_DB"):
            env = dict(self.env)
            del env[name]
            with self.subTest(name=name), self.assertRaises(ProductionConfigError):
                validate_production_config(env, self.root)
        for value in ("http://academy.example", "https://academy.example/", "https://academy.example/path",
                      "https://user:secret@academy.example", "https://academy.example?x=y", "https://academy.example#token",
                      "https://academy.example?", "https://academy.example#", "https://academy.example:99999", "https://bad host"):
            with self.subTest(origin=value), self.assertRaises(ProductionConfigError) as error:
                validate_production_config({**self.env, "ACADEMY_APP_ORIGIN": value}, self.root)
            self.assertNotIn(value, str(error.exception))

    def test_database_must_be_outside_checkout_and_writable(self):
        for path in ("relative.sqlite3", str(self.root / "private.sqlite3"), str(self.root)):
            with self.subTest(path=path), self.assertRaises(ProductionConfigError):
                validate_production_config({**self.env, "ACADEMY_DB": path}, self.root)
        readonly = Path(self.temp.name) / "readonly"
        readonly.mkdir(mode=0o500)
        try:
            with self.assertRaises(ProductionConfigError):
                validate_production_config({**self.env, "ACADEMY_DB": str(readonly / "db.sqlite3")}, self.root)
        finally:
            readonly.chmod(0o700)
        link = Path(self.temp.name) / "linked"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ProductionConfigError):
            validate_production_config({**self.env, "ACADEMY_DB": str(link / "db.sqlite3")}, self.root)

    def test_production_binds_host_origin_and_secure_cookies_without_forwarded_trust(self):
        (self.root / "data").mkdir()
        (self.root / "data" / "curriculum.json").write_text('{"lessons":[]}')
        app = create_application(root=self.root, environ=self.env)
        try:
            client = WSGIClient(app, "academy.example", "https")
            response = client.request("GET", "/api/session")
            self.assertEqual(response[0], 200)
            self.assertIn("; Secure", response[2]["Set-Cookie"])
            self.assertEqual(response[2]["Strict-Transport-Security"], "max-age=31536000")
            self.assertEqual(client.mutate("POST", "/api/logout", headers={"Origin": "http://academy.example"})[0], 403)
            self.assertEqual(client.mutate("POST", "/api/logout", headers={"Origin": "https://academy.example"})[0], 200)
            client.host = "attacker.example"
            self.assertEqual(client.request("GET", "/api/session", headers={"X-Forwarded-Host": "academy.example"})[0], 400)
        finally:
            app.close()

    def test_production_cannot_override_database_or_disable_secure_cookies(self):
        with self.assertRaises(ProductionConfigError):
            create_application(root=self.root, secure_cookies=False, environ=self.env)
        with self.assertRaises(ProductionConfigError):
            create_application(Path(self.temp.name) / "different.sqlite3", self.root, environ=self.env)


if __name__ == "__main__":
    unittest.main()
