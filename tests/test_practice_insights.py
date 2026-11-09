"""Retained repeat results drive private suggestions without new tracking."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from app_errors import ServiceError
from practice_insights import PracticeInsightsService
from server import create_server
from test_server import Client
from test_wsgi import WSGIClient
from wsgi import create_application


class PracticeInsightsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'insights.sqlite3'
        self.now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        self.course = {'lessons': [
            {'id': 'first', 'exercises': [{'id': 'shared'}, {'id': 'other'}]},
            {'id': 'second', 'exercises': [{'id': 'shared'}]},
            {'id': 'third', 'exercises': [{'id': 'last'}]},
            {'id': 'locked', 'exercises': [{'id': 'locked-question'}]},
        ]}
        self.sequence = 0
        with self.db() as db:
            db.executescript('''
                CREATE TABLE users(id TEXT PRIMARY KEY);
                INSERT INTO users VALUES('alice'),('bob');
                CREATE TABLE completions(user_id TEXT,lesson_id TEXT,xp INTEGER,completed_at TEXT,
                    PRIMARY KEY(user_id,lesson_id));
                CREATE TABLE review_cards(user_id TEXT,lesson_id TEXT,exercise_id TEXT,
                    PRIMARY KEY(user_id,lesson_id,exercise_id));
                CREATE TABLE review_attempts(user_id TEXT,attempt_id TEXT,lesson_id TEXT,
                    exercise_id TEXT,request_hash TEXT,result TEXT,created_at TEXT,
                    PRIMARY KEY(user_id,attempt_id));
            ''')
        self.service = self.make_service()

    def db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def make_service(self, limit=1000):
        return PracticeInsightsService(self.db, lambda db=None: deepcopy(self.course),
                                       attempt_limit=limit, clock=lambda: self.now)

    def learn(self, lesson_id, user='alice'):
        lesson = next(item for item in self.course['lessons'] if item['id'] == lesson_id)
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO completions VALUES(?,?,?,?)',
                       (user, lesson_id, 30, self.now.isoformat()))
            for exercise in lesson['exercises']:
                db.execute('INSERT OR IGNORE INTO review_cards VALUES(?,?,?)',
                           (user, lesson_id, exercise['id']))

    def attempt(self, lesson, exercise, correct=False, *, user='alice', at=None,
                result=None, attempt_id=None):
        self.sequence += 1
        at = self.now - timedelta(minutes=10 - self.sequence) if at is None else at
        if isinstance(at, datetime):
            at = at.isoformat()
        encoded = json.dumps({'correct': correct, 'expectedAnswer': 'private-result-detail'})
        with self.db() as db:
            db.execute('INSERT INTO review_attempts VALUES(?,?,?,?,?,?,?)',
                       (user, attempt_id or f'attempt-{self.sequence}', lesson, exercise,
                        'private-request-fingerprint', encoded if result is None else result, at))

    def test_existing_user_required_and_empty_history_is_not_a_mastery_claim(self):
        for value in (None, [], False, '', 'missing'):
            with self.subTest(user=value), self.assertRaises(ServiceError) as caught:
                self.service.get(value)
            self.assertEqual(caught.exception.status, 401)
        self.learn('first')
        result = self.service.get('alice')
        self.assertEqual(result['items'], [])
        self.assertEqual(result['summary'], {'needsPracticeCount': 0, 'reviewedExerciseCount': 0})
        self.assertEqual(result['scope'], 'retained-review-history')
        self.assertIn('Δεν περιλαμβάνονται λάθη πρώτης μελέτης', result['notice'])

    def test_latest_wrong_then_correct_and_new_wrong_change_only_suggestions(self):
        self.learn('first')
        self.attempt('first', 'shared', False)
        self.assertEqual(self.service.get('alice')['summary']['needsPracticeCount'], 1)
        self.attempt('first', 'shared', True)
        self.assertEqual(self.service.get('alice')['items'], [])
        self.assertEqual(self.service.get('alice')['summary']['reviewedExerciseCount'], 1)
        self.attempt('first', 'shared', False)
        result = self.service.get('alice')
        self.assertEqual([(item['lessonId'], item['exerciseId']) for item in result['items']],
                         [('first', 'shared')])

    def test_user_and_composite_exercise_key_isolation(self):
        self.learn('first')
        self.learn('second')
        self.learn('first', 'bob')
        self.attempt('first', 'shared', False)
        self.attempt('second', 'shared', True)
        self.attempt('first', 'shared', True, user='bob')
        alice = self.service.get('alice')
        bob = self.service.get('bob')
        self.assertEqual(alice['summary'], {'needsPracticeCount': 1, 'reviewedExerciseCount': 2})
        self.assertEqual(alice['items'][0]['lessonId'], 'first')
        self.assertEqual(bob['summary'], {'needsPracticeCount': 0, 'reviewedExerciseCount': 1})
        self.assertEqual(bob['items'], [])

    def test_removed_unlearned_and_non_reviewable_exercises_are_excluded(self):
        self.learn('first')
        self.learn('second')
        self.attempt('first', 'shared', False)
        self.attempt('first', 'other', False)
        self.attempt('second', 'shared', False)
        self.attempt('locked', 'locked-question', False)
        self.attempt('first', 'unknown-question', False)
        # A deleted review card is not actionable. A removed published exercise
        # and an archived lesson must not leak back via retained attempt rows.
        with self.db() as db:
            db.execute("DELETE FROM review_cards WHERE user_id='alice' AND exercise_id='other'")
        self.course['lessons'][0]['exercises'] = [{'id': 'other'}]
        self.course['lessons'] = [item for item in self.course['lessons'] if item['id'] != 'second']
        self.assertEqual(self.service.get('alice')['summary'],
                         {'needsPracticeCount': 0, 'reviewedExerciseCount': 0})

    def test_invalid_result_or_timestamp_does_not_override_last_valid_answer(self):
        self.learn('first')
        self.attempt('first', 'shared', False, at=self.now - timedelta(hours=1))
        for index, result in enumerate(('broken-json', '[]', '{}', '{"correct": 0}', '{"correct": "false"}')):
            self.attempt('first', 'shared', at=self.now - timedelta(minutes=10 - index), result=result)
        self.attempt('first', 'shared', True, at='not-a-time')
        self.attempt('first', 'shared', True, at=self.now + timedelta(days=1))
        result = self.service.get('alice')
        self.assertEqual(result['summary']['needsPracticeCount'], 1)
        self.assertEqual(result['items'][0]['lastAnsweredAt'],
                         (self.now - timedelta(hours=1)).isoformat())

    def test_timestamp_offsets_and_rowid_ties_decide_actual_latest_result(self):
        self.learn('first')
        self.attempt('first', 'shared', False, at='2026-11-09T11:00:00+01:00')
        self.attempt('first', 'shared', True, at='2026-11-09T10:30:00+00:00')
        self.assertEqual(self.service.get('alice')['items'], [])
        self.attempt('first', 'shared', False, at='2026-11-09T10:30:00+00:00', attempt_id='aaa-later')
        self.assertEqual(self.service.get('alice')['summary']['needsPracticeCount'], 1)
        self.attempt('first', 'shared', True, at='2026-11-09T10:30:00+00:00', attempt_id='000-last')
        self.assertEqual(self.service.get('alice')['items'], [])

    def test_history_limit_applies_to_retained_attempts_without_inventing_mastery(self):
        for lesson in ('first', 'second', 'third'):
            self.learn(lesson)
        self.attempt('first', 'shared', False)
        self.attempt('second', 'shared', True)
        self.attempt('third', 'last', False)
        result = self.make_service(limit=2).get('alice')
        self.assertEqual(result['summary'], {'needsPracticeCount': 1, 'reviewedExerciseCount': 2})
        self.assertEqual(result['items'][0]['lessonId'], 'third')
        self.assertEqual(result['scope'], 'retained-review-history')

    def test_repeated_reads_change_no_records_and_expose_no_result_details(self):
        self.learn('first')
        self.attempt('first', 'shared', False)
        with self.db() as db:
            before = list(db.iterdump())
        first = self.service.get('alice')
        self.assertEqual(first, self.service.get('alice'))
        with self.db() as db:
            self.assertEqual(before, list(db.iterdump()))
        serialized = json.dumps(first)
        for private in ('private-result-detail', 'private-request-fingerprint',
                        'expectedAnswer', 'request_hash', 'attemptId', 'user_id'):
            self.assertNotIn(private, serialized)
        self.assertEqual(set(first['items'][0]), {'lessonId', 'exerciseId', 'lastAnsweredAt'})


class PracticeInsightsHTTPTests(unittest.TestCase):
    def assert_flow(self, state, alice, bob, anonymous):
        status, _, headers = anonymous.request('GET', '/api/practice-insights')
        self.assertEqual(status, 401)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(alice.register('alice-insights@example.test')[0], 200)
        self.assertEqual(bob.register('bob-insights@example.test')[0], 200)
        lesson = state.lessons()[0]
        exercise = lesson['exercises'][0]
        completed = alice.mutate('POST', '/api/progress', {
            'lessonId': lesson['id'], 'answers': [item['answer'] for item in lesson['exercises']]})
        self.assertEqual(completed[0], 200)
        wrong = {'lessonId': lesson['id'], 'exerciseId': exercise['id'],
                 'answer': 'definitely incorrect', 'attemptId': 'insights-wrong-01'}
        self.assertEqual(alice.mutate('POST', '/api/review', wrong)[0], 200)
        status, result, headers = alice.request('GET', '/api/practice-insights?userId=bob')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(result['summary'], {'needsPracticeCount': 1, 'reviewedExerciseCount': 1})
        self.assertEqual((result['items'][0]['lessonId'], result['items'][0]['exerciseId']),
                         (lesson['id'], exercise['id']))
        self.assertTrue(alice.mutate('POST', '/api/review', wrong)[1]['duplicate'])
        self.assertEqual(alice.request('GET', '/api/practice-insights')[1], result)
        self.assertEqual(bob.request('GET', '/api/practice-insights?userId=alice')[1]['items'], [])
        correct = {**wrong, 'answer': exercise['answer'], 'attemptId': 'insights-correct-02'}
        self.assertEqual(alice.mutate('POST', '/api/review', correct)[0], 200)
        self.assertEqual(alice.request('GET', '/api/practice-insights')[1]['items'], [])
        self.assertEqual(alice.request('GET', '/api/session')[1]['progress']['xp'], lesson['xp'])

    def test_real_review_history_and_authenticated_insights_through_wsgi(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'ACADEMY_MAIL_MODE': 'disabled', 'ACADEMY_AI_PROVIDER': 'disabled'}):
            app = create_application(Path(directory) / 'insights.sqlite3', environ={})
            try:
                self.assert_flow(app.state, WSGIClient(app), WSGIClient(app), WSGIClient(app))
            finally:
                app.close()

    def test_same_privacy_and_idempotent_flow_through_real_http(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'ACADEMY_MAIL_MODE': 'disabled', 'ACADEMY_AI_PROVIDER': 'disabled'}):
            server = create_server('127.0.0.1', 0, Path(directory) / 'insights.sqlite3')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                self.assert_flow(server.state, Client(server.server_port), Client(server.server_port),
                                 Client(server.server_port))
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
