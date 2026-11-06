"""WSGI transport for the same Academy handlers used by the local server.

Start with ``run-production.py`` to apply validated, bounded server options.
Importing this module does not open a listener or initialize a database.
"""

from __future__ import annotations

import io
import ipaddress
import os
import re
from dataclasses import dataclass
from email.message import Message
from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote, urlsplit

from server import AcademyHandler, AcademyState, ROOT
from app_origin import canonical_origin


class ProductionConfigError(ValueError):
    """Safe configuration error: messages name settings but never their values."""


@dataclass(frozen=True)
class ProductionConfig:
    db_path: Path
    app_origin: str
    host: str
    port: int
    secure_cookies: bool = True
    trusted_proxy: str | None = None


def validate_production_config(environ=None, root=ROOT):
    """Validate production configuration without writing data or contacting services."""
    env = os.environ if environ is None else environ
    if env.get("ACADEMY_ENV") != "production":
        raise ProductionConfigError("Set ACADEMY_ENV=production for the production server.")
    origin = env.get("ACADEMY_APP_ORIGIN", "")
    try:
        origin = canonical_origin(origin)
    except (ValueError, TypeError):
        raise ProductionConfigError("ACADEMY_APP_ORIGIN must be an HTTPS origin without credentials, path, query or fragment.")
    if env.get("ACADEMY_SECURE_COOKIES") != "1":
        raise ProductionConfigError("Set ACADEMY_SECURE_COOKIES=1 for production.")
    explicit_db = env.get("ACADEMY_DB", "")
    if not explicit_db or not Path(explicit_db).is_absolute():
        raise ProductionConfigError("ACADEMY_DB must be an explicit absolute path outside the checkout.")
    db_path = Path(explicit_db).resolve()
    if db_path.is_relative_to(Path(root).resolve()):
        raise ProductionConfigError("ACADEMY_DB must be outside the checkout.")
    if db_path.exists() and (not db_path.is_file() or not _writable(db_path)):
        raise ProductionConfigError("ACADEMY_DB must be a writable database file.")
    parent = db_path.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if not parent.is_dir() or not _writable(parent, directory=True):
        raise ProductionConfigError("The ACADEMY_DB parent directory must be writable.")
    try:
        port = int(env.get("PORT", "8000"))
    except (TypeError, ValueError):
        raise ProductionConfigError("PORT must be an integer from 1 to 65535.") from None
    if not 1 <= port <= 65535:
        raise ProductionConfigError("PORT must be an integer from 1 to 65535.")
    host = env.get("HOST", "127.0.0.1")
    if not host or any(char.isspace() for char in host):
        raise ProductionConfigError("HOST must be a valid explicit bind address.")
    trusted_proxy = env.get('ACADEMY_TRUSTED_PROXY') or None
    if trusted_proxy is not None:
        try:
            address = ipaddress.ip_address(trusted_proxy)
            if address.is_unspecified or address.is_multicast:
                raise ValueError
            trusted_proxy = str(address)
        except (ValueError, TypeError):
            raise ProductionConfigError('ACADEMY_TRUSTED_PROXY must be one explicit proxy IP address; wildcard trust is forbidden.') from None
    return ProductionConfig(db_path, origin, host, port, trusted_proxy=trusted_proxy)


def _writable(path, directory=False):
    # The mode check also works when operator tools happen to run as root.
    return bool(path.stat().st_mode & 0o222) and os.access(path, os.W_OK | (os.X_OK if directory else 0))


class WSGIHandler(AcademyHandler):
    """Adapt parsed WSGI requests to handlers without constructing an HTTP socket."""

    def __init__(self, state, environ):
        self.server = state
        self.command = environ.get("REQUEST_METHOD", "GET").upper()
        # PEP 3333 PATH_INFO is an already URL-decoded Latin-1 representation.
        # Re-encode once so the shared static-path validation decodes it once.
        path = environ.get("PATH_INFO", "/") or "/"
        self.path = quote(path.encode("latin-1"), safe="/")
        if environ.get("QUERY_STRING"):
            self.path += "?" + environ["QUERY_STRING"]
        self.headers = Message()
        for name, value in environ.items():
            if name.startswith("HTTP_") and name not in ("HTTP_CONTENT_TYPE", "HTTP_CONTENT_LENGTH") and isinstance(value, str):
                self.headers[name[5:].replace("_", "-")] = value
        for name in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            if environ.get(name) not in (None, ""):
                self.headers[name.replace("_", "-")] = str(environ[name])
        # A proxy must preserve Host. Forwarded headers never establish identity.
        if "Host" not in self.headers:
            self.headers["Host"] = ""
        self.client_address = (environ.get("REMOTE_ADDR", "unknown"), 0)
        self.rfile = environ["wsgi.input"]
        self.wfile = io.BytesIO()
        self.response_headers = []
        self.response_status = 500
        self.new_cookie = None

    def send_response(self, code, message=None):
        self.response_status = code
        self.response_headers = []

    def send_header(self, keyword, value):
        self.response_headers.append((str(keyword), str(value)))

    def end_headers(self):
        pass


class AcademyApplication:
    def __init__(self, state):
        self.state = state

    def __call__(self, environ, start_response):
        handler = WSGIHandler(self.state, environ)
        if handler.command not in ("GET", "HEAD", "POST", "PUT", "DELETE"):
            handler.json_response(405, {"error": "Η μέθοδος δεν υποστηρίζεται."})
        else:
            handler.dispatch(handler.command)
        status = handler.response_status
        start_response(f"{status} {HTTPStatus(status).phrase}", handler.response_headers)
        return [handler.wfile.getvalue()]

    def close(self):
        self.state.close()


def create_application(db_path=None, root=ROOT, secure_cookies=None, environ=None):
    env = os.environ if environ is None else environ
    origin = None
    if env.get("ACADEMY_ENV") == "production":
        config = validate_production_config(env, root)
        if db_path is not None and Path(db_path).resolve() != config.db_path:
            raise ProductionConfigError("Production database overrides must match ACADEMY_DB.")
        if secure_cookies is False:
            raise ProductionConfigError("Production cookies must be Secure.")
        db_path, secure_cookies, origin = config.db_path, True, config.app_origin
    else:
        db_path = db_path or env.get("ACADEMY_DB") or ROOT.parent / "academy-data" / "academy.sqlite3"
        if secure_cookies is None:
            secure_cookies = env.get("ACADEMY_SECURE_COOKIES") == "1"
    return AcademyApplication(AcademyState(Path(db_path), Path(root), bool(secure_cookies), origin))
