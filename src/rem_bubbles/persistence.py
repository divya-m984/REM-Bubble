"""Atomically replacing a file, and the permissions personal data gets.

Both the quote collection and the reminder collection are personal files that
must survive an interrupted write, so the crash-safe replace they share lives
here rather than in either of them. This module imports nothing but the
standard library — no GTK, no ``gi`` — so every writer above it stays headless.

The behaviour is exactly what :mod:`rem_bubbles.quote_store` implemented in
Milestone 3; only its address changed. ``quote_store`` re-exports these names,
so ``from rem_bubbles.quote_store import write_text_atomic`` still works.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

__all__ = [
    "PRIVATE_DIR_MODE",
    "PRIVATE_FILE_MODE",
    "write_text_atomic",
]

#: Permissions for freshly created personal data. Existing files keep theirs.
PRIVATE_FILE_MODE = 0o600
PRIVATE_DIR_MODE = 0o700


def _destination_mode(path: Path) -> int:
    """Permissions for the replacement file: the current ones, or user-private.

    An existing file keeps whatever the user chose for it; only files this
    application creates are forced to 0600.
    """
    try:
        return os.stat(path).st_mode & 0o777
    except OSError:
        return PRIVATE_FILE_MODE


def write_text_atomic(path: Path | str, text: str) -> None:
    """Replace ``path`` with ``text`` in one step, or leave it untouched.

    The temporary file is created in the destination's own directory so that
    :func:`os.replace` stays a same-filesystem rename, which is atomic: a reader
    (or a crash) sees either the whole old file or the whole new one, never a
    half-written collection. The temporary file is removed if anything fails.
    """
    path = Path(path)
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)

    mode = _destination_mode(path)
    handle, temporary = tempfile.mkstemp(
        dir=directory, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        # Interrupted or failed: drop the partial file, keep the original.
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
