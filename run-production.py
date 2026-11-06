#!/usr/bin/env python3
"""Start the optional production WSGI server after fail-closed config validation."""

import sys

from server import MAX_BODY
from wsgi import ProductionConfigError, create_application, validate_production_config


def main():
    try:
        config = validate_production_config()
    except ProductionConfigError as exc:
        print(f"Production configuration: {exc}", file=sys.stderr)
        return 2
    try:
        import waitress
    except ImportError:
        print("Install the optional pinned runtime: python -m pip install --require-hashes -r requirements-runtime.txt", file=sys.stderr)
        return 2
    application = create_application()
    proxy_options = {}
    if config.trusted_proxy:
        # Exactly one configured peer may report client IP/protocol. Host is
        # always preserved and validated against ACADEMY_APP_ORIGIN.
        proxy_options = {'trusted_proxy': config.trusted_proxy, 'trusted_proxy_count': 1,
                         'trusted_proxy_headers': {'x-forwarded-for', 'x-forwarded-proto'}}
    try:
        waitress.serve(application, host=config.host, port=config.port, threads=4,
                       connection_limit=100, channel_timeout=30, cleanup_interval=10,
                       max_request_header_size=16384, max_request_body_size=MAX_BODY,
                       ident="Academy", expose_tracebacks=False,
                       clear_untrusted_proxy_headers=True, **proxy_options)
    except KeyboardInterrupt:
        pass
    finally:
        application.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
