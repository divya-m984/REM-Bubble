"""Tests for the reminder half of the CLI: list, add, remove, enable, disable.

Everything runs against a temporary ``XDG_CONFIG_HOME``; the real
``~/.config/rem-bubbles`` is never touched. No GTK, Wayland or display server is
involved, and nothing here depends on what time it is except the handful of
tests that deliberately schedule relative to ``datetime.now()``.
"""

import contextlib
import io
import json
import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from rem_bubbles import cli
from rem_bubbles.quote_store import load_quotes
from rem_bubbles.reminder_store import (
    DAILY,
    NONE,
    WEEKLY,
    Reminder,
    load_reminders,
    write_reminders,
)

from test_config import IsolatedConfigTestCase


class ReminderCommandTestCase(IsolatedConfigTestCase):
    """Runs CLI commands against an isolated personal reminder file."""

    def setUp(self):
        super().setUp()
        self.reminders_path = self.config_dir / "reminders.json"

    def run_cli(self, argv) -> int:
        with open(os.devnull, "w", encoding="utf-8") as sink:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                return cli.main(argv)

    def output_of(self, argv):
        """Run a command, returning (status, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cli.main(argv)
        return status, out.getvalue(), err.getvalue()

    def add(self, text, *argv) -> int:
        return self.run_cli(["reminder", "add", text, *argv])

    def add_at(self, text, when, *argv) -> int:
        return self.add(text, "--at", when, *argv)

    def stored(self) -> list[Reminder]:
        return load_reminders(self.reminders_path)

    def ids(self) -> list[str]:
        return [reminder.id for reminder in self.stored()]

    def by_id(self, reminder_id: str) -> Reminder:
        for reminder in self.stored():
            if reminder.id == reminder_id:
                return reminder
        raise AssertionError(f"no reminder {reminder_id!r} in {self.ids()}")

    def seed(self, *texts) -> None:
        for index, text in enumerate(texts):
            self.assertEqual(self.add_at(text, f"2030-01-0{index + 1} 09:00"), 0)


# -- due-time parsing -------------------------------------------------------


class DueParsingTests(unittest.TestCase):
    def test_space_separated(self):
        self.assertEqual(
            cli.parse_due_at("2026-08-30 18:00"), datetime(2026, 8, 30, 18, 0)
        )

    def test_t_separated(self):
        self.assertEqual(
            cli.parse_due_at("2026-08-30T18:00"), datetime(2026, 8, 30, 18, 0)
        )

    def test_seconds_are_optional(self):
        self.assertEqual(
            cli.parse_due_at("2026-08-30 18:00:45"),
            datetime(2026, 8, 30, 18, 0, 45),
        )

    def test_a_bare_date_is_refused(self):
        with self.assertRaises(cli.CommandError):
            cli.parse_due_at("2026-08-30")

    def test_natural_language_is_refused(self):
        for text in ("tomorrow", "tomorrow evening", "next Tuesday", "in an hour"):
            with self.assertRaises(cli.CommandError):
                cli.parse_due_at(text)

    def test_a_timezone_offset_is_refused(self):
        with self.assertRaises(cli.CommandError):
            cli.parse_due_at("2026-08-30T18:00:00+02:00")

    def test_the_error_shows_the_expected_shape(self):
        with self.assertRaises(cli.CommandError) as caught:
            cli.parse_due_at("soon")
        self.assertIn("2026-08-30 18:00", str(caught.exception))


# -- add --------------------------------------------------------------------


class AddTests(ReminderCommandTestCase):
    def test_first_add_creates_the_file(self):
        self.assertFalse(self.reminders_path.exists())
        self.assertEqual(self.add_at("Submit the report.", "2030-08-30 18:00"), 0)
        self.assertTrue(self.reminders_path.is_file())
        self.assertEqual(self.ids(), ["submit-the-report"])

    def test_first_add_creates_the_config_directory(self):
        self.assertFalse(self.config_dir.exists())
        self.add_at("Submit the report.", "2030-08-30 18:00")
        self.assertTrue(self.config_dir.is_dir())

    def test_new_config_directory_is_user_private(self):
        self.add_at("Submit the report.", "2030-08-30 18:00")
        self.assertEqual(os.stat(self.config_dir).st_mode & 0o777, 0o700)

    def test_the_new_file_is_user_private(self):
        self.add_at("Submit the report.", "2030-08-30 18:00")
        self.assertEqual(os.stat(self.reminders_path).st_mode & 0o777, 0o600)

    def test_first_add_stores_only_what_was_asked_for(self):
        # examples/reminders.json must never be seeded into a personal file.
        self.add_at("Submit the report.", "2030-08-30 18:00")
        self.assertEqual(len(self.stored()), 1)

    def test_the_due_time_is_normalised(self):
        self.add_at("Submit the report.", "2030-08-30 18:00")
        self.assertEqual(
            self.by_id("submit-the-report").due_at, datetime(2030, 8, 30, 18, 0)
        )
        payload = json.loads(self.reminders_path.read_text(encoding="utf-8"))
        self.assertEqual(payload[0]["due_at"], "2030-08-30T18:00:00")

    def test_the_t_separated_form_is_accepted(self):
        self.assertEqual(self.add_at("Submit.", "2030-08-30T18:00"), 0)
        self.assertEqual(self.by_id("submit").due_at, datetime(2030, 8, 30, 18, 0))

    def test_generated_id(self):
        self.add_at("Submit the report.", "2030-08-30 18:00")
        self.assertEqual(self.ids(), ["submit-the-report"])

    def test_explicit_id(self):
        self.assertEqual(
            self.add_at("Submit the report.", "2030-08-30 18:00", "--id", "report"), 0
        )
        self.assertEqual(self.ids(), ["report"])

    def test_duplicate_explicit_id_is_rejected(self):
        self.add_at("One.", "2030-08-30 18:00", "--id", "same")
        self.assertEqual(self.add_at("Two.", "2030-08-31 18:00", "--id", "same"), 1)
        self.assertEqual(self.ids(), ["same"])
        self.assertEqual(self.by_id("same").text, "One.")

    def test_blank_explicit_id_is_rejected(self):
        self.assertEqual(self.add_at("Something.", "2030-08-30 18:00", "--id", "  "), 1)
        self.assertFalse(self.reminders_path.exists())

    def test_duplicate_generated_slug_gets_a_suffix(self):
        self.add_at("Submit the report.", "2030-08-30 18:00")
        self.add_at("Submit the report.", "2030-09-30 18:00")
        self.assertEqual(
            self.ids(), ["submit-the-report", "submit-the-report-2"]
        )

    def test_third_duplicate_keeps_counting(self):
        for month in (8, 9, 10):
            self.add_at("Same text.", f"2030-{month:02d}-01 18:00")
        self.assertEqual(self.ids(), ["same-text", "same-text-2", "same-text-3"])

    def test_recurrence_defaults_to_none(self):
        self.add_at("Submit.", "2030-08-30 18:00")
        self.assertEqual(self.by_id("submit").recurrence, NONE)

    def test_daily_recurrence(self):
        self.add_at("Stand up.", "2030-08-30 11:00", "--repeat", "daily")
        self.assertEqual(self.by_id("stand-up").recurrence, DAILY)

    def test_weekly_recurrence(self):
        self.add_at("Review my week.", "2030-08-31 09:00", "--repeat", "weekly")
        self.assertEqual(self.by_id("review-my-week").recurrence, WEEKLY)

    def test_explicit_none_recurrence(self):
        self.add_at("Submit.", "2030-08-30 18:00", "--repeat", "none")
        self.assertEqual(self.by_id("submit").recurrence, NONE)

    def test_recurrence_is_reported(self):
        _, output, _ = self.output_of(
            ["reminder", "add", "Review.", "--at", "2030-08-31 09:00",
             "--repeat", "weekly"]
        )
        self.assertIn("weekly", output)

    def test_enabled_by_default(self):
        self.add_at("Submit.", "2030-08-30 18:00")
        self.assertTrue(self.by_id("submit").enabled)

    def test_disabled_flag(self):
        self.assertEqual(self.add_at("Submit.", "2030-08-30 18:00", "--disabled"), 0)
        self.assertFalse(self.by_id("submit").enabled)

    def test_a_disabled_reminder_can_be_the_only_one(self):
        # Unlike quotes, there is no "one must stay enabled" rule.
        self.assertEqual(self.add_at("Submit.", "2030-08-30 18:00", "--disabled"), 0)
        self.assertEqual(len(self.stored()), 1)

    def test_state_fields_start_empty(self):
        self.add_at("Submit.", "2030-08-30 18:00")
        saved = self.by_id("submit")
        self.assertIsNone(saved.snoozed_until)
        self.assertIsNone(saved.dismissed_occurrence)

    def test_a_past_time_is_allowed(self):
        past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        self.assertEqual(self.add_at("Already late.", past), 0)
        self.assertEqual(len(self.stored()), 1)

    def test_a_past_time_is_pointed_out(self):
        past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        _, output, _ = self.output_of(["reminder", "add", "Already late.", "--at", past])
        self.assertIn("already passed", output)

    def test_a_past_reminder_is_immediately_due(self):
        past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        self.add_at("Already late.", past)
        _, output, _ = self.output_of(["reminder", "list"])
        self.assertIn("overdue", output)

    def test_unicode_text_is_preserved(self):
        text = "書くことは考えること — café ☕"
        self.assertEqual(self.add_at(text, "2030-08-30 18:00"), 0)
        self.assertEqual(self.stored()[0].text, text)

    def test_unicode_text_gets_a_digest_id(self):
        self.add_at("日本語のことば", "2030-08-30 18:00")
        self.assertTrue(self.ids()[0].startswith("reminder-"))

    def test_unicode_survives_on_disk_unescaped(self):
        self.add_at("café ☕", "2030-08-30 18:00")
        self.assertIn("café ☕", self.reminders_path.read_text(encoding="utf-8"))

    def test_text_is_trimmed(self):
        self.add_at("   Padded.   ", "2030-08-30 18:00")
        self.assertEqual(self.stored()[0].text, "Padded.")

    def test_blank_text_is_rejected(self):
        self.assertEqual(self.add_at("   ", "2030-08-30 18:00"), 1)
        self.assertFalse(self.reminders_path.exists())

    def test_a_malformed_time_is_rejected_before_anything_is_written(self):
        self.assertEqual(self.add_at("Submit.", "tomorrow"), 1)
        self.assertFalse(self.reminders_path.exists())

    def test_appends_to_the_end(self):
        self.seed("First.", "Second.", "Third.")
        self.assertEqual(self.ids(), ["first", "second", "third"])

    def test_the_created_id_is_reported(self):
        _, output, _ = self.output_of(
            ["reminder", "add", "Submit the report.", "--at", "2030-08-30 18:00"]
        )
        self.assertIn("submit-the-report", output)
        self.assertIn(str(self.reminders_path), output)

    def test_writes_to_the_configured_path(self):
        elsewhere = self.xdg / "elsewhere" / "mine.json"
        self.write_config(f'[reminders]\nfile = "{elsewhere}"\n')
        self.add_at("Somewhere else.", "2030-08-30 18:00")
        self.assertTrue(elsewhere.is_file())
        self.assertFalse(self.reminders_path.exists())

    def test_a_quote_only_config_still_uses_the_default_path(self):
        self.write_config('[quotes]\nfile = "quotes.json"\n')
        self.add_at("Submit.", "2030-08-30 18:00")
        self.assertTrue(self.reminders_path.is_file())

    def test_malformed_config_blocks_the_write(self):
        self.write_config("[reminders]\nfile = 7\n")
        self.assertEqual(self.add_at("Should not be written.", "2030-08-30 18:00"), 1)
        self.assertFalse(self.reminders_path.exists())

    def test_a_corrupt_collection_is_not_overwritten(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.reminders_path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.add_at("New one.", "2030-08-30 18:00"), 1)
        self.assertEqual(
            self.reminders_path.read_text(encoding="utf-8"), "{not json"
        )

    def test_adding_does_not_touch_the_quote_file(self):
        self.add_at("Submit.", "2030-08-30 18:00")
        self.assertFalse((self.config_dir / "quotes.json").exists())


# -- list -------------------------------------------------------------------


class ListTests(ReminderCommandTestCase):
    def test_missing_file_is_a_successful_empty_result(self):
        status, output, _ = self.output_of(["reminder", "list"])
        self.assertEqual(status, 0)
        self.assertIn("No reminders yet.", output)

    def test_list_does_not_create_anything(self):
        self.output_of(["reminder", "list"])
        self.assertFalse(self.reminders_path.exists())
        self.assertFalse(self.config_dir.exists())

    def test_names_the_file_being_managed(self):
        self.seed("A reminder.")
        _, output, _ = self.output_of(["reminder", "list"])
        self.assertIn(str(self.reminders_path), output)

    def test_names_the_file_even_when_it_does_not_exist(self):
        _, output, _ = self.output_of(["reminder", "list"])
        self.assertIn(str(self.reminders_path), output)

    def test_an_empty_collection_lists_cleanly(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        write_reminders(self.reminders_path, [])
        status, output, _ = self.output_of(["reminder", "list"])
        self.assertEqual(status, 0)
        self.assertIn("No reminders yet.", output)

    def test_shows_id_text_and_time(self):
        self.add_at("Submit the report.", "2030-08-30 18:00")
        _, output, _ = self.output_of(["reminder", "list"])
        self.assertIn("submit-the-report", output)
        self.assertIn("Submit the report.", output)
        self.assertIn("2030-08-30 18:00", output)

    def test_shows_recurrence(self):
        self.add_at("Review my week.", "2030-08-31 09:00", "--repeat", "weekly")
        _, output, _ = self.output_of(["reminder", "list"])
        self.assertIn("Weekly", output)

    def test_upcoming_status(self):
        self.add_at("Later.", "2030-08-30 18:00")
        _, output, _ = self.output_of(["reminder", "list"])
        self.assertIn("Status: upcoming", output)

    def test_overdue_status(self):
        past = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
        self.add_at("Long gone.", past)
        _, output, _ = self.output_of(["reminder", "list"])
        self.assertIn("Status: overdue", output)

    def test_disabled_status(self):
        self.add_at("Paused.", "2030-08-30 18:00", "--disabled")
        _, output, _ = self.output_of(["reminder", "list"])
        self.assertIn("Status: disabled", output)

    def test_snoozed_status(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        write_reminders(
            self.reminders_path,
            [
                Reminder(
                    id="napping",
                    text="Napping.",
                    due_at=datetime.now() - timedelta(hours=1),
                    snoozed_until=datetime.now() + timedelta(hours=1),
                )
            ],
        )
        _, output, _ = self.output_of(["reminder", "list"])
        self.assertIn("Status: snoozed", output)
        self.assertIn("Snoozed until:", output)

    def test_dismissed_status(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        due_at = (datetime.now() - timedelta(hours=1)).replace(microsecond=0)
        write_reminders(
            self.reminders_path,
            [
                Reminder(
                    id="done",
                    text="Done.",
                    due_at=due_at,
                    dismissed_occurrence=due_at,
                )
            ],
        )
        _, output, _ = self.output_of(["reminder", "list"])
        self.assertIn("Status: dismissed", output)

    def test_a_due_reminder_is_marked(self):
        past = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
        self.add_at("Waiting.", past)
        _, output, _ = self.output_of(["reminder", "list"])
        self.assertIn(f"{cli.DUE_MARK} waiting", output)

    def test_a_recurring_reminder_is_marked(self):
        self.add_at("Every week.", "2030-08-31 09:00", "--repeat", "weekly")
        _, output, _ = self.output_of(["reminder", "list"])
        self.assertIn(f"{cli.RECURRING_MARK} every-week", output)

    def test_a_recurring_reminder_shows_its_active_occurrence(self):
        # A daily reminder from years ago shows today, not its original date.
        started = datetime.now().replace(microsecond=0) - timedelta(days=400)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        write_reminders(
            self.reminders_path,
            [Reminder(id="daily", text="Daily.", due_at=started, recurrence=DAILY)],
        )
        _, output, _ = self.output_of(["reminder", "list"])
        self.assertIn(datetime.now().strftime("%Y-%m-%d"), output)
        self.assertNotIn(started.strftime("%Y-%m-%d "), output)

    def test_counts_are_summarised(self):
        self.add_at("On.", "2030-08-30 18:00")
        self.add_at("Off.", "2030-08-31 18:00", "--disabled")
        _, output, _ = self.output_of(["reminder", "list"])
        self.assertIn("2 reminders (1 enabled, 1 disabled, 0 due)", output)

    def test_a_single_reminder_is_not_pluralised(self):
        self.seed("Only one.")
        _, output, _ = self.output_of(["reminder", "list"])
        self.assertIn("1 reminder (", output)

    def test_list_does_not_modify_an_existing_file(self):
        self.seed("A reminder.")
        before = self.reminders_path.read_text(encoding="utf-8")
        mtime = os.stat(self.reminders_path).st_mtime_ns
        self.output_of(["reminder", "list"])
        self.assertEqual(self.reminders_path.read_text(encoding="utf-8"), before)
        self.assertEqual(os.stat(self.reminders_path).st_mtime_ns, mtime)

    def test_malformed_data_is_fatal_and_reported(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.reminders_path.write_text("{not json", encoding="utf-8")
        status, _, errors = self.output_of(["reminder", "list"])
        self.assertEqual(status, 1)
        self.assertIn("not valid JSON", errors)
        self.assertNotIn("Traceback", errors)

    def test_a_malformed_config_is_fatal(self):
        self.write_config("[reminders]\nfile = 7\n")
        status, output, errors = self.output_of(["reminder", "list"])
        self.assertEqual(status, 1)
        self.assertIn("rem-bubbles:", errors)
        self.assertEqual(output, "")


# -- remove -----------------------------------------------------------------


class RemoveTests(ReminderCommandTestCase):
    def setUp(self):
        super().setUp()
        self.seed("First.", "Second.", "Third.")

    def test_removes_by_exact_id(self):
        self.assertEqual(self.run_cli(["reminder", "remove", "second"]), 0)
        self.assertEqual(self.ids(), ["first", "third"])

    def test_unknown_id_fails_and_changes_nothing(self):
        before = self.reminders_path.read_text(encoding="utf-8")
        self.assertEqual(self.run_cli(["reminder", "remove", "nope"]), 1)
        self.assertEqual(self.reminders_path.read_text(encoding="utf-8"), before)

    def test_the_unknown_id_message_suggests_listing(self):
        _, _, errors = self.output_of(["reminder", "remove", "nope"])
        self.assertIn("reminder list", errors)

    def test_partial_id_is_not_accepted(self):
        self.assertEqual(self.run_cli(["reminder", "remove", "sec"]), 1)
        self.assertEqual(len(self.stored()), 3)

    def test_preserves_the_order_of_the_rest(self):
        self.run_cli(["reminder", "remove", "first"])
        self.assertEqual(self.ids(), ["second", "third"])

    def test_preserves_other_reminder_data(self):
        self.add_at("Kept.", "2030-06-01 09:00", "--repeat", "daily")
        self.add_at("Hidden.", "2030-06-02 09:00", "--disabled")
        self.run_cli(["reminder", "remove", "first"])
        self.assertEqual(self.by_id("kept").recurrence, DAILY)
        self.assertFalse(self.by_id("hidden").enabled)

    def test_the_last_reminder_can_be_removed(self):
        for name in ("first", "second", "third"):
            self.assertEqual(self.run_cli(["reminder", "remove", name]), 0)
        self.assertEqual(self.stored(), [])

    def test_an_emptied_collection_is_a_valid_json_array(self):
        for name in ("first", "second", "third"):
            self.run_cli(["reminder", "remove", name])
        self.assertEqual(
            json.loads(self.reminders_path.read_text(encoding="utf-8")), []
        )

    def test_an_emptied_collection_still_lists(self):
        for name in ("first", "second", "third"):
            self.run_cli(["reminder", "remove", name])
        status, output, _ = self.output_of(["reminder", "list"])
        self.assertEqual(status, 0)
        self.assertIn("No reminders yet.", output)

    def test_missing_file_fails_clearly(self):
        self.reminders_path.unlink()
        status, _, errors = self.output_of(["reminder", "remove", "first"])
        self.assertEqual(status, 1)
        self.assertIn("No personal reminder file yet", errors)


# -- enable / disable -------------------------------------------------------


class EnableDisableTests(ReminderCommandTestCase):
    def setUp(self):
        super().setUp()
        self.seed("First.", "Second.")

    def test_disable(self):
        self.assertEqual(self.run_cli(["reminder", "disable", "second"]), 0)
        self.assertFalse(self.by_id("second").enabled)
        self.assertTrue(self.by_id("first").enabled)

    def test_enable(self):
        self.run_cli(["reminder", "disable", "second"])
        self.assertEqual(self.run_cli(["reminder", "enable", "second"]), 0)
        self.assertTrue(self.by_id("second").enabled)

    def test_the_last_enabled_reminder_can_be_disabled(self):
        # No "one must remain enabled" rule: a fully paused collection is valid.
        self.run_cli(["reminder", "disable", "second"])
        self.assertEqual(self.run_cli(["reminder", "disable", "first"]), 0)
        self.assertEqual([r.enabled for r in self.stored()], [False, False])

    def test_enable_already_enabled_is_a_success(self):
        self.assertEqual(self.run_cli(["reminder", "enable", "first"]), 0)
        self.assertTrue(self.by_id("first").enabled)

    def test_enable_already_enabled_does_not_rewrite_the_file(self):
        mtime = os.stat(self.reminders_path).st_mtime_ns
        self.run_cli(["reminder", "enable", "first"])
        self.assertEqual(os.stat(self.reminders_path).st_mtime_ns, mtime)

    def test_disable_already_disabled_is_a_success(self):
        self.run_cli(["reminder", "disable", "second"])
        self.assertEqual(self.run_cli(["reminder", "disable", "second"]), 0)
        self.assertFalse(self.by_id("second").enabled)

    def test_disable_already_disabled_does_not_rewrite_the_file(self):
        self.run_cli(["reminder", "disable", "second"])
        mtime = os.stat(self.reminders_path).st_mtime_ns
        self.run_cli(["reminder", "disable", "second"])
        self.assertEqual(os.stat(self.reminders_path).st_mtime_ns, mtime)

    def test_unknown_id_fails(self):
        self.assertEqual(self.run_cli(["reminder", "enable", "nope"]), 1)
        self.assertEqual(self.run_cli(["reminder", "disable", "nope"]), 1)

    def test_order_is_preserved(self):
        self.seed("Third.")
        self.run_cli(["reminder", "disable", "second"])
        self.assertEqual(self.ids(), ["first", "second", "third"])

    def test_text_and_schedule_are_preserved(self):
        self.add_at("Recurring.", "2030-06-01 09:00", "--repeat", "weekly")
        self.run_cli(["reminder", "disable", "recurring"])
        saved = self.by_id("recurring")
        self.assertEqual(saved.text, "Recurring.")
        self.assertEqual(saved.recurrence, WEEKLY)
        self.assertEqual(saved.due_at, datetime(2030, 6, 1, 9, 0))

    def test_disabling_preserves_snooze_and_dismissal(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        write_reminders(
            self.reminders_path,
            [
                Reminder(
                    id="stateful",
                    text="Stateful.",
                    due_at=datetime(2030, 6, 1, 9, 0),
                    recurrence=DAILY,
                    snoozed_until=datetime(2030, 6, 1, 9, 10),
                    dismissed_occurrence=datetime(2030, 5, 31, 9, 0),
                )
            ],
        )
        self.assertEqual(self.run_cli(["reminder", "disable", "stateful"]), 0)
        saved = self.by_id("stateful")
        self.assertEqual(saved.snoozed_until, datetime(2030, 6, 1, 9, 10))
        self.assertEqual(saved.dismissed_occurrence, datetime(2030, 5, 31, 9, 0))

    def test_enabling_preserves_snooze_and_dismissal(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        write_reminders(
            self.reminders_path,
            [
                Reminder(
                    id="stateful",
                    text="Stateful.",
                    due_at=datetime(2030, 6, 1, 9, 0),
                    recurrence=DAILY,
                    enabled=False,
                    snoozed_until=datetime(2030, 6, 1, 9, 10),
                    dismissed_occurrence=datetime(2030, 5, 31, 9, 0),
                )
            ],
        )
        self.assertEqual(self.run_cli(["reminder", "enable", "stateful"]), 0)
        saved = self.by_id("stateful")
        self.assertTrue(saved.enabled)
        self.assertEqual(saved.snoozed_until, datetime(2030, 6, 1, 9, 10))
        self.assertEqual(saved.dismissed_occurrence, datetime(2030, 5, 31, 9, 0))

    def test_unaffected_entries_keep_their_state(self):
        self.add_at("Hidden.", "2030-07-01 09:00", "--disabled")
        self.run_cli(["reminder", "disable", "second"])
        self.assertFalse(self.by_id("hidden").enabled)
        self.assertTrue(self.by_id("first").enabled)

    def test_missing_file_fails_clearly(self):
        self.reminders_path.unlink()
        for name in ("enable", "disable"):
            self.assertEqual(self.run_cli(["reminder", name, "first"]), 1)


# -- the two collections stay apart -----------------------------------------


class IndependenceTests(ReminderCommandTestCase):
    def test_quotes_and_reminders_use_separate_files(self):
        self.run_cli(["quote", "add", "A quote."])
        self.add_at("A reminder.", "2030-08-30 18:00")
        quotes = load_quotes(self.config_dir / "quotes.json")
        self.assertEqual([quote.id for quote in quotes], ["a-quote"])
        self.assertEqual(self.ids(), ["a-reminder"])

    def test_removing_a_reminder_leaves_quotes_alone(self):
        self.run_cli(["quote", "add", "A quote."])
        self.add_at("A reminder.", "2030-08-30 18:00")
        before = (self.config_dir / "quotes.json").read_text(encoding="utf-8")
        self.run_cli(["reminder", "remove", "a-reminder"])
        self.assertEqual(
            (self.config_dir / "quotes.json").read_text(encoding="utf-8"), before
        )

    def test_an_id_may_be_shared_between_the_two_collections(self):
        self.run_cli(["quote", "add", "Shared name."])
        self.assertEqual(self.add_at("Shared name.", "2030-08-30 18:00"), 0)
        self.assertEqual(self.ids(), ["shared-name"])


# -- the real config is never touched ---------------------------------------


class IsolationTests(ReminderCommandTestCase):
    def test_the_temporary_config_home_is_in_use(self):
        self.add_at("Isolated.", "2030-08-30 18:00")
        self.assertTrue(str(self.reminders_path).startswith(str(self.xdg)))
        self.assertNotIn(str(Path.home() / ".config" / "rem-bubbles"),
                         str(self.reminders_path))


if __name__ == "__main__":
    unittest.main()
