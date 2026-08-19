"""Unit tests for the quote engine.

Nothing here touches GTK, Wayland or a display server — ``quote_store`` and
``config`` are deliberately import-clean, so these run anywhere.
"""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from rem_bubbles.config import quote_file_candidates
from rem_bubbles.quote_store import (
    Quote,
    QuoteStore,
    QuoteStoreError,
    parse_quotes,
)

VALID = [
    {"id": "a", "text": "Alpha.", "author": None, "enabled": True},
    {"id": "b", "text": "Beta.", "author": "Someone", "enabled": True},
    {"id": "c", "text": "Gamma."},
    {"id": "d", "text": "Delta.", "enabled": False},
    {"id": "e", "text": "Epsilon.", "author": "   "},
]


def store_from(entries) -> QuoteStore:
    return QuoteStore.from_json(json.dumps(entries), source="test.json")


class TempQuoteFile:
    """Context manager writing ``content`` to a temporary quotes file."""

    def __init__(self, content: str) -> None:
        self._content = content
        self._dir: tempfile.TemporaryDirectory | None = None

    def __enter__(self) -> Path:
        self._dir = tempfile.TemporaryDirectory()
        path = Path(self._dir.name) / "quotes.json"
        path.write_text(self._content, encoding="utf-8")
        return path

    def __exit__(self, *exc_info) -> None:
        assert self._dir is not None
        self._dir.cleanup()


# -- parsing ----------------------------------------------------------------


class ParsingTests(unittest.TestCase):
    def test_parses_a_valid_collection(self):
        quotes = parse_quotes(VALID, "test.json")
        self.assertEqual(len(quotes), 5)
        self.assertIsInstance(quotes[0], Quote)
        self.assertEqual(quotes[0].id, "a")
        self.assertEqual(quotes[0].text, "Alpha.")

    def test_optional_fields_may_be_omitted(self):
        quote = parse_quotes([{"id": "c", "text": "Gamma."}])[0]
        self.assertIsNone(quote.author)
        self.assertTrue(quote.enabled)

    def test_enabled_defaults_to_true(self):
        self.assertTrue(parse_quotes([{"id": "x", "text": "X."}])[0].enabled)

    def test_enabled_false_is_respected(self):
        self.assertFalse(
            parse_quotes([{"id": "x", "text": "X.", "enabled": False}])[0].enabled
        )

    def test_author_null_becomes_none(self):
        self.assertIsNone(
            parse_quotes([{"id": "x", "text": "X.", "author": None}])[0].author
        )

    def test_blank_author_is_treated_as_absent(self):
        self.assertIsNone(
            parse_quotes([{"id": "x", "text": "X.", "author": "   "}])[0].author
        )

    def test_text_and_id_are_trimmed(self):
        quote = parse_quotes([{"id": "  x  ", "text": "  X.  ", "author": " Y "}])[0]
        self.assertEqual(quote.id, "x")
        self.assertEqual(quote.text, "X.")
        self.assertEqual(quote.author, "Y")

    def test_disabled_quotes_are_still_parsed(self):
        quotes = parse_quotes(VALID)
        self.assertEqual(sum(1 for q in quotes if not q.enabled), 1)

    def test_reads_a_file_from_disk(self):
        with TempQuoteFile(json.dumps(VALID)) as path:
            store = QuoteStore.from_file(path)
        self.assertEqual(len(store), 4)
        self.assertEqual(store.source, str(path))

    def test_bundled_example_file_is_valid(self):
        example = Path(__file__).resolve().parents[1] / "examples" / "quotes.json"
        store = QuoteStore.from_file(example)
        self.assertGreaterEqual(len(store), 4, "need several quotes to test navigation")


# -- validation -------------------------------------------------------------


