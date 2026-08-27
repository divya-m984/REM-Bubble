"""Where REM Bubbles keeps its configuration, and where quotes come from.

Two responsibilities live here, both GTK-free:

* resolving the user's configuration directory, following the XDG Base
  Directory convention, and reading the small ``config.toml`` inside it;
* turning that into an ordered list of quote sources for the application.

``tomllib`` is standard library from Python 3.11, so minimal configuration
support costs no dependency. This is still not a settings framework: the only
recognised key is ``[quotes].file``.

Nothing in this module writes to the filesystem — creating the config directory
is the CLI's job, so that merely launching the bubble never leaves files behind.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from rem_bubbles.quote_store import QuoteStore, QuoteStoreError

__all__ = [
    "APP_DIR_NAME",
    "CONFIG_FILENAME",
    "ConfigError",
    "DEFAULT_CONFIG_TEXT",
    "EXAMPLE_QUOTES",
    "LOCAL_QUOTES",
    "QUOTES_FILENAME",
    "REPO_ROOT",
    "UserConfig",
    "default_quote_file",
    "load_quote_store",
    "managed_quote_file",
    "quote_file_candidates",
    "read_user_config",
    "user_config_dir",
    "user_config_file",
]

#: Directory name used under the XDG config base directory.
APP_DIR_NAME = "rem-bubbles"

CONFIG_FILENAME = "config.toml"
QUOTES_FILENAME = "quotes.json"

#: What ``rem-bubbles init`` writes when no config file exists yet.
DEFAULT_CONFIG_TEXT = f'[quotes]\nfile = "{QUOTES_FILENAME}"\n'

#: Repository checkout root, as seen from ``src/rem_bubbles/config.py``.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: A developer's own collection in the checkout. Git-ignored, never created.
LOCAL_QUOTES = REPO_ROOT / QUOTES_FILENAME

#: Tracked sample data, used as the last file-based fallback.
EXAMPLE_QUOTES = REPO_ROOT / "examples" / QUOTES_FILENAME


class ConfigError(Exception):
    """Raised for an unusable ``config.toml``.

    Like :class:`~rem_bubbles.quote_store.QuoteStoreError`, the message is
    written to be shown to a human as-is rather than as a traceback.
    """


# -- locations --------------------------------------------------------------


def user_config_dir() -> Path:
    """The REM Bubbles configuration directory.

    ``$XDG_CONFIG_HOME/rem-bubbles`` when that variable holds an absolute path,
    otherwise ``~/.config/rem-bubbles``. Per the XDG specification an unset,
    empty or relative value is invalid and falls back to the default, which also
    keeps a stray ``XDG_CONFIG_HOME=`` in a shell profile from pointing the
    application at the current working directory.
    """
    raw = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(raw) if raw.strip() and Path(raw).is_absolute() else Path.home() / ".config"
    return base / APP_DIR_NAME


def user_config_file() -> Path:
    """``<config dir>/config.toml``, whether or not it exists."""
    return user_config_dir() / CONFIG_FILENAME


def default_quote_file() -> Path:
    """``<config dir>/quotes.json`` — the personal collection's default home."""
    return user_config_dir() / QUOTES_FILENAME


# -- config.toml ------------------------------------------------------------


@dataclass(frozen=True)
class UserConfig:
    """The result of consulting ``config.toml``.

    ``quote_file`` is None when the file is absent or declares no quote path;
    both mean "use the default location", which is not an error.
    """

    path: Path
    exists: bool
    quote_file: Path | None = None


def _resolve_quote_path(raw: str, config_file: Path) -> Path:
    """Turn a configured ``file`` value into an absolute path.

    ``~`` is expanded, and a relative path is taken relative to the directory
    holding ``config.toml`` — so the common ``file = "quotes.json"`` means the
    file next to the config, no matter where the process was started from.
    """
    candidate = Path(raw.strip()).expanduser()
    if not candidate.is_absolute():
        candidate = config_file.parent / candidate
    # normpath rather than resolve(): tidy up ``..`` without following symlinks
    # or requiring the file to exist.
    return Path(os.path.normpath(candidate))


