"""Opt-in, one-shot SQLite backups for an operator's external scheduler.

No scheduler is installed, no background worker is started and no snapshot is
pruned. A private receipt counts only after its matching snapshot passes both
its stored SHA256 and the application's SQLite integrity checks.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time

import manage


class BackupScheduleError(Exception):
    """An intentionally safe operator message, with no database contents."""


_NAME = re.compile(r"academy-scheduled-\d{8}T\d{6}Z-[0-9a-f]{24}\.sqlite3\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_KEYS = {'version', 'filename', 'sha256', 'createdAt', 'sourceHash'}
NOTICE = ('Η εντολή δεν ενεργοποιεί χρονοπρογραμματιστή και δεν διαγράφει παλιά '
          'αντίγραφα. Περιέχουν προσωπικά δεδομένα· χρειάζονται ιδιωτική '
          'αποθήκευση και πολιτική διατήρησης από τον χειριστή.')


def _safe_path(value):
    path = Path(value).expanduser()
    # Inspect the supplied spelling before resolving, including any existing
    # ancestor. Resolving first would hide a symlink to another directory.
    if not path.is_absolute():
        path = Path.cwd() / path
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or candidate.is_junction():
            raise BackupScheduleError('Δεν επιτρέπονται συμβολικοί σύνδεσμοι στις διαδρομές αντιγράφων.')
    return path.resolve()


def _private_directory(value):
    directory = _safe_path(value)
    if directory.is_relative_to(manage.ROOT):
        raise BackupScheduleError('Ο φάκελος αντιγράφων πρέπει να είναι έξω από τον φάκελο κώδικα.')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = directory.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise BackupScheduleError('Χρειάζεται ιδιωτικός φάκελος αντιγράφων.')
    if os.name == 'posix' and (stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.getuid()):
        raise BackupScheduleError('Ο φάκελος αντιγράφων χρειάζεται ιδιωτικά δικαιώματα 0700 για τον χειριστή.')
    return directory


def _open_regular(path, flags, mode=0o600):
    if path.is_symlink():
        raise BackupScheduleError('Δεν επιτρέπονται συμβολικοί σύνδεσμοι στα αρχεία αντιγράφων.')
    # A forged FIFO must not block before fstat can reject it. Regular files
    # ignore O_NONBLOCK; Windows does not expose this flag.
    descriptor = os.open(path, flags | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0), mode)
    info = os.fstat(descriptor)
    if (not stat.S_ISREG(info.st_mode)
            or os.name == 'posix' and (stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.getuid())):
        os.close(descriptor)
        raise BackupScheduleError('Τα αρχεία αντιγράφων χρειάζονται ιδιωτικά δικαιώματα 0600 για τον χειριστή.')
    return descriptor


@contextmanager
def _exclusive(directory, timeout):
    # Keep this inode in place. Unlinking lock files after unlocking permits
    # independent processes to acquire locks on different inodes.
    descriptor = _open_regular(directory / '.backup-schedule.lock', os.O_RDWR | os.O_CREAT)
    locked = False
    try:
        if os.name == 'nt':
            import msvcrt
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b'\0')
            def acquire():
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            def release():
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        deadline = time.monotonic() + timeout
        while True:
            try:
                acquire()
                locked = True
                break
            except OSError as error:
                if error.errno not in (errno.EAGAIN, errno.EACCES, errno.EDEADLK) and getattr(error, 'winerror', None) not in (33, 36):
                    raise
                if time.monotonic() >= deadline:
                    raise BackupScheduleError('Εκτελείται άλλο προγραμματισμένο αντίγραφο. Δοκίμασε ξανά αργότερα.') from None
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        yield
    finally:
        try:
            if locked:
                release()
        finally:
            os.close(descriptor)


def _digest(path):
    descriptor = _open_regular(path, os.O_RDONLY)
    with os.fdopen(descriptor, 'rb') as stream:
        result = hashlib.sha256()
        for chunk in iter(lambda: stream.read(128 * 1024), b''):
            result.update(chunk)
        return result.hexdigest()


def _receipt(path, directory, source_hash, now):
    """Untrusted or interrupted metadata is never evidence of a good backup."""
    try:
        descriptor = _open_regular(path, os.O_RDONLY)
        with os.fdopen(descriptor, 'rb') as stream:
            raw = stream.read(2049)
        if len(raw) > 2048:
            return None
        data = json.loads(raw)
        if (not isinstance(data, dict) or set(data) != _KEYS
                or type(data['version']) is not int or data['version'] != 1
                or data['sourceHash'] != source_hash
                or not isinstance(data['filename'], str) or not _NAME.fullmatch(data['filename'])
                or path.name != data['filename'] + '.json'
                or not isinstance(data['sha256'], str) or not _HASH.fullmatch(data['sha256'])
                or not isinstance(data['createdAt'], str)):
            return None
        created = datetime.fromisoformat(data['createdAt'])
        if created.tzinfo is None or created.utcoffset() != timedelta(0) or created > now:
            return None
        snapshot = directory / data['filename']
        if _digest(snapshot) != data['sha256']:
            return None
        manage.check_database(snapshot)
        return data, created
    except (BackupScheduleError, manage.ManagementError, OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return None


def _save_receipt(path, data):
    # Exclusive creation forbids replacement. A process crash during this write
    # leaves invalid/absent metadata, forcing verification/new backup next time.
    descriptor = _open_regular(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=True, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def backup_if_due(database, directory=None, interval_hours=24, *, clock=None, lock_timeout=5):
    """Return created/not-due only for a newly validated private snapshot.

    Intervals use UTC instants; this is deliberately independent of local DST.
    The caller must arrange future invocations using their operating system.
    """
    try:
        if (type(interval_hours) not in (int, float) or not math.isfinite(interval_hours)
                or not 0 < interval_hours <= 24 * 366
                or type(lock_timeout) not in (int, float) or not math.isfinite(lock_timeout)
                or not 0 <= lock_timeout <= 30):
            raise BackupScheduleError('Χρειάζεται θετικό διάστημα έως 366 ημέρες και αναμονή κλειδώματος έως 30 δευτερόλεπτα.')
        source = _safe_path(database)
        if source.is_relative_to(manage.ROOT):
            raise BackupScheduleError('Η βάση και τα αντίγραφα πρέπει να βρίσκονται έξω από τον φάκελο κώδικα.')
        manage.check_database(source)
        folder = _private_directory(directory or manage.ROOT.parent / 'academy-backups' / 'scheduled')
        source_hash = hashlib.sha256(str(source).encode('utf-8')).hexdigest()
        interval = timedelta(hours=interval_hours)
        with _exclusive(folder, lock_timeout):
            # Capture time after acquiring the lock. Otherwise an invocation
            # waiting behind another backup could see that valid receipt as
            # being from its "future" and create an unnecessary second copy.
            now = (clock or (lambda: datetime.now(timezone.utc)))()
            if not isinstance(now, datetime) or now.tzinfo is None:
                raise BackupScheduleError('Ο χρόνος ελέγχου χρειάζεται ρητή ζώνη ώρας.')
            now = now.astimezone(timezone.utc)
            latest = None
            ignored = 0
            for path in folder.glob('academy-scheduled-*.sqlite3.json'):
                item = _receipt(path, folder, source_hash, now)
                if item is None:
                    ignored += 1
                elif latest is None or item[1] > latest[1]:
                    latest = item
            if latest and latest[1] + interval > now:
                data, created = latest
                return {'status': 'not-due', 'backup': str(folder / data['filename']),
                        'createdAt': data['createdAt'], 'nextDueAt': (created + interval).isoformat(),
                        'sha256': data['sha256'], 'unverifiedReceipts': ignored, 'notice': NOTICE}
            stamp = now.strftime('%Y%m%dT%H%M%SZ')
            filename = f'academy-scheduled-{stamp}-{secrets.token_hex(12)}.sqlite3'
            destination = folder / filename
            # copy_database resolves its destination. Reject a pre-existing
            # alias first so that resolution cannot redirect a new file.
            _safe_path(destination)
            manage.copy_database(source, destination)
            digest = _digest(destination)
            manage.check_database(destination)
            data = {'version': 1, 'filename': filename, 'sha256': digest,
                    'createdAt': now.isoformat(), 'sourceHash': source_hash}
            _save_receipt(folder / (filename + '.json'), data)
            return {'status': 'created', 'backup': str(destination), 'createdAt': data['createdAt'],
                    'nextDueAt': (now + interval).isoformat(), 'sha256': digest,
                    'unverifiedReceipts': ignored, 'notice': NOTICE}
    except BackupScheduleError:
        raise
    except (manage.ManagementError, OSError, ValueError, sqlite3.Error):
        raise BackupScheduleError('Δεν επιβεβαιώθηκε ασφαλές αντίγραφο. Έλεγξε τη βάση, τα δικαιώματα και τον διαθέσιμο χώρο.') from None
