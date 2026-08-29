"""Tests for process lifecycle: signals, one shutdown path, one instance.

Two kinds of test live here.

The first kind runs :mod:`rem_bubbles.app` in a **child interpreter**. That
module imports GTK, and importing it into the test runner would put ``gi`` in
``sys.modules`` for every other test in the suite — including the import
isolation checks this project exists to keep honest. Nothing here needs a
display: a ``Gtk.Application`` can be constructed, signalled and shut down
without one, because everything that touches a compositor happens inside
``run()``.

The second kind starts a **real ``rem-bubbles`` process** and signals it. Those
need a live Wayland session to open a layer surface at all, so they are guarded
rather than faked — a single-instance test that only compared process names
would pass without proving anything. Under a Hyprland session they all run; with
no compositor they are skipped and say so.

No test touches the real ``~/.config/rem-bubbles``: the child processes get a
temporary HOME and XDG_CONFIG_HOME.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: How long a child interpreter gets before a test calls it hung.
CHILD_TIMEOUT = 60

#: How long to let a real GUI process settle before signalling it. Startup is
#: well under a second in practice; this is headroom, not a measurement.
SETTLE_SECONDS = 1.5

HAS_WAYLAND = bool(os.environ.get("WAYLAND_DISPLAY"))
HAS_HYPRCTL = shutil.which("hyprctl") is not None

requires_wayland = unittest.skipUnless(
    HAS_WAYLAND, "needs a live Wayland session to open a layer surface"
)


def child(script: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Run ``script`` in a fresh interpreter, with no display."""
    environment = {k: v for k, v in os.environ.items()}
    environment.pop("WAYLAND_DISPLAY", None)
    environment.pop("DISPLAY", None)
    environment.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=environment,
        timeout=CHILD_TIMEOUT,
    )


class TemporaryHomeMixin:
    """Give child processes a home of their own, never the user's."""

    def temporary_home(self) -> dict:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        home = Path(temp.name)
        (home / ".config").mkdir()
        return {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
        }


# -- the shutdown path, without a compositor --------------------------------


