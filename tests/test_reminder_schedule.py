"""Tests for recurrence, due state, snooze and dismissal.

Every single assertion here passes an explicit ``now``. No test changes the
system clock, sleeps, or depends on when it happens to run — which is the whole
reason the scheduling model lives in a GTK-free module with a ``now`` parameter
on every time-dependent method.
"""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from rem_bubbles.reminder_store import (
    DAILY,
    OVERDUE_AFTER,
    SNOOZE,
    STATUS_DISABLED,
    STATUS_DISMISSED,
    STATUS_DUE,
    STATUS_OVERDUE,
    STATUS_SNOOZED,
    STATUS_UPCOMING,
    WEEKLY,
    Reminder,
    ReminderStore,
    ReminderStoreError,
    current_occurrence,
    next_occurrence,
    parse_local_datetime,
    write_reminders,
)


def at(text: str) -> datetime:
    return parse_local_datetime(text)


def reminder(**overrides) -> Reminder:
    fields = {
        "id": "r",
        "text": "Do it.",
        "due_at": at("2026-08-30T08:00"),
    }
    fields.update(overrides)
    return Reminder(**fields)


class ScheduleTestCase(unittest.TestCase):
    """A store built in memory, with no file behind it."""

    def store(self, *reminders) -> ReminderStore:
        return ReminderStore(reminders)

    def assertStatus(self, entry, now, expected):
        self.assertEqual(self.store(entry).status(entry, at(now)), expected)


# -- recurrence: none -------------------------------------------------------


class NonRecurringTests(ScheduleTestCase):
    def setUp(self):
        self.entry = reminder(due_at=at("2026-08-30T08:00"))

    def test_before_the_due_time_there_is_no_occurrence(self):
        self.assertIsNone(current_occurrence(self.entry, at("2026-08-30T07:59")))

    def test_exactly_at_the_due_time(self):
        self.assertEqual(
            current_occurrence(self.entry, at("2026-08-30T08:00")),
            at("2026-08-30T08:00"),
        )

    def test_after_the_due_time_the_occurrence_does_not_move(self):
        self.assertEqual(
            current_occurrence(self.entry, at("2026-09-15T12:34")),
            at("2026-08-30T08:00"),
        )

    def test_the_next_occurrence_before_it_is_due(self):
        self.assertEqual(
            next_occurrence(self.entry, at("2026-08-29T08:00")), at("2026-08-30T08:00")
        )

    def test_there_is_no_next_occurrence_once_it_has_passed(self):
        self.assertIsNone(next_occurrence(self.entry, at("2026-08-30T08:01")))

    def test_upcoming_before_it_is_due(self):
        self.assertStatus(self.entry, "2026-08-30T07:59", STATUS_UPCOMING)

    def test_due_at_the_moment_it_arrives(self):
        self.assertStatus(self.entry, "2026-08-30T08:00", STATUS_DUE)

    def test_overdue_once_the_grace_window_has_passed(self):
        self.assertStatus(self.entry, "2026-08-30T09:00", STATUS_OVERDUE)

    def test_a_dismissed_one_time_reminder_stays_dismissed(self):
        done = reminder(dismissed_occurrence=at("2026-08-30T08:00"))
        for moment in ("2026-08-30T08:00", "2026-08-31T08:00", "2027-01-01T08:00"):
            self.assertStatus(done, moment, STATUS_DISMISSED)

    def test_a_dismissed_one_time_reminder_never_becomes_due_again(self):
        done = reminder(dismissed_occurrence=at("2026-08-30T08:00"))
        store = self.store(done)
        self.assertEqual(store.due_reminders(at("2030-01-01T08:00")), ())


# -- recurrence: daily ------------------------------------------------------


