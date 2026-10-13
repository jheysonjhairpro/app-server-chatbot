import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import PROJECT_DIR, Settings
from indexing.settings import Settings as IndexSettings


class ConfigurationTests(unittest.TestCase):
    def test_defaults_do_not_depend_on_working_directory(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            try:
                os.chdir(directory)
                for settings_type in (Settings, IndexSettings):
                    settings = settings_type(_env_file=None)
                    self.assertEqual(settings.chroma_path, PROJECT_DIR / 'chroma_db')
                    self.assertEqual(settings.state_path, PROJECT_DIR / 'data/web.sqlite3')
                self.assertEqual(Settings.model_config['env_file'], PROJECT_DIR / '.env')
            finally:
                os.chdir(previous)

    def test_relative_environment_paths_and_absolute_override(self):
        with tempfile.TemporaryDirectory() as directory:
            absolute = Path(directory).resolve() / 'catalog.sqlite3'
            with patch.dict(os.environ, {'CHROMA_PATH': 'custom/vectors', 'STATE_PATH': str(absolute)}, clear=True):
                settings = Settings(_env_file=None)
            self.assertEqual(settings.chroma_path, PROJECT_DIR / 'custom/vectors')
            self.assertEqual(settings.state_path, absolute)


if __name__ == '__main__':
    unittest.main()