class ShutdownPathTests(unittest.TestCase, TemporaryHomeMixin):
    """The cleanup path is one path, and it is safe to walk twice."""

    def probe(self, body: str) -> dict:
        script = (
            "import json, signal\n"
            "import rem_bubbles.app as app\n"
            "result = {}\n"
            f"{body}\n"
            "print('RESULT ' + json.dumps(result))\n"
        )
        result = child(script, self.temporary_home())
        self.assertEqual(result.returncode, 0, result.stderr)
        line = next(l for l in result.stdout.splitlines() if l.startswith("RESULT "))
        return json.loads(line[len("RESULT "):])

    def test_a_fresh_application_has_no_signal_status(self):
        data = self.probe(
            "a = app.RemBubblesApp()\n"
            "result['status'] = a.exit_status\n"
        )
        self.assertIsNone(data["status"])

    def test_sigint_records_the_conventional_status(self):
        data = self.probe(
            "a = app.RemBubblesApp()\n"
            "a._on_signal(signal.SIGINT)\n"
            "result['status'] = a.exit_status\n"
        )
        self.assertEqual(data["status"], 128 + int(signal.SIGINT))

    def test_sigterm_records_the_conventional_status(self):
        data = self.probe(
            "a = app.RemBubblesApp()\n"
            "a._on_signal(signal.SIGTERM)\n"
            "result['status'] = a.exit_status\n"
        )
        self.assertEqual(data["status"], 128 + int(signal.SIGTERM))

    def test_the_first_signal_wins(self):
        # A second Ctrl+C while the first is still being processed must not
        # rewrite why we are stopping.
        data = self.probe(
            "a = app.RemBubblesApp()\n"
            "a._on_signal(signal.SIGINT)\n"
            "a._on_signal(signal.SIGTERM)\n"
            "result['status'] = a.exit_status\n"
        )
        self.assertEqual(data["status"], 128 + int(signal.SIGINT))

    def test_the_signal_source_stays_attached(self):
        # SOURCE_CONTINUE: a second signal must arrive here too, rather than
        # falling through to a default disposition mid-shutdown.
        data = self.probe(
            "a = app.RemBubblesApp()\n"
            "result['keep'] = bool(a._on_signal(signal.SIGINT))\n"
        )
        self.assertTrue(data["keep"])

    def test_requesting_shutdown_twice_is_safe(self):
        data = self.probe(
            "a = app.RemBubblesApp()\n"
            "a.request_shutdown()\n"
            "a.request_shutdown()\n"
            "a.request_shutdown()\n"
            "result['ok'] = True\n"
        )
        self.assertTrue(data["ok"])

    def test_both_shutdown_signals_are_handled(self):
        data = self.probe(
            "result['signals'] = sorted(int(s) for s in app.SHUTDOWN_SIGNALS)\n"
        )
        self.assertEqual(
            data["signals"], sorted([int(signal.SIGINT), int(signal.SIGTERM)])
        )

    def test_nothing_calls_os_underscore_exit(self):
        # A normal shutdown must let GTK tear down; _exit would skip all of it.
        source = (REPO_ROOT / "src" / "rem_bubbles" / "app.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("os._exit", source)

    def test_no_watchdog_or_restart_loop_exists(self):
        source = (REPO_ROOT / "src" / "rem_bubbles" / "app.py").read_text(
            encoding="utf-8"
        )
        for forbidden in ("import subprocess", "fork(", "Popen", "while True"):
            self.assertNotIn(forbidden, source)


class KeyboardInterruptTests(unittest.TestCase, TemporaryHomeMixin):
    """Ctrl+C is an intentional stop, not a crash to be reported as one."""

    def test_main_turns_a_keyboard_interrupt_into_a_status(self):
        script = (
            "from unittest import mock\n"
            "import rem_bubbles.app as app\n"
            "with mock.patch.object(app.RemBubblesApp, 'run', "
            "side_effect=KeyboardInterrupt):\n"
            "    print('STATUS', app.main([]))\n"
        )
        result = child(script, self.temporary_home())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"STATUS {128 + int(signal.SIGINT)}", result.stdout)

    def test_a_keyboard_interrupt_prints_no_traceback(self):
        script = (
            "from unittest import mock\n"
            "import rem_bubbles.app as app\n"
            "with mock.patch.object(app.RemBubblesApp, 'run', "
            "side_effect=KeyboardInterrupt):\n"
            "    app.main([])\n"
        )
        result = child(script, self.temporary_home())
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("KeyboardInterrupt", result.stderr)

    def test_other_exceptions_are_not_swallowed(self):
        # The guard is for one intentional interruption, not a blanket catch:
        # a real fault must still be a real traceback.
        script = (
            "from unittest import mock\n"
            "import rem_bubbles.app as app\n"
            "with mock.patch.object(app.RemBubblesApp, 'run', "
            "side_effect=RuntimeError('a genuine bug')):\n"
            "    app.main([])\n"
        )
        result = child(script, self.temporary_home())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("a genuine bug", result.stderr)
        self.assertIn("Traceback", result.stderr)

    def test_a_normal_run_status_is_returned_untouched(self):
        script = (
            "from unittest import mock\n"
            "import rem_bubbles.app as app\n"
            "with mock.patch.object(app.RemBubblesApp, 'run', return_value=7):\n"
            "    print('STATUS', app.main([]))\n"
        )
        result = child(script, self.temporary_home())
        self.assertIn("STATUS 7", result.stdout)

    def test_a_signal_status_outranks_the_run_status(self):
        script = (
            "from unittest import mock\n"
            "import signal\n"
            "import rem_bubbles.app as app\n"
            "def fake(self, argv):\n"
            "    self._on_signal(signal.SIGTERM)\n"
            "    return 0\n"
            "with mock.patch.object(app.RemBubblesApp, 'run', fake):\n"
            "    print('STATUS', app.main([]))\n"
        )
        result = child(script, self.temporary_home())
        self.assertIn(f"STATUS {128 + int(signal.SIGTERM)}", result.stdout)


# -- the timer, under a real compositor -------------------------------------


