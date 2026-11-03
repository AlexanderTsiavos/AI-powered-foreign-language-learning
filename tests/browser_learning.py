"""Acceptance checks for durable lesson checkpoints and review retries.

Optional test tooling: Playwright and Chromium. Uses temporary accounts/database.
Run from the project root: python3 tests/browser_learning.py
"""
import os
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import create_server
from playwright.sync_api import sync_playwright, expect


def session(context, url):
    return context.request.get(url + '/api/session').json()


def mutate(context, url, method, path, body):
    payload = {**body, 'csrfToken': session(context, url)['csrfToken']}
    response = context.request.fetch(url + path, method=method, data=payload)
    assert response.ok, (response.status, response.text())
    return response.json()


def run():
    screenshots = Path(os.environ.get('ACADEMY_SCREENSHOTS', '/tmp/academy-learning-artifacts'))
    screenshots.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp, sync_playwright() as p:
        server = create_server('127.0.0.1', 0, Path(temp) / 'learning.sqlite3')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'), args=['--no-sandbox'])
        errors = []
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        url = f'http://127.0.0.1:{server.server_port}'
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            mutate(context, url, 'POST', '/api/register', {
                'name': 'Ελένη Δοκιμή', 'email': 'eleni@example.test',
                'password': 'unique learning password 42', 'ageConfirmed': True,
            })
            mutate(context, url, 'PUT', '/api/profile', {
                'nativeLanguage': 'el', 'targetLanguage': 'en', 'goalMinutes': 10,
                'reason': 'travel', 'ageGroup': 'adult',
            })
            page.goto(url + '/#lesson/first-words')
            expect(page.locator('.word-presentation h2')).to_have_text('car')
            page.locator('[data-action=next-card]').click()
            expect(page.locator('.word-presentation h2')).to_have_text('book')
            page.reload()
            expect(page.locator('.word-presentation h2')).to_have_text('book')
            page.goto(url + '/#dashboard')
            expect(page.locator('.dashboard-page')).to_be_visible()
            page.goto(url + '/#lesson/first-words')
            expect(page.locator('.word-presentation h2')).to_have_text('book')
            print('PASS card checkpoint survives reload and navigation')

            # A failed checkpoint save must leave the currently visible card intact.
            def unavailable(route):
                if route.request.method == 'PUT':
                    route.fulfill(status=503, content_type='application/json', body='{"error":"Δοκιμαστική διακοπή"}')
                else:
                    route.continue_()
            page.route('**/api/lesson-state', unavailable)
            page.locator('[data-action=next-card]').click()
            expect(page.locator('#toast')).to_contain_text('Δεν προχωρήσαμε')
            expect(page.locator('.word-presentation h2')).to_have_text('book')
            page.unroute('**/api/lesson-state', unavailable)
            page.locator('[data-action=next-card]').click()
            expect(page.locator('.word-presentation h2')).to_have_text('apple')
            page.locator('[data-action=next-card]').click()
            page.locator('#answer').fill('car')
            page.locator('#exercise-form button[type=submit]').click()
            expect(page.locator('#answer-feedback')).to_contain_text('Σωστά!')
            page.reload()
            expect(page.locator('#answer')).to_have_value('car')
            expect(page.locator('#answer')).to_have_attribute('readonly', '')
            expect(page.locator('#answer-feedback')).to_contain_text('αποθηκευτεί')
            print('PASS failed checkpoint does not advance; validated exercise survives reload')

            # A separate browser session recovers the exact validated exercise.
            mobile = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
            mutate(mobile, url, 'POST', '/api/login', {'email': 'eleni@example.test', 'password': 'unique learning password 42'})
            phone = mobile.new_page()
            phone.on('pageerror', lambda error: errors.append(str(error)))
            phone.goto(url + '/#lesson/first-words')
            expect(phone.locator('#answer')).to_have_value('car')
            phone.locator('#exercise-form button[type=submit]').click()
            for answer in ('book', 'apple'):
                phone.locator('#answer').fill(answer)
                phone.locator('#exercise-form button[type=submit]').click()
                expect(phone.locator('#answer-feedback')).to_contain_text('Σωστά!')
                phone.locator('#exercise-form button[type=submit]').click()
            expect(phone.locator('.completion-page')).to_be_visible()
            assert session(mobile, url)['progress']['xp'] == 30
            checkpoint = mobile.request.get(url + '/api/lesson-state?lessonId=first-words').json()
            assert checkpoint['checkpoint'] is None
            print('PASS fresh mobile session resumes exact exercise, completion clears checkpoint')

            # Review is real server-checked practice and must not create extra XP.
            phone.goto(url + '/#review')
            expect(phone.locator('.review-page')).to_be_visible()
            review = mobile.request.get(url + '/api/review').json()
            assert len(review['items']) == 3 and review['dueCount'] == 0
            target = review['items'][0]
            phone.locator('[data-action=review-start-all]').first.click()
            expect(phone.locator('#review-form')).to_be_visible()
            phone.locator('#review-answer').fill(target['answer'])
            phone.locator('#review-form button[type=submit]').click()
            expect(phone.locator('#review-feedback')).to_contain_text('Σωστά')
            early = mobile.request.get(url + '/api/review').json()
            early_car = next(item for item in early['items'] if item['exerciseId'] == target['exerciseId'])
            assert early_car['stage'] == 0, early_car
            assert early_car['dueAt'] == next(item for item in review['items'] if item['exerciseId'] == target['exerciseId'])['dueAt']
            phone.goto(url + '/#dashboard')
            expect(phone.locator('.dashboard-page')).to_be_visible()
            phone.goto(url + '/#review')
            expect(phone.locator('.review-page')).to_be_visible()
            phone.locator('[data-action=review-start-all]').first.click()
            phone.locator('#review-answer').fill('wrong')
            phone.locator('#review-form button[type=submit]').click()
            expect(phone.locator('#review-feedback')).to_contain_text(target['answer'])
            assert session(mobile, url)['progress']['xp'] == 30
            phone.screenshot(path=str(screenshots / 'review-mobile.png'), full_page=True)
            print('PASS review shows learned exercises, corrects wrong answer and preserves XP')

            # Begin another review and lose the response after the server commits.
            phone.goto(url + '/#dashboard')
            expect(phone.locator('.dashboard-page')).to_be_visible()
            with server.db() as db:
                db.execute("UPDATE review_cards SET due_at='2026-01-01T00:00:00+00:00' WHERE lesson_id=? AND exercise_id=?", (target['lessonId'], target['exerciseId']))
            phone.goto(url + '/#review')
            expect(phone.locator('.review-page')).to_be_visible()
            phone.locator('[data-action=review-start-all]').first.click()
            phone.locator('#review-answer').fill(target['answer'])
            request_ids = []
            lost_response = False

            def lose_response(route):
                nonlocal lost_response
                if route.request.method != 'POST':
                    route.continue_()
                    return
                request_ids.append(route.request.post_data_json['attemptId'])
                if not lost_response:
                    lost_response = True
                    response = route.fetch()
                    assert response.ok
                    route.abort()
                else:
                    route.continue_()

            phone.route('**/api/review', lose_response)
            phone.locator('#review-form button[type=submit]').click()
            expect(phone.locator('#review-error')).to_be_visible()
            phone.locator('[data-action=review-retry]').click()
            expect(phone.locator('#review-feedback')).to_contain_text('Σωστά')
            assert len(request_ids) == 2 and request_ids[0] == request_ids[1], request_ids
            phone.unroute('**/api/review', lose_response)
            stored = mobile.request.get(url + '/api/review').json()
            car = next(item for item in stored['items'] if item['exerciseId'] == target['exerciseId'])
            assert car['stage'] == 1, car
            assert session(mobile, url)['progress']['xp'] == 30
            phone.set_viewport_size({'width': 320, 'height': 740})
            assert phone.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Narrow review layout overflow'
            assert not errors, errors
            print('PASS ambiguous-response retry reuses attempt ID, advances once, 320px review layout, no runtime errors')

            # The completion response must update this user's dashboard even if
            # navigation happens while the response is in flight.
            mutate(mobile, url, 'PUT', '/api/lesson-state', {
                'lessonId': 'greetings', 'phase': 'exercises', 'index': 2,
                'answers': ['hello', 'thank you', 'name'], 'correct': True,
            })
            phone.goto(url + '/#lesson/greetings')
            expect(phone.locator('#answer')).to_have_value('name')

            def navigate_before_response(route):
                response = route.fetch()
                assert response.ok
                phone.evaluate("location.hash = 'dashboard'")
                expect(phone.locator('.dashboard-page')).to_be_visible()
                route.fulfill(response=response)

            phone.route('**/api/progress', navigate_before_response)
            phone.locator('#exercise-form button[type=submit]').click()
            expect(phone.locator('.dashboard-page')).to_be_visible()
            expect(phone.locator('.path-row.completed')).to_have_count(2)
            assert session(mobile, url)['progress']['xp'] == 60
            phone.unroute('**/api/progress', navigate_before_response)
            assert not errors, errors
            print('PASS delayed completion response updates dashboard after navigation')
            mobile.close()
        finally:
            browser.close()
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    run()
