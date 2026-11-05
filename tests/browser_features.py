"""Account, varied exercise and teacher UI acceptance checks.

The Ollama HTTP fixture is deterministic test data, NOT a real AI model.
Run: python3 tests/browser_features.py (optional Playwright/Chromium required).
"""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import create_server
from teacher import TeacherService
from playwright.sync_api import sync_playwright, expect
from browser_learning import mutate, session


class LocalModelFixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.respond({'models': [{'name': 'test-model'}]})

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path == '/api/show':
            self.respond({'model_info': {'general.architecture': 'test'}, 'details': {'parameter_size': '1B'}})
        elif self.path == '/api/chat':
            result = {'message': 'Το σωστό είναι: He goes to school. Με he προσθέτουμε -s.',
                      'boardAction': {'type': 'show_card', 'lessonId': 'first-words', 'cardIndex': 0}}
            self.respond({'message': {'role': 'assistant', 'content': json.dumps(result)}})
        else:
            self.send_error(404)


def run():
    artifacts = Path('/tmp/academy-features-artifacts')
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as p:
        server = create_server('127.0.0.1', 0, Path(directory) / 'features.sqlite3')
        server.teacher = TeacherService('disabled')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'), args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        other = browser.new_context()
        url = f'http://127.0.0.1:{server.server_port}'
        errors = []
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(error.stack))
        model = None
        model_thread = None
        try:
            user = {'name': 'Μαρία', 'email': 'features@example.test', 'password': 'old unique password 42', 'ageConfirmed': True}
            mutate(context, url, 'POST', '/api/register', user)
            mutate(context, url, 'PUT', '/api/profile', {'goalMinutes': 10, 'reason': 'travel', 'nativeLanguage': 'el', 'targetLanguage': 'en', 'ageGroup': 'adult'})
            mutate(other, url, 'POST', '/api/login', {'email': user['email'], 'password': user['password']})
            page.goto(url + '/#settings')
            expect(page.locator('#account-profile-form')).to_be_visible()
            page.locator('#account-profile-form [name=name]').fill('Μαρία Παπαδοπούλου')
            page.locator('#account-profile-form button[type=submit]').click()
            expect(page.locator('#account-profile-form [name=name]')).to_have_value('Μαρία Παπαδοπούλου')
            expect(page.locator('#account-sessions')).to_be_visible()
            page.locator('[data-action=revoke-other-sessions]').click()
            expect(page.locator('#account-sessions [data-action=revoke-session]')).to_have_count(0)
            assert session(other, url)['user'] is None
            mutate(other, url, 'POST', '/api/login', {'email': user['email'], 'password': user['password']})
            page.locator('#password-form [name=currentPassword]').fill(user['password'])
            page.locator('#password-form [name=newPassword]').fill('new unique password 42')
            page.locator('#password-form [name=confirmPassword]').fill('new unique password 42')
            page.locator('#password-form button[type=submit]').click()
            expect(page.locator('#toast')).to_contain_text('κωδικός')
            assert session(other, url)['user'] is None
            assert session(context, url)['user']['name'] == 'Μαρία Παπαδοπούλου'
            # A second mutation proves the password change also refreshed CSRF.
            mutate(context, url, 'PUT', '/api/profile', {'goalMinutes': 15, 'reason': 'travel'})
            print('PASS name update, sessions revocation, password change, other-device logout and refreshed CSRF', flush=True)

            original_status = server.teacher.status
            def delayed_status():
                time.sleep(0.5)
                return original_status()
            server.teacher.status = delayed_status
            page.goto(url + '/#teacher')
            expect(page.locator('#teacher-status')).to_contain_text('Ελέγχουμε')
            page.locator('[data-action=teacher-stop]').click()
            expect(page.locator('[data-action=teacher-retry-status]')).to_be_visible()
            server.teacher.status = original_status
            page.locator('[data-action=teacher-retry-status]').click()
            expect(page.locator('#teacher-status')).to_be_visible()
            assert context.request.get(url + '/api/teacher/status').json()['available'] is False
            expect(page.locator('#teacher-form button[type=submit]')).to_be_disabled()
            print('PASS disabled teacher clearly unavailable; no simulated conversation', flush=True)

            curriculum = context.request.get(url + '/api/curriculum').json()
            # Prepare the prefix using the real API so new exercise types unlock.
            for lesson in curriculum['lessons'][:3]:
                mutate(context, url, 'POST', '/api/progress', {'lessonId': lesson['id'], 'answers': [e['answer'] for e in lesson['exercises']]})
            page.reload()  # API fixture setup changed progress outside this tab.
            covered = set()
            for lesson in curriculum['lessons'][3:]:
                page.goto(url + '/#lesson/' + lesson['id'])
                expect(page.locator('.whiteboard')).to_be_visible()
                for _ in lesson['cards']:
                    page.locator('[data-action=next-card]').click()
                for exercise in lesson['exercises']:
                    kind = exercise['type']
                    covered.add(kind)
                    if kind == 'multiple-choice':
                        page.locator('[data-exercise-choice]').filter(has_text=re.compile('^' + re.escape(exercise['answer']) + '$')).first.click()
                        expect(page.locator('#answer')).to_have_value(exercise['answer'])
                    elif kind == 'word-order':
                        for token in exercise['answer'].split():
                            page.locator('[data-exercise-token]:not(:disabled)').filter(has_text=re.compile('^' + re.escape(token) + '$')).first.click()
                        expect(page.locator('#answer')).to_have_value(exercise['answer'])
                    elif kind == 'reading':
                        expect(page.locator('.reading-stimulus')).to_contain_text(exercise['passage'])
                        page.locator('#answer').fill(exercise['answer'])
                    elif kind == 'listening':
                        page.locator('.listening-stimulus summary').click()
                        expect(page.locator('.listening-stimulus')).to_contain_text(exercise['audioText'])
                        page.locator('#answer').fill(exercise['answer'])
                    else:
                        page.locator('#answer').fill(exercise['answer'])
                    page.locator('#exercise-form button[type=submit]').click()
                    expect(page.locator('#answer-feedback')).to_contain_text('Σωστά!')
                    page.locator('#exercise-form button[type=submit]').click()
                expect(page.locator('.completion-page')).to_be_visible()
            assert {'multiple-choice', 'word-order', 'reading', 'listening'}.issubset(covered)
            assert len(session(context, url)['progress']['completedLessons']) == 9
            print('PASS all six new lessons and four new exercise types through the real UI', flush=True)

            model = ThreadingHTTPServer(('127.0.0.1', 11434), LocalModelFixture)
            model_thread = threading.Thread(target=model.serve_forever, daemon=True)
            model_thread.start()
            server.teacher = TeacherService('ollama', 'test-model')
            probe = context.request.get(url + '/api/teacher/status').json()
            assert probe['available'], probe
            page.goto(url + '/#teacher')
            expect(page.locator('#teacher-status.available')).to_be_visible()
            page.locator('#teacher-consent').check()
            page.locator('#teacher-message').fill('Γιατί είναι λάθος το He go to school;')
            page.locator('#teacher-form button[type=submit]').click()
            expect(page.locator('#teacher-chat')).to_contain_text('He goes to school')
            expect(page.locator('#teacher-board')).to_contain_text('car')
            page.screenshot(path=str(artifacts / 'teacher-test-fixture.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.goto(url + '/#settings')
            expect(page.locator('#account-profile-form')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors, errors
            print('PASS teacher UI + local HTTP fixture + controlled board, mobile settings/chat, no runtime errors (NOT a real model)', flush=True)
        finally:
            browser.close()
            if model:
                model.shutdown()
                model.server_close()
                model_thread.join()
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    run()
