"""Tests for command parsing, dispatch, exit statuses and ``init``.

The import-isolation tests here are the automated half of the Milestone 3
guarantee that management commands never touch GTK: importing
:mod:`rem_bubbles.cli` must not pull ``gi`` into ``sys.modules``.
"""

import contextlib
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from rem_bubbles import cli
from rem_bubbles.config import read_user_config

from test_config import IsolatedConfigTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]


def run(argv):
    """Run the CLI, capturing output. Returns (status, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        status = cli.main(argv)
    return status, out.getvalue(), err.getvalue()


def run_expecting_exit(argv):
    """Run the CLI for a command argparse handles itself (--help, usage errors)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            cli.main(argv)
            status = 0
        except SystemExit as exc:
            status = exc.code if isinstance(exc.code, int) else 1
    return status, out.getvalue(), err.getvalue()


# -- import isolation -------------------------------------------------------


class ImportIsolationTests(unittest.TestCase):
    def test_importing_the_cli_does_not_import_gtk(self):
        # Runs in a fresh interpreter so an already-imported gi from another
        # test cannot mask a regression.
        script = (
            "import sys; import rem_bubbles.cli; "
            "print(sorted(m for m in sys.modules "
            "if m.split('.')[0] in {'gi', 'gtk', 'Gtk'}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env={**os.environ, "WAYLAND_DISPLAY": "", "DISPLAY": ""},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "[]")

    def test_help_works_without_a_display(self):
        environment = {k: v for k, v in os.environ.items()}
        environment.pop("WAYLAND_DISPLAY", None)
        environment.pop("DISPLAY", None)
        result = subprocess.run(
            [sys.executable, "-m", "rem_bubbles.cli", "--help"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rem-bubbles", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_config_and_stores_import_no_gtk(self):
        script = (
            "import sys; import rem_bubbles.config, rem_bubbles.quote_store, "
            "rem_bubbles.reminder_store, rem_bubbles.persistence, "
            "rem_bubbles.notifications; "
            "print('gi' in sys.modules)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, cwd=REPO_ROOT
        )
        self.assertEqual(result.stdout.strip(), "False")

    def test_the_notification_engine_imports_no_gtk(self):
        # Notification *policy* is GTK-free on purpose: it is what makes every
        # deduplication rule testable without a desktop notification daemon.
        script = (
            "import sys; import rem_bubbles.notifications; "
            "print(sorted(m for m in sys.modules "
            "if m.split('.')[0] in {'gi', 'gtk'}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, cwd=REPO_ROOT
        )
        self.assertEqual(result.stdout.strip(), "[]")

    def test_the_cli_does_not_import_the_application_module(self):
        # rem_bubbles.app is where libgtk4-layer-shell is loaded. Importing it
        # for a headless command would defeat the whole arrangement.
        script = "import sys; import rem_bubbles.cli; print('rem_bubbles.app' in sys.modules)"
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env={**os.environ, "WAYLAND_DISPLAY": "", "DISPLAY": ""},
        )
        self.assertEqual(result.stdout.strip(), "False")

    def test_headless_commands_import_no_gtk(self):
        for argv in (["--help"], ["doctor"], ["integration", "hyprland"]):
            with self.subTest(argv=argv):
                script = (
                    "import sys, io, contextlib\n"
                    "from rem_bubbles import cli\n"
                    "try:\n"
                    "    with contextlib.redirect_stdout(io.StringIO()), "
                    "contextlib.redirect_stderr(io.StringIO()):\n"
                    f"        cli.main({argv!r})\n"
                    "except SystemExit:\n"
                    "    pass\n"
                    "print(sorted(m for m in sys.modules "
                    "if m.split('.')[0] in {'gi', 'gtk'} "
                    "or m == 'rem_bubbles.app' "
                    "or 'layershell' in m.lower() or 'layer_shell' in m.lower()))\n"
                )
                environment = {k: v for k, v in os.environ.items()}
                environment.pop("WAYLAND_DISPLAY", None)
                environment.pop("DISPLAY", None)
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    capture_output=True,
                    text=True,
                    cwd=REPO_ROOT,
                    env=environment,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "[]")

    def test_the_reminder_engine_imports_no_layer_shell(self):
        # The GTK-free guarantee is about more than 'gi': loading
        # libgtk4-layer-shell out of order is what produced the Milestone 1 bug.
        script = (
            "import sys; import rem_bubbles.cli; "
            "print(sorted(m for m in sys.modules "
            "if 'layershell' in m.lower() or 'layer_shell' in m.lower() "
            "or m.split('.')[0] in {'gi', 'ctypes'}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env={**os.environ, "WAYLAND_DISPLAY": "", "DISPLAY": ""},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "[]")


# -- parsing ----------------------------------------------------------------


class ParserTests(unittest.TestCase):
    def parse(self, argv):
        return cli.build_parser().parse_args(argv)

    def test_no_command_means_gui(self):
        self.assertIsNone(self.parse([]).command)

    def test_explicit_gui_command(self):
        self.assertEqual(self.parse(["gui"]).command, "gui")

    def test_init_command(self):
        self.assertEqual(self.parse(["init"]).command, "init")

    def test_quote_list(self):
        args = self.parse(["quote", "list"])
        self.assertEqual((args.command, args.quote_command), ("quote", "list"))

    def test_quote_add_arguments(self):
        args = self.parse(["quote", "add", "Text.", "--author", "Me", "--id", "x"])
        self.assertEqual(args.text, "Text.")
        self.assertEqual(args.author, "Me")
        self.assertEqual(args.id, "x")
        self.assertFalse(args.disabled)

    def test_quote_add_disabled_flag(self):
        self.assertTrue(self.parse(["quote", "add", "Text.", "--disabled"]).disabled)

    def test_quote_add_defaults(self):
        args = self.parse(["quote", "add", "Text."])
        self.assertIsNone(args.author)
        self.assertIsNone(args.id)

    def test_id_bearing_commands(self):
        for name in ("remove", "enable", "disable"):
            self.assertEqual(self.parse(["quote", name, "some-id"]).id, "some-id")

    def test_help_exits_zero(self):
        status, output, _ = run_expecting_exit(["--help"])
        self.assertEqual(status, 0)
        self.assertIn("quote", output)
        self.assertIn("init", output)

    def test_quote_help_exits_zero(self):
        status, output, _ = run_expecting_exit(["quote", "--help"])
        self.assertEqual(status, 0)
        for name in ("list", "add", "remove", "enable", "disable"):
            self.assertIn(name, output)

    def test_version_exits_zero(self):
        status, output, _ = run_expecting_exit(["--version"])
        self.assertEqual(status, 0)
        self.assertIn("rem-bubbles", output)

    def test_quote_without_an_action_is_a_usage_error(self):
        status, _, _ = run_expecting_exit(["quote"])
        self.assertEqual(status, 2)

    def test_unknown_command_is_a_usage_error(self):
        status, _, _ = run_expecting_exit(["nonsense"])
        self.assertEqual(status, 2)

    def test_add_without_text_is_a_usage_error(self):
        status, _, _ = run_expecting_exit(["quote", "add"])
        self.assertEqual(status, 2)

    def test_remove_without_an_id_is_a_usage_error(self):
        status, _, _ = run_expecting_exit(["quote", "remove"])
        self.assertEqual(status, 2)

    # -- reminder subcommands ---------------------------------------------

    def test_reminder_list(self):
        args = self.parse(["reminder", "list"])
        self.assertEqual((args.command, args.reminder_command), ("reminder", "list"))

    def test_reminder_add_arguments(self):
        args = self.parse(
            ["reminder", "add", "Do it.", "--at", "2026-08-30 18:00", "--id", "x"]
        )
        self.assertEqual(args.text, "Do it.")
        self.assertEqual(args.at, "2026-08-30 18:00")
        self.assertEqual(args.id, "x")
        self.assertEqual(args.repeat, "none")
        self.assertFalse(args.disabled)

    def test_reminder_add_repeat_choices(self):
        for value in ("none", "daily", "weekly"):
            args = self.parse(
                ["reminder", "add", "Do it.", "--at", "2026-08-30 18:00",
                 "--repeat", value]
            )
            self.assertEqual(args.repeat, value)

    def test_reminder_add_rejects_an_unsupported_repeat(self):
        status, _, _ = run_expecting_exit(
            ["reminder", "add", "Do it.", "--at", "2026-08-30 18:00",
             "--repeat", "monthly"]
        )
        self.assertEqual(status, 2)

    def test_reminder_add_disabled_flag(self):
        args = self.parse(
            ["reminder", "add", "Do it.", "--at", "2026-08-30 18:00", "--disabled"]
        )
        self.assertTrue(args.disabled)

    def test_reminder_add_requires_at(self):
        status, _, _ = run_expecting_exit(["reminder", "add", "Do it."])
        self.assertEqual(status, 2)

    def test_reminder_id_bearing_commands(self):
        for name in ("remove", "enable", "disable"):
            args = self.parse(["reminder", name, "some-id"])
            self.assertEqual(args.id, "some-id")

    def test_reminder_without_an_action_is_a_usage_error(self):
        status, _, _ = run_expecting_exit(["reminder"])
        self.assertEqual(status, 2)

    def test_reminder_help_exits_zero(self):
        status, output, _ = run_expecting_exit(["reminder", "--help"])
        self.assertEqual(status, 0)
        for name in ("list", "add", "remove", "enable", "disable"):
            self.assertIn(name, output)

    def test_top_level_help_lists_reminder(self):
        _, output, _ = run_expecting_exit(["--help"])
        self.assertIn("reminder", output)

    def test_top_level_help_lists_the_milestone_5_commands(self):
        _, output, _ = run_expecting_exit(["--help"])
        self.assertIn("doctor", output)
        self.assertIn("integration", output)

    def test_doctor_takes_no_arguments(self):
        self.assertEqual(self.parse(["doctor"]).command, "doctor")
        status, _, _ = run_expecting_exit(["doctor", "extra"])
        self.assertEqual(status, 2)

    def test_no_daemon_or_service_command_exists(self):
        # Milestone 5 is desktop integration, not a background service: the
        # running GUI is the only scheduler there is.
        for name in ("daemon", "service", "watch", "start", "stop"):
            status, _, _ = run_expecting_exit([name])
            self.assertEqual(status, 2)

    def test_no_systemd_integration_target_exists(self):
        status, _, _ = run_expecting_exit(["integration", "systemd"])
        self.assertEqual(status, 2)

    def test_no_edit_command_exists_yet(self):
        # Rescheduling is remove-then-add for this milestone, on purpose.
        for name in ("edit", "reschedule", "reset"):
            status, _, _ = run_expecting_exit(["reminder", name, "an-id"])
            self.assertEqual(status, 2)


# -- GUI dispatch -----------------------------------------------------------


class GuiDispatchTests(unittest.TestCase):
    def test_no_command_calls_run_gui(self):
        with mock.patch.object(cli, "run_gui", return_value=0) as launch:
            self.assertEqual(cli.main([]), 0)
        launch.assert_called_once_with()

    def test_gui_command_calls_run_gui(self):
        with mock.patch.object(cli, "run_gui", return_value=0) as launch:
            self.assertEqual(cli.main(["gui"]), 0)
        launch.assert_called_once_with()

    def test_gui_exit_status_is_propagated(self):
        with mock.patch.object(cli, "run_gui", return_value=3):
            self.assertEqual(cli.main(["gui"]), 3)

    def test_quote_commands_never_launch_the_gui(self):
        with mock.patch.object(cli, "run_gui", return_value=0) as launch:
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(io.StringIO()):
                    cli.main(["quote", "list"])
        launch.assert_not_called()

    def test_reminder_commands_never_launch_the_gui(self):
        with mock.patch.object(cli, "run_gui", return_value=0) as launch:
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(io.StringIO()):
                    cli.main(["reminder", "list"])
        launch.assert_not_called()

    def test_doctor_never_launches_the_gui(self):
        with mock.patch.object(cli, "run_gui", return_value=0) as launch:
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(io.StringIO()):
                    cli.main(["doctor"])
        launch.assert_not_called()

    def test_integration_never_launches_the_gui(self):
        with mock.patch.object(cli, "run_gui", return_value=0) as launch:
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(io.StringIO()):
                    cli.main(["integration", "hyprland"])
        launch.assert_not_called()

    def test_run_gui_passes_only_the_program_name(self):
        fake = mock.Mock(return_value=0)
        module = mock.Mock(main=fake)
        with mock.patch.dict(sys.modules, {"rem_bubbles.app": module}):
            with mock.patch.object(sys, "argv", ["rem-bubbles", "gui"]):
                self.assertEqual(cli.run_gui(), 0)
        fake.assert_called_once_with(["rem-bubbles"])


# -- init -------------------------------------------------------------------


class InitTests(IsolatedConfigTestCase):
    def setUp(self):
        super().setUp()
        self.config_path = self.config_dir / "config.toml"
        self.quotes_path = self.config_dir / "quotes.json"
        self.reminders_path = self.config_dir / "reminders.json"

    def test_creates_the_directory_and_config(self):
        status, output, _ = run(["init"])
        self.assertEqual(status, 0)
        self.assertTrue(self.config_path.is_file())
        self.assertIn(str(self.config_path), output)

    def test_written_config_is_the_documented_default(self):
        # A fresh config names all three tables, so the file documents itself —
        # including where the notification switch lives and that it is off.
        # Milestone 3 wrote only [quotes] and Milestone 4 added [reminders];
        # configs from either are still valid and are never rewritten (see
        # test_a_quote_only_config_is_left_alone and its Milestone 4 sibling).
        run(["init"])
        self.assertEqual(
            self.config_path.read_text(encoding="utf-8"),
            '[quotes]\nfile = "quotes.json"\n'
            '\n[reminders]\nfile = "reminders.json"\n'
            "\n[notifications]\nenabled = false\n",
        )

    def test_the_written_config_is_readable(self):
        run(["init"])
        config = read_user_config(self.config_path)
        self.assertEqual(config.quote_file, self.quotes_path)
        self.assertEqual(config.reminder_file, self.reminders_path)
        self.assertFalse(config.notifications)

    def test_a_fresh_config_does_not_switch_notifications_on(self):
        # Writing the table is documentation, not a change of behaviour.
        run(["init"])
        self.assertFalse(read_user_config(self.config_path).notifications)

    def test_a_quote_only_config_is_left_alone(self):
        # Backwards compatibility: a Milestone 3 user must not have their file
        # rewritten just because [reminders] now exists as a concept.
        self.write_config('[quotes]\nfile = "quotes.json"\n')
        status, output, _ = run(["init"])
        self.assertEqual(status, 0)
        self.assertEqual(
            self.config_path.read_text(encoding="utf-8"),
            '[quotes]\nfile = "quotes.json"\n',
        )
        self.assertIn("left unchanged", output)

    def test_a_quote_and_reminder_config_is_left_alone(self):
        # The same promise one milestone later: a Milestone 4 config must not
        # gain a [notifications] table behind the user's back.
        milestone_4 = (
            '[quotes]\nfile = "quotes.json"\n\n[reminders]\nfile = "reminders.json"\n'
        )
        self.write_config(milestone_4)
        status, output, _ = run(["init"])
        self.assertEqual(status, 0)
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), milestone_4)
        self.assertIn("left unchanged", output)

    def test_an_untouched_legacy_config_still_means_notifications_off(self):
        for text in (
            '[quotes]\nfile = "quotes.json"\n',
            '[quotes]\nfile = "quotes.json"\n\n[reminders]\nfile = "reminders.json"\n',
        ):
            self.write_config(text)
            run(["init"])
            self.assertFalse(read_user_config(self.config_path).notifications)

    def test_does_not_create_a_reminder_file(self):
        run(["init"])
        self.assertFalse(self.reminders_path.exists())

    def test_reports_the_reminder_file(self):
        _, output, _ = run(["init"])
        self.assertIn(str(self.reminders_path), output)
        self.assertIn("reminder add", output)

    def test_the_directory_is_user_private(self):
        run(["init"])
        self.assertEqual(os.stat(self.config_dir).st_mode & 0o777, 0o700)

    def test_the_config_file_is_user_private(self):
        run(["init"])
        self.assertEqual(os.stat(self.config_path).st_mode & 0o777, 0o600)

    def test_does_not_create_a_quote_file(self):
        run(["init"])
        self.assertFalse(self.quotes_path.exists())

    def test_explains_how_the_quote_file_gets_created(self):
        _, output, _ = run(["init"])
        self.assertIn("quote add", output)

    def test_running_twice_is_safe(self):
        run(["init"])
        self.config_path.write_text('[quotes]\nfile = "mine.json"\n', encoding="utf-8")
        status, output, _ = run(["init"])
        self.assertEqual(status, 0)
        self.assertEqual(
            self.config_path.read_text(encoding="utf-8"),
            '[quotes]\nfile = "mine.json"\n',
        )
        self.assertIn("left unchanged", output)

    def test_does_not_overwrite_an_existing_quote_file(self):
        run(["init"])
        run(["quote", "add", "Mine."])
        before = self.quotes_path.read_text(encoding="utf-8")
        run(["init"])
        self.assertEqual(self.quotes_path.read_text(encoding="utf-8"), before)

    def test_reports_an_existing_quote_file(self):
        run(["quote", "add", "Mine."])
        _, output, _ = run(["init"])
        self.assertIn(str(self.quotes_path), output)

    def test_a_malformed_config_fails_without_changes(self):
        self.write_config("[quotes\nbroken")
        status, _, errors = run(["init"])
        self.assertEqual(status, 1)
        self.assertIn("not valid TOML", errors)
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), "[quotes\nbroken")

    def test_init_then_add_then_list(self):
        self.assertEqual(run(["init"])[0], 0)
        self.assertEqual(run(["quote", "add", "Headless test quote"])[0], 0)
        status, output, _ = run(["quote", "list"])
        self.assertEqual(status, 0)
        self.assertIn("headless-test-quote", output)
        self.assertIn("Headless test quote", output)


# -- error reporting --------------------------------------------------------


class ErrorReportingTests(IsolatedConfigTestCase):
    def test_config_errors_go_to_stderr(self):
        self.write_config("[quotes]\nfile = 7\n")
        status, output, errors = run(["quote", "list"])
        self.assertEqual(status, 1)
        self.assertIn("rem-bubbles:", errors)
        self.assertEqual(output, "")

    def test_quote_file_errors_go_to_stderr(self):
        self.config_dir.mkdir(parents=True)
        (self.config_dir / "quotes.json").write_text("{not json", encoding="utf-8")
        status, _, errors = run(["quote", "list"])
        self.assertEqual(status, 1)
        self.assertIn("not valid JSON", errors)

    def test_unknown_id_message_suggests_listing(self):
        run(["quote", "add", "A quote."])
        status, _, errors = run(["quote", "remove", "nope"])
        self.assertEqual(status, 1)
        self.assertIn("quote list", errors)

    def test_no_traceback_is_printed(self):
        self.write_config("[quotes]\nfile = 7\n")
        _, _, errors = run(["quote", "add", "x"])
        self.assertNotIn("Traceback", errors)


if __name__ == "__main__":
    unittest.main()
