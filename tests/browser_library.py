"""Real browser acceptance for private vocabulary and targeted review.

All accounts, records and delayed requests use a temporary local database.
No model, SMTP provider, microphone or external analytics is used.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import create_server
from playwright.sync_api import sync_playwright, expect
from browser_learning import mutate, session


def wait_for(page, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        page.wait_for_timeout(20)
    assert predicate(), 'Expected browser request was not observed'


def run():
    artifacts = Path('/tmp/academy-library-artifacts')
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as p:
        server = create_server('127.0.0.1', 0, Path(directory) / 'library.sqlite3')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'), args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        device = browser.new_context()
        other = browser.new_context()
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: errors.append(message.text) if message.type == 'error' and ('Content Security Policy' in message.text or 'Refused to' in message.text) else None)
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            credentials = {'email': 'library@example.test', 'password': 'unique library password 42'}
            mutate(context, url, 'POST', '/api/register', {**credentials, 'name': 'Ελένη Λεξικό', 'ageConfirmed': True})
            mutate(context, url, 'PUT', '/api/profile', {'goalMinutes': 10, 'reason': 'travel'})
            user_id = session(context, url)['user']['id']
            page.goto(url + '/#library')
            expect(page.locator('#library-total-count')).to_have_text('0')
            expect(page.locator('.library-card')).to_have_count(0)
            lesson = context.request.get(url + '/api/curriculum').json()['lessons'][0]
            mutate(context, url, 'POST', '/api/progress', {'lessonId': lesson['id'], 'answers': [e['answer'] for e in lesson['exercises']]})
            page.reload()
            expect(page.locator('.library-card')).to_have_count(3)
            search = page.locator('#library-search')
            search.fill('αυτοκινητο')
            expect(page.locator('.library-card h2')).to_have_text('car')
            search.fill('BOOK')
            expect(page.locator('.library-card h2')).to_have_text('book')
            search.fill('')
            expect(page.locator('.library-card')).to_have_count(3)
            assert session(context, url)['progress']['xp'] == lesson['xp']
            print('PASS learned cards only, Greek accent-insensitive and English search, unchanged XP', flush=True)

            failed = False
            def fail_load(route):
                nonlocal failed
                if not failed:
                    failed = True
                    route.fulfill(status=503, content_type='application/json', body='{"error":"Δοκιμαστική διακοπή"}')
                else:
                    route.continue_()
            page.route('**/api/library', fail_load)
            page.reload()
            expect(page.locator('#library-error')).to_be_visible()
            page.locator('[data-action=library-reload]').click()
            expect(page.locator('.library-card')).to_have_count(3)
            page.unroute('**/api/library', fail_load)
            car = page.locator('[data-library-card="first-words:0"]')
            bookmark = car.locator('.library-bookmark')
            def fail_save(route):
                route.fulfill(status=503, content_type='application/json', body='{"error":"Δοκιμαστική αποτυχία"}')
            page.route('**/api/library/bookmarks', fail_save)
            bookmark.click()
            expect(page.locator('#library-save-error')).to_be_visible()
            expect(bookmark).to_have_attribute('aria-pressed', 'false')
            assert not context.request.get(url + '/api/library').json()['cards'][0]['bookmarked']
            page.unroute('**/api/library/bookmarks', fail_save)

            lost_commit = []
            def lose_save_response(route):
                response = route.fetch()
                assert response.ok
                lost_commit.append(True)
                route.abort()
            page.route('**/api/library/bookmarks', lose_save_response)
            bookmark.click()
            wait_for(page, lambda: bool(lost_commit))
            expect(page.locator('#library-save-error')).to_be_visible()
            expect(page.locator('#library-save-error')).to_contain_text('Δεν επιβεβαιώθηκε')
            assert context.request.get(url + '/api/library').json()['cards'][0]['bookmarked']
            page.unroute('**/api/library/bookmarks', lose_save_response)
            bookmark.click()  # Same desired state after the ambiguous response.
            expect(bookmark).to_have_attribute('aria-pressed', 'true')
            expect(page.locator('#library-saved-count')).to_have_text('1')
            mutate(device, url, 'POST', '/api/login', credentials)
            assert device.request.get(url + '/api/library').json()['summary']['bookmarkedCards'] == 1
            page.reload()
            page.locator('[data-action=library-saved]').click()
            expect(page.locator('.library-card')).to_have_count(1)
            page.locator('[data-action=library-all]').click()
            expect(page.locator('.library-card')).to_have_count(3)
            page.screenshot(path=str(artifacts / 'library-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 320, 'height': 740})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.evaluate("document.documentElement.dataset.theme='dark'")
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(artifacts / 'library-mobile-dark.png'), full_page=True)
            page.evaluate("document.documentElement.dataset.theme='light'")
            page.set_viewport_size({'width': 1440, 'height': 1000})
            print('PASS bookmark failure/recovery, lost-response idempotence, persistence and 320px/dark layout', flush=True)

            held_put = []
            def hold_put(route):
                if not held_put:
                    held_put.append(route)
                else:
                    route.continue_()
            page.route('**/api/library/bookmarks', hold_put)
            bookmark.click()  # Remove, but hold before the database commit.
            wait_for(page, lambda: bool(held_put))
            expect(page.locator('.library-bookmark:disabled')).to_have_count(3)
            page.goto(url + '/#dashboard')
            page.goto(url + '/#library')
            expect(page.locator('#library-saved-count')).to_have_text('1')
            held_put[0].continue_()
            expect(page.locator('#library-saved-count')).to_have_text('0')
            page.unroute('**/api/library/bookmarks', hold_put)
            print('PASS delayed bookmark commit refreshes a newer visit without stale saved state', flush=True)

            page.goto(url + '/#review')
            page.locator('[data-action=review-start-all]').first.click()
            review_item = context.request.get(url + '/api/review').json()['items'][0]
            page.locator('#review-answer').fill('an unrelated incorrect answer')
            page.locator('#review-form button[type=submit]').click()
            expect(page.locator('#review-feedback')).to_have_class('answer-feedback error')
            expect(page.locator('#review-mistake-count')).to_have_text('1')
            page.goto(url + '/#review/mistakes')
            expect(page.locator('#review-answer')).to_be_visible()
            page.locator('#review-answer').fill(review_item['answer'])
            page.locator('#review-form button[type=submit]').click()
            expect(page.locator('#review-feedback')).to_have_class('answer-feedback success')
            expect(page.locator('#review-mistake-count')).to_have_text('0')
            page.locator('[data-action=review-next]').click()
            expect(page.locator('#review-mistakes-empty')).to_contain_text('Δεν υπάρχουν καταγεγραμμένα λάθη')
            assert not context.request.get(url + '/api/practice-insights').json()['items']
            assert session(context, url)['progress']['xp'] == lesson['xp']

            insights_failed = False
            def fail_insights(route):
                nonlocal insights_failed
                if not insights_failed:
                    insights_failed = True
                    route.fulfill(status=503, content_type='application/json', body='{"error":"Δοκιμαστική διακοπή"}')
                else:
                    route.continue_()
            page.route('**/api/practice-insights', fail_insights)
            page.reload()
            expect(page.locator('#review-insights-error')).to_be_visible()
            page.locator('[data-action=review-mistakes-reload]').click()
            expect(page.locator('#review-mistakes-empty')).to_be_visible()
            page.unroute('**/api/practice-insights', fail_insights)
            print('PASS actual wrong-to-correct targeted review, truthful empty state, failed-insights retry and no extra XP', flush=True)

            mutate(context, url, 'POST', '/api/review', {**{k: review_item[k] for k in ('lessonId', 'exerciseId')}, 'answer': 'incorrect again', 'attemptId': 'library-race-wrong'})
            with server.db() as db:
                db.execute("UPDATE review_cards SET due_at='2020-01-01T00:00:00+00:00' WHERE user_id=? AND lesson_id=? AND exercise_id=?", (user_id, review_item['lessonId'], review_item['exerciseId']))
            posts, gets = [], []
            capture_gets = False
            def hold_review(route):
                if route.request.method == 'POST' and not posts:
                    posts.append(route)
                elif route.request.method == 'GET' and capture_gets:
                    response = route.fetch()
                    gets.append((route, response.body()))
                else:
                    route.continue_()
            page.route('**/api/review', hold_review)
            page.route('**/api/practice-insights', hold_review)
            page.goto(url + '/#review')
            expect(page.locator('#review-answer')).to_be_visible()
            page.locator('#review-answer').fill(review_item['answer'])
            page.locator('#review-form button[type=submit]').click()
            wait_for(page, lambda: bool(posts))
            page.goto(url + '/#library')
            expect(page.locator('#library-search')).to_be_visible()
            capture_gets = True
            page.goto(url + '/#review')
            wait_for(page, lambda: len(gets) == 2)
            capture_gets = False
            posts[0].continue_()
            expect(page.locator('[data-action=review-start-due] .review-count')).to_have_text('0')
            expect(page.locator('#review-mistake-count')).to_have_text('0')
            for route, body in gets:
                route.fulfill(status=200, content_type='application/json', body=body)
            page.wait_for_timeout(150)
            expect(page.locator('[data-action=review-start-due] .review-count')).to_have_text('0')
            expect(page.locator('#review-mistake-count')).to_have_text('0')
            page.unroute('**/api/review', hold_review)
            page.unroute('**/api/practice-insights', hold_review)
            print('PASS old pre-commit review and mistake responses cannot overwrite a newer review visit', flush=True)

            second = {'email': 'library-other@example.test', 'password': 'other library password 42'}
            mutate(other, url, 'POST', '/api/register', {**second, 'name': 'Άλλος Μαθητής', 'ageConfirmed': True})
            mutate(other, url, 'PUT', '/api/profile', {'goalMinutes': 10, 'reason': 'travel'})
            held = []
            def hold_first_user(route):
                if not held:
                    response = route.fetch()
                    held.append((route, response.body()))
                else:
                    route.continue_()
            page.route('**/api/library', hold_first_user)
            page.goto(url + '/#library')
            wait_for(page, lambda: bool(held))
            page.locator('.user-block').click()
            expect(page.locator('.landing')).to_be_visible()
            page.goto(url + '/#login')
            expect(page.locator('#auth-form')).to_be_visible()
            page.locator('#auth-form [name=email]').fill(second['email'])
            page.locator('#auth-form [name=password]').fill(second['password'])
            page.locator('#auth-form button[type=submit]').click()
            expect(page.locator('.dashboard-page')).to_be_visible()
            page.goto(url + '/#library')
            expect(page.locator('#library-total-count')).to_have_text('0')
            held[0][0].fulfill(status=200, content_type='application/json', body=held[0][1])
            page.wait_for_timeout(150)
            expect(page.locator('#library-total-count')).to_have_text('0')
            expect(page.locator('.library-card')).to_have_count(0)
            expect(page.locator('#library-search')).to_have_value('')
            assert not errors, errors
            print('PASS previous-account response isolation, cleared search and no JavaScript/CSP errors', flush=True)
        finally:
            browser.close()
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    run()
