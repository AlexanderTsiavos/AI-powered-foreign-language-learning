"""Real snapshots and bounded operator jobs, using temporary private data only."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from backup_schedule import backup_if_due, BackupScheduleError, _exclusive
import manage


class ScheduledBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.sqlite3'
        self.directory = self.root / 'scheduled'
        self.now = datetime(2026, 11, 9, 10, tzinfo=timezone.utc)
        self.connection = sqlite3.connect(self.source)
        self.addCleanup(self.connection.close)
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.executescript('''
            CREATE TABLE users(id TEXT PRIMARY KEY, email TEXT);
            CREATE TABLE sessions(id TEXT);
            CREATE TABLE profiles(id TEXT);
            CREATE TABLE completions(id TEXT);
            INSERT INTO users VALUES('one','private@example.test');
            INSERT INTO completions VALUES('committed-in-wal');
        ''')
        self.connection.commit()

    def run_backup(self, **kwargs):
        return backup_if_due(self.source, self.directory, clock=lambda: self.now, **kwargs)

    def receipt(self, report):
        return Path(report['backup'] + '.json')

    def cli(self, directory=None):
        return [sys.executable, str(manage.ROOT / 'manage.py'), 'backup-if-due',
                '--db', str(self.source), '--directory', str(directory or self.directory), '--json']

    def test_snapshot_includes_wal_and_not_due_verifies_existing_snapshot_without_replacing_it(self):
        first = self.run_backup()
        self.assertEqual(first['status'], 'created')
        original = Path(first['backup']).read_bytes()
        self.connection.execute("INSERT INTO completions VALUES('after-backup')")
        self.connection.commit()
        self.now += timedelta(hours=23, minutes=59)
        again = self.run_backup()
        self.assertEqual(again['status'], 'not-due')
        self.assertEqual(again['backup'], first['backup'])
        self.assertEqual(Path(first['backup']).read_bytes(), original)
        with sqlite3.connect(first['backup']) as snapshot:
            self.assertEqual(snapshot.execute('SELECT id FROM completions').fetchall(), [('committed-in-wal',)])
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM completions').fetchone()[0], 2)
        self.assertEqual(again['sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual(len(list(self.directory.glob('*.sqlite3'))), 1)

    def test_exact_due_boundary_creates_new_snapshot_and_preserves_previous_files(self):
        first = self.run_backup()
        self.now += timedelta(hours=24)
        self.connection.execute("INSERT INTO completions VALUES('next-day')")
        self.connection.commit()
        second = self.run_backup()
        self.assertEqual(second['status'], 'created')
        self.assertNotEqual(first['backup'], second['backup'])
        self.assertTrue(Path(first['backup']).exists())
        self.assertTrue(self.receipt(first).exists())
        self.assertEqual(len(list(self.directory.glob('*.sqlite3'))), 2)
        with sqlite3.connect(second['backup']) as snapshot:
            self.assertEqual(snapshot.execute('SELECT COUNT(*) FROM completions').fetchone()[0], 2)

    def test_concurrent_processes_create_only_one_snapshot_and_receipt(self):
        def invoke(_):
            result = subprocess.run(self.cli(), capture_output=True, text=True, timeout=12)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        with ThreadPoolExecutor(max_workers=4) as pool:
            reports = list(pool.map(invoke, range(4)))
        self.assertEqual([item['status'] for item in reports].count('created'), 1)
        self.assertEqual([item['status'] for item in reports].count('not-due'), 3)
        self.assertEqual(len({item['backup'] for item in reports}), 1)
        self.assertEqual(len(list(self.directory.glob('*.sqlite3'))), 1)
        self.assertEqual(len(list(self.directory.glob('*.json'))), 1)

    def test_busy_lock_is_bounded_and_a_crashed_process_releases_it(self):
        self.directory.mkdir(mode=0o700)
        script = ("import os,sys; from pathlib import Path; from backup_schedule import _exclusive; "
                  "lock=_exclusive(Path(sys.argv[1]),1); lock.__enter__(); "
                  "print('locked',flush=True); sys.stdin.readline(); os._exit(0)")
        process = subprocess.Popen([sys.executable, '-c', script, str(self.directory)],
                                   cwd=manage.ROOT, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'locked')
            start = time.monotonic()
            with self.assertRaises(BackupScheduleError):
                self.run_backup(lock_timeout=0.05)
            self.assertLess(time.monotonic() - start, 1)
            process.stdin.write('exit\n')
            process.stdin.flush()
            self.assertEqual(process.wait(timeout=3), 0)
            self.assertEqual(self.run_backup()['status'], 'created')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def test_failed_copy_and_receipt_write_never_report_success_or_delete_orphan_snapshot(self):
        with patch('backup_schedule.manage.copy_database', side_effect=OSError('raw-password-and-row')):
            with self.assertRaises(BackupScheduleError) as error:
                self.run_backup()
        self.assertNotIn('raw-password', str(error.exception))
        self.assertEqual(list(self.directory.glob('*.json')), [])
        with patch('backup_schedule._save_receipt', side_effect=OSError('raw-token')):
            with self.assertRaises(BackupScheduleError) as error:
                self.run_backup()
        self.assertNotIn('raw-token', str(error.exception))
        orphan = list(self.directory.glob('*.sqlite3'))
        self.assertEqual(len(orphan), 1)
        self.assertEqual(list(self.directory.glob('*.json')), [])
        self.assertEqual(self.run_backup()['status'], 'created')
        self.assertTrue(orphan[0].exists())
        self.assertEqual(len(list(self.directory.glob('*.sqlite3'))), 2)

    def test_corrupt_snapshot_does_not_count_even_with_a_matching_modified_hash(self):
        first = self.run_backup()
        snapshot = Path(first['backup'])
        snapshot.write_bytes(b'not a SQLite backup')
        receipt = json.loads(self.receipt(first).read_text())
        receipt['sha256'] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        self.receipt(first).write_text(json.dumps(receipt))
        next_report = self.run_backup()
        self.assertEqual(next_report['status'], 'created')
        self.assertEqual(next_report['unverifiedReceipts'], 1)
        self.assertTrue(snapshot.exists())
        manage.check_database(Path(next_report['backup']))

    def test_untrusted_incomplete_future_and_traversal_receipts_never_skip_a_backup(self):
        variants = [lambda data: '{unfinished',
                    lambda data: ' ' * 2049,
                    lambda data: json.dumps({**data, 'createdAt': '2099-01-01T00:00:00+00:00'}),
                    lambda data: json.dumps({**data, 'filename': '../source.sqlite3'}),
                    lambda data: json.dumps({**data, 'version': True})]
        for index, mutate in enumerate(variants):
            with self.subTest(case=index):
                folder = self.root / ('variant-' + str(index))
                first = backup_if_due(self.source, folder, clock=lambda: self.now)
                path = self.receipt(first)
                data = json.loads(path.read_text())
                path.write_text(mutate(data))
                again = backup_if_due(self.source, folder, clock=lambda: self.now)
                self.assertEqual(again['status'], 'created')
                self.assertEqual(again['unverifiedReceipts'], 1)
        manage.check_database(self.source)

    def test_each_source_has_its_own_interval_even_when_directory_is_shared(self):
        first = self.run_backup()
        second_source = self.root / 'another.sqlite3'
        manage.copy_database(self.source, second_source)
        other = backup_if_due(second_source, self.directory, clock=lambda: self.now)
        self.assertEqual(other['status'], 'created')
        self.assertNotEqual(other['backup'], first['backup'])
        self.assertEqual(self.run_backup()['status'], 'not-due')

    def test_repository_and_symlink_paths_are_rejected_without_touching_targets(self):
        inside = manage.ROOT / 'never-create-scheduled-backups'
        self.assertFalse(inside.exists())
        with self.assertRaises(BackupScheduleError):
            backup_if_due(self.source, inside)
        self.assertFalse(inside.exists())
        target = self.root / 'private-target'
        target.mkdir(mode=0o700)
        alias = self.root / 'alias'
        alias.symlink_to(target, target_is_directory=True)
        with self.assertRaises(BackupScheduleError):
            backup_if_due(self.source, alias / 'nested')
        self.assertEqual(list(target.iterdir()), [])
        source_alias = self.root / 'source-alias.sqlite3'
        source_alias.symlink_to(self.source)
        with self.assertRaises(BackupScheduleError):
            backup_if_due(source_alias, self.directory)

    def test_existing_snapshot_name_cannot_overwrite_or_redirect_an_output(self):
        self.directory.mkdir(mode=0o700)
        filename = 'academy-scheduled-20261109T100000Z-' + '0' * 24 + '.sqlite3'
        destination = self.directory / filename
        outside = self.root / 'must-not-be-created.sqlite3'
        destination.symlink_to(outside)
        with patch('backup_schedule.secrets.token_hex', return_value='0' * 24):
            with self.assertRaises(BackupScheduleError):
                self.run_backup()
        self.assertFalse(outside.exists())
        self.assertTrue(destination.is_symlink())
        destination.unlink()
        destination.write_bytes(b'preserve existing snapshot')
        with patch('backup_schedule.secrets.token_hex', return_value='0' * 24):
            with self.assertRaises(BackupScheduleError):
                self.run_backup()
        self.assertEqual(destination.read_bytes(), b'preserve existing snapshot')

    @unittest.skipUnless(os.name == 'posix', 'POSIX permission bits')
    def test_backup_catalog_and_lock_are_private_and_nonprivate_folder_is_rejected(self):
        report = self.run_backup()
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        for path in [Path(report['backup']), self.receipt(report), self.directory / '.backup-schedule.lock']:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        public = self.root / 'public'
        public.mkdir(mode=0o755)
        public.chmod(0o755)  # A restrictive host umask may have removed these bits.
        with self.assertRaises(BackupScheduleError):
            backup_if_due(self.source, public)
        self.assertEqual(list(public.iterdir()), [])

    @unittest.skipUnless(os.name == 'posix', 'POSIX FIFO behavior')
    def test_forged_receipt_or_snapshot_fifo_never_blocks_the_cli(self):
        for kind in ('receipt', 'snapshot'):
            with self.subTest(kind=kind):
                folder = self.root / ('fifo-' + kind)
                first = backup_if_due(self.source, folder, clock=lambda: self.now)
                target = self.receipt(first) if kind == 'receipt' else Path(first['backup'])
                target.unlink()
                os.mkfifo(target, 0o600)
                result = subprocess.run(self.cli(folder), capture_output=True, text=True, timeout=3)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['status'], 'created')

    def test_cli_missing_invalid_or_private_errors_fail_without_false_success(self):
        result = subprocess.run(self.cli() + ['--interval-hours', '0'],
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.directory.exists())
        self.connection.close()
        self.source.write_bytes(b'private corrupt bytes')
        result = subprocess.run(self.cli(), capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('private corrupt bytes', result.stdout + result.stderr)
        self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
