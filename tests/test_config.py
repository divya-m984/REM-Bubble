"""Tests for XDG path resolution and config.toml parsing.

Every test runs against a temporary ``XDG_CONFIG_HOME`` (and, where the default
branch is under test, a patched ``Path.home``), so the real
``~/.config/rem-bubbles`` is never read or written.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rem_bubbles.config import (
    ConfigError,
    default_quote_file,
    managed_quote_file,
    read_user_config,
    user_config_dir,
    user_config_file,
)


class IsolatedConfigTestCase(unittest.TestCase):
    """Base class giving each test its own empty config home."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.xdg = Path(self._temp.name)

        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.xdg)})
        patcher.start()
        self.addCleanup(patcher.stop)

        self.config_dir = self.xdg / "rem-bubbles"

    def write_config(self, text: str) -> Path:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        path = self.config_dir / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path


# -- directory resolution ---------------------------------------------------


class ConfigDirectoryTests(IsolatedConfigTestCase):
    def test_xdg_config_home_is_used_when_set(self):
        self.assertEqual(user_config_dir(), self.xdg / "rem-bubbles")

    def test_falls_back_to_dot_config_under_home(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("XDG_CONFIG_HOME", None)
                with mock.patch.object(Path, "home", return_value=Path(home)):
                    self.assertEqual(
                        user_config_dir(), Path(home) / ".config" / "rem-bubbles"
                    )

    def test_blank_xdg_config_home_falls_back(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "   "}):
                with mock.patch.object(Path, "home", return_value=Path(home)):
                    self.assertEqual(
                        user_config_dir(), Path(home) / ".config" / "rem-bubbles"
                    )

    def test_relative_xdg_config_home_is_ignored(self):
        # The XDG spec requires an absolute path; a relative one would otherwise
        # make the config location depend on the working directory.
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "relative/config"}):
                with mock.patch.object(Path, "home", return_value=Path(home)):
                    self.assertTrue(user_config_dir().is_absolute())
                    self.assertEqual(
                        user_config_dir(), Path(home) / ".config" / "rem-bubbles"
                    )

    def test_config_file_path(self):
        self.assertEqual(user_config_file(), self.config_dir / "config.toml")

    def test_default_quote_file_path(self):
        self.assertEqual(default_quote_file(), self.config_dir / "quotes.json")

    def test_resolution_does_not_create_anything(self):
        user_config_dir()
        user_config_file()
        default_quote_file()
        self.assertFalse(self.config_dir.exists())


# -- config.toml ------------------------------------------------------------


class ConfigReadingTests(IsolatedConfigTestCase):
    def test_missing_config_is_not_an_error(self):
        config = read_user_config()
        self.assertFalse(config.exists)
        self.assertIsNone(config.quote_file)

    def test_empty_config_declares_no_quote_file(self):
        self.write_config("")
        config = read_user_config()
        self.assertTrue(config.exists)
        self.assertIsNone(config.quote_file)

    def test_quotes_table_without_file_key(self):
        self.write_config("[quotes]\n")
        self.assertIsNone(read_user_config().quote_file)

    def test_relative_path_resolves_against_the_config_directory(self):
        self.write_config('[quotes]\nfile = "quotes.json"\n')
        self.assertEqual(read_user_config().quote_file, self.config_dir / "quotes.json")

    def test_relative_subdirectory_path(self):
        self.write_config('[quotes]\nfile = "personal/mine.json"\n')
        self.assertEqual(
            read_user_config().quote_file, self.config_dir / "personal" / "mine.json"
        )

    def test_relative_path_is_independent_of_the_working_directory(self):
        self.write_config('[quotes]\nfile = "quotes.json"\n')
        with tempfile.TemporaryDirectory() as elsewhere:
            previous = Path.cwd()
            os.chdir(elsewhere)
            try:
                self.assertEqual(
                    read_user_config().quote_file, self.config_dir / "quotes.json"
                )
            finally:
                os.chdir(previous)

    def test_absolute_path_is_used_verbatim(self):
        self.write_config('[quotes]\nfile = "/some/private/location/my-quotes.json"\n')
        self.assertEqual(
            read_user_config().quote_file,
            Path("/some/private/location/my-quotes.json"),
        )

    def test_tilde_is_expanded(self):
        with tempfile.TemporaryDirectory() as home:
            self.write_config('[quotes]\nfile = "~/notes/quotes.json"\n')
            with mock.patch.dict(os.environ, {"HOME": home}):
                self.assertEqual(
                    read_user_config().quote_file,
                    Path(home) / "notes" / "quotes.json",
                )

    def test_dot_segments_are_normalised(self):
        self.write_config('[quotes]\nfile = "../rem-bubbles/quotes.json"\n')
        self.assertEqual(read_user_config().quote_file, self.config_dir / "quotes.json")

    def test_reading_does_not_create_the_quote_file(self):
        self.write_config('[quotes]\nfile = "quotes.json"\n')
        read_user_config()
        self.assertFalse((self.config_dir / "quotes.json").exists())


class ConfigErrorTests(IsolatedConfigTestCase):
    def assertRejects(self, text, needle):
        self.write_config(text)
        with self.assertRaises(ConfigError) as caught:
            read_user_config()
        self.assertIn(needle, str(caught.exception))

    def test_malformed_toml(self):
        self.assertRejects("[quotes\nfile = ", "not valid TOML")

    def test_malformed_toml_names_the_file(self):
        self.write_config("this is not toml at all")
        with self.assertRaises(ConfigError) as caught:
            read_user_config()
        self.assertIn(str(self.config_dir / "config.toml"), str(caught.exception))

    def test_quotes_is_not_a_table(self):
        self.assertRejects('quotes = "quotes.json"\n', "[quotes] must be a table")

    def test_quotes_is_an_array(self):
        self.assertRejects('quotes = ["a", "b"]\n', "[quotes] must be a table")

    def test_file_is_not_a_string(self):
        self.assertRejects("[quotes]\nfile = 7\n", '"file" must be a string')

    def test_file_is_a_boolean(self):
        self.assertRejects("[quotes]\nfile = true\n", '"file" must be a string')

    def test_file_is_an_array(self):
        self.assertRejects('[quotes]\nfile = ["a.json"]\n', '"file" must be a string')

    def test_blank_file_value(self):
        self.assertRejects('[quotes]\nfile = ""\n', '"file" is blank')

    def test_whitespace_only_file_value(self):
        self.assertRejects('[quotes]\nfile = "   "\n', '"file" is blank')

    def test_config_path_is_a_directory(self):
        (self.config_dir / "config.toml").mkdir(parents=True)
        with self.assertRaises(ConfigError) as caught:
            read_user_config()
        self.assertIn("directory", str(caught.exception))


# -- management target ------------------------------------------------------


class ManagedQuoteFileTests(IsolatedConfigTestCase):
    def test_defaults_to_the_xdg_location(self):
        self.assertEqual(managed_quote_file(), self.config_dir / "quotes.json")

    def test_follows_the_configured_path(self):
        self.write_config('[quotes]\nfile = "/tmp/rem-bubbles-test/elsewhere.json"\n')
        self.assertEqual(
            managed_quote_file(), Path("/tmp/rem-bubbles-test/elsewhere.json")
        )

    def test_never_points_into_the_repository(self):
        # examples/ and a checkout-local quotes.json are read-only fallbacks.
        self.assertNotIn("examples", managed_quote_file().parts)

    def test_malformed_config_propagates(self):
        self.write_config("[quotes]\nfile = 7\n")
        with self.assertRaises(ConfigError):
            managed_quote_file()


if __name__ == "__main__":
    unittest.main()