class DailyTests(ScheduleTestCase):
    def setUp(self):
        self.entry = reminder(due_at=at("2026-08-30T08:00"), recurrence=DAILY)

    def test_before_the_first_occurrence(self):
        self.assertIsNone(current_occurrence(self.entry, at("2026-08-29T23:59")))

    def test_the_first_occurrence(self):
        self.assertEqual(
            current_occurrence(self.entry, at("2026-08-30T08:00")),
            at("2026-08-30T08:00"),
        )

    def test_later_the_same_day(self):
        self.assertEqual(
            current_occurrence(self.entry, at("2026-08-30T23:00")),
            at("2026-08-30T08:00"),
        )

    def test_the_next_day(self):
        self.assertEqual(
            current_occurrence(self.entry, at("2026-08-31T09:00")),
            at("2026-08-31T08:00"),
        )

    def test_before_the_next_day_s_time(self):
        self.assertEqual(
            current_occurrence(self.entry, at("2026-08-31T07:59")),
            at("2026-08-30T08:00"),
        )

    def test_many_days_later(self):
        self.assertEqual(
            current_occurrence(self.entry, at("2026-12-25T10:00")),
            at("2026-12-25T08:00"),
        )

    def test_the_wall_clock_time_never_drifts(self):
        for day in range(0, 400, 37):
            moment = at("2026-08-30T09:00") + timedelta(days=day)
            occurrence = current_occurrence(self.entry, moment)
            self.assertEqual((occurrence.hour, occurrence.minute), (8, 0))

    def test_missed_days_collapse_to_the_latest_occurrence(self):
        # The application was closed Monday to Wednesday; Thursday morning
        # there is one reminder waiting, not four.
        thursday = at("2026-09-03T10:00")
        self.assertEqual(
            current_occurrence(self.entry, thursday), at("2026-09-03T08:00")
        )

    def test_missed_days_produce_exactly_one_due_reminder(self):
        store = self.store(self.entry)
        self.assertEqual(len(store.due_reminders(at("2026-09-03T10:00"))), 1)

    def test_the_next_occurrence_is_tomorrow(self):
        self.assertEqual(
            next_occurrence(self.entry, at("2026-08-30T09:00")), at("2026-08-31T08:00")
        )

    def test_a_dismissed_day_is_not_due(self):
        done = reminder(
            due_at=at("2026-08-30T08:00"),
            recurrence=DAILY,
            dismissed_occurrence=at("2026-08-30T08:00"),
        )
        self.assertStatus(done, "2026-08-30T12:00", STATUS_DISMISSED)

    def test_dismissing_one_day_does_not_suppress_the_next(self):
        done = reminder(
            due_at=at("2026-08-30T08:00"),
            recurrence=DAILY,
            dismissed_occurrence=at("2026-08-30T08:00"),
        )
        self.assertStatus(done, "2026-08-31T08:00", STATUS_DUE)

    def test_a_dismissed_day_is_upcoming_again_before_the_next_time(self):
        done = reminder(
            due_at=at("2026-08-30T08:00"),
            recurrence=DAILY,
            dismissed_occurrence=at("2026-08-31T08:00"),
        )
        self.assertStatus(done, "2026-08-31T20:00", STATUS_DISMISSED)
        self.assertStatus(done, "2026-09-01T08:00", STATUS_DUE)


# -- recurrence: weekly -----------------------------------------------------


