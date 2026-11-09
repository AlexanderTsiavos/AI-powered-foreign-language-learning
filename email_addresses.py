"""A shared plain-mailbox policy for new accounts and SMTP delivery.

The application does not support display names, quoted local parts or SMTPUTF8.
Existing account identifiers remain usable for sign-in without migration.
"""

import re


_LOCAL = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+\Z", re.ASCII)
_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z", re.ASCII)


def is_mailbox(value):
    if not isinstance(value, str) or not value.isascii() or not 3 <= len(value) <= 254:
        return False
    if value.count('@') != 1:
        return False
    local, domain = value.split('@')
    return bool(1 <= len(local) <= 64 and _LOCAL.fullmatch(local)
                and not local.startswith('.') and not local.endswith('.') and '..' not in local
                and 1 <= len(domain) <= 253 and '.' in domain
                and all(_LABEL.fullmatch(label) for label in domain.split('.')))


def normalize_email(value):
    if not isinstance(value, str):
        raise ValueError('Invalid email address')
    value = value.strip().casefold()
    if not is_mailbox(value):
        raise ValueError('Invalid email address')
    return value
