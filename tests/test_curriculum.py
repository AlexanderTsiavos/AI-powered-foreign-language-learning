"""Content contracts and real HTTP completion of the shipped curriculum."""
import json
from pathlib import Path
import tempfile
import threading
import unittest

from server import ROOT, create_server, normalize_answer
from test_server import Client


class CurriculumTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.course = json.loads((ROOT / 'data/curriculum.json').read_text())

    def test_stable_introductory_identifiers_and_answers(self):
        expected = [
            ('first-words', 30, ['car', 'book', 'apple']),
            ('greetings', 30, ['hello', 'thank you', 'name']),
            ('simple-sentences', 40, ['am', 'an', 'a']),
        ]
        actual = [(x['id'], x['xp'], [e['answer'] for e in x['exercises']]) for x in self.course['lessons'][:3]]
        self.assertEqual(actual, expected, 'Existing saved progress must retain its meaning')

    def test_lesson_content_and_local_asset_paths(self):
        lessons = self.course['lessons']
        self.assertGreaterEqual(len(lessons), 9)
        self.assertEqual(len({x['id'] for x in lessons}), len(lessons))
        for lesson in lessons:
            with self.subTest(lesson=lesson['id']):
                self.assertTrue(lesson['title'].strip())
                self.assertTrue(lesson['objective'].strip())
                self.assertEqual(len(lesson['cards']), 3)
                self.assertEqual(len(lesson['exercises']), 3)
                self.assertGreater(lesson['xp'], 0)
                for card in lesson['cards']:
                    self.assertTrue(card['word'].strip())
                    self.assertTrue(card['translation'].strip())
                    if card.get('image'):
                        asset = (ROOT / 'web' / card['image'].lstrip('/')).resolve()
                        self.assertTrue(asset.is_relative_to((ROOT / 'web/assets').resolve()))
                        self.assertTrue(asset.is_file())

    def test_varied_exercises_have_valid_canonical_answers(self):
        seen = set()
        for lesson in self.course['lessons']:
            self.assertEqual(len({x['id'] for x in lesson['exercises']}), len(lesson['exercises']))
            for exercise in lesson['exercises']:
                with self.subTest(lesson=lesson['id'], exercise=exercise['id']):
                    kind = exercise['type']
                    seen.add(kind)
                    answer = exercise['answer']
                    self.assertIsInstance(answer, str)
                    self.assertIn(answer, exercise['acceptedAnswers'])
                    self.assertTrue(exercise['prompt'].strip())
                    self.assertTrue(exercise['explanation'].strip())
                    if kind == 'multiple-choice':
                        self.assertGreaterEqual(len(exercise['choices']), 2)
                        self.assertIn(answer, exercise['choices'])
                    elif kind == 'word-order':
                        self.assertCountEqual(normalize_answer(answer).split(), normalize_answer(' '.join(exercise['tokens'])).split())
                    elif kind == 'reading':
                        self.assertTrue(exercise['passage'].strip())
                    elif kind == 'listening':
                        self.assertTrue(exercise['audioText'].strip())
        self.assertTrue({'multiple-choice', 'word-order', 'reading', 'listening'}.issubset(seen))

    def test_all_shipped_lessons_complete_through_authenticated_http(self):
        with tempfile.TemporaryDirectory() as directory:
            server = create_server('127.0.0.1', 0, Path(directory) / 'curriculum.sqlite3')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = Client(server.server_port)
                self.assertEqual(client.register('course@example.test')[0], 200)
                total_xp = 0
                for lesson in self.course['lessons']:
                    answers = [x['answer'] for x in lesson['exercises']]
                    invalid = { 'lessonId': lesson['id'], 'answers': ['definitely incorrect'] + answers[1:] }
                    self.assertEqual(client.mutate('POST', '/api/progress', invalid)[0], 422)
                    status, result, _ = client.mutate('POST', '/api/progress', {'lessonId': lesson['id'], 'answers': answers})
                    self.assertEqual(status, 200, result)
                    total_xp += lesson['xp']
                    self.assertEqual(result['progress']['xp'], total_xp)
                review = client.request('GET', '/api/review')[1]
                self.assertEqual(len(review['items']), sum(len(x['exercises']) for x in self.course['lessons']))
                self.assertEqual(review['dueCount'], 0)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