class WeeklyTests(ScheduleTestCase):
    def setUp(self):
        self.entry = reminder(due_at=at("2026-08-31T09:00"), recurrence=WEEKLY)

    def test_before_the_first_occurrence(self):
        self.assertIsNone(current_occurrence(self.entry, at("2026-08-31T08:59")))

    def test_the_first_occurrence(self):
        self.assertEqual(
            current_occurrence(self.entry, at("2026-08-31T09:00")),
            at("2026-08-31T09:00"),
        )

    def test_six_days_later_is_still_the_first_occurrence(self):
        self.assertEqual(
            current_occurrence(self.entry, at("2026-09-06T23:00")),
            at("2026-08-31T09:00"),
        )

    def test_seven_days_later(self):
        self.assertEqual(
            current_occurrence(self.entry, at("2026-09-07T09:00")),
            at("2026-09-07T09:00"),
        )

    def test_many_weeks_later(self):
        # 2026-08-31 plus 20 weeks is 2027-01-18.
        self.assertEqual(
            current_occurrence(self.entry, at("2027-01-18T12:00")),
            at("2027-01-18T09:00"),
        )

    def test_the_weekday_never_drifts(self):
        monday = at("2026-08-31T09:00").weekday()
        for weeks in range(0, 60, 7):
            moment = at("2026-08-31T12:00") + timedelta(weeks=weeks)
            self.assertEqual(current_occurrence(self.entry, moment).weekday(), monday)

    def test_missed_weeks_collapse_to_the_latest_occurrence(self):
        self.assertEqual(
            current_occurrence(self.entry, at("2026-09-22T10:00")),
            at("2026-09-21T09:00"),
        )

    def test_missed_weeks_produce_exactly_one_due_reminder(self):
        store = self.store(self.entry)
        self.assertEqual(len(store.due_reminders(at("2026-09-22T10:00"))), 1)

    def test_the_next_occurrence_is_in_seven_days(self):
        self.assertEqual(
            next_occurrence(self.entry, at("2026-08-31T10:00")), at("2026-09-07T09:00")
        )

    def test_a_dismissed_week_is_not_due(self):
        done = reminder(
            due_at=at("2026-08-31T09:00"),
            recurrence=WEEKLY,
            dismissed_occurrence=at("2026-08-31T09:00"),
        )
        self.assertStatus(done, "2026-09-02T09:00", STATUS_DISMISSED)

    def test_dismissing_one_week_does_not_suppress_the_next(self):
        done = reminder(
            due_at=at("2026-08-31T09:00"),
            recurrence=WEEKLY,
            dismissed_occurrence=at("2026-08-31T09:00"),
        )
        self.assertStatus(done, "2026-09-07T09:00", STATUS_DUE)


# -- due state --------------------------------------------------------------


