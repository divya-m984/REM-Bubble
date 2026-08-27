"""Tests for quote persistence: serialisation, atomic writes, and the
add/remove/enable/disable behaviour driven through the CLI handlers.

Everything runs against a temporary ``XDG_CONFIG_HOME``; the real
``~/.config/rem-bubbles`` is never touched. No GTK, Wayland or display server
is involved.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rem_bubbles import cli
from rem_bubbles.quote_store import (
    Quote,
    QuoteStore,
    QuoteStoreError,
    decode_quotes,
    load_quotes,
    quotes_to_json,
    write_quotes,
    write_text_atomic,
)

from test_config import IsolatedConfigTestCase


class QuoteCommandTestCase(IsolatedConfigTestCase):
    """Runs CLI commands against an isolated personal quote file."""

    def setUp(self):
        super().setUp()
        self.quotes_path = self.config_dir / "quotes.json"

    def run_cli(self, argv) -> int:
        """Invoke the CLI, discarding its output. Returns the exit status."""
        with open(os.devnull, "w", encoding="utf-8") as sink:
            with mock.patch("sys.stdout", sink), mock.patch("sys.stderr", sink):
                return cli.main(argv)

    def add(self, *argv) -> int:
        return self.run_cli(["quote", "add", *argv])

    def stored(self) -> list[Quote]:
        return load_quotes(self.quotes_path)

    def ids(self) -> list[str]:
        return [quote.id for quote in self.stored()]

    def by_id(self, quote_id: str) -> Quote:
        for quote in self.stored():
            if quote.id == quote_id:
                return quote
        raise AssertionError(f"no quote {quote_id!r} in {self.ids()}")

    def seed(self, *texts) -> None:
        for text in texts:
            self.assertEqual(self.add(text), 0)


# -- serialisation ----------------------------------------------------------


class SerialisationTests(unittest.TestCase):
    def test_round_trips_every_field(self):
        original = [
            Quote(id="a", text="Alpha.", author=None, enabled=True),
            Quote(id="b", text="Beta.", author="Someone", enabled=False),
        ]
        self.assertEqual(decode_quotes(quotes_to_json(original)), original)

    def test_output_is_pretty_and_newline_terminated(self):
        text = quotes_to_json([Quote(id="a", text="Alpha.")])
        self.assertTrue(text.endswith("\n"))
        self.assertIn("\n  {\n", text)

    def test_output_is_valid_json(self):
        payload = json.loads(quotes_to_json([Quote(id="a", text="Alpha.")]))
        self.assertEqual(
            payload,
            [{"id": "a", "text": "Alpha.", "author": None, "enabled": True}],
        )

    def test_unicode_is_not_escaped(self):
        text = quotes_to_json([Quote(id="u", text="café — 日本語", author="Ünïcode")])
        self.assertIn("café — 日本語", text)
        self.assertIn("Ünïcode", text)
        self.assertNotIn("\\u", text)


# -- atomic writes ----------------------------------------------------------


class AtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.directory = Path(self._temp.name)
        self.path = self.directory / "quotes.json"

    def temporaries(self):
        return [p for p in self.directory.iterdir() if p.name != self.path.name]

    def test_successful_write_replaces_the_destination(self):
        write_quotes(self.path, [Quote(id="a", text="Alpha.")])
        write_quotes(self.path, [Quote(id="b", text="Beta.")])
        self.assertEqual([q.id for q in load_quotes(self.path)], ["b"])

    def test_no_temporary_file_survives_a_successful_write(self):
        write_quotes(self.path, [Quote(id="a", text="Alpha.")])
        self.assertEqual(self.temporaries(), [])

    def test_creates_missing_parent_directories(self):
        nested = self.directory / "deep" / "deeper" / "quotes.json"
        write_quotes(nested, [Quote(id="a", text="Alpha.")])
        self.assertTrue(nested.is_file())

    def test_new_files_are_user_private(self):
        write_quotes(self.path, [Quote(id="a", text="Alpha.")])
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_existing_permissions_are_preserved(self):
        write_quotes(self.path, [Quote(id="a", text="Alpha.")])
        os.chmod(self.path, 0o644)
        write_quotes(self.path, [Quote(id="b", text="Beta.")])
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o644)

    def test_a_failed_replace_leaves_the_original_intact(self):
        write_quotes(self.path, [Quote(id="a", text="Alpha.")])
        before = self.path.read_text(encoding="utf-8")

        with mock.patch("os.replace", side_effect=OSError(5, "Input/output error")):
            with self.assertRaises(OSError):
                write_quotes(self.path, [Quote(id="b", text="Beta.")])

        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_a_failed_replace_leaves_no_temporary_file(self):
        write_quotes(self.path, [Quote(id="a", text="Alpha.")])
        with mock.patch("os.replace", side_effect=OSError(5, "Input/output error")):
            with self.assertRaises(OSError):
                write_quotes(self.path, [Quote(id="b", text="Beta.")])
        self.assertEqual(self.temporaries(), [])

    def test_invalid_data_never_reaches_the_destination(self):
        write_quotes(self.path, [Quote(id="a", text="Alpha.")])
        duplicated = [Quote(id="a", text="Alpha."), Quote(id="a", text="Again.")]

        with self.assertRaises(QuoteStoreError):
            write_quotes(self.path, duplicated)

        self.assertEqual([q.id for q in load_quotes(self.path)], ["a"])
        self.assertEqual(self.temporaries(), [])

    def test_write_text_atomic_replaces_content(self):
        target = self.directory / "config.toml"
        write_text_atomic(target, "[quotes]\n")
        write_text_atomic(target, '[quotes]\nfile = "q.json"\n')
        self.assertEqual(target.read_text(encoding="utf-8"), '[quotes]\nfile = "q.json"\n')


# -- identifier generation --------------------------------------------------


class IdentifierTests(unittest.TestCase):
    def test_slug_from_plain_text(self):
        self.assertEqual(
            cli.slugify("Finish the simple version first."),
            "finish-the-simple-version-first",
        )

    def test_slug_folds_accents(self):
        self.assertEqual(cli.slugify("Café au lait"), "cafe-au-lait")

    def test_slug_collapses_punctuation_and_spaces(self):
        self.assertEqual(cli.slugify("  Read   the -- error! "), "read-the-error")

    def test_slug_is_length_limited_at_a_word_boundary(self):
        slug = cli.slugify("alpha beta gamma delta epsilon zeta eta theta iota kappa")
        self.assertLessEqual(len(slug), cli.MAX_SLUG_LENGTH)
        self.assertFalse(slug.endswith("-"))
        self.assertTrue(slug.startswith("alpha-beta"))

    def test_slug_of_a_single_overlong_word_is_not_empty(self):
        slug = cli.slugify("x" * 200)
        self.assertTrue(slug)
        self.assertLessEqual(len(slug), cli.MAX_SLUG_LENGTH)

    def test_non_ascii_scripts_produce_no_slug(self):
        self.assertEqual(cli.slugify("日本語のことば"), "")

    def test_digest_id_is_stable_and_well_formed(self):
        first = cli.digest_id("日本語のことば")
        self.assertEqual(first, cli.digest_id("日本語のことば"))
        self.assertTrue(first.startswith("quote-"))
        self.assertEqual(len(first), len("quote-") + 8)

    def test_generated_id_falls_back_to_a_digest(self):
        self.assertTrue(cli.generate_id("日本語のことば", set()).startswith("quote-"))

    def test_generated_id_is_deterministic(self):
        text = "Keep making weird things."
        self.assertEqual(cli.generate_id(text, set()), cli.generate_id(text, set()))

    def test_collision_gets_a_numeric_suffix(self):
        taken = {"keep-making-weird-things"}
        self.assertEqual(
            cli.generate_id("Keep making weird things.", taken),
            "keep-making-weird-things-2",
        )

    def test_repeated_collisions_keep_counting(self):
        taken = {"a-quote", "a-quote-2", "a-quote-3"}
        self.assertEqual(cli.generate_id("A quote", taken), "a-quote-4")


# -- add --------------------------------------------------------------------


class AddTests(QuoteCommandTestCase):
    def test_first_add_creates_the_file(self):
        self.assertFalse(self.quotes_path.exists())
        self.assertEqual(self.add("My first quote"), 0)
        self.assertTrue(self.quotes_path.is_file())
        self.assertEqual(self.ids(), ["my-first-quote"])

    def test_first_add_creates_the_config_directory(self):
        self.assertFalse(self.config_dir.exists())
        self.add("My first quote")
        self.assertTrue(self.config_dir.is_dir())

    def test_new_config_directory_is_user_private(self):
        self.add("My first quote")
        self.assertEqual(os.stat(self.config_dir).st_mode & 0o777, 0o700)

    def test_first_add_does_not_import_example_quotes(self):
        self.add("My first quote")
        self.assertEqual(len(self.stored()), 1)

    def test_generated_id(self):
        self.add("Finish the simple version first.")
        self.assertEqual(self.ids(), ["finish-the-simple-version-first"])

    def test_explicit_id(self):
        self.assertEqual(self.add("Keep making weird things.", "--id", "weird-things"), 0)
        self.assertEqual(self.ids(), ["weird-things"])

    def test_duplicate_explicit_id_is_rejected(self):
        self.add("One.", "--id", "same")
        self.assertEqual(self.add("Two.", "--id", "same"), 1)
        self.assertEqual(self.ids(), ["same"])
        self.assertEqual(self.by_id("same").text, "One.")

    def test_duplicate_generated_slug_gets_a_suffix(self):
        self.add("Keep making weird things.")
        self.add("Keep making weird things.")
        self.assertEqual(
            self.ids(),
            ["keep-making-weird-things", "keep-making-weird-things-2"],
        )

    def test_third_duplicate_keeps_counting(self):
        for _ in range(3):
            self.add("Same text.")
        self.assertEqual(self.ids(), ["same-text", "same-text-2", "same-text-3"])

    def test_author_is_stored(self):
        self.add("Keep making weird things.", "--author", "Me")
        self.assertEqual(self.by_id("keep-making-weird-things").author, "Me")

    def test_absent_author_is_null(self):
        self.add("No byline.")
        self.assertIsNone(self.by_id("no-byline").author)

    def test_blank_author_is_stored_as_null(self):
        self.add("Blank byline.", "--author", "   ")
        self.assertIsNone(self.by_id("blank-byline").author)

    def test_disabled_flag(self):
        self.add("Enabled one.")
        self.assertEqual(self.add("Disabled one.", "--disabled"), 0)
        self.assertFalse(self.by_id("disabled-one").enabled)
        self.assertTrue(self.by_id("enabled-one").enabled)

    def test_a_disabled_quote_cannot_be_the_only_quote(self):
        self.assertEqual(self.add("Only one.", "--disabled"), 1)
        self.assertFalse(self.quotes_path.exists())

    def test_unicode_text_is_preserved(self):
        text = "書くことは考えること — café ☕"
        self.assertEqual(self.add(text), 0)
        self.assertEqual(self.stored()[0].text, text)

    def test_unicode_text_gets_a_digest_id(self):
        self.add("日本語のことば")
        self.assertTrue(self.ids()[0].startswith("quote-"))

    def test_unicode_survives_on_disk_unescaped(self):
        self.add("café ☕")
        self.assertIn("café ☕", self.quotes_path.read_text(encoding="utf-8"))

    def test_text_is_trimmed(self):
        self.add("   Padded.   ")
        self.assertEqual(self.stored()[0].text, "Padded.")

    def test_blank_text_is_rejected(self):
        self.assertEqual(self.add("   "), 1)
        self.assertFalse(self.quotes_path.exists())

    def test_blank_explicit_id_is_rejected(self):
        self.assertEqual(self.add("Something.", "--id", "  "), 1)
        self.assertFalse(self.quotes_path.exists())

    def test_appends_to_the_end(self):
        self.seed("First.", "Second.", "Third.")
        self.assertEqual(self.ids(), ["first", "second", "third"])

    def test_written_file_is_valid_json_and_reloadable(self):
        self.seed("First.", "Second.")
        payload = json.loads(self.quotes_path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload), 2)
        self.assertEqual(len(QuoteStore.from_file(self.quotes_path)), 2)

    def test_writes_to_the_configured_path(self):
        elsewhere = self.xdg / "elsewhere" / "mine.json"
        self.write_config(f'[quotes]\nfile = "{elsewhere}"\n')
        self.add("Somewhere else.")
        self.assertTrue(elsewhere.is_file())
        self.assertFalse(self.quotes_path.exists())

    def test_malformed_config_blocks_the_write(self):
        self.write_config("[quotes]\nfile = 7\n")
        self.assertEqual(self.add("Should not be written."), 1)
        self.assertFalse(self.quotes_path.exists())

    def test_a_corrupt_collection_is_not_overwritten(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.quotes_path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.add("New quote."), 1)
        self.assertEqual(self.quotes_path.read_text(encoding="utf-8"), "{not json")


# -- list -------------------------------------------------------------------


class ListTests(QuoteCommandTestCase):
    def output_of(self, argv):
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            status = cli.main(argv)
        return status, buffer.getvalue()

    def test_lists_existing_quotes(self):
        self.seed("First quote.", "Second quote.")
        status, output = self.output_of(["quote", "list"])
        self.assertEqual(status, 0)
        self.assertIn("first-quote", output)
        self.assertIn("First quote.", output)
        self.assertIn("second-quote", output)

    def test_names_the_file_being_managed(self):
        self.seed("A quote.")
        _, output = self.output_of(["quote", "list"])
        self.assertIn(str(self.quotes_path), output)

    def test_shows_enabled_and_disabled_states(self):
        self.seed("Enabled one.")
        self.add("Disabled one.", "--disabled")
        _, output = self.output_of(["quote", "list"])
        self.assertIn(f"{cli.ENABLED_MARK} enabled-one", output)
        self.assertIn(f"{cli.DISABLED_MARK} disabled-one", output)

    def test_shows_the_author_when_present(self):
        self.add("With byline.", "--author", "Me")
        _, output = self.output_of(["quote", "list"])
        self.assertIn("— Me", output)

    def test_omits_the_byline_when_absent(self):
        self.seed("No byline.")
        _, output = self.output_of(["quote", "list"])
        self.assertNotIn("—", output)

    def test_missing_file_is_reported_with_guidance(self):
        status, output = self.output_of(["quote", "list"])
        self.assertEqual(status, 0)
        self.assertIn("No personal quote file yet", output)
        self.assertIn("quote add", output)

    def test_list_does_not_create_the_file(self):
        self.output_of(["quote", "list"])
        self.assertFalse(self.quotes_path.exists())
        self.assertFalse(self.config_dir.exists())

    def test_list_does_not_modify_an_existing_file(self):
        self.seed("A quote.")
        before = self.quotes_path.read_text(encoding="utf-8")
        mtime = os.stat(self.quotes_path).st_mtime_ns
        self.output_of(["quote", "list"])
        self.assertEqual(self.quotes_path.read_text(encoding="utf-8"), before)
        self.assertEqual(os.stat(self.quotes_path).st_mtime_ns, mtime)

    def test_lists_an_all_disabled_collection(self):
        # A store would refuse this collection; listing must still work.
        self.config_dir.mkdir(parents=True, exist_ok=True)
        write_quotes(self.quotes_path, [Quote(id="off", text="Off.", enabled=False)])
        status, output = self.output_of(["quote", "list"])
        self.assertEqual(status, 0)
        self.assertIn("off", output)


# -- remove -----------------------------------------------------------------


class RemoveTests(QuoteCommandTestCase):
    def setUp(self):
        super().setUp()
        self.seed("First.", "Second.", "Third.")

    def test_removes_by_exact_id(self):
        self.assertEqual(self.run_cli(["quote", "remove", "second"]), 0)
        self.assertEqual(self.ids(), ["first", "third"])

    def test_unknown_id_fails_and_changes_nothing(self):
        before = self.quotes_path.read_text(encoding="utf-8")
        self.assertEqual(self.run_cli(["quote", "remove", "nope"]), 1)
        self.assertEqual(self.quotes_path.read_text(encoding="utf-8"), before)

    def test_partial_id_is_not_accepted(self):
        self.assertEqual(self.run_cli(["quote", "remove", "sec"]), 1)
        self.assertEqual(len(self.stored()), 3)

    def test_preserves_the_order_of_the_rest(self):
        self.run_cli(["quote", "remove", "first"])
        self.assertEqual(self.ids(), ["second", "third"])

    def test_preserves_other_quote_data(self):
        self.add("Kept.", "--author", "Someone")
        self.add("Hidden.", "--disabled")
        self.run_cli(["quote", "remove", "first"])
        self.assertEqual(self.by_id("kept").author, "Someone")
        self.assertFalse(self.by_id("hidden").enabled)

    def test_cannot_remove_the_last_enabled_quote(self):
        self.run_cli(["quote", "remove", "second"])
        self.run_cli(["quote", "remove", "third"])
        self.assertEqual(self.run_cli(["quote", "remove", "first"]), 1)
        self.assertEqual(self.ids(), ["first"])

    def test_a_disabled_quote_can_be_removed(self):
        self.add("Hidden.", "--disabled")
        self.assertEqual(self.run_cli(["quote", "remove", "hidden"]), 0)
        self.assertEqual(self.ids(), ["first", "second", "third"])

    def test_missing_file_fails_clearly(self):
        self.quotes_path.unlink()
        self.assertEqual(self.run_cli(["quote", "remove", "first"]), 1)


# -- enable / disable -------------------------------------------------------


class EnableDisableTests(QuoteCommandTestCase):
    def setUp(self):
        super().setUp()
        self.seed("First.", "Second.")

    def test_disable(self):
        self.assertEqual(self.run_cli(["quote", "disable", "second"]), 0)
        self.assertFalse(self.by_id("second").enabled)
        self.assertTrue(self.by_id("first").enabled)

    def test_enable(self):
        self.run_cli(["quote", "disable", "second"])
        self.assertEqual(self.run_cli(["quote", "enable", "second"]), 0)
        self.assertTrue(self.by_id("second").enabled)

    def test_enable_already_enabled_is_a_success(self):
        self.assertEqual(self.run_cli(["quote", "enable", "first"]), 0)
        self.assertTrue(self.by_id("first").enabled)

    def test_enable_already_enabled_does_not_rewrite_the_file(self):
        mtime = os.stat(self.quotes_path).st_mtime_ns
        self.run_cli(["quote", "enable", "first"])
        self.assertEqual(os.stat(self.quotes_path).st_mtime_ns, mtime)

    def test_disable_already_disabled_is_a_success(self):
        self.run_cli(["quote", "disable", "second"])
        self.assertEqual(self.run_cli(["quote", "disable", "second"]), 0)
        self.assertFalse(self.by_id("second").enabled)

    def test_disable_already_disabled_does_not_rewrite_the_file(self):
        self.run_cli(["quote", "disable", "second"])
        mtime = os.stat(self.quotes_path).st_mtime_ns
        self.run_cli(["quote", "disable", "second"])
        self.assertEqual(os.stat(self.quotes_path).st_mtime_ns, mtime)

    def test_cannot_disable_the_last_enabled_quote(self):
        self.run_cli(["quote", "disable", "second"])
        self.assertEqual(self.run_cli(["quote", "disable", "first"]), 1)
        self.assertTrue(self.by_id("first").enabled)

    def test_disabling_the_last_enabled_quote_reports_why(self):
        import contextlib
        import io

        self.run_cli(["quote", "disable", "second"])
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            cli.main(["quote", "disable", "first"])
        self.assertIn("at least one quote must remain enabled", buffer.getvalue())

    def test_unknown_id_fails(self):
        self.assertEqual(self.run_cli(["quote", "enable", "nope"]), 1)
        self.assertEqual(self.run_cli(["quote", "disable", "nope"]), 1)

    def test_order_is_preserved(self):
        self.seed("Third.")
        self.run_cli(["quote", "disable", "second"])
        self.assertEqual(self.ids(), ["first", "second", "third"])

    def test_text_and_author_are_preserved(self):
        self.add("Attributed.", "--author", "Someone")
        self.run_cli(["quote", "disable", "attributed"])
        quote = self.by_id("attributed")
        self.assertEqual(quote.text, "Attributed.")
        self.assertEqual(quote.author, "Someone")

    def test_unaffected_entries_keep_their_state(self):
        self.add("Hidden.", "--disabled")
        self.run_cli(["quote", "disable", "second"])
        self.assertFalse(self.by_id("hidden").enabled)
        self.assertTrue(self.by_id("first").enabled)


if __name__ == "__main__":
    unittest.main()
