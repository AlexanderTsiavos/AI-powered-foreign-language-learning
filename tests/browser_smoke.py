"""Optional real-browser acceptance test; requires Playwright and Chromium.

Run from repository root: python3 tests/browser_smoke.py
Uses a temporary database and never touches the development accounts.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import create_server
from playwright.sync_api import sync_playwright, expect


def run():
    artifacts = Path(os.environ.get('ACADEMY_SCREENSHOTS', '/tmp/academy-smoke-artifacts'))
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp, sync_playwright() as p:
        db_path = Path(temp) / 'test.sqlite3'
        server = create_server('127.0.0.1', 0, db_path)
        port = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'), args=['--no-sandbox'])
        errors = []
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
        url = f'http://127.0.0.1:{port}'
        try:
            page.goto(url)
            expect(page.locator('.hero')).to_be_visible()
            page.screenshot(path=str(artifacts / 'landing-desktop.png'), full_page=True)
            page.get_by_role('link', name='Ας κάνουμε την αρχή').click()
            page.locator('[name=name]').fill('Μαρία Δοκιμή')
            page.locator('[name=email]').fill('maria@example.test')
            page.locator('[name=password]').fill('a unique test password 42')
            page.locator('[name=ageConfirmed]').check()
            page.locator('#auth-form button[type=submit]').click()
            expect(page.locator('#onboarding-form')).to_be_visible()
            page.locator('#onboarding-form button[type=submit]').click()
            expect(page.locator('.dashboard-page')).to_be_visible()
            expect(page.locator('.path-row.locked')).to_have_count(2)
            page.screenshot(path=str(artifacts / 'dashboard-desktop.png'), full_page=True)
            curriculum = page.request.get(url + '/api/curriculum').json()
            for lesson_index, lesson in enumerate(curriculum['lessons']):
                page.goto(url + '/#lesson/' + lesson['id'])
                expect(page.locator('.whiteboard')).to_be_visible()
                if lesson_index == 0:
                    page.screenshot(path=str(artifacts / 'lesson-desktop.png'), full_page=True)
                for _ in lesson['cards']:
                    page.locator('[data-action=next-card]').click()
                if lesson_index == 0:
                    page.locator('#answer').fill('wrong')
                    page.locator('#exercise-form button[type=submit]').click()
                    expect(page.locator('#answer-feedback')).to_contain_text('Ας το ξαναδούμε')
                    assert page.request.get(url + '/api/session').json()['progress']['xp'] == 0
                for index, exercise in enumerate(lesson['exercises']):
                    answer = exercise.get('acceptedAnswers', [exercise['answer']])[-1]
                    if lesson_index == 0 and index == 0:
                        answer = '  A CAR,  '
                    page.locator('#answer').fill(answer)
                    page.locator('#exercise-form button[type=submit]').click()
                    expect(page.locator('#answer-feedback')).to_contain_text('Σωστά!')
                    page.locator('#exercise-form button[type=submit]').click()
                expect(page.locator('.completion-page')).to_be_visible()
                assert page.request.get(url + '/api/session').json()['progress']['xp'] == sum(x['xp'] for x in curriculum['lessons'][:lesson_index + 1])
            page.goto(url + '/#settings')
            page.locator('#theme-select').select_option('dark')
            assert page.locator('html').get_attribute('data-theme') == 'dark'
            page.locator('#settings-form [name=goalMinutes]').select_option('15')
            page.locator('#settings-form button[type=submit]').click()
            expect(page.locator('#toast')).to_contain_text('αποθηκεύτηκε')
            with page.expect_download() as downloaded:
                page.locator('[data-action=export]').click()
            data = json.loads(Path(downloaded.value.path()).read_text())
            assert data['progress']['xp'] == 100 and 'csrfToken' not in data
            print('PASS desktop registration/onboarding, 3 lessons, wrong-answer correction, 100 XP, settings/export')

            # A new server instance must recover data from the same SQLite file.
            context.close()
            server.shutdown()
            server.server_close()
            thread.join()
            server = create_server('127.0.0.1', port, db_path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            mobile = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
            page = mobile.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url + '/#login')
            page.locator('[name=email]').fill('maria@example.test')
            page.locator('[name=password]').fill('a unique test password 42')
            page.locator('#auth-form button[type=submit]').click()
            expect(page.locator('.dashboard-page')).to_be_visible()
            expect(page.locator('.path-row.completed')).to_have_count(3)
            assert page.request.get(url + '/api/session').json()['profile']['goalMinutes'] == 15
            page.set_viewport_size({'width': 320, 'height': 740})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Narrow mobile overflow'
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Horizontal mobile overflow'
            page.screenshot(path=str(artifacts / 'dashboard-mobile.png'), full_page=True)
            page.goto(url + '/#lesson/first-words')
            expect(page.locator('.whiteboard')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Lesson mobile overflow'
            page.screenshot(path=str(artifacts / 'lesson-mobile.png'), full_page=True)
            page.goto(url + '/#settings')
            page.locator('.settings-page [data-action=logout]').click()
            expect(page.locator('.hero')).to_be_visible()
            page.goto(url + '/#login')
            page.locator('[name=email]').fill('maria@example.test')
            page.locator('[name=password]').fill('a unique test password 42')
            page.locator('#auth-form button[type=submit]').click()
            expect(page.locator('.dashboard-page')).to_be_visible()
            page.goto(url + '/#settings')
            page.locator('[data-action=show-delete]').click()
            page.locator('#delete-form [name=password]').fill('a unique test password 42')
            page.locator('#delete-form button[type=submit]').click()
            expect(page.locator('.hero')).to_be_visible()
            assert page.request.get(url + '/api/session').json()['user'] is None
            print('PASS server restart, fresh mobile login, persistent progress/goal, 320/390px layout, mobile logout, account deletion')
            mobile.close()
            assert not errors, '\n'.join(errors)
            print('PASS no JavaScript runtime or browser console errors')
            print('Screenshots:', artifacts)
        finally:
            browser.close()
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    run()
