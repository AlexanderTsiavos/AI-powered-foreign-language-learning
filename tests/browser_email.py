"""Browser account-mail acceptance using an in-memory delivery sink, not email.

No SMTP provider is contacted; all accounts and token links are temporary.
"""
import os
from pathlib import Path
import re
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mailer import Mailer
from server import create_server
from browser_learning import mutate, session
from playwright.sync_api import sync_playwright, expect


def run():
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as p:
        server = create_server('127.0.0.1', 0, Path(directory) / 'email.sqlite3')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'), args=['--no-sandbox'])
        context, device = browser.new_context(), browser.new_context()
        page = context.new_page()
        errors, messages = [], []
        page.on('pageerror', lambda error: errors.append(error.stack))

        def delivered(route, recipient):
            assert server.mailer.wait_idle(3)
            for message in reversed(messages):
                if message.recipient == recipient:
                    match = re.search(re.escape(url) + '/#' + route + r'/([A-Za-z0-9_-]+)', message.body)
                    if match:
                        return match.group(0), match.group(1)
            raise AssertionError('Expected link missing from temporary delivery sink')

        try:
            page.goto(url + '/#reset-request')
            expect(page.locator('#email-status')).to_contain_text('δεν έχει ενεργοποιηθεί')
            expect(page.locator('#reset-request-form button[type=submit]')).to_be_disabled()
            print('PASS disabled email is shown honestly and cannot pretend delivery', flush=True)
            server.mailer.close()
            server.mailer = Mailer.for_testing(messages.append, origin=url)
            server.email_auth.mailer = server.mailer
            mutate(context, url, 'POST', '/api/register', {'name': 'Μαρία', 'email': 'mail@example.test', 'password': 'first unique password 42', 'ageConfirmed': True})
            mutate(context, url, 'PUT', '/api/profile', {'goalMinutes': 10, 'reason': 'travel'})
            page.goto(url + '/?mail-fixture=1#settings')
            expect(page.locator('#verify-email-request-form button[type=submit]')).to_be_enabled()
            page.locator('#verify-email-request-form button[type=submit]').click()
            expect(page.locator('#email-request-result')).to_be_visible()
            link, token = delivered('verify-email', 'mail@example.test')
            page.goto(link)
            expect(page.locator('#verify-email-form')).to_be_visible()
            with server.db() as db:
                assert db.execute('SELECT email_verified_at FROM users').fetchone()[0] is None

            def failed_refresh(route):
                route.abort()

            page.route('**/api/session', failed_refresh)
            page.locator('#verify-email-form button[type=submit]').click()
            expect(page.locator('#mail-action-result')).to_contain_text('Η ενέργεια ολοκληρώθηκε')
            assert token not in page.url
            page.unroute('**/api/session', failed_refresh)
            assert session(context, url)['user']['emailVerified']
            page.goto(link)
            page.locator('#verify-email-form button[type=submit]').click()
            expect(page.locator('#mail-action-result')).to_contain_text('δεν μπορεί να χρησιμοποιηθεί')
            assert token not in page.url
            print('PASS explicit one-time verification, cleared token, success survives failed session refresh', flush=True)

            page.goto(url + '/#settings')
            page.reload()
            expect(page.locator('#email-verification-status')).to_have_text('Επιβεβαιωμένο email')
            mutate(device, url, 'POST', '/api/login', {'email': 'mail@example.test', 'password': 'first unique password 42'})
            page.locator('.change-email-details summary').click()
            page.locator('#change-email-form [name=newEmail]').fill('changed@example.test')
            page.locator('#change-email-form [name=currentPassword]').fill('first unique password 42')
            page.locator('#change-email-form button[type=submit]').click()
            expect(page.locator('#change-email-result')).to_be_visible()
            assert session(context, url)['user']['email'] == 'mail@example.test'
            change_link, change_token = delivered('confirm-email-change', 'changed@example.test')
            page.goto(change_link)
            expect(page.locator('#confirm-email-change-form')).to_be_visible()
            assert session(context, url)['user']['email'] == 'mail@example.test'
            page.locator('#confirm-email-change-form button[type=submit]').click()
            expect(page.locator('#mail-action-result')).to_contain_text('Η ενέργεια ολοκληρώθηκε')
            assert change_token not in page.url
            assert session(context, url)['user'] is None and session(device, url)['user'] is None
            print('PASS email changes only after confirmation and revokes all device sessions', flush=True)

            mutate(device, url, 'POST', '/api/login', {'email': 'changed@example.test', 'password': 'first unique password 42'})
            page.goto(url + '/#reset-request')
            page.locator('#reset-request-form [name=email]').fill('changed@example.test')
            page.locator('#reset-request-form button[type=submit]').click()
            expect(page.locator('#reset-request-result')).to_be_visible()
            reset_link, reset_token = delivered('reset-password', 'changed@example.test')
            page.goto(reset_link)
            page.locator('#reset-password-form [name=newPassword]').fill('second unique password 42')
            page.locator('#reset-password-form [name=confirmPassword]').fill('second unique password 42')
            page.locator('#reset-password-form button[type=submit]').click()
            expect(page.locator('#mail-action-result')).to_contain_text('Η ενέργεια ολοκληρώθηκε')
            assert reset_token not in page.url
            assert session(device, url)['user'] is None and session(context, url)['user'] is None
            page.goto(url + '/#login')
            page.locator('#auth-form [name=email]').fill('changed@example.test')
            page.locator('#auth-form [name=password]').fill('second unique password 42')
            page.locator('#auth-form button[type=submit]').click()
            expect(page.locator('.dashboard-page')).to_be_visible()
            print('PASS password reset revokes devices, never auto-signs in and accepts the new password', flush=True)
            page.set_viewport_size({'width': 360, 'height': 800})
            for route in ('privacy', 'terms', 'settings'):
                page.goto(url + '/#' + route)
                expect(page.locator('#main-content')).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), route
            page.goto(url + '/#reset-password/' + 'a' * 43)
            page.locator('#reset-password-form [name=newPassword]').fill('third unique password 42')
            page.locator('#reset-password-form [name=confirmPassword]').fill('third unique password 42')
            page.locator('#reset-password-form button[type=submit]').click()
            expect(page.locator('#mail-action-result')).to_contain_text('δεν μπορεί να χρησιμοποιηθεί')
            assert not errors, errors
            print('PASS invalid links, legal draft pages and mobile layout; no JavaScript runtime errors', flush=True)
        finally:
            browser.close()
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    run()