class DueStateTests(ScheduleTestCase):
    def test_an_empty_store_has_nothing_due(self):
        self.assertEqual(self.store().due_reminders(at("2026-08-30T08:00")), ())

    def test_an_empty_store_has_no_next_due(self):
        self.assertIsNone(self.store().next_due(at("2026-08-30T08:00")))

    def test_an_upcoming_reminder_is_not_due(self):
        entry = reminder()
        self.assertEqual(self.store(entry).due_reminders(at("2026-08-29T08:00")), ())

    def test_a_due_reminder_is_due(self):
        entry = reminder()
        self.assertEqual(
            self.store(entry).due_reminders(at("2026-08-30T08:00")), (entry,)
        )

    def test_an_overdue_reminder_is_due(self):
        entry = reminder()
        self.assertEqual(
            self.store(entry).due_reminders(at("2026-08-30T18:00")), (entry,)
        )

    def test_a_disabled_reminder_is_never_due(self):
        entry = reminder(enabled=False)
        store = self.store(entry)
        self.assertEqual(store.status(entry, at("2026-09-30T08:00")), STATUS_DISABLED)
        self.assertEqual(store.due_reminders(at("2026-09-30T08:00")), ())

    def test_an_all_disabled_store_has_nothing_due(self):
        store = self.store(
            reminder(id="a", enabled=False), reminder(id="b", enabled=False)
        )
        self.assertEqual(store.due_reminders(at("2026-09-30T08:00")), ())

    def test_a_disabled_reminder_keeps_its_recorded_state(self):
        # Disabling is a pause, not a reset: re-enabling must not replay an
        # occurrence the user already dismissed.
        entry = reminder(enabled=False, dismissed_occurrence=at("2026-08-30T08:00"))
        enabled_again = Reminder(
            id=entry.id,
            text=entry.text,
            due_at=entry.due_at,
            recurrence=entry.recurrence,
            enabled=True,
            snoozed_until=entry.snoozed_until,
            dismissed_occurrence=entry.dismissed_occurrence,
        )
        self.assertEqual(
            self.store(enabled_again).status(enabled_again, at("2026-08-30T12:00")),
            STATUS_DISMISSED,
        )

    def test_a_snoozed_reminder_is_not_due(self):
        entry = reminder(snoozed_until=at("2026-08-30T08:10"))
        store = self.store(entry)
        self.assertEqual(store.status(entry, at("2026-08-30T08:05")), STATUS_SNOOZED)
        self.assertEqual(store.due_reminders(at("2026-08-30T08:05")), ())

    def test_a_snooze_expiring_makes_it_due_again(self):
        entry = reminder(snoozed_until=at("2026-08-30T08:10"))
        store = self.store(entry)
        self.assertEqual(store.due_reminders(at("2026-08-30T08:11")), (entry,))

    def test_a_snooze_expires_exactly_at_its_deadline(self):
        entry = reminder(snoozed_until=at("2026-08-30T08:10"))
        store = self.store(entry)
        self.assertEqual(store.status(entry, at("2026-08-30T08:10")), STATUS_OVERDUE)

    def test_a_snooze_in_the_past_does_not_hide_anything(self):
        entry = reminder(snoozed_until=at("2026-08-29T08:10"))
        self.assertEqual(
            self.store(entry).due_reminders(at("2026-08-30T08:00")), (entry,)
        )

    def test_a_dismissed_occurrence_outranks_a_live_snooze(self):
        entry = reminder(
            snoozed_until=at("2026-08-30T09:00"),
            dismissed_occurrence=at("2026-08-30T08:00"),
        )
        self.assertEqual(
            self.store(entry).status(entry, at("2026-08-30T08:30")), STATUS_DISMISSED
        )

    def test_a_stale_dismissal_of_another_occurrence_does_not_hide_this_one(self):
        entry = reminder(
            recurrence=DAILY, dismissed_occurrence=at("2026-08-29T08:00")
        )
        self.assertEqual(
            self.store(entry).status(entry, at("2026-08-30T08:00")), STATUS_DUE
        )

    def test_the_overdue_boundary(self):
        entry = reminder()
        store = self.store(entry)
        just_inside = at("2026-08-30T08:00") + OVERDUE_AFTER - timedelta(seconds=1)
        self.assertEqual(store.status(entry, just_inside), STATUS_DUE)
        self.assertEqual(
            store.status(entry, at("2026-08-30T08:00") + OVERDUE_AFTER), STATUS_OVERDUE
        )

    def test_is_due_covers_both_due_and_overdue(self):
        entry = reminder()
        store = self.store(entry)
        self.assertTrue(store.is_due(entry, at("2026-08-30T08:00")))
        self.assertTrue(store.is_due(entry, at("2026-08-30T20:00")))
        self.assertFalse(store.is_due(entry, at("2026-08-30T07:00")))


# -- ordering ---------------------------------------------------------------


