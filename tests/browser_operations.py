"""Real browser checks for assessments, support, notifications and content admin.

All accounts, role grants, publications and backups use a temporary database.
Requires the optional Playwright/Chromium testing tools, not an external service.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import create_server
from manage import copy_database, set_admin_role
from test_server import Client
from browser_learning import mutate, session
from playwright.sync_api import sync_playwright, expect


def run():
    artifacts = Path('/tmp/academy-operations-artifacts')
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as p:
        db_path = Path(directory) / 'operations.sqlite3'
        server = create_server('127.0.0.1', 0, db_path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'), args=['--no-sandbox'])
        student = browser.new_context(viewport={'width': 1440, 'height': 1000})
        admin = browser.new_context(viewport={'width': 1440, 'height': 1000})
        url = f'http://127.0.0.1:{server.server_port}'
        page, control = student.new_page(), admin.new_page()
        errors = []
        for tab in (page, control):
            tab.on('pageerror', lambda error: errors.append(error.stack))
        try:
            for context, name, email in ((student, 'Μαρία', 'student@example.test'), (admin, 'Διαχειριστής', 'admin@example.test')):
                result = mutate(context, url, 'POST', '/api/register', {
                    'name': name, 'email': email, 'password': 'operations unique password 42', 'ageConfirmed': True,
                })
                assert not result['user']['isAdmin'], 'Signing up must never grant admin privileges'
                mutate(context, url, 'PUT', '/api/profile', {'goalMinutes': 10, 'reason': 'travel'})
            assert student.request.get(url + '/api/admin/overview').status == 403
            set_admin_role(db_path, 'admin@example.test', True)
            assert session(admin, url)['user']['isAdmin']
            assert not session(student, url)['user']['isAdmin']

            page.goto(url + '/#assessments')
            expect(page.locator('[data-action=assessment-start][data-kind=placement]')).to_be_enabled()
            expect(page.locator('[data-unit-id=unit-1]')).to_be_disabled()
            with page.expect_response('**/api/assessments/start') as started:
                page.locator('[data-action=assessment-start][data-kind=placement]').click()
            attempt = started.value.json()
            assert all('answer' not in question and 'acceptedAnswers' not in question for question in attempt['questions'])
            curriculum = student.request.get(url + '/api/curriculum').json()
            known = {(exercise['prompt'], exercise.get('passage'), exercise.get('audioText')): exercise['answer']
                     for lesson in curriculum['lessons'] for exercise in lesson['exercises']}
            submitted = []
            lost = False

            def lose_result(route):
                nonlocal lost
                submitted.append(route.request.post_data_json)
                if not lost:
                    lost = True
                    response = route.fetch()
                    assert response.ok
                    route.abort()
                else:
                    route.continue_()

            page.route('**/api/assessments/submit', lose_result)
            for index, question in enumerate(attempt['questions']):
                expect(page.locator('#assessment-answer')).to_be_visible()
                answer = known[(question['prompt'], question.get('passage'), question.get('audioText'))]
                page.locator('#assessment-answer').fill('incorrect' if index == 0 else answer)
                page.locator('#assessment-form button[type=submit]').click()
            expect(page.locator('[data-action=assessment-submit-retry]')).to_be_visible()
            page.locator('[data-action=assessment-submit-retry]').click()
            expect(page.locator('#assessment-result')).to_contain_text('5 από 6')
            assert len(submitted) == 2 and submitted[0] == submitted[1]
            assert session(student, url)['progress']['xp'] == 0
            page.unroute('**/api/assessments/submit', lose_result)
            print('PASS diagnostic grading, no answer leak or XP, immutable retry after lost response', flush=True)

            for lesson in curriculum['lessons'][:3]:
                mutate(student, url, 'POST', '/api/progress', {'lessonId': lesson['id'], 'answers': [e['answer'] for e in lesson['exercises']]})
            page.reload()
            expect(page.locator('[data-unit-id=unit-1]')).to_be_enabled()
            with page.expect_response('**/api/assessments/start') as unit_start:
                page.locator('[data-unit-id=unit-1]').click()
            unit = unit_start.value.json()
            for question in unit['questions']:
                page.locator('#assessment-answer').fill(known[(question['prompt'], question.get('passage'), question.get('audioText'))])
                page.locator('#assessment-form button[type=submit]').click()
            expect(page.locator('#assessment-result')).to_contain_text('6 από 6')
            assert session(student, url)['progress']['xp'] == 100
            page.screenshot(path=str(artifacts / 'assessment-desktop.png'), full_page=True)
            print('PASS completed-unit check unlocks and grades without extra XP', flush=True)

            page.goto(url + '/#notifications')
            expect(page.locator('#notification-list .notification-item')).to_have_count(3)
            page.locator('[data-action=notification-read-all]').click()
            expect(page.locator('#notification-list .unread')).to_have_count(0)
            page.locator('#notification-preferences-form [name=achievements]').uncheck()
            page.locator('#notification-preferences-form button[type=submit]').click()
            expect(page.locator('#toast')).to_contain_text('αποθηκεύ')
            fourth = curriculum['lessons'][3]
            mutate(student, url, 'POST', '/api/progress', {'lessonId': fourth['id'], 'answers': [e['answer'] for e in fourth['exercises']]})
            assert len(student.request.get(url + '/api/notifications').json()['items']) == 3
            print('PASS in-app notifications read state and opt-out preference', flush=True)

            page.goto(url + '/#support')
            page.locator('#support-create-form [name=category]').select_option('technical')
            page.locator('#support-create-form [name=subject]').fill('Ερώτηση για το μάθημα')
            page.locator('#support-create-form [name=message]').fill('Θα ήθελα μια διευκρίνιση για την ολοκλήρωση του μαθήματος.')
            with page.expect_response('**/api/support/tickets') as created:
                page.locator('#support-create-form button[type=submit]').click()
            ticket = created.value.json()['ticket']
            expect(page.locator('.ticket-detail h2')).to_have_text(ticket['subject'])
            assert admin.request.get(url + '/api/support/tickets/' + ticket['id']).status == 404
            control.goto(url + '/#admin')
            expect(control.locator('#admin-overview')).to_be_visible()
            control.locator('[data-action=admin-tab][data-tab=support]').click()
            control.locator('[data-action=admin-support-open][data-ticket-id="' + ticket['id'] + '"]').click()
            control.locator('#admin-support-reply-form [name=message]').fill('Το αίτημά σου καταγράφηκε και ελέγχθηκε στη δοκιμαστική εγκατάσταση.')
            control.locator('#admin-support-reply-form button[type=submit]').click()
            expect(control.locator('.ticket-messages')).to_contain_text('καταγράφηκε και ελέγχθηκε')
            page.reload()
            expect(page.locator('.ticket-messages')).to_contain_text('καταγράφηκε και ελέγχθηκε')
            page.locator('[data-action=support-status]').click()
            expect(page.locator('#support-reply-form')).to_have_count(0)
            assert any(item['link'] == '#support/' + ticket['id'] for item in student.request.get(url + '/api/notifications').json()['items'])
            print('PASS private ticket, real admin response, learner notification and close', flush=True)

            control.locator('[data-action=admin-tab][data-tab=content]').click()
            control.locator('[data-action=admin-new]').click()
            title_field = control.locator('[data-lesson-field=title]')
            expect(title_field).to_be_visible()
            title_field.fill('Μάθημα από απλή φόρμα')
            assert json.loads(control.locator('#admin-lesson-json').input_value())['title'] == 'Μάθημα από απλή φόρμα'
            draft = json.loads(json.dumps(curriculum['lessons'][0]))
            draft.update(id='browser-extra', title='Νέο μάθημα δοκιμής')
            advanced = control.locator('#admin-lesson-json')
            if not advanced.is_visible():
                control.locator('details:has(#admin-lesson-json) > summary').click()
            advanced.fill(json.dumps(draft, ensure_ascii=False))
            with control.expect_response('**/api/admin/lessons') as saved:
                control.locator('#admin-lesson-form button[type=submit]').click()
            assert saved.value.ok, saved.value.text()
            assert len(student.request.get(url + '/api/curriculum').json()['lessons']) == len(curriculum['lessons'])
            expect(control.locator('[data-action=admin-publish]')).to_be_enabled()
            control.locator('[data-action=admin-publish]').click()
            expect(control.locator('#toast')).to_contain_text('δημοσιεύ')
            published = student.request.get(url + '/api/curriculum').json()['lessons']
            assert len(published) == len(curriculum['lessons']) + 1 and published[-1]['id'] == draft['id']
            control.screenshot(path=str(artifacts / 'admin-desktop.png'), full_page=True)
            print('PASS admin draft remains private until explicit publication', flush=True)

            # Export and backup include the new private records; snapshots are not
            # just a source ZIP and restore never replaces the running database.
            exported = student.request.get(url + '/api/export').json()
            serialized = json.dumps(exported, ensure_ascii=False)
            assert ticket['subject'] in serialized and attempt['attemptId'] in serialized
            assert 'questions_json' not in serialized and 'request_hash' not in serialized
            backup = copy_database(db_path, Path(directory) / 'backup.sqlite3')
            restored = copy_database(backup, Path(directory) / 'restored.sqlite3')
            check = create_server('127.0.0.1', 0, restored)
            check_thread = threading.Thread(target=check.serve_forever, daemon=True)
            check_thread.start()
            try:
                assert len(check.curriculum()['lessons']) == len(curriculum['lessons']) + 1
                restored_client = Client(check.server_port)
                restored_client.request('GET', '/api/session')
                status, restored_session, _ = restored_client.mutate('POST', '/api/login', {'email': 'student@example.test', 'password': 'operations unique password 42'})
                assert status == 200 and restored_session['progress']['xp'] == 130
                assert restored_client.request('GET', '/api/support/tickets')[1]['tickets'][0]['status'] == 'closed'
                assert restored_client.request('GET', '/api/assessments')[1]['units'][0]['bestScore'] == 100
                assert restored_client.request('GET', '/api/notifications/preferences')[1]['preferences']['achievements'] is False
                with check.db() as db:
                    assert db.execute('SELECT COUNT(*) FROM support_tickets').fetchone()[0] == 1
                    assert db.execute('SELECT COUNT(*) FROM assessment_attempts WHERE result_json IS NOT NULL').fetchone()[0] == 2
            finally:
                check.shutdown()
                check.server_close()
                check_thread.join()
            page.set_viewport_size({'width': 360, 'height': 800})
            for route, ready in [('assessments', '.assessment-page'), ('support', '#support-create-form')]:
                page.goto(url + '/#' + route)
                expect(page.locator(ready)).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), route
            control.set_viewport_size({'width': 360, 'height': 800})
            expect(control.locator('.admin-page')).to_be_visible()
            assert control.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'admin content'
            control.locator('[data-action=admin-tab][data-tab=support]').click()
            expect(control.locator('#admin-support-list')).to_be_visible()
            assert control.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'admin support'
            page.goto(url + '/#notifications')
            expect(page.locator('#notification-preferences-form')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(artifacts / 'notifications-mobile.png'), full_page=True)
            assert not errors, errors
            print('PASS own-data export, full backup/restore, mobile layout and no JS runtime errors', flush=True)
        finally:
            browser.close()
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    run()