@requires_wayland
class SchedulerLifetimeTests(unittest.TestCase, TemporaryHomeMixin):
    """The thirty-second timer is created once and removed once."""

    def probe(self) -> dict:
        script = (
            "import json, sys\n"
            # rem_bubbles.app first: it loads libgtk4-layer-shell before any gi
            # import. A probe that imports GTK above this line reproduces the
            # load-order bug rather than testing for it.
            "import rem_bubbles.app as app\n"
            "from gi.repository import GLib\n"
            "result = {}\n"
            "a = app.RemBubblesApp()\n"
            "def probe():\n"
            "    window = a._window\n"
            "    result['window_exists'] = window is not None\n"
            "    result['scheduler_running'] = window.scheduler_running\n"
            "    first_tick = window._tick_source\n"
            "    a.activate()\n"
            "    a.activate()\n"
            "    result['same_window'] = a._window is window\n"
            "    result['same_tick'] = a._window._tick_source == first_tick\n"
            "    result['expanded_on_reactivation'] = window._expanded\n"
            "    window.shutdown()\n"
            "    window.shutdown()\n"
            "    result['idempotent_shutdown'] = not window.scheduler_running\n"
            "    a.request_shutdown()\n"
            "    return GLib.SOURCE_REMOVE\n"
            "a.connect('activate', lambda *_: GLib.idle_add(probe))\n"
            "result['run_status'] = a.run([sys.argv[0]])\n"
            "result['scheduler_after_shutdown'] = a._window.scheduler_running\n"
            "print('RESULT ' + json.dumps(result))\n"
        )
        environment = {k: v for k, v in os.environ.items()}
        environment.update(self.temporary_home())
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=environment,
            timeout=CHILD_TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.stderr = result.stderr
        line = next(l for l in result.stdout.splitlines() if l.startswith("RESULT "))
        return json.loads(line[len("RESULT "):])

    def setUp(self):
        self.data = self.probe()

    def test_the_window_opens(self):
        self.assertTrue(self.data["window_exists"])

    def test_the_scheduler_is_running(self):
        self.assertTrue(self.data["scheduler_running"])

    def test_reactivation_reuses_the_same_window(self):
        self.assertTrue(self.data["same_window"])

    def test_reactivation_does_not_create_a_second_timer(self):
        self.assertTrue(self.data["same_tick"])

    def test_reactivation_expands_the_bubble(self):
        self.assertTrue(self.data["expanded_on_reactivation"])

    def test_shutdown_is_idempotent(self):
        self.assertTrue(self.data["idempotent_shutdown"])

    def test_the_timer_is_gone_after_shutdown(self):
        self.assertFalse(self.data["scheduler_after_shutdown"])

    def test_a_clean_quit_exits_zero(self):
        self.assertEqual(self.data["run_status"], 0)

    def test_no_layer_shell_load_order_warning(self):
        self.assertNotIn("linked after libwayland", self.stderr)
        self.assertNotIn("Failed to initialize layer surface", self.stderr)

    def test_no_traceback(self):
        self.assertNotIn("Traceback", self.stderr)


# -- real processes, real signals -------------------------------------------


