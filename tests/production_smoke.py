"""Real pinned Waitress processes on loopback; emulates proxy headers, not TLS.

Run with the optional runtime virtualenv. Never contacts an SMTP/AI provider.
"""
import http.client
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_server import Client


class BrowserLikeClient(Client):
    def request(self, method, path, body=None, headers=None):
        return super().request(method, path, body, {'Host': 'academy.example', 'Origin': 'https://academy.example', **(headers or {})})


def run_case(directory, trusted):
    with socket.socket() as chosen:
        chosen.bind(('127.0.0.1', 0))
        port = chosen.getsockname()[1]
    env = {**os.environ, 'ACADEMY_ENV': 'production', 'ACADEMY_APP_ORIGIN': 'https://ACADEMY.EXAMPLE:443',
           'ACADEMY_DB': str(directory / ('trusted.sqlite3' if trusted else 'direct.sqlite3')),
           'ACADEMY_SECURE_COOKIES': '1', 'HOST': '127.0.0.1', 'PORT': str(port),
           'ACADEMY_MAIL_MODE': 'disabled', 'ACADEMY_AI_PROVIDER': 'disabled'}
    env.pop('ACADEMY_TRUSTED_PROXY', None)
    if trusted:
        env['ACADEMY_TRUSTED_PROXY'] = '127.0.0.1'
    with tempfile.TemporaryFile() as logs:
        process = subprocess.Popen([sys.executable, str(ROOT / 'run-production.py')], cwd=ROOT, env=env, stdout=logs, stderr=logs)
        try:
            client = BrowserLikeClient(port)
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None:
                    raise AssertionError('Production process exited before readiness')
                try:
                    if client.request('GET', '/api/health')[0] == 200:
                        break
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise AssertionError('Production readiness timed out')
                time.sleep(0.1)
            client.request('GET', '/api/session')
            if not trusted:
                status, user, headers = client.mutate('POST', '/api/register', {
                    'name': 'Δοκιμή', 'email': 'production@example.test', 'password': 'production test password', 'ageConfirmed': True})
                assert status == 200 and user['user']
                assert 'Secure' in headers['Set-Cookie'] and 'HttpOnly' in headers['Set-Cookie']
                assert client.request('GET', '/api/health', headers={'Host': 'attacker.example', 'X-Forwarded-Host': 'academy.example'})[0] == 400
                assert client.request('POST', '/api/logout', {'csrfToken': client.csrf}, headers={'Origin': 'https://attacker.example'})[0] == 403
                assert client.request('GET', '/.env')[0] == 404
                assert client.request('POST', '/api/logout', {'blob': 'a' * 33000})[0] == 413
                connection = http.client.HTTPConnection('127.0.0.1', port)
                connection.request('HEAD', '/api/curriculum', headers={'Host': 'academy.example'})
                head = connection.getresponse()
                assert head.status == 200 and head.read() == b''
                assert int(head.getheader('Content-Length')) > 0
                connection.close()
                print('PASS real Waitress: auth, Secure cookies, canonical origin, Host/CSRF restrictions, HEAD and body limits', flush=True)
            codes = []
            for index in range(16):
                codes.append(client.request('POST', '/api/login', {
                    'email': f'unknown-{index}@example.test', 'password': 'not a valid password', 'csrfToken': client.csrf},
                    headers={'X-Forwarded-For': f'198.51.100.{index + 1}', 'X-Forwarded-Proto': 'https'})[0])
            if trusted:
                assert codes == [401] * 16, codes
                print('PASS explicitly trusted proxy preserves separate client-IP rate limits', flush=True)
            else:
                assert 429 in codes, codes
                print('PASS untrusted forwarded addresses cannot bypass client-IP rate limits', flush=True)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def run():
    if importlib.util.find_spec('waitress') is None:
        raise SystemExit('Install optional requirements-runtime.txt with --require-hashes first.')
    with tempfile.TemporaryDirectory() as directory:
        run_case(Path(directory), False)
        run_case(Path(directory), True)
    print('No external hosting, real HTTPS certificate, SMTP delivery or AI model was activated.', flush=True)


if __name__ == '__main__':
    run()
