"""Tests for desktop notification policy: when, what, and above all how often.

No test here needs a desktop notification daemon, a display server or GTK.
:class:`~rem_bubbles.notifications.NotificationCenter` reaches the outside world
through a plain ``send`` callable, so a list collects exactly what the real
application would have handed to Gio — which is what lets every deduplication
rule be asserted with explicit datetimes instead of wall-clock waiting.
"""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from rem_bubbles.notifications import (
    NOTIFICATION_TITLE,
    NotificationCenter,
    episode_key,
    format_occurrence,
    notification_body,
    notification_id,
    select_active,
)
from rem_bubbles.reminder_store import (
    DAILY,
    WEEKLY,
    Reminder,
    ReminderStore,
    parse_local_datetime,
    write_reminders,
)


def at(text: str) -> datetime:
    return parse_local_datetime(text)


def reminder(**overrides) -> Reminder:
    fields = {
        "id": "r",
        "text": "Submit the report.",
        "due_at": at("2026-08-30T08:00"),
    }
    fields.update(overrides)
    return Reminder(**fields)


class Recorder:
    """A stand-in for Gio: records sends and withdrawals, or raises on demand."""

    def __init__(self, fail: bool = False):
        self.sent: list[tuple[str, str, str, str]] = []
        self.withdrawn: list[str] = []
        self.reported: list[str] = []
        self.fail = fail

    def send(self, ident, title, body, reminder_id):
        if self.fail:
            raise RuntimeError("no notification backend")
        self.sent.append((ident, title, body, reminder_id))

    def withdraw(self, ident):
        if self.fail:
            raise RuntimeError("no notification backend")
        self.withdrawn.append(ident)

    def report(self, message):
        self.reported.append(message)

    @property
    def bodies(self) -> list[str]:
        return [entry[2] for entry in self.sent]

    @property
    def ids(self) -> list[str]:
        return [entry[3] for entry in self.sent]