class ValidationTests(unittest.TestCase):
    def assertRejects(self, entries, needle):
        with self.assertRaises(QuoteStoreError) as caught:
            store_from(entries)
        self.assertIn(needle, str(caught.exception))

    def test_missing_file(self):
        with self.assertRaises(QuoteStoreError) as caught:
            QuoteStore.from_file("/nonexistent/definitely/quotes.json")
        self.assertIn("not found", str(caught.exception))

    def test_invalid_json(self):
        with self.assertRaises(QuoteStoreError) as caught:
            QuoteStore.from_json("{not json", source="test.json")
        self.assertIn("not valid JSON", str(caught.exception))

    def test_root_is_not_a_list(self):
        with self.assertRaises(QuoteStoreError) as caught:
            QuoteStore.from_json('{"id": "a"}', source="test.json")
        self.assertIn("JSON array", str(caught.exception))

    def test_entry_is_not_an_object(self):
        self.assertRejects(["just a string"], "not a JSON object")

    def test_missing_id(self):
        self.assertRejects([{"text": "X."}], 'missing the required "id"')

    def test_blank_id(self):
        self.assertRejects([{"id": "   ", "text": "X."}], 'empty "id"')

    def test_non_string_id(self):
        self.assertRejects([{"id": 7, "text": "X."}], 'non-string "id"')

    def test_missing_text(self):
        self.assertRejects([{"id": "x"}], 'missing the required "text"')

    def test_blank_text(self):
        self.assertRejects([{"id": "x", "text": "   "}], 'empty "text"')

    def test_non_string_text(self):
        self.assertRejects([{"id": "x", "text": 7}], 'non-string "text"')

    def test_duplicate_ids(self):
        entries = [{"id": "x", "text": "One."}, {"id": "x", "text": "Two."}]
        self.assertRejects(entries, 'Duplicate quote id: "x"')

    def test_invalid_author_type(self):
        self.assertRejects([{"id": "x", "text": "X.", "author": 7}], 'invalid "author"')

    def test_invalid_enabled_value(self):
        self.assertRejects(
            [{"id": "x", "text": "X.", "enabled": "yes"}], 'invalid "enabled"'
        )

    def test_enabled_rejects_integers(self):
        self.assertRejects([{"id": "x", "text": "X.", "enabled": 1}], 'invalid "enabled"')

    def test_no_enabled_quotes(self):
        entries = [{"id": "x", "text": "X.", "enabled": False}]
        self.assertRejects(entries, "no enabled quotes")

    def test_empty_collection(self):
        self.assertRejects([], "no enabled quotes")

    def test_error_message_names_the_source(self):
        with self.assertRaises(QuoteStoreError) as caught:
            store_from([{"id": "x"}])
        self.assertIn("test.json", str(caught.exception))

    def test_malformed_entries_are_not_silently_dropped(self):
        entries = [{"id": "good", "text": "Fine."}, {"id": "bad"}]
        with self.assertRaises(QuoteStoreError):
            store_from(entries)


# -- selection --------------------------------------------------------------


class DailySelectionTests(unittest.TestCase):
    def setUp(self):
        self.store = store_from(VALID)

    def test_disabled_quotes_are_excluded_from_the_collection(self):
        self.assertEqual([q.id for q in self.store.quotes], ["a", "b", "c", "e"])
        self.assertEqual(len(self.store), 4)
        self.assertEqual(len(self.store.all_quotes), 5)

    def test_same_date_gives_the_same_quote(self):
        day = date(2026, 8, 19)
        first = self.store.daily_quote(day)
        for _ in range(20):
            self.assertEqual(store_from(VALID).daily_quote(day), first)

    def test_index_is_always_in_range(self):
        for ordinal in range(date(2026, 1, 1).toordinal(), date(2027, 1, 1).toordinal()):
            index = self.store.daily_index(date.fromordinal(ordinal))
            self.assertGreaterEqual(index, 0)
            self.assertLess(index, len(self.store))

    def test_selection_varies_across_dates(self):
        picked = {
            self.store.daily_quote(date.fromordinal(o)).id
            for o in range(date(2026, 1, 1).toordinal(), date(2026, 3, 1).toordinal())
        }
        self.assertGreater(len(picked), 1, "every day selected the same quote")

    def test_daily_quote_is_never_a_disabled_quote(self):
        for ordinal in range(date(2026, 1, 1).toordinal(), date(2026, 4, 1).toordinal()):
            self.assertTrue(self.store.daily_quote(date.fromordinal(ordinal)).enabled)

    def test_daily_quote_does_not_move_the_cursor(self):
        before = self.store.index
        self.store.daily_quote(date(2020, 1, 1))
        self.assertEqual(self.store.index, before)

    def test_cursor_starts_on_todays_quote(self):
        self.assertEqual(self.store.current, self.store.daily_quote())

    def test_defaults_to_today(self):
        self.assertEqual(self.store.daily_index(), self.store.daily_index(date.today()))

    def test_reordering_may_change_the_selection(self):
        # Documented Milestone 2 behaviour: no state is persisted to pin a quote
        # across edits, so this is allowed to differ. Only stability across
        # restarts of an *unchanged* collection is guaranteed.
        day = date(2026, 8, 19)
        reordered = store_from(list(reversed(VALID)))
        self.assertIn(reordered.daily_quote(day).id, {q.id for q in self.store.quotes})

    def test_single_quote_collection_selects_it(self):
        store = store_from([{"id": "only", "text": "Only."}])
        self.assertEqual(store.daily_quote(date(2026, 8, 19)).id, "only")

    def test_emergency_store_is_usable(self):
        store = QuoteStore.emergency()
        self.assertEqual(len(store), 1)
        self.assertEqual(store.current.text, "Keep making weird things.")


