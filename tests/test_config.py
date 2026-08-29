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
    default_reminder_file,
    load_notification_preference,
    load_reminder_store,
    managed_quote_file,
    managed_reminder_file,
    notifications_enabled,
    read_user_config,
    reminder_file,
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

    def test_default_reminder_file_path(self):
        self.assertEqual(default_reminder_file(), self.config_dir / "reminders.json")

    def test_default_reminder_file_follows_xdg_config_home(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": elsewhere}):
                self.assertEqual(
                    default_reminder_file(),
                    Path(elsewhere) / "rem-bubbles" / "reminders.json",
                )

    def test_default_reminder_file_falls_back_to_dot_config(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("XDG_CONFIG_HOME", None)
                with mock.patch.object(Path, "home", return_value=Path(home)):
                    self.assertEqual(
                        default_reminder_file(),
                        Path(home) / ".config" / "rem-bubbles" / "reminders.json",
                    )

    def test_resolution_does_not_create_anything(self):
        user_config_dir()
        user_config_file()
        default_quote_file()
        default_reminder_file()
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


class ReminderConfigTests(IsolatedConfigTestCase):
    """``[reminders]`` follows exactly the same rules as ``[quotes]``."""

    def test_missing_config_declares_no_reminder_file(self):
        self.assertIsNone(read_user_config().reminder_file)

    def test_a_quote_only_config_stays_valid(self):
        # The Milestone 3 config, unmodified. This must keep working forever.
        self.write_config('[quotes]\nfile = "quotes.json"\n')
        config = read_user_config()
        self.assertEqual(config.quote_file, self.config_dir / "quotes.json")
        self.assertIsNone(config.reminder_file)

    def test_a_quote_only_config_still_finds_reminders(self):
        self.write_config('[quotes]\nfile = "quotes.json"\n')
        self.assertEqual(managed_reminder_file(), self.config_dir / "reminders.json")

    def test_reminders_table_without_file_key(self):
        self.write_config("[quotes]\n[reminders]\n")
        self.assertIsNone(read_user_config().reminder_file)

    def test_a_reminders_only_config_is_valid(self):
        self.write_config('[reminders]\nfile = "reminders.json"\n')
        config = read_user_config()
        self.assertIsNone(config.quote_file)
        self.assertEqual(config.reminder_file, self.config_dir / "reminders.json")

    def test_relative_path_resolves_against_the_config_directory(self):
        self.write_config('[reminders]\nfile = "reminders.json"\n')
        self.assertEqual(
            read_user_config().reminder_file, self.config_dir / "reminders.json"
        )

    def test_relative_subdirectory_path(self):
        self.write_config('[reminders]\nfile = "personal/mine.json"\n')
        self.assertEqual(
            read_user_config().reminder_file,
            self.config_dir / "personal" / "mine.json",
        )

    def test_relative_path_is_independent_of_the_working_directory(self):
        self.write_config('[reminders]\nfile = "reminders.json"\n')
        with tempfile.TemporaryDirectory() as elsewhere:
            previous = Path.cwd()
            os.chdir(elsewhere)
            try:
                self.assertEqual(
                    read_user_config().reminder_file,
                    self.config_dir / "reminders.json",
                )
            finally:
                os.chdir(previous)

    def test_absolute_path_is_used_verbatim(self):
        self.write_config('[reminders]\nfile = "/some/private/place/mine.json"\n')
        self.assertEqual(
            read_user_config().reminder_file, Path("/some/private/place/mine.json")
        )

    def test_tilde_is_expanded(self):
        with tempfile.TemporaryDirectory() as home:
            self.write_config('[reminders]\nfile = "~/notes/reminders.json"\n')
            with mock.patch.dict(os.environ, {"HOME": home}):
                self.assertEqual(
                    read_user_config().reminder_file,
                    Path(home) / "notes" / "reminders.json",
                )

    def test_dot_segments_are_normalised(self):
        self.write_config('[reminders]\nfile = "../rem-bubbles/reminders.json"\n')
        self.assertEqual(
            read_user_config().reminder_file, self.config_dir / "reminders.json"
        )

    def test_both_tables_are_read(self):
        self.write_config(
            '[quotes]\nfile = "q.json"\n\n[reminders]\nfile = "r.json"\n'
        )
        config = read_user_config()
        self.assertEqual(config.quote_file, self.config_dir / "q.json")
        self.assertEqual(config.reminder_file, self.config_dir / "r.json")

    def test_reading_does_not_create_the_reminder_file(self):
        self.write_config('[reminders]\nfile = "reminders.json"\n')
        read_user_config()
        self.assertFalse((self.config_dir / "reminders.json").exists())


class ReminderConfigErrorTests(IsolatedConfigTestCase):
    def assertRejects(self, text, needle):
        self.write_config(text)
        with self.assertRaises(ConfigError) as caught:
            read_user_config()
        self.assertIn(needle, str(caught.exception))

    def test_reminders_is_not_a_table(self):
        self.assertRejects(
            'reminders = "reminders.json"\n', "[reminders] must be a table"
        )

    def test_reminders_is_an_array(self):
        self.assertRejects('reminders = ["a", "b"]\n', "[reminders] must be a table")

    def test_file_is_not_a_string(self):
        self.assertRejects("[reminders]\nfile = 7\n", '"file" must be a string')

    def test_file_is_a_boolean(self):
        self.assertRejects("[reminders]\nfile = true\n", '"file" must be a string')

    def test_file_is_an_array(self):
        self.assertRejects('[reminders]\nfile = ["a.json"]\n', '"file" must be a string')

    def test_blank_file_value(self):
        self.assertRejects('[reminders]\nfile = ""\n', '"file" is blank')

    def test_whitespace_only_file_value(self):
        self.assertRejects('[reminders]\nfile = "   "\n', '"file" is blank')

    def test_the_message_names_the_reminders_table(self):
        self.write_config('[quotes]\nfile = "q.json"\n\n[reminders]\nfile = 7\n')
        with self.assertRaises(ConfigError) as caught:
            read_user_config()
        self.assertIn("[reminders]", str(caught.exception))

    def test_a_broken_quotes_table_is_reported_first(self):
        # Quotes are validated first, so the message stays predictable.
        self.write_config("[quotes]\nfile = 7\n\n[reminders]\nfile = 9\n")
        with self.assertRaises(ConfigError) as caught:
            read_user_config()
        self.assertIn("[quotes]", str(caught.exception))


class ReminderSourceTests(IsolatedConfigTestCase):
    """The runtime chain: explicit, then configured, then default. No fallback."""

    def test_defaults_to_the_xdg_location(self):
        self.assertEqual(reminder_file(), self.config_dir / "reminders.json")

    def test_follows_the_configured_path(self):
        self.write_config(f'[reminders]\nfile = "{self.xdg}/elsewhere.json"\n')
        self.assertEqual(reminder_file(), self.xdg / "elsewhere.json")

    def test_an_explicit_path_wins(self):
        self.write_config('[reminders]\nfile = "configured.json"\n')
        self.assertEqual(reminder_file("/tmp/explicit.json"), Path("/tmp/explicit.json"))

    def test_never_points_into_the_repository(self):
        # examples/reminders.json is documentation. It must never be loaded as
        # somebody's real reminders.
        self.assertNotIn("examples", reminder_file().parts)

    def test_missing_default_file_is_an_empty_store(self):
        store = load_reminder_store()
        self.assertEqual(len(store), 0)

    def test_missing_default_file_is_silent(self):
        import contextlib
        import io

        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            load_reminder_store()
        self.assertEqual(errors.getvalue(), "")

    def test_a_default_path_named_in_the_config_is_still_silent(self):
        # 'rem-bubbles init' writes exactly this, so warning here would put a
        # line on stderr at every launch until the first reminder is added.
        import contextlib
        import io

        self.write_config('[reminders]\nfile = "reminders.json"\n')
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            store = load_reminder_store()
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(len(store), 0)

    def test_a_missing_configured_file_elsewhere_is_reported(self):
        import contextlib
        import io

        self.write_config(f'[reminders]\nfile = "{self.xdg}/nowhere.json"\n')
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            store = load_reminder_store()
        self.assertIn("not found", errors.getvalue())
        self.assertEqual(len(store), 0)

    def test_malformed_reminder_data_degrades_to_empty(self):
        import contextlib
        import io

        self.config_dir.mkdir(parents=True, exist_ok=True)
        (self.config_dir / "reminders.json").write_text("{not json", encoding="utf-8")
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            store = load_reminder_store()
        self.assertIn("not valid JSON", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())
        self.assertEqual(len(store), 0)

    def test_a_malformed_config_degrades_to_empty(self):
        import contextlib
        import io

        self.write_config("[reminders]\nfile = 7\n")
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            store = load_reminder_store()
        self.assertIn("must be a string", errors.getvalue())
        self.assertEqual(len(store), 0)

    def test_a_valid_file_is_loaded(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        (self.config_dir / "reminders.json").write_text(
            '[{"id": "r", "text": "Do it.", "due_at": "2026-08-30T18:00:00"}]',
            encoding="utf-8",
        )
        store = load_reminder_store()
        self.assertEqual([r.id for r in store.reminders], ["r"])

    def test_loading_does_not_create_the_file(self):
        load_reminder_store()
        self.assertFalse((self.config_dir / "reminders.json").exists())
        self.assertFalse(self.config_dir.exists())


class ManagedReminderFileTests(IsolatedConfigTestCase):
    def test_defaults_to_the_xdg_location(self):
        self.assertEqual(managed_reminder_file(), self.config_dir / "reminders.json")

    def test_follows_the_configured_path(self):
        self.write_config('[reminders]\nfile = "/tmp/rem-bubbles-test/elsewhere.json"\n')
        self.assertEqual(
            managed_reminder_file(), Path("/tmp/rem-bubbles-test/elsewhere.json")
        )

    def test_never_points_into_the_repository(self):
        self.assertNotIn("examples", managed_reminder_file().parts)

    def test_malformed_config_propagates(self):
        self.write_config("[reminders]\nfile = 7\n")
        with self.assertRaises(ConfigError):
            managed_reminder_file()


# -- notifications ----------------------------------------------------------


class NotificationConfigTests(IsolatedConfigTestCase):
    """``[notifications].enabled`` — optional, boolean, and false by default."""

    def test_missing_config_means_disabled(self):
        self.assertFalse(read_user_config().notifications)
        self.assertFalse(notifications_enabled())

    def test_empty_config_means_disabled(self):
        self.write_config("")
        self.assertFalse(notifications_enabled())

    def test_a_missing_notifications_table_means_disabled(self):
        self.write_config('[quotes]\nfile = "quotes.json"\n')
        self.assertFalse(notifications_enabled())

    def test_a_notifications_table_without_the_key_means_disabled(self):
        self.write_config("[notifications]\n")
        self.assertFalse(notifications_enabled())

    def test_enabled_true(self):
        self.write_config("[notifications]\nenabled = true\n")
        self.assertTrue(notifications_enabled())

    def test_enabled_false(self):
        self.write_config("[notifications]\nenabled = false\n")
        self.assertFalse(notifications_enabled())

    def test_the_default_is_off(self):
        # Deliberate. Updating REM Bubbles must not start putting notifications
        # on the screen of somebody who never asked for them.
        self.write_config('[quotes]\nfile = "quotes.json"\n')
        self.assertFalse(read_user_config().notifications)

    def test_all_three_tables_together(self):
        self.write_config(
            '[quotes]\nfile = "q.json"\n\n'
            '[reminders]\nfile = "r.json"\n\n'
            "[notifications]\nenabled = true\n"
        )
        config = read_user_config()
        self.assertEqual(config.quote_file, self.config_dir / "q.json")
        self.assertEqual(config.reminder_file, self.config_dir / "r.json")
        self.assertTrue(config.notifications)

    def test_the_init_default_text_parses_as_disabled(self):
        from rem_bubbles.config import DEFAULT_CONFIG_TEXT

        self.write_config(DEFAULT_CONFIG_TEXT)
        self.assertFalse(notifications_enabled())

    def test_reading_does_not_create_anything(self):
        self.write_config("[notifications]\nenabled = true\n")
        notifications_enabled()
        self.assertFalse((self.config_dir / "quotes.json").exists())
        self.assertFalse((self.config_dir / "reminders.json").exists())


class NotificationConfigErrorTests(IsolatedConfigTestCase):
    def assertRejects(self, text, needle):
        self.write_config(text)
        with self.assertRaises(ConfigError) as caught:
            read_user_config()
        self.assertIn(needle, str(caught.exception))

    def test_notifications_is_not_a_table(self):
        self.assertRejects(
            "notifications = true\n", "[notifications] must be a table"
        )

    def test_notifications_is_a_string(self):
        self.assertRejects(
            'notifications = "on"\n', "[notifications] must be a table"
        )

    def test_notifications_is_an_array(self):
        self.assertRejects(
            "notifications = [1, 2]\n", "[notifications] must be a table"
        )

    def test_enabled_is_a_string(self):
        self.assertRejects(
            '[notifications]\nenabled = "true"\n', '"enabled" must be true or false'
        )

    def test_enabled_is_an_integer(self):
        # TOML distinguishes 1 from true, so this is a real mistake, not a
        # spelling of "on". Guessing either way would be guessing about
        # somebody's screen.
        self.assertRejects(
            "[notifications]\nenabled = 1\n", '"enabled" must be true or false'
        )

    def test_enabled_is_an_array(self):
        self.assertRejects(
            "[notifications]\nenabled = [true]\n", '"enabled" must be true or false'
        )

    def test_the_message_names_the_config_file(self):
        self.write_config("[notifications]\nenabled = 1\n")
        with self.assertRaises(ConfigError) as caught:
            read_user_config()
        self.assertIn(str(self.config_dir / "config.toml"), str(caught.exception))

    def test_a_broken_quotes_table_is_still_reported_first(self):
        self.write_config("[quotes]\nfile = 7\n\n[notifications]\nenabled = 1\n")
        with self.assertRaises(ConfigError) as caught:
            read_user_config()
        self.assertIn("[quotes]", str(caught.exception))

    def test_notifications_errors_do_not_break_quote_parsing(self):
        # The quote and reminder tables are read before the switch, so a broken
        # switch must not be able to hide a good path.
        self.write_config(
            '[quotes]\nfile = "q.json"\n\n[notifications]\nenabled = 1\n'
        )
        with self.assertRaises(ConfigError):
            read_user_config()
        # ...and with the switch fixed, the path is exactly what it always was.
        self.write_config(
            '[quotes]\nfile = "q.json"\n\n[notifications]\nenabled = true\n'
        )
        self.assertEqual(read_user_config().quote_file, self.config_dir / "q.json")


class NotificationPreferenceTests(IsolatedConfigTestCase):
    """The application's non-raising reader, used at launch."""

    def read(self):
        import contextlib
        import io

        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            enabled = load_notification_preference()
        return enabled, errors.getvalue()

    def test_a_healthy_config_is_read(self):
        self.write_config("[notifications]\nenabled = true\n")
        enabled, errors = self.read()
        self.assertTrue(enabled)
        self.assertEqual(errors, "")

    def test_a_missing_config_is_silent(self):
        enabled, errors = self.read()
        self.assertFalse(enabled)
        self.assertEqual(errors, "")

    def test_a_malformed_config_degrades_to_off(self):
        # Off, not on: a typo in a config file must not be able to switch
        # notifications on for somebody.
        self.write_config("[notifications]\nenabled = 1\n")
        enabled, errors = self.read()
        self.assertFalse(enabled)
        self.assertIn("must be true or false", errors)

    def test_a_malformed_config_reports_without_a_traceback(self):
        self.write_config("[quotes\nbroken")
        _, errors = self.read()
        self.assertIn("rem-bubbles:", errors)
        self.assertNotIn("Traceback", errors)


# -- backwards compatibility ------------------------------------------------


class LegacyConfigTests(IsolatedConfigTestCase):
    """Configs written by earlier milestones stay valid, unchanged, forever."""

    MILESTONE_3 = '[quotes]\nfile = "quotes.json"\n'
    MILESTONE_4 = (
        '[quotes]\nfile = "quotes.json"\n\n[reminders]\nfile = "reminders.json"\n'
    )

    def test_a_quote_only_config_is_still_valid(self):
        self.write_config(self.MILESTONE_3)
        config = read_user_config()
        self.assertEqual(config.quote_file, self.config_dir / "quotes.json")
        self.assertIsNone(config.reminder_file)
        self.assertFalse(config.notifications)

    def test_a_quote_only_config_still_resolves_reminders(self):
        self.write_config(self.MILESTONE_3)
        self.assertEqual(managed_reminder_file(), self.config_dir / "reminders.json")

    def test_a_quote_and_reminder_config_is_still_valid(self):
        self.write_config(self.MILESTONE_4)
        config = read_user_config()
        self.assertEqual(config.quote_file, self.config_dir / "quotes.json")
        self.assertEqual(config.reminder_file, self.config_dir / "reminders.json")
        self.assertFalse(config.notifications)

    def test_neither_legacy_config_switches_notifications_on(self):
        for text in (self.MILESTONE_3, self.MILESTONE_4):
            self.write_config(text)
            self.assertFalse(notifications_enabled())

    def test_reading_a_legacy_config_does_not_rewrite_it(self):
        self.write_config(self.MILESTONE_3)
        read_user_config()
        notifications_enabled()
        self.assertEqual(
            (self.config_dir / "config.toml").read_text(encoding="utf-8"),
            self.MILESTONE_3,
        )


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