class NotificationTestCase(unittest.TestCase):
    """A store on a real temporary file, so snooze and dismiss can persist."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.path = Path(self._temp.name) / "reminders.json"
        self.recorder = Recorder()

    def stored(self, *reminders) -> ReminderStore:
        write_reminders(self.path, reminders)
        return ReminderStore.from_file(self.path)

    def centre(self, enabled: bool = True, recorder: Recorder | None = None):
        recorder = recorder if recorder is not None else self.recorder
        return NotificationCenter(
            send=recorder.send,
            withdraw=recorder.withdraw,
            enabled=enabled,
            report=recorder.report,
        )

    def tick(self, centre, store, moment: str):
        """One scheduler tick: ask the store what is due, then evaluate."""
        now = at(moment)
        return centre.evaluate(store, store.due_reminders(now), now)


# -- content ----------------------------------------------------------------


class ContentTests(NotificationTestCase):
    def test_the_title_names_the_application(self):
        store = self.stored(reminder())
        self.tick(self.centre(), store, "2026-08-30T08:00")
        self.assertEqual(self.recorder.sent[0][1], NOTIFICATION_TITLE)

    def test_the_body_carries_the_reminder_text(self):
        store = self.stored(reminder())
        self.tick(self.centre(), store, "2026-08-30T08:00")
        self.assertIn("Submit the report.", self.recorder.bodies[0])

    def test_the_body_carries_the_time(self):
        store = self.stored(reminder(due_at=at("2026-08-30T18:00")))
        self.tick(self.centre(), store, "2026-08-30T18:00")
        self.assertIn("6:00 PM", self.recorder.bodies[0])

    def test_a_due_reminder_reads_as_due(self):
        store = self.stored(reminder())
        body = notification_body(store, store.reminder_by_id("r"), at("2026-08-30T08:00"))
        self.assertIn("Due", body)
        self.assertNotIn("Overdue", body)

    def test_an_overdue_reminder_reads_as_overdue(self):
        store = self.stored(reminder())
        body = notification_body(store, store.reminder_by_id("r"), at("2026-08-30T12:00"))
        self.assertIn("Overdue", body)

    def test_the_body_does_not_leak_the_id_or_recurrence(self):
        # A notification is a nudge towards the card, not a copy of it.
        store = self.stored(reminder(id="secret-slug", recurrence=DAILY))
        body = notification_body(store, store.reminder_by_id("secret-slug"), at("2026-08-30T08:00"))
        self.assertNotIn("secret-slug", body)
        self.assertNotIn("aily", body)

    def test_the_notification_id_is_stable_per_reminder(self):
        # One id per reminder, so a new occurrence replaces the old on screen.
        self.assertEqual(notification_id("abc"), notification_id("abc"))
        self.assertNotEqual(notification_id("abc"), notification_id("abd"))

    def test_the_reminder_id_is_passed_to_the_backend(self):
        store = self.stored(reminder(id="the-report"))
        self.tick(self.centre(), store, "2026-08-30T08:00")
        self.assertEqual(self.recorder.ids, ["the-report"])

    def test_format_occurrence_is_readable(self):
        self.assertEqual(format_occurrence(at("2026-08-30T18:00")), "Aug 30 · 6:00 PM")

    def test_format_occurrence_handles_midnight_and_noon(self):
        self.assertIn("12:00 AM", format_occurrence(at("2026-08-30T00:00")))
        self.assertIn("12:00 PM", format_occurrence(at("2026-08-30T12:00")))


# -- the switch -------------------------------------------------------------


class EnabledTests(NotificationTestCase):
    def test_disabled_means_no_sends_at_all(self):
        store = self.stored(reminder())
        centre = self.centre(enabled=False)
        for moment in ("2026-08-30T08:00", "2026-08-30T08:30", "2026-08-31T08:00"):
            self.tick(centre, store, moment)
        self.assertEqual(self.recorder.sent, [])

    def test_disabled_remembers_nothing(self):
        store = self.stored(reminder())
        centre = self.centre(enabled=False)
        self.tick(centre, store, "2026-08-30T08:00")
        self.assertIsNone(centre.notified_key("r"))

    def test_disabled_never_withdraws(self):
        centre = self.centre(enabled=False)
        centre.withdraw("r")
        self.assertEqual(self.recorder.withdrawn, [])

    def test_enabled_is_reported(self):
        self.assertTrue(self.centre(enabled=True).enabled)
        self.assertFalse(self.centre(enabled=False).enabled)

    def test_an_upcoming_reminder_does_not_notify(self):
        store = self.stored(reminder())
        self.tick(self.centre(), store, "2026-08-29T08:00")
        self.assertEqual(self.recorder.sent, [])

    def test_a_disabled_reminder_does_not_notify(self):
        store = self.stored(reminder(enabled=False))
        self.tick(self.centre(), store, "2026-08-30T08:00")
        self.assertEqual(self.recorder.sent, [])

    def test_an_empty_store_notifies_nothing(self):
        store = self.stored()
        self.assertEqual(self.tick(self.centre(), store, "2026-08-30T08:00"), ())


# -- deduplication: one notification per episode ----------------------------


class DeduplicationTests(NotificationTestCase):
    def test_the_first_due_evaluation_notifies(self):
        store = self.stored(reminder())
        self.assertEqual(self.tick(self.centre(), store, "2026-08-30T08:00"), ("r",))

    def test_a_second_evaluation_of_the_same_episode_does_not(self):
        store = self.stored(reminder())
        centre = self.centre()
        self.tick(centre, store, "2026-08-30T08:00")
        self.assertEqual(self.tick(centre, store, "2026-08-30T08:00"), ())

    def test_repeated_thirty_second_ticks_do_not_duplicate(self):
        # The scheduler runs every thirty seconds all day. One due episode must
        # still be one notification.
        store = self.stored(reminder())
        centre = self.centre()
        moment = at("2026-08-30T08:00")
        for _ in range(120):  # an hour of ticks
            centre.evaluate(store, store.due_reminders(moment), moment)
            moment += timedelta(seconds=30)
        self.assertEqual(len(self.recorder.sent), 1)

    def test_a_new_centre_notifies_again(self):
        # Deduplication is process-local by design: a restart may re-announce a
        # reminder that is still overdue. Documented, not persisted.
        store = self.stored(reminder())
        self.tick(self.centre(), store, "2026-08-30T09:00")
        self.tick(self.centre(), store, "2026-08-30T09:30")
        self.assertEqual(len(self.recorder.sent), 2)

    def test_reset_forgets_the_episode(self):
        store = self.stored(reminder())
        centre = self.centre()
        self.tick(centre, store, "2026-08-30T08:00")
        centre.reset()
        self.assertIsNone(centre.notified_key("r"))
        self.assertEqual(self.tick(centre, store, "2026-08-30T08:00"), ("r",))

    def test_the_key_distinguishes_occurrences(self):
        entry = reminder()
        first = episode_key(entry, at("2026-08-30T08:00"))
        second = episode_key(entry, at("2026-08-31T08:00"))
        self.assertNotEqual(first, second)

    def test_the_key_distinguishes_snooze_episodes(self):
        occurrence = at("2026-08-30T08:00")
        fresh = episode_key(reminder(), occurrence)
        snoozed = episode_key(reminder(snoozed_until=at("2026-08-30T08:15")), occurrence)
        self.assertNotEqual(fresh, snoozed)

    def test_the_key_is_not_the_reminder_id_alone(self):
        entry = reminder()
        self.assertNotEqual(
            episode_key(entry, at("2026-08-30T08:00")), (entry.id,)
        )

    def test_the_key_is_stable_for_the_same_state(self):
        entry = reminder()
        self.assertEqual(
            episode_key(entry, at("2026-08-30T08:00")),
            episode_key(entry, at("2026-08-30T08:00")),
        )


# -- recurrence -------------------------------------------------------------


class RecurrenceTests(NotificationTestCase):
    def test_a_daily_reminder_notifies_once_on_the_first_day(self):
        store = self.stored(reminder(recurrence=DAILY))
        centre = self.centre()
        self.assertEqual(self.tick(centre, store, "2026-08-30T08:00"), ("r",))
        self.assertEqual(self.tick(centre, store, "2026-08-30T08:30"), ())
        self.assertEqual(self.tick(centre, store, "2026-08-30T23:00"), ())

    def test_a_daily_reminder_notifies_again_the_next_day(self):
        store = self.stored(reminder(recurrence=DAILY))
        centre = self.centre()
        self.tick(centre, store, "2026-08-30T08:00")
        self.assertEqual(self.tick(centre, store, "2026-08-31T08:00"), ("r",))

    def test_a_daily_reminder_notifies_once_per_day_across_a_week(self):
        store = self.stored(reminder(recurrence=DAILY))
        centre = self.centre()
        for day in range(30, 37):
            date = f"2026-{8 if day <= 31 else 9:02d}-{day if day <= 31 else day - 31:02d}"
            for minute in ("08:00", "08:30", "12:00"):
                self.tick(centre, store, f"{date}T{minute}")
        self.assertEqual(len(self.recorder.sent), 7)

    def test_a_weekly_reminder_notifies_again_the_next_week(self):
        store = self.stored(reminder(due_at=at("2026-08-31T09:00"), recurrence=WEEKLY))
        centre = self.centre()
        self.assertEqual(self.tick(centre, store, "2026-08-31T09:00"), ("r",))
        self.assertEqual(self.tick(centre, store, "2026-09-03T09:00"), ())
        self.assertEqual(self.tick(centre, store, "2026-09-07T09:00"), ("r",))

    def test_missed_days_collapse_to_one_notification(self):
        # Closed Monday to Wednesday. Thursday morning is one notification, not
        # four — the store already collapses the backlog.
        store = self.stored(reminder(recurrence=DAILY))
        self.assertEqual(self.tick(self.centre(), store, "2026-09-03T10:00"), ("r",))

    def test_dismissing_monday_does_not_suppress_tuesday(self):
        store = self.stored(reminder(recurrence=DAILY))
        centre = self.centre()
        self.tick(centre, store, "2026-08-30T08:00")
        store.dismiss("r", at("2026-08-30T08:05"))
        self.assertEqual(self.tick(centre, store, "2026-08-30T12:00"), ())
        self.assertEqual(self.tick(centre, store, "2026-08-31T08:00"), ("r",))


# -- snooze -----------------------------------------------------------------


class SnoozeTests(NotificationTestCase):
    def test_snooze_suppresses_an_immediate_notification(self):
        store = self.stored(reminder())
        centre = self.centre()
        self.tick(centre, store, "2026-08-30T08:00")
        store.snooze("r", at("2026-08-30T08:01"))
        self.assertEqual(self.tick(centre, store, "2026-08-30T08:02"), ())
        self.assertEqual(len(self.recorder.sent), 1)

    def test_a_snooze_expiring_allows_one_new_notification(self):
        store = self.stored(reminder())
        centre = self.centre()
        self.tick(centre, store, "2026-08-30T08:00")
        store.snooze("r", at("2026-08-30T08:01"))
        self.assertEqual(self.tick(centre, store, "2026-08-30T08:12"), ("r",))

    def test_it_does_not_keep_notifying_after_the_snooze_expires(self):
        store = self.stored(reminder())
        centre = self.centre()
        self.tick(centre, store, "2026-08-30T08:00")
        store.snooze("r", at("2026-08-30T08:01"))
        self.tick(centre, store, "2026-08-30T08:12")
        for moment in ("2026-08-30T08:13", "2026-08-30T08:30", "2026-08-30T09:00"):
            self.assertEqual(self.tick(centre, store, moment), ())
        self.assertEqual(len(self.recorder.sent), 2)

    def test_a_second_snooze_earns_a_third_notification_when_it_expires(self):
        store = self.stored(reminder())
        centre = self.centre()
        self.tick(centre, store, "2026-08-30T08:00")
        store.snooze("r", at("2026-08-30T08:01"))
        self.tick(centre, store, "2026-08-30T08:12")
        store.snooze("r", at("2026-08-30T08:13"))
        self.assertEqual(self.tick(centre, store, "2026-08-30T08:24"), ("r",))

    def test_snooze_withdraws_the_visible_notification(self):
        centre = self.centre()
        centre.withdraw("r")
        self.assertEqual(self.recorder.withdrawn, [notification_id("r")])

    def test_withdrawing_does_not_re_arm_the_same_episode(self):
        store = self.stored(reminder())
        centre = self.centre()
        self.tick(centre, store, "2026-08-30T08:00")
        centre.withdraw("r")
        self.assertEqual(self.tick(centre, store, "2026-08-30T08:00"), ())


# -- dismissal --------------------------------------------------------------


class DismissTests(NotificationTestCase):
    def test_a_dismissed_occurrence_does_not_notify(self):
        store = self.stored(reminder())
        store.dismiss("r", at("2026-08-30T08:00"))
        self.assertEqual(self.tick(self.centre(), store, "2026-08-30T08:00"), ())

    def test_a_dismissed_occurrence_never_notifies_again(self):
        store = self.stored(reminder())
        centre = self.centre()
        self.tick(centre, store, "2026-08-30T08:00")
        store.dismiss("r", at("2026-08-30T08:01"))
        for moment in ("2026-08-30T08:02", "2026-08-31T08:00", "2027-01-01T08:00"):
            self.assertEqual(self.tick(centre, store, moment), ())
        self.assertEqual(len(self.recorder.sent), 1)

    def test_dismissal_withdraws_the_visible_notification(self):
        centre = self.centre()
        centre.withdraw("r")
        self.assertEqual(self.recorder.withdrawn, [notification_id("r")])


# -- several reminders ------------------------------------------------------


class MultipleReminderTests(NotificationTestCase):
    def test_each_due_reminder_notifies(self):
        store = self.stored(
            reminder(id="a", due_at=at("2026-08-30T08:00")),
            reminder(id="b", due_at=at("2026-08-30T08:30")),
        )
        sent = self.tick(self.centre(), store, "2026-08-30T09:00")
        self.assertEqual(sorted(sent), ["a", "b"])

    def test_one_reminder_falling_due_later_notifies_on_its_own(self):
        store = self.stored(
            reminder(id="a", due_at=at("2026-08-30T08:00")),
            reminder(id="b", due_at=at("2026-08-30T18:00")),
        )
        centre = self.centre()
        self.assertEqual(self.tick(centre, store, "2026-08-30T08:00"), ("a",))
        self.assertEqual(self.tick(centre, store, "2026-08-30T18:00"), ("b",))

    def test_dealing_with_one_does_not_re_notify_the_other(self):
        store = self.stored(
            reminder(id="a", due_at=at("2026-08-30T08:00")),
            reminder(id="b", due_at=at("2026-08-30T08:00")),
        )
        centre = self.centre()
        self.tick(centre, store, "2026-08-30T08:00")
        store.dismiss("a", at("2026-08-30T08:01"))
        self.assertEqual(self.tick(centre, store, "2026-08-30T08:02"), ())

    def test_episodes_are_tracked_per_reminder(self):
        store = self.stored(
            reminder(id="a", due_at=at("2026-08-30T08:00")),
            reminder(id="b", due_at=at("2026-08-30T08:00")),
        )
        centre = self.centre()
        self.tick(centre, store, "2026-08-30T08:00")
        self.assertNotEqual(centre.notified_key("a"), centre.notified_key("b"))


# -- backend failure --------------------------------------------------------


class FailureTests(NotificationTestCase):
    def setUp(self):
        super().setUp()
        self.broken = Recorder(fail=True)

    def test_a_failing_backend_does_not_raise(self):
        store = self.stored(reminder())
        centre = self.centre(recorder=self.broken)
        self.assertEqual(self.tick(centre, store, "2026-08-30T08:00"), ())

    def test_a_failing_backend_is_reported_once(self):
        store = self.stored(reminder(recurrence=DAILY))
        centre = self.centre(recorder=self.broken)
        for day in ("2026-08-30", "2026-08-31", "2026-09-01"):
            self.tick(centre, store, f"{day}T08:00")
        self.assertEqual(len(self.broken.reported), 1)

    def test_the_report_explains_itself(self):
        store = self.stored(reminder())
        self.tick(self.centre(recorder=self.broken), store, "2026-08-30T08:00")
        self.assertIn("notification", self.broken.reported[0])

    def test_a_failing_backend_does_not_retry_every_tick(self):
        # Reporting each failed send would put the same line on stderr every
        # thirty seconds all day.
        store = self.stored(reminder())
        centre = self.centre(recorder=self.broken)
        for _ in range(10):
            self.tick(centre, store, "2026-08-30T08:00")
        self.assertEqual(len(self.broken.reported), 1)

    def test_a_failing_backend_does_not_change_the_reminder_store(self):
        store = self.stored(reminder())
        before = self.path.read_text(encoding="utf-8")
        self.tick(self.centre(recorder=self.broken), store, "2026-08-30T08:00")
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_a_failing_backend_leaves_the_reminder_due(self):
        store = self.stored(reminder())
        self.tick(self.centre(recorder=self.broken), store, "2026-08-30T08:00")
        self.assertEqual(
            [r.id for r in store.due_reminders(at("2026-08-30T08:00"))], ["r"]
        )

    def test_a_failing_backend_does_not_dismiss_anything(self):
        store = self.stored(reminder())
        self.tick(self.centre(recorder=self.broken), store, "2026-08-30T08:00")
        self.assertIsNone(store.reminder_by_id("r").dismissed_occurrence)
        self.assertIsNone(store.reminder_by_id("r").snoozed_until)

    def test_a_failing_withdrawal_does_not_raise(self):
        self.centre(recorder=self.broken).withdraw("r")
        self.assertEqual(len(self.broken.reported), 1)

    def test_a_centre_with_no_withdraw_support_is_silent(self):
        centre = NotificationCenter(send=self.recorder.send, withdraw=None, enabled=True)
        centre.withdraw("r")  # must not raise
        self.assertEqual(self.recorder.withdrawn, [])


# -- which reminder the card shows ------------------------------------------


class SelectActiveTests(unittest.TestCase):
    def setUp(self):
        self.first = reminder(id="a")
        self.second = reminder(id="b")

    def test_nothing_due_means_nothing_active(self):
        self.assertIsNone(select_active(()))

    def test_the_first_due_reminder_wins_by_default(self):
        self.assertIs(select_active((self.first, self.second)), self.first)

    def test_a_preferred_reminder_is_honoured(self):
        self.assertIs(select_active((self.first, self.second), "b"), self.second)

    def test_a_preferred_reminder_that_is_not_due_falls_back(self):
        self.assertIs(select_active((self.first, self.second), "gone"), self.first)

    def test_a_preference_cannot_conjure_a_reminder_from_nothing(self):
        self.assertIsNone(select_active((), "a"))

    def test_no_preference_keeps_the_store_ordering(self):
        self.assertIs(select_active((self.second, self.first), None), self.second)


if __name__ == "__main__":
    unittest.main()