class LiveInstance:
    """One real ``rem-bubbles`` process, with a home of its own.

    Starting a GUI process costs a second or two, so a scenario is run *once*
    per test class and the observations are asserted many times. That keeps each
    assertion a separate, individually named test without paying for a separate
    launch each time.
    """

    def __init__(self):
        self._temp = tempfile.TemporaryDirectory()
        home = Path(self._temp.name)
        (home / ".config").mkdir()
        self.environment = {k: v for k, v in os.environ.items()}
        self.environment.update(
            {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")}
        )
        self.process = subprocess.Popen(
            [sys.executable, "-m", "rem_bubbles.cli"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=REPO_ROOT,
            env=self.environment,
            start_new_session=True,
        )
        self.status: int | None = None
        self.output = ""
        self.errors = ""
        time.sleep(SETTLE_SECONDS)

    @property
    def alive(self) -> bool:
        return self.process.poll() is None

    def relaunch(self) -> subprocess.CompletedProcess:
        """A second ``rem-bubbles`` against the same session and same home."""
        return subprocess.run(
            [sys.executable, "-m", "rem_bubbles.cli"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=self.environment,
            timeout=30,
        )

    def signal_and_wait(self, signum) -> bool:
        """Signal the process and collect its output. False if it had to be killed."""
        self.process.send_signal(signum)
        try:
            self.output, self.errors = self.process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.output, self.errors = self.process.communicate(timeout=10)
            self.status = self.process.returncode
            return False
        self.status = self.process.returncode
        return True

    def close(self) -> None:
        """Make sure nothing is left running, and no pipe is left open."""
        if self.process.poll() is None:
            self.process.kill()
            self.process.communicate(timeout=10)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        self._temp.cleanup()


class LiveScenario(unittest.TestCase):
    """Base for a class-wide live process. Subclasses set up the scenario once."""

    instance: LiveInstance

    @classmethod
    def start(cls) -> LiveInstance:
        cls.instance = LiveInstance()
        cls.addClassCleanup(cls.instance.close)
        assert cls.instance.alive, "the application exited before it was signalled"
        return cls.instance


@requires_wayland
class SigintShutdownTests(LiveScenario):
    """Ctrl+C: the user meant to stop, so stopping is not an error report."""

    @classmethod
    def setUpClass(cls):
        cls.responded = cls.start().signal_and_wait(signal.SIGINT)

    def test_it_stops(self):
        self.assertTrue(self.responded, "SIGINT was ignored and the process was killed")

    def test_it_prints_no_traceback(self):
        self.assertNotIn("Traceback", self.instance.errors)

    def test_it_does_not_mention_keyboardinterrupt(self):
        self.assertNotIn("KeyboardInterrupt", self.instance.errors)

    def test_it_prints_nothing_at_all_when_healthy(self):
        # Detailed diagnostics belong in 'doctor', not on the way out.
        self.assertEqual(self.instance.errors.strip(), "")
        self.assertEqual(self.instance.output.strip(), "")

    def test_it_was_handled_rather_than_left_to_the_default(self):
        # A handled signal exits through Python with a status; an uncaught one
        # is reported by Popen as a negative number.
        self.assertGreaterEqual(self.instance.status, 0)


@requires_wayland
class SigtermShutdownTests(LiveScenario):
    """SIGTERM: a logout, a compositor shutting down, a process supervisor."""

    @classmethod
    def setUpClass(cls):
        cls.responded = cls.start().signal_and_wait(signal.SIGTERM)

    def test_it_stops(self):
        self.assertTrue(self.responded, "SIGTERM was ignored and the process was killed")

    def test_it_prints_no_traceback(self):
        self.assertNotIn("Traceback", self.instance.errors)

    def test_it_is_quiet(self):
        self.assertEqual(self.instance.errors.strip(), "")
        self.assertEqual(self.instance.output.strip(), "")

    def test_it_was_handled_rather_than_left_to_the_default(self):
        self.assertGreaterEqual(self.instance.status, 0)


@requires_wayland
class RelaunchTests(unittest.TestCase):
    """Stopping cleanly has to leave the session able to start it again."""

    def test_it_can_be_started_again_after_a_clean_stop(self):
        first = LiveInstance()
        self.addCleanup(first.close)
        self.assertTrue(first.signal_and_wait(signal.SIGTERM))

        second = LiveInstance()
        self.addCleanup(second.close)
        self.assertTrue(second.alive, "could not start again after a clean shutdown")
        self.assertTrue(second.signal_and_wait(signal.SIGTERM))
        self.assertNotIn("Traceback", second.errors)
        self.assertGreaterEqual(second.status, 0)


@requires_wayland
class SingleInstanceTests(LiveScenario):
    """Gio's own D-Bus uniqueness, exercised with real processes.

    Not a process-name comparison: a second real ``rem-bubbles`` is started and
    what it does is observed. The layer-surface count is the assertion that
    would actually catch a second bubble appearing.
    """

    @classmethod
    def surface_count(cls) -> int:
        listing = subprocess.run(
            ["hyprctl", "layers", "-j"], capture_output=True, text=True, timeout=15
        )
        return listing.stdout.count('"namespace": "rem-bubbles"')

    @classmethod
    def setUpClass(cls):
        instance = cls.start()
        cls.surfaces_before = cls.surface_count() if HAS_HYPRCTL else None

        started = time.monotonic()
        cls.second = instance.relaunch()
        cls.elapsed = time.monotonic() - started

        cls.further = [instance.relaunch() for _ in range(2)]
        time.sleep(1.0)
        cls.surfaces_after = cls.surface_count() if HAS_HYPRCTL else None
        cls.first_alive = instance.alive
        cls.responded = instance.signal_and_wait(signal.SIGTERM)

    def test_the_second_launch_exits_successfully(self):
        self.assertEqual(self.second.returncode, 0)

    def test_the_second_launch_returns_promptly(self):
        # It delegates to the running instance rather than starting a main loop.
        self.assertLess(self.elapsed, 20)

    def test_the_second_launch_prints_no_traceback(self):
        self.assertNotIn("Traceback", self.second.stderr)

    def test_the_first_process_survives(self):
        self.assertTrue(self.first_alive, "the first instance was killed")

    def test_repeated_launches_all_delegate(self):
        for result in self.further:
            self.assertEqual(result.returncode, 0)

    @unittest.skipUnless(HAS_HYPRCTL, "needs hyprctl to count layer surfaces")
    def test_there_was_exactly_one_layer_surface_to_begin_with(self):
        self.assertEqual(self.surfaces_before, 1)

    @unittest.skipUnless(HAS_HYPRCTL, "needs hyprctl to count layer surfaces")
    def test_three_further_launches_added_no_layer_surface(self):
        self.assertEqual(self.surfaces_after, 1)

    def test_the_survivor_still_shuts_down_cleanly(self):
        self.assertTrue(self.responded)
        self.assertNotIn("Traceback", self.instance.errors)
        self.assertGreaterEqual(self.instance.status, 0)


if __name__ == "__main__":
    unittest.main()
