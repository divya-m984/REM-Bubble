"""Tests for what happens when a desktop notification is clicked.

The click arrives as the ``app.open-reminder`` action with the reminder id as a
string target. No desktop notification daemon is involved in these tests: the
action is activated directly through Gio's own action machinery, which is
exactly what the notification backend does on the other side.

The window part does need a compositor — a layer surface cannot be created
without one — so these run under a live Wayland session and are skipped, loudly,
without one. The pure selection logic they build on is covered without any
display in ``test_notifications.py``.

The child process gets a temporary HOME and XDG_CONFIG_HOME, so the real
``~/.config/rem-bubbles`` is never read or written.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

HAS_WAYLAND = bool(os.environ.get("WAYLAND_DISPLAY"))

#: Two reminders, both long overdue, so both are due whenever the test runs.
#: Ordered by id once their occurrences tie, so "aaa" is the one shown first.
REMINDERS = json.dumps(
    [
        {
            "id": "aaa",
            "text": "The older one.",
            "due_at": "2020-01-01T08:00:00",
            "recurrence": "none",
            "enabled": True,
            "snoozed_until": None,
            "dismissed_occurrence": None,
        },
        {
            "id": "bbb",
            "text": "The newer one.",
            "due_at": "2020-01-01T08:00:00",
            "recurrence": "none",
            "enabled": True,
            "snoozed_until": None,
            "dismissed_occurrence": None,
        },
    ]
)

PROBE = """
import json, sys

# rem_bubbles.app is imported first on purpose: it loads libgtk4-layer-shell
# before any gi import. Importing GTK above this line would reproduce the
# load-order bug instead of testing around it.
import rem_bubbles.app as app
from gi.repository import GLib

result = {}
a = app.RemBubblesApp()

def probe():
    window = a._window

    # The ordinary state: the oldest waiting reminder is the one on the card.
    result['default_active'] = window.active_reminder.id
    result['starts_collapsed'] = not window._expanded

    # A click on the notification for the *other* due reminder, delivered the
    # way the notification backend delivers it: as an action with a target.
    a.activate_action(app.OPEN_REMINDER_ACTION, GLib.Variant('s', 'bbb'))
    result['after_action_active'] = window.active_reminder.id
    result['after_action_expanded'] = window._expanded

    # An id that was never in the collection at all.
    result['unknown_returned'] = window.focus_reminder('no-such-reminder')
    result['unknown_active'] = window.active_reminder.id

    # An id that was real but has since been dealt with.
    window._reminders.dismiss('bbb')
    result['dismissed_returned'] = window.focus_reminder('bbb')
    result['dismissed_active'] = window.active_reminder.id

    # Focusing something that is genuinely still due still works.
    result['still_due_returned'] = window.focus_reminder('aaa')

    # And once nothing is due at all, the card goes back to quotes.
    window._reminders.dismiss('aaa')
    window.refresh_reminders()
    result['nothing_due'] = window.active_reminder is None
    result['quote_after'] = window._store.current.id

    a.request_shutdown()
    return GLib.SOURCE_REMOVE

a.connect('activate', lambda *_: GLib.idle_add(probe))
result['quote_before'] = None
result['run_status'] = a.run([sys.argv[0]])
print('RESULT ' + json.dumps(result))
"""


@unittest.skipUnless(
    HAS_WAYLAND, "needs a live Wayland session to open a layer surface"
)
class NotificationActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._temp.cleanup)
        home = Path(cls._temp.name)
        config = home / ".config" / "rem-bubbles"
        config.mkdir(parents=True)
        (config / "config.toml").write_text(
            '[reminders]\nfile = "reminders.json"\n\n[notifications]\nenabled = false\n',
            encoding="utf-8",
        )
        (config / "reminders.json").write_text(REMINDERS, encoding="utf-8")

        environment = {k: v for k, v in os.environ.items()}
        environment.update(
            {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")}
        )
        completed = subprocess.run(
            [sys.executable, "-c", PROBE],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=environment,
            timeout=60,
        )
        cls.stderr = completed.stderr
        assert completed.returncode == 0, completed.stderr
        line = next(
            l for l in completed.stdout.splitlines() if l.startswith("RESULT ")
        )
        cls.data = json.loads(line[len("RESULT "):])

    # -- the ordinary state ------------------------------------------------

    def test_the_oldest_waiting_reminder_is_shown_by_default(self):
        self.assertEqual(self.data["default_active"], "aaa")

    def test_the_window_starts_collapsed(self):
        self.assertTrue(self.data["starts_collapsed"])

    # -- a clicked notification --------------------------------------------

    def test_the_action_target_selects_that_reminder(self):
        # Not "aaa", which is what the ordering alone would have picked.
        self.assertEqual(self.data["after_action_active"], "bbb")

    def test_the_action_expands_the_card(self):
        self.assertTrue(self.data["after_action_expanded"])

    # -- ids that no longer mean anything ----------------------------------

    def test_an_unknown_id_fails_safely(self):
        self.assertFalse(self.data["unknown_returned"])

    def test_an_unknown_id_falls_back_to_the_current_ui(self):
        self.assertEqual(self.data["unknown_active"], "aaa")

    def test_an_already_dismissed_reminder_is_not_resurrected(self):
        self.assertFalse(self.data["dismissed_returned"])

    def test_an_already_dismissed_reminder_falls_back_to_the_current_ui(self):
        self.assertEqual(self.data["dismissed_active"], "aaa")

    def test_a_still_due_reminder_is_shown(self):
        self.assertTrue(self.data["still_due_returned"])

    # -- back to quotes ----------------------------------------------------

    def test_the_card_returns_to_quotes_when_nothing_is_due(self):
        self.assertTrue(self.data["nothing_due"])

    def test_a_quote_is_still_available_afterwards(self):
        self.assertTrue(self.data["quote_after"])

    # -- hygiene -----------------------------------------------------------

    def test_the_run_ends_cleanly(self):
        self.assertEqual(self.data["run_status"], 0)

    def test_no_traceback(self):
        self.assertNotIn("Traceback", self.stderr)

    def test_no_layer_shell_load_order_warning(self):
        self.assertNotIn("linked after libwayland", self.stderr)


if __name__ == "__main__":
    unittest.main()
