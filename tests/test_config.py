"""签名密钥必填、环境隔离与平台变量覆盖回归。"""

import os
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from console_server.core.config import Settings
from console_server.core import environment


class SigningConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root_patch = patch.object(environment, 'PROJECT_ROOT', self.root)
        self.root_patch.start()
        self.env_patch = patch.dict(os.environ, {}, clear=True)
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.root_patch.stop()
        self.temp.cleanup()

    def test_missing_or_blank_secret_fails_without_source_fallback(self):
        for values in ({}, {'SECRET_KEY': ''}, {'SECRET_KEY': 'short-secret'}):
            with patch.dict(os.environ, values, clear=True):
                with self.assertRaises(ValidationError) as failure:
                    Settings()
                self.assertIn('SECRET_KEY', str(failure.exception))
                self.assertNotIn('short-secret', str(failure.exception))

    def test_distinct_environments_and_normalized_aliases(self):
        dev_key, prod_key = secrets.token_urlsafe(64), secrets.token_urlsafe(64)
        (self.root / '.env').write_text(f'SECRET_KEY={dev_key}\n')
        (self.root / '.env.prod').write_text(f'SECRET_KEY={prod_key}\nDATABASE_URL=production-db\n')
        for selected, expected, name in [('local', dev_key, 'dev'), ('production', prod_key, 'prod')]:
            with patch.dict(os.environ, {'ENV': selected}, clear=True):
                environment.load_environment()
                settings = Settings()
                self.assertEqual(settings.ENV, name)
                self.assertEqual(settings.SECRET_KEY, expected)
                self.assertNotIn(expected, repr(settings))

    def test_production_never_falls_back_to_local_file(self):
        (self.root / '.env').write_text(f'SECRET_KEY={secrets.token_urlsafe(64)}\nDATABASE_URL=local-db\n')
        os.environ['ENV'] = 'prod'
        environment.load_environment()
        with self.assertRaises(ValidationError):
            Settings()
        os.environ['SECRET_KEY'] = secrets.token_urlsafe(64)
        with self.assertRaises(ValidationError):
            Settings()

    def test_system_variables_override_selected_file(self):
        file_key, platform_key = secrets.token_urlsafe(64), secrets.token_urlsafe(64)
        (self.root / 'custom.env').write_text(f'SECRET_KEY={file_key}\nENV=prod\n')
        os.environ.update(ENV='dev', ENV_FILE='custom.env', SECRET_KEY=platform_key)
        environment.load_environment()
        settings = Settings()
        self.assertEqual(settings.ENV, 'dev')
        self.assertEqual(settings.SECRET_KEY, platform_key)

    def test_invalid_placeholder_secret_and_bad_file_are_rejected(self):
        with self.assertRaises(ValidationError):
            Settings(SECRET_KEY='replace-with-your-secret' + 'x' * 64)
        for file in ('', 'missing.env'):
            os.environ['ENV_FILE'] = file
            with self.assertRaises(ValueError):
                environment.load_environment()

    def test_csrf_origins_are_exact_and_normalized(self):
        key = secrets.token_urlsafe(64)
        configured = Settings(ENV='dev', SECRET_KEY=key, AUTH_ALLOWED_ORIGINS=["https://Console.Example:443/", "http://localhost:8080"])
        self.assertEqual(configured.AUTH_ALLOWED_ORIGINS, ["https://console.example", "http://localhost:8080"])
        for invalid in ("*", "https://*.example", "https://example/path", "null", "https://user:pass@example", "https://example:bad"):
            with self.assertRaises(ValidationError):
                Settings(ENV='dev', SECRET_KEY=key, AUTH_ALLOWED_ORIGINS=[invalid])

    def test_default_environment_and_ports_match_loader(self):
        os.environ.update(SECRET_KEY=secrets.token_urlsafe(64), DATABASE_URL='isolated-db')
        self.assertIsNone(environment.load_environment())
        self.assertEqual(Settings().ENV, environment.DEFAULT_ENV)
        self.assertEqual(os.environ['PORT'], str(environment.DEFAULT_PORT))
        self.assertEqual(os.environ['RELOAD'], 'false')
        self.assertEqual(os.environ['COOKIE_SECURE'], 'true')

    def test_application_does_not_load_a_second_file_after_startup(self):
        selected = self.root / '.env.pre'
        selected.write_text('PORT=8080\nDATABASE_URL=pre-db\n')
        (self.root / '.env.prod').write_text('SECOND_FILE=must-not-load\n')
        self.assertEqual(environment.load_environment('staging'), selected.resolve())
        selected.write_text('PORT=9999\nLATE_VALUE=must-not-load\n')
        self.assertEqual(environment.load_environment('prod'), selected.resolve())
        self.assertEqual(os.environ['ENV'], 'pre')
        self.assertEqual(os.environ['PORT'], '8080')
        self.assertNotIn('LATE_VALUE', os.environ)
        self.assertNotIn('SECOND_FILE', os.environ)
