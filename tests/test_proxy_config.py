from pathlib import Path
import tempfile
import unittest
from wsgi import ProductionConfigError, create_application, validate_production_config
from test_wsgi import WSGIClient


class ProxyConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = {'ACADEMY_ENV': 'production', 'ACADEMY_APP_ORIGIN': 'https://ACADEMY.EXAMPLE:443',
                    'ACADEMY_DB': str(Path(self.temp.name) / 'app.sqlite3'), 'ACADEMY_SECURE_COOKIES': '1'}

    def tearDown(self):
        self.temp.cleanup()

    def test_proxy_trust_is_disabled_by_default_and_requires_one_concrete_ip(self):
        self.assertIsNone(validate_production_config(self.env).trusted_proxy)
        for address in ('127.0.0.1', '::1', '172.18.0.2'):
            self.assertEqual(validate_production_config({**self.env, 'ACADEMY_TRUSTED_PROXY': address}).trusted_proxy, address)
        for address in ('*', '127.0.0.0/8', 'proxy.example', '0.0.0.0', '::', '224.0.0.1'):
            with self.subTest(address=address), self.assertRaises(ProductionConfigError):
                validate_production_config({**self.env, 'ACADEMY_TRUSTED_PROXY': address})

    def test_normalized_origin_accepts_canonical_browser_host_and_mutations(self):
        application = create_application(environ=self.env)
        try:
            self.assertEqual(application.state.app_origin, 'https://academy.example')
            client = WSGIClient(application, host='academy.example', scheme='https')
            status, _, headers = client.register()
            self.assertEqual(status, 200)
            self.assertIn('Secure', headers['Set-Cookie'])
            self.assertEqual(client.mutate('POST', '/api/logout', headers={'Origin': 'https://academy.example'})[0], 200)
        finally:
            application.close()
