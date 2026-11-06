"""Operator tools: real SQLite WAL snapshots and non-destructive restores."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from manage import ManagementError, ROOT, check_database, copy_database, main, set_admin_role


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.database = self.directory / 'source.sqlite3'
        self.connection = sqlite3.connect(self.database)
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.executescript('''
            CREATE TABLE users(id TEXT PRIMARY KEY, email TEXT UNIQUE, role TEXT NOT NULL DEFAULT 'student');
            CREATE TABLE sessions(id TEXT);
            CREATE TABLE profiles(id TEXT);
            CREATE TABLE completions(id TEXT);
            INSERT INTO users VALUES('one','owner@example.test','student');
        ''')
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def test_backup_includes_committed_wal_and_restore_is_new_file(self):
        self.connection.execute("INSERT INTO completions VALUES('completed-in-wal')")
        self.connection.commit()
        backup = self.directory / 'backup.sqlite3'
        copy_database(self.database, backup)
        check_database(backup)
        self.connection.execute("INSERT INTO completions VALUES('after-backup')")
        self.connection.commit()
        restored = self.directory / 'restored.sqlite3'
        copy_database(backup, restored)
        with sqlite3.connect(restored) as connection:
            self.assertEqual(connection.execute('SELECT id FROM completions').fetchall(), [('completed-in-wal',)])
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM completions').fetchone()[0], 2)
        self.assertFalse(Path(str(backup) + '-wal').exists())

    def test_existing_destinations_and_repository_paths_are_rejected(self):
        existing = self.directory / 'existing.sqlite3'
        existing.write_bytes(b'preserve-this-file')
        with self.assertRaises(ManagementError):
            copy_database(self.database, existing)
        self.assertEqual(existing.read_bytes(), b'preserve-this-file')
        with self.assertRaises(ManagementError):
            copy_database(self.database, ROOT / 'test-unwanted-backup.sqlite3')
        self.assertFalse((ROOT / 'test-unwanted-backup.sqlite3').exists())

    def test_corrupt_and_foreign_sources_never_create_a_backup(self):
        corrupt = self.directory / 'bad.sqlite3'
        corrupt.write_bytes(b'not a database')
        destination = self.directory / 'output.sqlite3'
        with self.assertRaises(sqlite3.Error):
            copy_database(corrupt, destination)
        self.assertFalse(destination.exists())
        foreign = self.directory / 'foreign.sqlite3'
        with sqlite3.connect(foreign) as connection:
            connection.execute('CREATE TABLE unrelated(value TEXT)')
        with self.assertRaises(ManagementError):
            copy_database(foreign, destination)
        self.assertFalse(destination.exists())

    def test_admin_changes_only_an_existing_account(self):
        set_admin_role(self.database, ' OWNER@example.test ', True)
        self.assertEqual(self.connection.execute("SELECT role FROM users WHERE id='one'").fetchone()[0], 'admin')
        set_admin_role(self.database, 'owner@example.test', False)
        self.assertEqual(self.connection.execute("SELECT role FROM users WHERE id='one'").fetchone()[0], 'student')
        with self.assertRaises(ManagementError):
            set_admin_role(self.database, 'missing@example.test', True)
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM users').fetchone()[0], 1)

    def test_cli_restore_does_not_replace_active_or_existing_databases(self):
        self.assertEqual(main(['backup', '--db', str(self.database), '--output', str(self.directory / 'cli-backup.sqlite3')]), 0)
        self.assertEqual(main(['restore', str(self.directory / 'cli-backup.sqlite3'), '--output', str(self.database)]), 1)
        self.assertEqual(main(['check', '--db', str(self.database)]), 0)