class DueOrderingTests(ScheduleTestCase):
    def test_the_oldest_occurrence_comes_first(self):
        early = reminder(id="early", due_at=at("2026-08-30T08:00"))
        late = reminder(id="late", due_at=at("2026-08-30T09:00"))
        store = self.store(late, early)
        self.assertEqual(
            [r.id for r in store.due_reminders(at("2026-08-30T10:00"))],
            ["early", "late"],
        )

    def test_ties_are_broken_by_id(self):
        first = reminder(id="aaa", due_at=at("2026-08-30T08:00"))
        second = reminder(id="bbb", due_at=at("2026-08-30T08:00"))
        store = self.store(second, first)
        self.assertEqual(
            [r.id for r in store.due_reminders(at("2026-08-30T10:00"))], ["aaa", "bbb"]
        )

    def test_ordering_is_independent_of_file_order(self):
        entries = [
            reminder(id="c", due_at=at("2026-08-30T10:00")),
            reminder(id="a", due_at=at("2026-08-30T08:00")),
            reminder(id="b", due_at=at("2026-08-30T09:00")),
        ]
        forwards = self.store(*entries).due_reminders(at("2026-08-30T12:00"))
        backwards = self.store(*reversed(entries)).due_reminders(at("2026-08-30T12:00"))
        self.assertEqual([r.id for r in forwards], [r.id for r in backwards])

    def test_a_recurring_reminder_is_sorted_by_its_active_occurrence(self):
        # Not by its original due_at: Monday's missed 08:00 is irrelevant once
        # Thursday's 08:00 has arrived.
        daily = reminder(id="daily", due_at=at("2026-08-30T08:00"), recurrence=DAILY)
        once = reminder(id="once", due_at=at("2026-09-03T07:00"))
        store = self.store(daily, once)
        self.assertEqual(
            [r.id for r in store.due_reminders(at("2026-09-03T10:00"))],
            ["once", "daily"],
        )

    def test_next_due_is_the_first_of_the_ordering(self):
        early = reminder(id="early", due_at=at("2026-08-30T08:00"))
        late = reminder(id="late", due_at=at("2026-08-30T09:00"))
        store = self.store(late, early)
        self.assertEqual(store.next_due(at("2026-08-30T10:00")).id, "early")

    def test_only_due_reminders_are_listed(self):
        due = reminder(id="due", due_at=at("2026-08-30T08:00"))
        upcoming = reminder(id="upcoming", due_at=at("2026-09-30T08:00"))
        off = reminder(id="off", due_at=at("2026-08-30T08:00"), enabled=False)
        store = self.store(due, upcoming, off)
        self.assertEqual(
            [r.id for r in store.due_reminders(at("2026-08-30T10:00"))], ["due"]
        )


# -- mutation ---------------------------------------------------------------


class MutationTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.path = Path(self._temp.name) / "reminders.json"

    def stored(self, *reminders) -> ReminderStore:
        write_reminders(self.path, reminders)
        return ReminderStore.from_file(self.path)

    def on_disk(self, reminder_id: str) -> Reminder:
        return ReminderStore.from_file(self.path).reminder_by_id(reminder_id)

    # -- snooze ---------------------------------------------------------

    def test_snooze_sets_the_deadline_ten_minutes_out(self):
        store = self.stored(reminder())
        updated = store.snooze("r", at("2026-08-30T08:05"))
        self.assertEqual(updated.snoozed_until, at("2026-08-30T08:15"))

    def test_the_snooze_interval_is_ten_minutes(self):
        self.assertEqual(SNOOZE, timedelta(minutes=10))

    def test_snooze_removes_it_from_the_due_queue(self):
        store = self.stored(reminder())
        store.snooze("r", at("2026-08-30T08:05"))
        self.assertEqual(store.due_reminders(at("2026-08-30T08:06")), ())

    def test_it_comes_back_when_the_snooze_expires(self):
        store = self.stored(reminder())
        store.snooze("r", at("2026-08-30T08:05"))
        self.assertEqual(
            [r.id for r in store.due_reminders(at("2026-08-30T08:16"))], ["r"]
        )

    def test_snooze_does_not_dismiss_the_occurrence(self):
        store = self.stored(reminder())
        updated = store.snooze("r", at("2026-08-30T08:05"))
        self.assertIsNone(updated.dismissed_occurrence)

    def test_snooze_reaches_the_disk(self):
        store = self.stored(reminder())
        store.snooze("r", at("2026-08-30T08:05"))
        self.assertEqual(self.on_disk("r").snoozed_until, at("2026-08-30T08:15"))

    def test_snooze_does_not_move_the_occurrence(self):
        store = self.stored(reminder(recurrence=DAILY))
        store.snooze("r", at("2026-08-30T08:05"))
        self.assertEqual(
            store.occurrence(store.reminder_by_id("r"), at("2026-08-30T08:20")),
            at("2026-08-30T08:00"),
        )

    def test_snoozing_twice_extends_from_the_second_moment(self):
        store = self.stored(reminder())
        store.snooze("r", at("2026-08-30T08:05"))
        store.snooze("r", at("2026-08-30T08:20"))
        self.assertEqual(self.on_disk("r").snoozed_until, at("2026-08-30T08:30"))

    # -- dismiss --------------------------------------------------------

    def test_dismiss_records_the_current_occurrence(self):
        store = self.stored(reminder())
        updated = store.dismiss("r", at("2026-08-30T09:00"))
        self.assertEqual(updated.dismissed_occurrence, at("2026-08-30T08:00"))

    def test_dismiss_records_the_recurring_occurrence_not_the_original(self):
        store = self.stored(reminder(recurrence=DAILY))
        updated = store.dismiss("r", at("2026-09-03T10:00"))
        self.assertEqual(updated.dismissed_occurrence, at("2026-09-03T08:00"))

    def test_dismiss_clears_any_snooze(self):
        store = self.stored(reminder(snoozed_until=at("2026-08-30T08:15")))
        updated = store.dismiss("r", at("2026-08-30T09:00"))
        self.assertIsNone(updated.snoozed_until)

    def test_dismiss_removes_it_from_the_due_queue(self):
        store = self.stored(reminder())
        store.dismiss("r", at("2026-08-30T09:00"))
        self.assertEqual(store.due_reminders(at("2026-08-30T09:01")), ())

    def test_a_dismissed_one_time_reminder_never_returns(self):
        store = self.stored(reminder())
        store.dismiss("r", at("2026-08-30T09:00"))
        self.assertEqual(store.due_reminders(at("2030-01-01T09:00")), ())

    def test_dismiss_does_not_disable_a_recurring_reminder(self):
        store = self.stored(reminder(recurrence=DAILY))
        updated = store.dismiss("r", at("2026-08-30T09:00"))
        self.assertTrue(updated.enabled)

    def test_a_dismissed_daily_reminder_returns_the_next_day(self):
        store = self.stored(reminder(recurrence=DAILY))
        store.dismiss("r", at("2026-08-30T09:00"))
        self.assertEqual(store.due_reminders(at("2026-08-30T23:00")), ())
        self.assertEqual(
            [r.id for r in store.due_reminders(at("2026-08-31T08:00"))], ["r"]
        )

    def test_a_dismissed_weekly_reminder_returns_the_next_week(self):
        store = self.stored(reminder(recurrence=WEEKLY))
        store.dismiss("r", at("2026-08-30T09:00"))
        self.assertEqual(store.due_reminders(at("2026-09-05T09:00")), ())
        self.assertEqual(
            [r.id for r in store.due_reminders(at("2026-09-06T08:00"))], ["r"]
        )

    def test_dismiss_reaches_the_disk(self):
        store = self.stored(reminder())
        store.dismiss("r", at("2026-08-30T09:00"))
        self.assertEqual(self.on_disk("r").dismissed_occurrence, at("2026-08-30T08:00"))

    def test_dismissing_something_not_yet_due_is_refused(self):
        store = self.stored(reminder())
        with self.assertRaises(ReminderStoreError):
            store.dismiss("r", at("2026-08-29T09:00"))

    def test_an_unknown_id_is_refused(self):
        store = self.stored(reminder())
        for operation in (store.snooze, store.dismiss):
            with self.assertRaises(ReminderStoreError):
                operation("nope", at("2026-08-30T09:00"))

    # -- isolation ------------------------------------------------------

    def test_other_reminders_are_untouched(self):
        store = self.stored(reminder(id="a"), reminder(id="b"), reminder(id="c"))
        store.dismiss("b", at("2026-08-30T09:00"))
        self.assertIsNone(self.on_disk("a").dismissed_occurrence)
        self.assertIsNone(self.on_disk("c").dismissed_occurrence)

    def test_order_survives_a_mutation(self):
        store = self.stored(reminder(id="c"), reminder(id="a"), reminder(id="b"))
        store.dismiss("a", at("2026-08-30T09:00"))
        self.assertEqual(
            [r.id for r in ReminderStore.from_file(self.path).reminders],
            ["c", "a", "b"],
        )

    def test_text_and_schedule_survive_a_mutation(self):
        store = self.stored(reminder(text="Café ☕", recurrence=WEEKLY))
        store.snooze("r", at("2026-08-30T09:00"))
        saved = self.on_disk("r")
        self.assertEqual(saved.text, "Café ☕")
        self.assertEqual(saved.recurrence, WEEKLY)
        self.assertEqual(saved.due_at, at("2026-08-30T08:00"))