def read_user_config(path: Path | str | None = None) -> UserConfig:
    """Read ``config.toml``, raising :class:`ConfigError` on anything malformed.

    A missing file is normal and returns an empty configuration. A file that
    exists but cannot be understood is never silently ignored: guessing would
    risk writing a user's quotes somewhere they did not ask for.
    """
    config_file = Path(path) if path is not None else user_config_file()

    try:
        with open(config_file, "rb") as stream:
            data = tomllib.load(stream)
    except FileNotFoundError:
        return UserConfig(path=config_file, exists=False)
    except IsADirectoryError as exc:
        raise ConfigError(f"Config path is a directory, not a file: {config_file}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read config file {config_file}: {exc.strerror}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"The config file {config_file} is not valid TOML: {exc}") from exc

    quotes = data.get("quotes")
    if quotes is None:
        return UserConfig(path=config_file, exists=True)
    if not isinstance(quotes, dict):
        raise ConfigError(
            f"In {config_file}: [quotes] must be a table, "
            f"but it is {type(quotes).__name__}."
        )

    raw_file = quotes.get("file")
    if raw_file is None:
        return UserConfig(path=config_file, exists=True)
    if not isinstance(raw_file, str):
        raise ConfigError(
            f'In {config_file}: [quotes] "file" must be a string, '
            f"but it is {type(raw_file).__name__}."
        )
    if not raw_file.strip():
        raise ConfigError(f'In {config_file}: [quotes] "file" is blank.')

    return UserConfig(
        path=config_file,
        exists=True,
        quote_file=_resolve_quote_path(raw_file, config_file),
    )


def managed_quote_file(config: UserConfig | None = None) -> Path:
    """The personal quote file the CLI reads and writes.

    The configured path when there is one, otherwise the default XDG location.
    Repository data is deliberately not part of this chain: ``examples`` and the
    checkout root are read-only fallbacks for the running application, never
    management targets.
    """
    config = config if config is not None else read_user_config()
    return config.quote_file or default_quote_file()


# -- runtime quote sources --------------------------------------------------


def _existing(paths: tuple[Path | None, ...]) -> tuple[Path, ...]:
    """Keep the paths that are real files, in order, without repeats."""
    seen: set[Path] = set()
    kept: list[Path] = []
    for path in paths:
        if path is None or path in seen:
            continue
        seen.add(path)
        if path.is_file():
            kept.append(path)
    return tuple(kept)


def quote_file_candidates(explicit: Path | str | None = None) -> tuple[Path, ...]:
    """Quote sources to try, highest priority first.

    1. ``explicit`` — a path handed in by a caller.
    2. the file named by ``[quotes].file`` in the user's ``config.toml``.
    3. ``<config dir>/quotes.json`` — the default personal collection.
    4. ``<repo>/quotes.json`` — a checkout-local collection, for development.
    5. ``<repo>/examples/quotes.json`` — tracked sample data.

    The user's own data outranks anything in the repository, so an installed
    copy run from inside a checkout still shows the personal collection.

    An explicit path is always returned even when it does not exist, so that a
    caller asking for a specific file gets a real "not found" error rather than
    silently falling through. The rest are only returned when they exist: a
    personal file that has not been created yet is the normal state before the
    first ``rem-bubbles quote add``, not a failure worth reporting on every
    launch.

    Raises :class:`ConfigError` if ``config.toml`` is malformed.
    """
    if explicit is not None:
        return (Path(explicit),)
    configured = read_user_config().quote_file
    return _existing((configured, default_quote_file(), LOCAL_QUOTES, EXAMPLE_QUOTES))


def load_quote_store(explicit: Path | str | None = None) -> QuoteStore:
    """Load the first usable quote source, reporting whatever failed on the way.

    Every failure is printed to stderr — the original error is never swallowed —
    and the next candidate is tried. A broken ``config.toml`` is reported and
    then stepped over: for the graphical application, refusing to open a window
    would hide the very message explaining why. Management commands treat the
    same error as fatal, because there the risk is writing to the wrong file.

    If nothing loads, a single built-in quote keeps the application openable
    instead of aborting the launch.
    """
    try:
        candidates = quote_file_candidates(explicit)
    except ConfigError as exc:
        print(f"rem-bubbles: {exc}", file=sys.stderr)
        print(
            "rem-bubbles: ignoring the configured quote path, using the defaults",
            file=sys.stderr,
        )
        candidates = _existing((default_quote_file(), LOCAL_QUOTES, EXAMPLE_QUOTES))

    for path in candidates:
        try:
            return QuoteStore.from_file(path)
        except QuoteStoreError as exc:
            print(f"rem-bubbles: {exc}", file=sys.stderr)

    print(
        "rem-bubbles: no usable quote file, falling back to the built-in quote",
        file=sys.stderr,
    )
    return QuoteStore.emergency()
