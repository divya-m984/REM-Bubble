"""Tests for reminder parsing, validation, serialisation and atomic writes.

No GTK, no display server, and no reliance on the current time: every test that
needs a "now" passes one in. The real ``~/.config/rem-bubbles`` is never read or
written — everything here uses a temporary directory or an in-memory string.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from rem_bubbles.reminder_store import (
    DAILY,
    NONE,
    WEEKLY,
    Reminder,
    ReminderStore,
    ReminderStoreError,
    decode_reminders,
    format_datetime,
    load_reminders,
    parse_local_datetime,
    parse_reminders,
    reminder_to_dict,
    reminders_to_json,
    write_reminders,
)


def at(text: str) -> datetime:
    """Shorthand for a local wall-clock datetime in a test."""
    return parse_local_datetime(text)


def one(**overrides) -> dict:
    """A minimal valid reminder object, with fields overridden or removed.

    Passing ``field=None`` for a required field removes it, which is how the
    "missing X" cases are written without repeating the whole object.
    """
    entry = {"id": "r", "text": "Do it.", "due_at": "2026-08-30T18:00:00"}
    for key, value in overrides.items():
        if value is _REMOVED:
            entry.pop(key, None)
        else:
            entry[key] = value
    return entry


class _Removed:
    pass


_REMOVED = _Removed()


# -- datetime parsing -------------------------------------------------------


class DatetimeParsingTests(unittest.TestCase):
    def test_space_separator(self):
        self.assertEqual(
            parse_local_datetime("2026-08-30 18:00"), datetime(2026, 8, 30, 18, 0)
        )

    def test_t_separator(self):
        self.assertEqual(
            parse_local_datetime("2026-08-30T18:00"), datetime(2026, 8, 30, 18, 0)
        )

    def test_seconds_are_accepted(self):
        self.assertEqual(
            parse_local_datetime("2026-08-30T18:00:30"),
            datetime(2026, 8, 30, 18, 0, 30),
        )

    def test_surrounding_whitespace_is_ignored(self):
        self.assertEqual(
            parse_local_datetime("  2026-08-30 18:00  "), datetime(2026, 8, 30, 18, 0)
        )

    def test_the_result_is_naive(self):
        self.assertIsNone(parse_local_datetime("2026-08-30T18:00").tzinfo)

    def test_a_bare_date_is_refused(self):
        # Assuming midnight would be guessing at a time nobody wrote down.
        with self.assertRaises(ValueError):
            parse_local_datetime("2026-08-30")

    def test_a_bare_time_is_refused(self):
        with self.assertRaises(ValueError):
            parse_local_datetime("18:00")

    def test_an_empty_value_is_refused(self):
        with self.assertRaises(ValueError):
            parse_local_datetime("   ")

    def test_natural_language_is_refused(self):
        for text in ("tomorrow", "next Tuesday", "in an hour", "tonight"):
            with self.assertRaises(ValueError):
                parse_local_datetime(text)

    def test_a_utc_designator_is_refused_with_a_reason(self):
        with self.assertRaises(ValueError) as caught:
            parse_local_datetime("2026-08-30T18:00:00Z")
        self.assertIn("timezone", str(caught.exception))

    def test_a_numeric_offset_is_refused_with_a_reason(self):
        for text in ("2026-08-30T18:00:00+02:00", "2026-08-30T18:00:00-0500"):
            with self.assertRaises(ValueError) as caught:
                parse_local_datetime(text)
            self.assertIn("timezone", str(caught.exception))

    def test_an_impossible_date_is_refused(self):
        with self.assertRaises(ValueError):
            parse_local_datetime("2026-02-30T18:00")

    def test_an_impossible_time_is_refused(self):
        with self.assertRaises(ValueError):
            parse_local_datetime("2026-08-30T25:00")

    def test_formatting_is_the_canonical_form(self):
        self.assertEqual(
            format_datetime(datetime(2026, 8, 30, 18, 0)), "2026-08-30T18:00:00"
        )

    def test_formatting_drops_microseconds(self):
        self.assertEqual(
            format_datetime(datetime(2026, 8, 30, 18, 0, 1, 500000)),
            "2026-08-30T18:00:01",
        )

    def test_format_and_parse_round_trip(self):
        value = datetime(2026, 12, 1, 7, 5, 9)
        self.assertEqual(parse_local_datetime(format_datetime(value)), value)


# -- valid documents --------------------------------------------------------


class ValidReminderTests(unittest.TestCase):
    def test_an_empty_array_is_valid(self):
        # Unlike quotes, having none is a completely normal state.
        self.assertEqual(decode_reminders("[]"), [])

    def test_a_one_time_reminder(self):
        (reminder,) = parse_reminders([one()])
        self.assertEqual(reminder.id, "r")
        self.assertEqual(reminder.text, "Do it.")
        self.assertEqual(reminder.due_at, datetime(2026, 8, 30, 18, 0))

    def test_a_daily_reminder(self):
        (reminder,) = parse_reminders([one(recurrence="daily")])
        self.assertEqual(reminder.recurrence, DAILY)

    def test_a_weekly_reminder(self):
        (reminder,) = parse_reminders([one(recurrence="weekly")])
        self.assertEqual(reminder.recurrence, WEEKLY)

    def test_recurrence_defaults_to_none(self):
        (reminder,) = parse_reminders([one()])
        self.assertEqual(reminder.recurrence, NONE)

    def test_an_explicit_null_recurrence_means_none(self):
        (reminder,) = parse_reminders([one(recurrence=None)])
        self.assertEqual(reminder.recurrence, NONE)

    def test_recurrence_is_case_insensitive(self):
        (reminder,) = parse_reminders([one(recurrence="Daily")])
        self.assertEqual(reminder.recurrence, DAILY)

    def test_enabled_defaults_to_true(self):
        (reminder,) = parse_reminders([one()])
        self.assertTrue(reminder.enabled)

    def test_enabled_can_be_false(self):
        (reminder,) = parse_reminders([one(enabled=False)])
        self.assertFalse(reminder.enabled)

    def test_state_fields_default_to_none(self):
        (reminder,) = parse_reminders([one()])
        self.assertIsNone(reminder.snoozed_until)
        self.assertIsNone(reminder.dismissed_occurrence)

    def test_explicit_null_state_fields(self):
        (reminder,) = parse_reminders(
            [one(snoozed_until=None, dismissed_occurrence=None)]
        )
        self.assertIsNone(reminder.snoozed_until)
        self.assertIsNone(reminder.dismissed_occurrence)

    def test_state_fields_are_parsed(self):
        (reminder,) = parse_reminders(
            [
                one(
                    snoozed_until="2026-08-30T18:10:00",
                    dismissed_occurrence="2026-08-29T18:00:00",
                )
            ]
        )
        self.assertEqual(reminder.snoozed_until, datetime(2026, 8, 30, 18, 10))
        self.assertEqual(reminder.dismissed_occurrence, datetime(2026, 8, 29, 18, 0))

    def test_id_and_text_are_trimmed(self):
        (reminder,) = parse_reminders([one(id="  r  ", text="  Do it.  ")])
        self.assertEqual((reminder.id, reminder.text), ("r", "Do it."))

    def test_several_reminders_keep_file_order(self):
        entries = [one(id="a"), one(id="b"), one(id="c")]
        self.assertEqual([r.id for r in parse_reminders(entries)], ["a", "b", "c"])

    def test_unicode_text_is_preserved(self):
        text = "書くこと — café ☕"
        (reminder,) = parse_reminders([one(text=text)])
        self.assertEqual(reminder.text, text)

    def test_a_disabled_reminder_is_still_parsed(self):
        # Nothing is dropped; disabled simply means "not scheduled".
        (reminder,) = parse_reminders([one(enabled=False)])
        self.assertEqual(reminder.id, "r")


# -- invalid documents ------------------------------------------------------


class InvalidReminderTests(unittest.TestCase):
    def assertRejects(self, entry, needle):
        with self.assertRaises(ReminderStoreError) as caught:
            parse_reminders([entry])
        self.assertIn(needle, str(caught.exception))

    def test_invalid_json(self):
        with self.assertRaises(ReminderStoreError) as caught:
            decode_reminders("{not json")
        self.assertIn("not valid JSON", str(caught.exception))

    def test_the_json_error_names_a_position(self):
        with self.assertRaises(ReminderStoreError) as caught:
            decode_reminders("[{]")
        self.assertIn("line", str(caught.exception))

    def test_a_non_array_root(self):
        for text in ("{}", '"a string"', "42", "null"):
            with self.assertRaises(ReminderStoreError) as caught:
                decode_reminders(text)
            self.assertIn("must contain a JSON array", str(caught.exception))

    def test_the_root_error_names_the_type_found(self):
        with self.assertRaises(ReminderStoreError) as caught:
            decode_reminders("{}")
        self.assertIn("dict", str(caught.exception))

    def test_a_non_object_entry(self):
        for entry in ("a string", 42, None, ["nested"]):
            with self.assertRaises(ReminderStoreError) as caught:
                parse_reminders([entry])
            self.assertIn("not a JSON object", str(caught.exception))

    def test_missing_id(self):
        self.assertRejects(one(id=_REMOVED), 'missing the required "id"')

    def test_blank_id(self):
        self.assertRejects(one(id="   "), 'empty "id"')

    def test_non_string_id(self):
        self.assertRejects(one(id=7), 'non-string "id"')

    def test_null_id(self):
        self.assertRejects(one(id=None), 'non-string "id"')

    def test_duplicate_id(self):
        with self.assertRaises(ReminderStoreError) as caught:
            parse_reminders([one(id="same"), one(id="same")])
        self.assertIn("Duplicate reminder id", str(caught.exception))

    def test_duplicate_id_after_trimming(self):
        with self.assertRaises(ReminderStoreError):
            parse_reminders([one(id="same"), one(id="  same  ")])

    def test_missing_text(self):
        self.assertRejects(one(text=_REMOVED), 'missing the required "text"')

    def test_blank_text(self):
        self.assertRejects(one(text="   "), 'empty "text"')

    def test_non_string_text(self):
        self.assertRejects(one(text=7), 'non-string "text"')

    def test_missing_due_at(self):
        self.assertRejects(one(due_at=_REMOVED), 'missing the required "due_at"')

    def test_null_due_at(self):
        self.assertRejects(one(due_at=None), 'missing the required "due_at"')

    def test_non_string_due_at(self):
        self.assertRejects(one(due_at=20260830), 'non-string "due_at"')

    def test_malformed_due_at(self):
        self.assertRejects(one(due_at="not a date"), 'invalid "due_at"')

    def test_date_only_due_at(self):
        self.assertRejects(one(due_at="2026-08-30"), 'invalid "due_at"')

    def test_timezone_aware_due_at_is_rejected(self):
        self.assertRejects(one(due_at="2026-08-30T18:00:00+02:00"), "timezone")

    def test_utc_due_at_is_rejected(self):
        self.assertRejects(one(due_at="2026-08-30T18:00:00Z"), "timezone")

    def test_invalid_recurrence(self):
        self.assertRejects(one(recurrence="monthly"), "unsupported")

    def test_the_recurrence_error_lists_what_is_supported(self):
        with self.assertRaises(ReminderStoreError) as caught:
            parse_reminders([one(recurrence="yearly")])
        message = str(caught.exception)
        for value in ("none", "daily", "weekly"):
            self.assertIn(value, message)

    def test_cron_syntax_is_not_recurrence(self):
        self.assertRejects(one(recurrence="0 8 * * *"), "unsupported")

    def test_non_string_recurrence(self):
        self.assertRejects(one(recurrence=7), 'non-string "recurrence"')

    def test_invalid_enabled(self):
        for value in ("true", 1, 0, [], {}):
            self.assertRejects(one(enabled=value), 'invalid "enabled"')

    def test_invalid_snoozed_until(self):
        self.assertRejects(one(snoozed_until="soon"), 'invalid "snoozed_until"')

    def test_non_string_snoozed_until(self):
        self.assertRejects(one(snoozed_until=17), 'non-string "snoozed_until"')

    def test_timezone_aware_snoozed_until(self):
        self.assertRejects(one(snoozed_until="2026-08-30T18:10:00Z"), "timezone")

    def test_invalid_dismissed_occurrence(self):
        self.assertRejects(
            one(dismissed_occurrence="yesterday"), 'invalid "dismissed_occurrence"'
        )

    def test_non_string_dismissed_occurrence(self):
        self.assertRejects(
            one(dismissed_occurrence=True), 'non-string "dismissed_occurrence"'
        )

    def test_nothing_is_silently_dropped(self):
        # One bad entry stops the whole file rather than quietly vanishing —
        # a reminder that fails to load is a reminder that never fires.
        with self.assertRaises(ReminderStoreError):
            parse_reminders([one(id="good"), one(id="bad", text="")])

    def test_the_message_names_the_reminder_once_the_id_is_known(self):
        with self.assertRaises(ReminderStoreError) as caught:
            parse_reminders([one(id="pay-rent", text="")])
        self.assertIn("pay-rent", str(caught.exception))

    def test_the_message_names_the_source_when_given(self):
        with self.assertRaises(ReminderStoreError) as caught:
            parse_reminders([one(text="")], source="/tmp/reminders.json")
        self.assertIn("/tmp/reminders.json", str(caught.exception))


# -- files ------------------------------------------------------------------


class LoadFileTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.directory = Path(self._temp.name)
        self.path = self.directory / "reminders.json"

    def test_missing_file_is_reported_by_name(self):
        with self.assertRaises(ReminderStoreError) as caught:
            load_reminders(self.path)
        self.assertIn("not found", str(caught.exception))
        self.assertIn(str(self.path), str(caught.exception))

    def test_a_directory_in_place_of_the_file(self):
        self.path.mkdir()
        with self.assertRaises(ReminderStoreError):
            load_reminders(self.path)

    def test_an_empty_array_file_loads(self):
        self.path.write_text("[]\n", encoding="utf-8")
        self.assertEqual(load_reminders(self.path), [])

    def test_a_valid_file_loads(self):
        self.path.write_text(json.dumps([one()]), encoding="utf-8")
        self.assertEqual([r.id for r in load_reminders(self.path)], ["r"])

    def test_a_store_can_be_built_from_a_file(self):
        self.path.write_text(json.dumps([one()]), encoding="utf-8")
        store = ReminderStore.from_file(self.path)
        self.assertEqual(len(store), 1)
        self.assertEqual(store.path, self.path)


# -- serialisation ----------------------------------------------------------


class SerialisationTests(unittest.TestCase):
    def test_every_field_is_written_explicitly(self):
        payload = reminder_to_dict(
            Reminder(id="r", text="Do it.", due_at=datetime(2026, 8, 30, 18, 0))
        )
        self.assertEqual(
            payload,
            {
                "id": "r",
                "text": "Do it.",
                "due_at": "2026-08-30T18:00:00",
                "recurrence": "none",
                "enabled": True,
                "snoozed_until": None,
                "dismissed_occurrence": None,
            },
        )

    def test_round_trips_every_field(self):
        original = [
            Reminder(id="a", text="Alpha.", due_at=at("2026-08-30T18:00")),
            Reminder(
                id="b",
                text="Beta.",
                due_at=at("2026-08-31T09:00"),
                recurrence=WEEKLY,
                enabled=False,
                snoozed_until=at("2026-08-31T09:10"),
                dismissed_occurrence=at("2026-08-24T09:00"),
            ),
        ]
        self.assertEqual(decode_reminders(reminders_to_json(original)), original)

    def test_an_empty_collection_serialises(self):
        self.assertEqual(reminders_to_json([]), "[]\n")

    def test_output_is_pretty_and_newline_terminated(self):
        text = reminders_to_json([Reminder(id="a", text="A.", due_at=at("2026-08-30T18:00"))])
        self.assertTrue(text.endswith("\n"))
        self.assertIn("\n  {\n", text)

    def test_output_is_valid_json(self):
        payload = json.loads(
            reminders_to_json([Reminder(id="a", text="A.", due_at=at("2026-08-30T18:00"))])
        )
        self.assertEqual(payload[0]["due_at"], "2026-08-30T18:00:00")

    def test_unicode_is_not_escaped(self):
        text = reminders_to_json(
            [Reminder(id="u", text="café — 日本語 ☕", due_at=at("2026-08-30T18:00"))]
        )
        self.assertIn("café — 日本語 ☕", text)
        self.assertNotIn("\\u", text)


# -- atomic writes ----------------------------------------------------------


class AtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.directory = Path(self._temp.name)
        self.path = self.directory / "reminders.json"

    def some(self, reminder_id="a", text="Alpha."):
        return Reminder(id=reminder_id, text=text, due_at=at("2026-08-30T18:00"))

    def temporaries(self):
        return [p for p in self.directory.iterdir() if p.name != self.path.name]

    def test_successful_write_replaces_the_destination(self):
        write_reminders(self.path, [self.some("a")])
        write_reminders(self.path, [self.some("b")])
        self.assertEqual([r.id for r in load_reminders(self.path)], ["b"])

    def test_no_temporary_file_survives_a_successful_write(self):
        write_reminders(self.path, [self.some()])
        self.assertEqual(self.temporaries(), [])

    def test_creates_missing_parent_directories(self):
        nested = self.directory / "deep" / "deeper" / "reminders.json"
        write_reminders(nested, [self.some()])
        self.assertTrue(nested.is_file())

    def test_new_files_are_user_private(self):
        write_reminders(self.path, [self.some()])
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_existing_permissions_are_preserved(self):
        write_reminders(self.path, [self.some("a")])
        os.chmod(self.path, 0o644)
        write_reminders(self.path, [self.some("b")])
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o644)

    def test_an_empty_collection_can_be_persisted(self):
        write_reminders(self.path, [self.some()])
        write_reminders(self.path, [])
        self.assertEqual(load_reminders(self.path), [])

    def test_order_is_preserved(self):
        write_reminders(
            self.path, [self.some("c"), self.some("a"), self.some("b")]
        )
        self.assertEqual([r.id for r in load_reminders(self.path)], ["c", "a", "b"])

    def test_a_failed_replace_leaves_the_original_intact(self):
        write_reminders(self.path, [self.some("a")])
        before = self.path.read_text(encoding="utf-8")

        with mock.patch("os.replace", side_effect=OSError(5, "Input/output error")):
            with self.assertRaises(OSError):
                write_reminders(self.path, [self.some("b")])

        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_a_failed_replace_leaves_no_temporary_file(self):
        write_reminders(self.path, [self.some("a")])
        with mock.patch("os.replace", side_effect=OSError(5, "Input/output error")):
            with self.assertRaises(OSError):
                write_reminders(self.path, [self.some("b")])
        self.assertEqual(self.temporaries(), [])

    def test_invalid_data_never_reaches_the_destination(self):
        write_reminders(self.path, [self.some("a")])
        duplicated = [self.some("a"), self.some("a", "Again.")]

        with self.assertRaises(ReminderStoreError):
            write_reminders(self.path, duplicated)

        self.assertEqual([r.id for r in load_reminders(self.path)], ["a"])
        self.assertEqual(self.temporaries(), [])

    def test_unicode_survives_a_round_trip_on_disk(self):
        text = "書くこと — café ☕"
        write_reminders(self.path, [self.some(text=text)])
        self.assertIn(text, self.path.read_text(encoding="utf-8"))
        self.assertEqual(load_reminders(self.path)[0].text, text)


if __name__ == "__main__":
    unittest.main()
