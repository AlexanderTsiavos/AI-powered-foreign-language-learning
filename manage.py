#!/usr/bin/env python3
"""Local operator tools. Never exposed through the application HTTP API."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parent
REQUIRED_TABLES = {'users', 'sessions', 'profiles', 'completions'}


class ManagementError(Exception):
    pass


def default_database() -> Path:
    return Path(os.environ.get('ACADEMY_DB', str(ROOT.parent / 'academy-data' / 'academy.sqlite3'))).expanduser().resolve()


def athens_time(timestamp):
    try:
        zone = ZoneInfo('Europe/Athens')
    except ZoneInfoNotFoundError:
        with (ROOT / 'data' / 'timezones' / 'Europe' / 'Athens').open('rb') as source:
            zone = ZoneInfo.from_file(source, key='Europe/Athens')
    return datetime.fromisoformat(timestamp).astimezone(zone).strftime('%d/%m/%Y %H:%M')


def read_connection(path: Path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ManagementError('Η βάση δεν βρέθηκε. Εκκίνησε πρώτα την εφαρμογή ή έλεγξε τη διαδρομή.')
    return sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)


def validate_connection(connection):
    if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise ManagementError('Ο έλεγχος ακεραιότητας της βάσης απέτυχε.')
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not REQUIRED_TABLES.issubset(tables):
        raise ManagementError('Το αρχείο δεν είναι αναγνωρίσιμη βάση της εφαρμογής.')


def check_database(path: Path):
    connection = read_connection(path)
    try:
        validate_connection(connection)
    finally:
        connection.close()


def copy_database(source: Path, destination: Path):
    """Make a consistent SQLite snapshot, including committed WAL transactions.

    Both backup and restore create a NEW file. Existing files are never replaced.
    """
    source = Path(source).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if destination.is_relative_to(ROOT):
        raise ManagementError('Αποθήκευσε τη βάση ή το αντίγραφο έξω από τον φάκελο του κώδικα.')
    if source == destination:
        raise ManagementError('Το αντίγραφο χρειάζεται διαφορετικό αρχείο.')
    reader = read_connection(source)
    created = False
    writer = None
    try:
        validate_connection(reader)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise ManagementError('Το αρχείο προορισμού υπάρχει ήδη. Επίλεξε νέο όνομα· δεν αντικαταστάθηκε τίποτα.') from exc
        os.close(descriptor)
        created = True
        writer = sqlite3.connect(destination, timeout=5)
        deadline = time.monotonic() + 30

        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise ManagementError('Το αντίγραφο δεν ολοκληρώθηκε εγκαίρως. Δοκίμασε όταν μειωθεί η δραστηριότητα.')

        reader.backup(writer, pages=100, progress=progress, sleep=0.05)
        writer.execute('PRAGMA journal_mode=DELETE')
        writer.commit()
        validate_connection(writer)
        writer.close()
        writer = None
        destination.chmod(0o600)
        return destination
    except Exception:
        if writer is not None:
            writer.close()
        if created:
            for item in (destination, Path(str(destination) + '-wal'), Path(str(destination) + '-shm'), Path(str(destination) + '-journal')):
                item.unlink(missing_ok=True)
        raise
    finally:
        reader.close()


def set_admin_role(database: Path, email: str, enabled: bool):
    database = Path(database).expanduser().resolve()
    check_database(database)
    email = email.strip().casefold()
    if not email or len(email) > 254:
        raise ManagementError('Συμπλήρωσε το email του υπάρχοντος λογαριασμού.')
    connection = sqlite3.connect(database.as_uri() + '?mode=rw', uri=True, timeout=5)
    try:
        columns = {row[1] for row in connection.execute('PRAGMA table_info(users)')}
        if 'role' not in columns:
            raise ManagementError('Εκκίνησε πρώτα τη νέα έκδοση της εφαρμογής για ενημέρωση της βάσης.')
        with connection:
            result = connection.execute('UPDATE users SET role=? WHERE email=?', ('admin' if enabled else 'student', email))
            if result.rowcount != 1:
                raise ManagementError('Δεν βρέθηκε υπάρχων λογαριασμός με αυτό το email. Δεν δημιουργήθηκε λογαριασμός.')
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Τοπική διαχείριση AI Language Academy')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('check', 'backup', 'grant-admin', 'revoke-admin'):
        command = commands.add_parser(name)
        command.add_argument('--db', type=Path, default=default_database())
        if name in ('grant-admin', 'revoke-admin'):
            command.add_argument('email')
        if name == 'backup':
            command.add_argument('--output', type=Path)
    restore = commands.add_parser('restore')
    restore.add_argument('backup', type=Path)
    restore.add_argument('--output', type=Path, required=True)
    scheduled = commands.add_parser('backup-if-due')
    scheduled.add_argument('--db', type=Path, default=default_database())
    scheduled.add_argument('--directory', type=Path)
    scheduled.add_argument('--interval-hours', type=float, default=24)
    scheduled.add_argument('--json', action='store_true')
    doctor = commands.add_parser('doctor')
    doctor.add_argument('--db', type=Path, default=default_database())
    doctor.add_argument('--production', action='store_true')
    doctor.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'doctor':
            from readiness import diagnose
            report = diagnose(database=args.db, production=args.production)
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                for check in report['checks']:
                    print(f"{check['status'].upper()} — {check['message']}")
                print(report['notice'])
            return 0 if report['checksPassed'] else 1
        elif args.command == 'backup-if-due':
            from backup_schedule import backup_if_due, BackupScheduleError
            try:
                report = backup_if_due(args.db, args.directory, args.interval_hours)
            except BackupScheduleError as error:
                raise ManagementError(str(error)) from None
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print('Δημιουργήθηκε ελεγμένο αντίγραφο.' if report['status'] == 'created'
                      else 'Δεν χρειάζεται νέο αντίγραφο ακόμη· το τελευταίο επαληθεύθηκε.')
                print(f"Αντίγραφο: {report['backup']}")
                print(f"Νέο αντίγραφο μπορεί να δημιουργηθεί μετά: {athens_time(report['nextDueAt'])} (ώρα Ελλάδας).")
                print(report['notice'])
        elif args.command == 'check':
            check_database(args.db)
            print('Ο έλεγχος ακεραιότητας της βάσης πέρασε.')
        elif args.command == 'backup':
            # UTC is explicit in the technical filename; no scheduled action occurs.
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            output = args.output or ROOT.parent / 'academy-backups' / f'academy-{stamp}-{secrets.token_hex(3)}.sqlite3'
            result = copy_database(args.db, output)
            print(f'Δημιουργήθηκε ελεγμένο αντίγραφο: {result}')
            print('Περιέχει προσωπικά δεδομένα. Κράτησέ το ιδιωτικό και έξω από το GitHub.')
        elif args.command == 'restore':
            result = copy_database(args.backup, args.output)
            print(f'Το αντίγραφο επαναφέρθηκε σε ΝΕΑ βάση: {result}')
            print('Η ενεργή βάση δεν άλλαξε. Σταμάτησε τον server και όρισε ACADEMY_DB στη νέα διαδρομή πριν τον ξανανοίξεις.')
        else:
            set_admin_role(args.db, args.email, args.command == 'grant-admin')
            print('Τα δικαιώματα του υπάρχοντος λογαριασμού ενημερώθηκαν. Ανανέωσε τη σελίδα της εφαρμογής.')
        return 0
    except (ManagementError, sqlite3.Error, OSError) as error:
        message = str(error) if isinstance(error, ManagementError) else 'Η ενέργεια απέτυχε. Έλεγξε το αρχείο, τα δικαιώματα πρόσβασης και τον διαθέσιμο χώρο.'
        print(message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
