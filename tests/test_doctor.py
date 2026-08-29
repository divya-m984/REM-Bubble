"""Tests for ``rem-bubbles doctor``.

Two properties matter and both are asserted here. The first is that it stays
headless: a diagnostic that needs a compositor is useless in exactly the
situation where somebody reaches for one, so importing GTK is checked in a fresh
interpreter rather than trusted. The second is that it stays discreet: quotes
and reminders are personal, and a diagnostic is the kind of output that ends up
pasted into a bug report.

Every test runs against a temporary ``XDG_CONFIG_HOME``, so the real
``~/.config/rem-bubbles`` is never read or written.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from test_cli import run
from test_config import IsolatedConfigTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]

VALID_QUOTES = (
    '[{"id": "q", "text": "A private quote nobody should see.", '
    '"author": "Someone Private", "enabled": true}]'
)
VALID_REMINDERS = (
    '[{"id": "r", "text": "A private reminder nobody should see.", '
    '"due_at": "2026-08-30T18:00:00"}]'
)


class DoctorTestCase(IsolatedConfigTestCase):
    def write_quotes(self, text: str) -> Path:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        path = self.config_dir / "quotes.json"
        path.write_text(text, encoding="utf-8")
        return path

    def write_reminders(self, text: str) -> Path:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        path = self.config_dir / "reminders.json"
        path.write_text(text, encoding="utf-8")
        return path

    def healthy(self):
        self.write_config(
            '[quotes]\nfile = "quotes.json"\n\n'
            '[reminders]\nfile = "reminders.json"\n\n'
            "[notifications]\nenabled = false\n"
        )
        self.write_quotes(VALID_QUOTES)
        self.write_reminders(VALID_REMINDERS)


# -- headless guarantee -----------------------------------------------------


class DoctorIsHeadlessTests(unittest.TestCase):
    def run_doctor(self, extra_script: str = ""):
        script = (
            "import sys, io, contextlib\n"
            "from rem_bubbles import cli\n"
            "out = io.StringIO()\n"
            "with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):\n"
            "    cli.main(['doctor'])\n"
            "print(sorted(m for m in sys.modules "
            "if m.split('.')[0] in {'gi', 'gtk'} "
            "or 'layershell' in m.lower() or 'layer_shell' in m.lower()))\n"
            + extra_script
        )
        environment = {k: v for k, v in os.environ.items()}
        environment.pop("WAYLAND_DISPLAY", None)
        environment.pop("DISPLAY", None)
        return subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=environment,
        )

    def test_doctor_imports_no_gtk(self):
        result = self.run_doctor()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "[]")

    def test_doctor_runs_with_no_display_at_all(self):
        environment = {k: v for k, v in os.environ.items()}
        environment.pop("WAYLAND_DISPLAY", None)
        environment.pop("DISPLAY", None)
        result = subprocess.run(
            [sys.executable, "-m", "rem_bubbles.cli", "doctor"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=environment,
        )
        self.assertIn("REM Bubbles doctor", result.stdout)

    def test_no_wayland_warning_is_printed(self):
        environment = {k: v for k, v in os.environ.items()}
        environment.pop("WAYLAND_DISPLAY", None)
        environment.pop("DISPLAY", None)
        result = subprocess.run(
            [sys.executable, "-m", "rem_bubbles.cli", "doctor"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=environment,
        )
        for noise in ("Gtk", "Gdk", "layer surface", "libwayland", "Traceback"):
            self.assertNotIn(noise, result.stderr)


# -- what it reports --------------------------------------------------------


class DoctorReportTests(DoctorTestCase):
    def test_a_healthy_environment_exits_zero(self):
        self.healthy()
        status, output, _ = run(["doctor"])
        self.assertEqual(status, 0)
        self.assertIn("No problems found.", output)

    def test_it_reports_the_version(self):
        from rem_bubbles import __version__

        _, output, _ = run(["doctor"])
        self.assertIn(__version__, output)

    def test_it_reports_the_python_version(self):
        _, output, _ = run(["doctor"])
        self.assertIn(sys.version.split()[0], output)

    def test_it_reports_the_config_directory(self):
        _, output, _ = run(["doctor"])
        self.assertIn(str(self.config_dir), output)

    def test_it_follows_an_xdg_override(self):
        # The whole test class already runs under a temporary XDG_CONFIG_HOME;
        # this asserts the report actually reflects it rather than the real home.
        _, output, _ = run(["doctor"])
        self.assertIn(str(self.xdg), output)
        self.assertNotIn(str(Path.home() / ".config" / "rem-bubbles"), output)

    def test_it_reports_the_config_file(self):
        self.healthy()
        _, output, _ = run(["doctor"])
        self.assertIn(str(self.config_dir / "config.toml"), output)

    def test_it_reports_the_resolved_quote_path(self):
        self.healthy()
        _, output, _ = run(["doctor"])
        self.assertIn(str(self.config_dir / "quotes.json"), output)

    def test_it_reports_the_resolved_reminder_path(self):
        self.healthy()
        _, output, _ = run(["doctor"])
        self.assertIn(str(self.config_dir / "reminders.json"), output)

    def test_it_reports_a_configured_path_elsewhere(self):
        elsewhere = self.xdg / "somewhere" / "mine.json"
        elsewhere.parent.mkdir(parents=True)
        elsewhere.write_text(VALID_REMINDERS, encoding="utf-8")
        self.write_config(f'[reminders]\nfile = "{elsewhere}"\n')
        _, output, _ = run(["doctor"])
        self.assertIn(str(elsewhere), output)

    def test_it_reports_the_executable(self):
        _, output, _ = run(["doctor"])
        self.assertIn("Executable:", output)

    def test_it_reports_the_wayland_variable(self):
        with mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-9"}):
            _, output, _ = run(["doctor"])
        self.assertIn("WAYLAND_DISPLAY: wayland-9", output)

    def test_it_reports_a_missing_wayland_variable_without_failing(self):
        environment = {k: v for k, v in os.environ.items()}
        environment.pop("WAYLAND_DISPLAY", None)
        with mock.patch.dict(os.environ, environment, clear=True):
            status, output, _ = run(["doctor"])
        self.assertEqual(status, 0)
        self.assertIn("not set", output)

    def test_it_reports_the_hyprland_signature_presence(self):
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "abc123"}):
            _, output, _ = run(["doctor"])
        self.assertIn("HYPRLAND_INSTANCE_SIGNATURE: set", output)

    def test_it_does_not_print_the_hyprland_signature_itself(self):
        # A session identifier, not a setting. Presence is the diagnostic.
        with mock.patch.dict(
            os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "a-secret-session-token"}
        ):
            _, output, _ = run(["doctor"])
        self.assertNotIn("a-secret-session-token", output)

    def test_it_reports_a_missing_hyprland_signature(self):
        environment = {k: v for k, v in os.environ.items()}
        environment.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
        with mock.patch.dict(os.environ, environment, clear=True):
            _, output, _ = run(["doctor"])
        self.assertIn("HYPRLAND_INSTANCE_SIGNATURE: not set", output)


class DoctorNotificationTests(DoctorTestCase):
    def test_notifications_disabled_is_reported(self):
        self.write_config("[notifications]\nenabled = false\n")
        _, output, _ = run(["doctor"])
        self.assertIn("Notifications: disabled", output)

    def test_notifications_enabled_is_reported(self):
        self.write_config("[notifications]\nenabled = true\n")
        status, output, _ = run(["doctor"])
        self.assertEqual(status, 0)
        self.assertIn("Notifications: enabled", output)

    def test_no_config_reports_disabled(self):
        _, output, _ = run(["doctor"])
        self.assertIn("Notifications: disabled", output)

    def test_a_legacy_config_reports_disabled(self):
        self.write_config('[quotes]\nfile = "quotes.json"\n')
        _, output, _ = run(["doctor"])
        self.assertIn("Notifications: disabled", output)


# -- problems ---------------------------------------------------------------


class DoctorProblemTests(DoctorTestCase):
    def test_a_missing_reminder_file_is_not_a_problem(self):
        self.write_config('[quotes]\nfile = "quotes.json"\n')
        self.write_quotes(VALID_QUOTES)
        status, output, _ = run(["doctor"])
        self.assertEqual(status, 0)
        self.assertIn("none yet", output)

    def test_a_missing_quote_file_is_not_a_problem(self):
        # The runtime chain falls through to repository data and then to one
        # built-in quote, so the bubble always has something to show.
        status, output, _ = run(["doctor"])
        self.assertEqual(status, 0)
        self.assertIn("not created yet", output)

    def test_a_completely_empty_environment_exits_zero(self):
        status, _, _ = run(["doctor"])
        self.assertEqual(status, 0)

    def test_a_malformed_config_exits_one(self):
        self.write_config("[quotes\nbroken")
        status, output, errors = run(["doctor"])
        self.assertEqual(status, 1)
        self.assertIn("MALFORMED", output)
        self.assertIn("config.toml", errors)

    def test_a_malformed_config_still_produces_a_report(self):
        self.write_config("[quotes\nbroken")
        _, output, _ = run(["doctor"])
        self.assertIn("REM Bubbles doctor", output)
        self.assertIn("Python:", output)

    def test_a_malformed_config_does_not_traceback(self):
        self.write_config("[quotes\nbroken")
        _, output, errors = run(["doctor"])
        self.assertNotIn("Traceback", output + errors)

    def test_malformed_quotes_exit_one(self):
        self.write_config('[quotes]\nfile = "quotes.json"\n')
        self.write_quotes("{not json")
        status, output, errors = run(["doctor"])
        self.assertEqual(status, 1)
        self.assertIn("MALFORMED", output)
        self.assertIn("quote file", errors)

    def test_malformed_reminders_exit_one(self):
        self.write_config('[reminders]\nfile = "reminders.json"\n')
        self.write_reminders('[{"id": "r"}]')
        status, output, errors = run(["doctor"])
        self.assertEqual(status, 1)
        self.assertIn("MALFORMED", output)
        self.assertIn("reminder file", errors)

    def test_malformed_reminders_do_not_hide_healthy_quotes(self):
        self.write_config(
            '[quotes]\nfile = "quotes.json"\n\n[reminders]\nfile = "reminders.json"\n'
        )
        self.write_quotes(VALID_QUOTES)
        self.write_reminders("{not json")
        status, output, _ = run(["doctor"])
        self.assertEqual(status, 1)
        self.assertIn("1 total, 1 enabled", output)

    def test_a_quote_file_with_nothing_enabled_is_a_problem(self):
        self.write_config('[quotes]\nfile = "quotes.json"\n')
        self.write_quotes('[{"id": "q", "text": "Off.", "enabled": false}]')
        status, _, errors = run(["doctor"])
        self.assertEqual(status, 1)
        self.assertIn("enabled", errors)


# -- discretion -------------------------------------------------------------


class DoctorDiscretionTests(DoctorTestCase):
    def test_quote_text_is_never_printed(self):
        self.healthy()
        _, output, errors = run(["doctor"])
        self.assertNotIn("A private quote nobody should see.", output + errors)

    def test_quote_authors_are_never_printed(self):
        self.healthy()
        _, output, errors = run(["doctor"])
        self.assertNotIn("Someone Private", output + errors)

    def test_reminder_text_is_never_printed(self):
        self.healthy()
        _, output, errors = run(["doctor"])
        self.assertNotIn("A private reminder nobody should see.", output + errors)

    def test_counts_are_printed_instead(self):
        self.healthy()
        _, output, _ = run(["doctor"])
        self.assertIn("1 total, 1 enabled", output)

    def test_a_malformed_file_report_does_not_dump_the_file(self):
        # The parse error names the reminder that broke, which is a diagnostic;
        # it must not turn into a listing of everything else in the file.
        self.write_config('[reminders]\nfile = "reminders.json"\n')
        self.write_reminders(
            '[{"id": "ok", "text": "A private reminder nobody should see.", '
            '"due_at": "2026-08-30T18:00:00"}, {"id": "bad"}]'
        )
        _, output, errors = run(["doctor"])
        self.assertNotIn("A private reminder nobody should see.", output + errors)


# -- doctor changes nothing -------------------------------------------------


class DoctorWritesNothingTests(DoctorTestCase):
    def test_it_does_not_create_the_config_directory(self):
        run(["doctor"])
        self.assertFalse(self.config_dir.exists())

    def test_it_does_not_create_a_config_file(self):
        run(["doctor"])
        self.assertFalse((self.config_dir / "config.toml").exists())

    def test_it_does_not_create_data_files(self):
        self.write_config('[quotes]\nfile = "quotes.json"\n')
        run(["doctor"])
        self.assertFalse((self.config_dir / "quotes.json").exists())
        self.assertFalse((self.config_dir / "reminders.json").exists())

    def test_it_leaves_existing_files_byte_for_byte(self):
        self.healthy()
        before = {
            path: path.read_bytes() for path in sorted(self.config_dir.iterdir())
        }
        run(["doctor"])
        after = {path: path.read_bytes() for path in sorted(self.config_dir.iterdir())}
        self.assertEqual(before, after)

    def test_running_it_twice_changes_nothing(self):
        self.healthy()
        run(["doctor"])
        listing = sorted(p.name for p in self.config_dir.iterdir())
        run(["doctor"])
        self.assertEqual(sorted(p.name for p in self.config_dir.iterdir()), listing)


if __name__ == "__main__":
    unittest.main()
