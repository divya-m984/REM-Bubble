"""Tests for ``rem-bubbles integration hyprland``.

The command prints an autostart line. The tests are mostly about what it does
*not* do: it must not write to a Hyprland configuration, must not run
``hyprctl``, must not import GTK, and must not print a snippet it knows will not
work. Editing somebody's compositor configuration behind their back is the
failure mode worth guarding against, so a fake ``~/.config/hypr`` is watched
byte for byte throughout.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rem_bubbles import cli

from test_cli import run, run_expecting_exit

REPO_ROOT = Path(__file__).resolve().parents[1]


class IntegrationTestCase(unittest.TestCase):
    """A temporary HOME with a decoy Hyprland configuration inside it."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.home = Path(self._temp.name)

        self.hypr = self.home / ".config" / "hypr"
        self.hypr.mkdir(parents=True)
        self.hyprland_conf = self.hypr / "hyprland.conf"
        self.hyprland_conf.write_text("# decoy\nmonitor = , preferred, auto, 1\n",
                                      encoding="utf-8")
        self.before = self.hyprland_conf.read_bytes()

        patcher = mock.patch.dict(
            os.environ,
            {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config")},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def assertHyprlandUntouched(self):
        self.assertEqual(self.hyprland_conf.read_bytes(), self.before)
        self.assertEqual(
            sorted(p.name for p in self.hypr.iterdir()), ["hyprland.conf"]
        )


# -- output -----------------------------------------------------------------


class OutputTests(IntegrationTestCase):
    def test_it_exits_zero(self):
        status, _, _ = run(["integration", "hyprland"])
        self.assertEqual(status, 0)

    def test_it_prints_an_exec_once_line(self):
        _, output, _ = run(["integration", "hyprland"])
        self.assertIn("exec-once = ", output)

    def test_the_path_is_absolute(self):
        _, output, _ = run(["integration", "hyprland"])
        line = next(l for l in output.splitlines() if "exec-once" in l)
        path = line.split("=", 1)[1].strip().strip("'\"")
        self.assertTrue(Path(path).is_absolute(), path)

    def test_the_path_names_the_executable(self):
        _, output, _ = run(["integration", "hyprland"])
        line = next(l for l in output.splitlines() if "exec-once" in l)
        self.assertIn("rem-bubbles", line)

    def test_it_explains_what_to_do_with_the_line(self):
        _, output, _ = run(["integration", "hyprland"])
        self.assertIn("Hyprland", output)
        self.assertIn("session", output)

    def test_it_says_it_wrote_nothing(self):
        _, output, _ = run(["integration", "hyprland"])
        self.assertIn("unchanged", output)

    def test_nothing_goes_to_stderr(self):
        _, _, errors = run(["integration", "hyprland"])
        self.assertEqual(errors, "")

    def test_no_personal_path_is_hard_coded_in_the_source(self):
        source = (REPO_ROOT / "src" / "rem_bubbles" / "cli.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("/home/", source)


class LineBuildingTests(unittest.TestCase):
    def test_an_ordinary_path_is_not_quoted(self):
        self.assertEqual(
            cli.hyprland_autostart_line("/usr/bin/rem-bubbles"),
            "exec-once = /usr/bin/rem-bubbles",
        )

    def test_a_path_with_spaces_is_quoted(self):
        # exec-once is handed to a shell, so an unquoted space would make
        # Hyprland run the first word and pass the rest as arguments.
        line = cli.hyprland_autostart_line("/home/a b/bin/rem-bubbles")
        self.assertEqual(line, "exec-once = '/home/a b/bin/rem-bubbles'")

    def test_a_path_with_a_quote_is_escaped(self):
        line = cli.hyprland_autostart_line("/home/o'brien/bin/rem-bubbles")
        self.assertIn("rem-bubbles", line)
        self.assertNotEqual(line, "exec-once = /home/o'brien/bin/rem-bubbles")

    def test_a_path_object_is_accepted(self):
        self.assertEqual(
            cli.hyprland_autostart_line(Path("/usr/bin/rem-bubbles")),
            "exec-once = /usr/bin/rem-bubbles",
        )


class ExecutableResolutionTests(IntegrationTestCase):
    def test_it_finds_an_executable(self):
        self.assertIsNotNone(cli.console_script_path())

    def test_the_result_is_absolute(self):
        self.assertTrue(cli.console_script_path().is_absolute())

    def test_the_result_is_executable(self):
        path = cli.console_script_path()
        self.assertTrue(os.access(path, os.X_OK))

    def test_argv0_is_preferred_when_it_is_the_console_script(self):
        fake = self.home / "elsewhere" / "rem-bubbles"
        fake.parent.mkdir(parents=True)
        fake.write_text("#!/bin/sh\n", encoding="utf-8")
        fake.chmod(0o755)
        with mock.patch.object(sys, "argv", [str(fake), "integration", "hyprland"]):
            self.assertEqual(cli.console_script_path(), fake)

    def test_a_relative_argv0_is_made_absolute(self):
        fake = self.home / "rem-bubbles"
        fake.write_text("#!/bin/sh\n", encoding="utf-8")
        fake.chmod(0o755)
        previous = Path.cwd()
        os.chdir(self.home)
        try:
            with mock.patch.object(sys, "argv", ["./rem-bubbles"]):
                resolved = cli.console_script_path()
        finally:
            os.chdir(previous)
        self.assertTrue(resolved.is_absolute())

    def test_a_non_existent_argv0_is_skipped(self):
        with mock.patch.object(sys, "argv", ["/nowhere/at/all/rem-bubbles"]):
            resolved = cli.console_script_path()
        # Falls through to the interpreter's own script directory.
        self.assertNotEqual(resolved, Path("/nowhere/at/all/rem-bubbles"))

    def test_an_unresolvable_executable_is_reported_rather_than_guessed(self):
        with mock.patch.object(sys, "argv", ["python"]):
            with mock.patch.object(cli.sysconfig, "get_path", return_value=""):
                with mock.patch.object(cli.shutil, "which", return_value=None):
                    self.assertIsNone(cli.console_script_path())
                    status, output, errors = run(["integration", "hyprland"])
        self.assertEqual(status, 1)
        self.assertNotIn("exec-once", output)
        self.assertIn("Could not work out", errors)


# -- safety -----------------------------------------------------------------


class SafetyTests(IntegrationTestCase):
    def test_it_does_not_touch_the_hyprland_config(self):
        run(["integration", "hyprland"])
        self.assertHyprlandUntouched()

    def test_it_does_not_create_a_hyprland_config(self):
        for entry in self.hypr.iterdir():
            entry.unlink()
        self.hypr.rmdir()
        run(["integration", "hyprland"])
        self.assertFalse(self.hypr.exists())

    def test_it_does_not_create_the_rem_bubbles_config_directory(self):
        run(["integration", "hyprland"])
        self.assertFalse((self.home / ".config" / "rem-bubbles").exists())

    def test_running_it_repeatedly_never_appends_anything(self):
        for _ in range(5):
            run(["integration", "hyprland"])
        self.assertHyprlandUntouched()

    def test_it_does_not_run_hyprctl(self):
        with mock.patch.object(subprocess, "run") as spawned:
            with mock.patch.object(subprocess, "Popen") as popened:
                with mock.patch.object(os, "system") as shelled:
                    run(["integration", "hyprland"])
        spawned.assert_not_called()
        popened.assert_not_called()
        shelled.assert_not_called()

    def test_the_cli_cannot_spawn_anything_at_all(self):
        # Stronger than grepping for "hyprctl", which legitimately appears in
        # the docstring explaining that it is never called: the headless CLI
        # imports no way to start a process in the first place.
        source = (REPO_ROOT / "src" / "rem_bubbles" / "cli.py").read_text(
            encoding="utf-8"
        )
        for spawner in ("import subprocess", "os.system", "os.popen", "os.exec"):
            self.assertNotIn(spawner, source)

    def test_it_imports_no_gtk(self):
        script = (
            "import sys, io, contextlib\n"
            "from rem_bubbles import cli\n"
            "with contextlib.redirect_stdout(io.StringIO()):\n"
            "    cli.main(['integration', 'hyprland'])\n"
            "print(sorted(m for m in sys.modules "
            "if m.split('.')[0] in {'gi', 'gtk'} "
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


# -- parsing ----------------------------------------------------------------


class IntegrationParserTests(unittest.TestCase):
    def test_the_target_is_parsed(self):
        args = cli.build_parser().parse_args(["integration", "hyprland"])
        self.assertEqual(args.command, "integration")
        self.assertEqual(args.integration_command, "hyprland")

    def test_integration_without_a_target_is_a_usage_error(self):
        status, _, _ = run_expecting_exit(["integration"])
        self.assertEqual(status, 2)

    def test_an_unknown_target_is_a_usage_error(self):
        for name in ("sway", "systemd", "waybar", "install"):
            status, _, _ = run_expecting_exit(["integration", name])
            self.assertEqual(status, 2)

    def test_there_is_no_install_mode(self):
        status, _, _ = run_expecting_exit(["integration", "hyprland", "--install"])
        self.assertEqual(status, 2)

    def test_the_top_level_help_lists_integration(self):
        _, output, _ = run_expecting_exit(["--help"])
        self.assertIn("integration", output)

    def test_the_help_says_it_only_prints(self):
        _, output, _ = run_expecting_exit(["integration", "hyprland", "--help"])
        # argparse re-wraps the description, so assert on a phrase short enough
        # to survive wrapping rather than on the sentence it came from.
        self.assertIn("not edited", output)
        self.assertIn("not reloaded", output)


if __name__ == "__main__":
    unittest.main()
