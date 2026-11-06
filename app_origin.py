"""Canonical application origins, shared by email links and HTTPS transport."""
import ipaddress
import re
from urllib.parse import urlsplit


def canonical_origin(value, *, allow_loopback_http=False, allow_trailing_slash=False):
    if (not isinstance(value, str) or not 1 <= len(value) <= 2048
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
            or '?' in value or '#' in value):
        raise ValueError('Invalid application origin')
    parsed = urlsplit(value)
    if (parsed.username is not None or parsed.password is not None or not parsed.hostname
            or parsed.path not in (('', '/') if allow_trailing_slash else ('',))):
        raise ValueError('Invalid application origin')
    hostname, port = parsed.hostname.lower(), parsed.port
    if '%' in hostname or (port is not None and not 1 <= port <= 65535):
        raise ValueError('Invalid application origin')
    try:
        address = ipaddress.ip_address(hostname)
        authority = f'[{address.compressed}]' if address.version == 6 else address.compressed
        loopback = address.is_loopback
    except ValueError:
        if not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                   for label in hostname.rstrip('.').split('.')):
            raise ValueError('Invalid application origin')
        authority, loopback = hostname, hostname == 'localhost'
    scheme = parsed.scheme.lower()
    if scheme != 'https' and not (scheme == 'http' and allow_loopback_http and loopback):
        raise ValueError('Invalid application origin')
    if port is not None and port != (443 if scheme == 'https' else 80):
        authority += f':{port}'
    return f'{scheme}://{authority}'
