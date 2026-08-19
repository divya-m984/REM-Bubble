"""Quote data: parsing, validation, daily selection and navigation.

This module is deliberately free of GTK — it holds no widgets and imports no
``gi``, so it can be unit-tested without a display server. The graphical layer
reads from a :class:`QuoteStore`; it never touches JSON itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "EMERGENCY_QUOTE",
    "Quote",
    "QuoteStore",
    "QuoteStoreError",
    "parse_quotes",
]


class QuoteStoreError(Exception):
    """Raised for any unusable quote source: missing, malformed or empty.

    Carries a message meant to be shown to a human as-is, so the application
    never has to surface a traceback for ordinary bad data.
    """


@dataclass(frozen=True)
class Quote:
    """A single quote. ``author`` is None when the quote is unattributed."""

    id: str
    text: str
    author: str | None = None
    enabled: bool = True


#: Last-resort quote, used only when every quote source fails to load. It keeps
#: the window openable so the user can see the error instead of nothing at all.
EMERGENCY_QUOTE = Quote(
    id="emergency-fallback",
    text="Keep making weird things.",
    author=None,
    enabled=True,
)


# -- parsing / validation ---------------------------------------------------


def _where(source: str | None) -> str:
    return f" in {source}" if source else ""


def _parse_one(entry: Any, index: int, source: str | None) -> Quote:
    at = f"Quote at index {index}{_where(source)}"

    if not isinstance(entry, dict):
        raise QuoteStoreError(f"{at} is not a JSON object.")

    if "id" not in entry:
        raise QuoteStoreError(f'{at} is missing the required "id" field.')
    raw_id = entry["id"]
    if not isinstance(raw_id, str):
        raise QuoteStoreError(f'{at} has a non-string "id" field.')
    quote_id = raw_id.strip()
    if not quote_id:
        raise QuoteStoreError(f'{at} has an empty "id" field.')

    if "text" not in entry:
        raise QuoteStoreError(f'Quote "{quote_id}"{_where(source)} is missing the required "text" field.')
    raw_text = entry["text"]
    if not isinstance(raw_text, str):
        raise QuoteStoreError(f'Quote "{quote_id}"{_where(source)} has a non-string "text" field.')
    text = raw_text.strip()
    if not text:
        raise QuoteStoreError(f'Quote "{quote_id}"{_where(source)} has an empty "text" field.')

    raw_author = entry.get("author")
    if raw_author is None:
        author: str | None = None
    elif isinstance(raw_author, str):
        # A blank author is the same as no author, not an empty byline.
        author = raw_author.strip() or None
    else:
        raise QuoteStoreError(
            f'Quote "{quote_id}"{_where(source)} has an invalid "author" field: '
            "expected a string or null."
        )

    raw_enabled = entry.get("enabled", True)
    # bool is a subclass of int, so this rejects 0/1 and "true" as intended.
    if not isinstance(raw_enabled, bool):
        raise QuoteStoreError(
            f'Quote "{quote_id}"{_where(source)} has an invalid "enabled" field: '
            "expected true or false."
        )

    return Quote(id=quote_id, text=text, author=author, enabled=raw_enabled)


def parse_quotes(raw: Any, source: str | None = None) -> list[Quote]:
    """Validate a decoded JSON document and return every quote it declares.

    Disabled quotes are returned too — they are still validated, they simply do
    not take part in selection later. Nothing is silently dropped: the first
    problem raises :class:`QuoteStoreError`.
    """
    if not isinstance(raw, list):
        raise QuoteStoreError(
            f"The quote file{_where(source)} must contain a JSON array of quote "
            f"objects, but the root value is {type(raw).__name__}."
        )

    quotes: list[Quote] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        quote = _parse_one(entry, index, source)
        if quote.id in seen:
            raise QuoteStoreError(f'Duplicate quote id: "{quote.id}"{_where(source)}')
        seen.add(quote.id)
        quotes.append(quote)

    return quotes


# -- store ------------------------------------------------------------------


class QuoteStore:
    """An ordered collection of enabled quotes, plus the current cursor.

    The cursor starts on the deterministic quote for the current local date and
    moves only when :meth:`next` or :meth:`previous` is called. It lives here
    rather than in the window so that collapsing and re-expanding the bubble
    cannot lose the user's manual selection.
    """

    def __init__(self, quotes: Iterable[Quote], source: str | None = None) -> None:
        self._all: tuple[Quote, ...] = tuple(quotes)
        self._enabled: tuple[Quote, ...] = tuple(q for q in self._all if q.enabled)
        self.source = source

        if not self._enabled:
            raise QuoteStoreError(
                f"The quote collection{_where(source)} contains no enabled quotes."
            )

        self._index = self.daily_index()

    # -- constructors ------------------------------------------------------

    @classmethod
    def from_json(cls, text: str, source: str | None = None) -> "QuoteStore":
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise QuoteStoreError(
                f"The quote file{_where(source)} is not valid JSON: {exc.msg} "
                f"(line {exc.lineno}, column {exc.colno})."
            ) from exc
        return cls(parse_quotes(raw, source), source)

    @classmethod
    def from_file(cls, path: Path | str) -> "QuoteStore":
        path = Path(path)
        source = str(path)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise QuoteStoreError(f"Quote file not found: {source}") from exc
        except OSError as exc:
            raise QuoteStoreError(f"Could not read quote file {source}: {exc.strerror}") from exc
        return cls.from_json(text, source)

    @classmethod
    def emergency(cls) -> "QuoteStore":
        """A one-quote store used when no real source could be loaded."""
        return cls([EMERGENCY_QUOTE], source="built-in emergency quote")

    # -- collection --------------------------------------------------------

    @property
    def quotes(self) -> tuple[Quote, ...]:
        """The enabled quotes, in file order. This is the active collection."""
        return self._enabled

    @property
    def all_quotes(self) -> tuple[Quote, ...]:
        """Every parsed quote, including disabled ones."""
        return self._all

    def __len__(self) -> int:
        return len(self._enabled)

    def quote_at(self, index: int) -> Quote:
        """Return the enabled quote at ``index``, wrapping out-of-range values."""
        return self._enabled[index % len(self._enabled)]

    def quote_by_id(self, quote_id: str) -> Quote | None:
        """Return the quote with this id, enabled or not, or None."""
        for quote in self._all:
            if quote.id == quote_id:
                return quote
        return None

    # -- deterministic daily selection -------------------------------------

    def _fingerprint(self) -> str:
        """A stable digest of the active collection and its ordering."""
        return "\n".join(q.id for q in self._enabled)

    def daily_index(self, for_date: date | None = None) -> int:
        """Index of the quote belonging to ``for_date`` (default: today, local).

        SHA-256 over "<date>\\n<enabled ids in order>" gives a value that is
        stable across processes — unlike ``hash()``, which is randomised per
        interpreter run — and that spreads consecutive days across the
        collection instead of walking it in order.

        Because the fingerprint includes the enabled ids, editing the collection
        may change today's quote. That is accepted for this milestone; no state
        is persisted to pin it.
        """
        day = for_date or date.today()
        digest = sha256(f"{day.isoformat()}\n{self._fingerprint()}".encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") % len(self._enabled)

    def daily_quote(self, for_date: date | None = None) -> Quote:
        """The quote for ``for_date``, without moving the cursor."""
        return self._enabled[self.daily_index(for_date)]

    def reset_to_daily(self, for_date: date | None = None) -> Quote:
        """Move the cursor back onto the deterministic quote for ``for_date``."""
        self._index = self.daily_index(for_date)
        return self.current

    # -- navigation --------------------------------------------------------

    @property
    def index(self) -> int:
        """Cursor position within the enabled quotes."""
        return self._index

    @property
    def current(self) -> Quote:
        """The quote currently being displayed."""
        return self._enabled[self._index]

    def next(self) -> Quote:
        """Advance one quote, wrapping past the end back to the first."""
        self._index = (self._index + 1) % len(self._enabled)
        return self.current

    def previous(self) -> Quote:
        """Step back one quote, wrapping past the start to the last."""
        self._index = (self._index - 1) % len(self._enabled)
        return self.current
