"""Portable model defaults for newly registered desktop accounts."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from services.api import server


class ModelDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(server.SCHEMA)
        self.addCleanup(self.connection.close)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'model-defaults.local.json'
        self.defaults = {
            'provider': 'OpenAI', 'base_url': 'https://model.example/v1',
            'model': 'default-chat', 'api_key': 'default-secret', 'enabled': True,
            'embedding_base_url': 'https://embedding.example/v1',
            'embedding_model': 'default-embedding', 'embedding_api_key': 'embedding-secret',
        }
        self.path.write_text(json.dumps(self.defaults))
        patcher = patch.object(server, 'DEFAULT_MODEL_CONFIG_PATH', self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.dict(server.os.environ, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_fresh_registration_receives_enabled_defaults_and_masked_keys(self):
        registered = server.handle_register(self.connection, {'account': 'new-account', 'password': 'test-password'})
        uid = registered['user']['id']
        settings = server.config(self.connection, uid)
        for key, value in self.defaults.items():
            self.assertEqual(settings[key], value)
        public = server.public_config(self.connection, uid)
        self.assertEqual(public['api_key'], '********')
        self.assertEqual(public['embedding_api_key'], '********')
        self.assertNotIn('default-secret', json.dumps(public))

    def test_save_after_masked_read_keeps_key_and_does_not_affect_other_accounts(self):
        server.public_config(self.connection, 'first')
        self.connection.execute("UPDATE settings SET value = 'custom-chat' WHERE key = 'first:model'")
        self.assertEqual(server.config(self.connection, 'first')['api_key'], 'default-secret')
        self.assertEqual(server.config(self.connection, 'first')['model'], 'custom-chat')
        self.assertEqual(server.config(self.connection, 'second')['model'], 'default-chat')
        self.path.unlink()
        self.assertEqual(server.config(self.connection, 'first')['api_key'], 'default-secret')

    def test_existing_configuration_is_not_mixed_with_shared_credentials(self):
        self.connection.executemany('INSERT INTO settings VALUES (?, ?)', [
            ('existing:base_url', 'https://personal.example/v1'),
            ('existing:model', 'personal-model'), ('existing:enabled', 'false'),
        ])
        settings = server.config(self.connection, 'existing')
        self.assertEqual(settings['base_url'], 'https://personal.example/v1')
        self.assertEqual(settings['api_key'], '')
        self.assertFalse(settings['enabled'])

    def test_missing_defaults_preserves_unconfigured_behavior(self):
        self.path.unlink()
        settings = server.config(self.connection, 'new')
        self.assertEqual(settings['api_key'], '')
        self.assertFalse(settings['enabled'])


if __name__ == '__main__':
    unittest.main()