# -- persistence failure ----------------------------------------------------


class FailedPersistenceTests(unittest.TestCase):
    """A mutation that cannot reach the disk must change nothing at all."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.directory = Path(self._temp.name)
        self.path = self.directory / "reminders.json"
        write_reminders(self.path, [reminder()])
        self.store = ReminderStore.from_file(self.path)
        self.before = self.path.read_text(encoding="utf-8")

    def failing(self):
        return mock.patch("os.replace", side_effect=OSError(28, "No space left"))

    def test_a_failed_dismiss_raises(self):
        with self.failing():
            with self.assertRaises(OSError):
                self.store.dismiss("r", at("2026-08-30T09:00"))

    def test_a_failed_dismiss_leaves_the_file_intact(self):
        with self.failing():
            with self.assertRaises(OSError):
                self.store.dismiss("r", at("2026-08-30T09:00"))
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.before)

    def test_a_failed_dismiss_leaves_the_reminder_due(self):
        with self.failing():
            with self.assertRaises(OSError):
                self.store.dismiss("r", at("2026-08-30T09:00"))
        self.assertEqual(
            [r.id for r in self.store.due_reminders(at("2026-08-30T09:00"))], ["r"]
        )

    def test_a_failed_dismiss_leaves_the_in_memory_state_unchanged(self):
        with self.failing():
            with self.assertRaises(OSError):
                self.store.dismiss("r", at("2026-08-30T09:00"))
        self.assertIsNone(self.store.reminder_by_id("r").dismissed_occurrence)

    def test_a_failed_snooze_leaves_the_reminder_due(self):
        with self.failing():
            with self.assertRaises(OSError):
                self.store.snooze("r", at("2026-08-30T09:00"))
        self.assertIsNone(self.store.reminder_by_id("r").snoozed_until)
        self.assertEqual(
            [r.id for r in self.store.due_reminders(at("2026-08-30T09:00"))], ["r"]
        )

    def test_a_failed_write_leaves_no_temporary_file(self):
        with self.failing():
            with self.assertRaises(OSError):
                self.store.dismiss("r", at("2026-08-30T09:00"))
        leftovers = [p for p in self.directory.iterdir() if p.name != self.path.name]
        self.assertEqual(leftovers, [])

    def test_a_store_with_no_file_cannot_be_mutated(self):
        detached = ReminderStore([reminder()])
        with self.assertRaises(ReminderStoreError):
            detached.dismiss("r", at("2026-08-30T09:00"))


# -- the default now --------------------------------------------------------


class DefaultNowTests(ScheduleTestCase):
    """Omitting ``now`` means the current local time — and nothing else."""

    def test_status_uses_datetime_now_when_not_given_one(self):
        entry = reminder()
        store = self.store(entry)
        # Parsed before the patch: fromisoformat lives on the class being mocked.
        before, after = at("2026-08-30T07:00"), at("2026-08-30T09:00")
        with mock.patch("rem_bubbles.reminder_store.datetime") as clock:
            clock.now.return_value = before
            self.assertEqual(store.status(entry), STATUS_UPCOMING)
            clock.now.return_value = after
            self.assertEqual(store.status(entry), STATUS_OVERDUE)

    def test_due_reminders_uses_datetime_now_when_not_given_one(self):
        entry = reminder()
        store = self.store(entry)
        moment = at("2026-08-30T09:00")
        with mock.patch("rem_bubbles.reminder_store.datetime") as clock:
            clock.now.return_value = moment
            self.assertEqual([r.id for r in store.due_reminders()], ["r"])


if __name__ == "__main__":
    unittest.main()
