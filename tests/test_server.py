import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.server import load_environment


class ServerConfigurationTests(unittest.TestCase):
    def test_windows_utf8_bom_comments_and_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('# comment\nBTL_TEST_SECRET=ab+c/==\n\n', encoding='utf-8-sig')
            with patch.dict(os.environ, {}, clear=True):
                load_environment(path)
                self.assertEqual(os.environ['BTL_TEST_SECRET'], 'ab+c/==')

    def test_malformed_setting_fails_without_echoing_its_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('secret-value-without-key\n', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, '^Invalid .env setting at line 1$'):
                load_environment(path)