# -- navigation -------------------------------------------------------------


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.store = store_from(VALID)
        self.ids = [q.id for q in self.store.quotes]

    def test_next_advances_one(self):
        start = self.store.index
        self.assertEqual(self.store.next().id, self.ids[(start + 1) % len(self.ids)])

    def test_previous_steps_back_one(self):
        start = self.store.index
        self.assertEqual(self.store.previous().id, self.ids[(start - 1) % len(self.ids)])

    def test_next_wraps_from_last_to_first(self):
        while self.store.index != len(self.ids) - 1:
            self.store.next()
        self.assertEqual(self.store.next().id, self.ids[0])
        self.assertEqual(self.store.index, 0)

    def test_previous_wraps_from_first_to_last(self):
        while self.store.index != 0:
            self.store.next()
        self.assertEqual(self.store.previous().id, self.ids[-1])
        self.assertEqual(self.store.index, len(self.ids) - 1)

    def test_next_then_previous_returns_to_start(self):
        start = self.store.current
        self.store.next()
        self.assertEqual(self.store.previous(), start)

    def test_a_full_cycle_visits_every_enabled_quote(self):
        seen = {self.store.current.id}
        for _ in range(len(self.ids) - 1):
            seen.add(self.store.next().id)
        self.assertEqual(seen, set(self.ids))

    def test_navigation_never_reaches_a_disabled_quote(self):
        for _ in range(len(self.ids) * 3):
            self.assertTrue(self.store.next().enabled)
            self.assertNotEqual(self.store.current.id, "d")

    def test_navigation_on_a_single_quote_store(self):
        store = store_from([{"id": "only", "text": "Only."}])
        self.assertEqual(store.next().id, "only")
        self.assertEqual(store.previous().id, "only")

    def test_reset_to_daily(self):
        daily = self.store.daily_quote()
        self.store.next()
        self.store.next()
        self.assertEqual(self.store.reset_to_daily(), daily)


# -- lookup -----------------------------------------------------------------


class LookupTests(unittest.TestCase):
    def setUp(self):
        self.store = store_from(VALID)

    def test_quote_at_index(self):
        self.assertEqual(self.store.quote_at(0).id, "a")

    def test_quote_at_wraps(self):
        self.assertEqual(self.store.quote_at(len(self.store)).id, "a")
        self.assertEqual(self.store.quote_at(-1).id, "e")

    def test_quote_by_id(self):
        self.assertEqual(self.store.quote_by_id("b").text, "Beta.")

    def test_quote_by_id_finds_disabled_quotes(self):
        disabled = self.store.quote_by_id("d")
        self.assertIsNotNone(disabled)
        self.assertFalse(disabled.enabled)

    def test_quote_by_id_returns_none_when_missing(self):
        self.assertIsNone(self.store.quote_by_id("nope"))


# -- source resolution ------------------------------------------------------


class QuoteSourceTests(unittest.TestCase):
    def test_explicit_path_wins_and_is_the_only_candidate(self):
        self.assertEqual(quote_file_candidates("/some/where.json"), (Path("/some/where.json"),))

    def test_explicit_path_is_returned_even_if_absent(self):
        # So the caller gets a real "not found" instead of a silent fallback.
        candidates = quote_file_candidates("/nonexistent/quotes.json")
        self.assertEqual(len(candidates), 1)

    def test_examples_file_is_a_candidate(self):
        names = [p.name for p in quote_file_candidates()]
        self.assertIn("quotes.json", names)
        self.assertTrue(all(p.is_file() for p in quote_file_candidates()))

    def test_local_root_file_outranks_the_examples(self):
        candidates = list(quote_file_candidates())
        parents = [p.parent.name for p in candidates]
        if "examples" in parents and len(candidates) > 1:
            self.assertNotEqual(candidates[0].parent.name, "examples")


if __name__ == "__main__":
    unittest.main()
