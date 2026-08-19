"""Where quotes come from.

Path resolution and the load-with-fallback chain live here so that the GTK
layer never has to know about the filesystem. This is not a configuration
framework — there is no TOML parsing and no XDG lookup yet; both belong to a
later milestone. Nothing in this module writes to the filesystem, and nothing
outside the repository checkout is read.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rem_bubbles.quote_store import QuoteStore, QuoteStoreError

__all__ = [
    "EXAMPLE_QUOTES",
    "LOCAL_QUOTES",
    "REPO_ROOT",
    "load_quote_store",
    "quote_file_candidates",
]

#: Repository checkout root, as seen from ``src/rem_bubbles/config.py``.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The developer's / user's own collection. Git-ignored, never created for them.
LOCAL_QUOTES = REPO_ROOT / "quotes.json"

#: Tracked sample data, used as the development fallback.
EXAMPLE_QUOTES = REPO_ROOT / "examples" / "quotes.json"


def quote_file_candidates(explicit: Path | str | None = None) -> tuple[Path, ...]:
    """Quote sources to try, highest priority first.

    1. ``explicit`` — a path handed in by a caller (API use, future CLI flag).
    2. ``<repo>/quotes.json`` — the local, git-ignored personal collection.
    3. ``<repo>/examples/quotes.json`` — tracked sample data.

    An explicit path is always returned even when it does not exist, so that a
    caller asking for a specific file gets a real "not found" error rather than
    silently falling through to the examples. The other two are only returned
    when they exist: a missing local ``quotes.json`` is the normal case, not a
    problem worth reporting.
    """
    if explicit is not None:
        return (Path(explicit),)
    return tuple(path for path in (LOCAL_QUOTES, EXAMPLE_QUOTES) if path.is_file())


def load_quote_store(explicit: Path | str | None = None) -> QuoteStore:
    """Load the first usable quote source, reporting whatever failed on the way.

    Every failure is printed to stderr — the original error is never swallowed —
    and the next candidate is tried. If nothing loads, a single built-in quote
    keeps the application openable instead of aborting the launch.
    """
    for path in quote_file_candidates(explicit):
        try:
            return QuoteStore.from_file(path)
        except QuoteStoreError as exc:
            print(f"rem-bubbles: {exc}", file=sys.stderr)

    print(
        "rem-bubbles: no usable quote file, falling back to the built-in quote",
        file=sys.stderr,
    )
    return QuoteStore.emergency()
