"""Where REM Bubbles keeps its configuration, and where its data comes from.

Two responsibilities live here, both GTK-free:

* resolving the user's configuration directory, following the XDG Base
  Directory convention, and reading the small ``config.toml`` inside it;
* turning that into quote sources and a reminder source for the application.

``tomllib`` is standard library from Python 3.11, so minimal configuration
support costs no dependency. This is still not a settings framework: the only
recognised keys are ``[quotes].file``, ``[reminders].file`` and
``[notifications].enabled``, and all three are optional — a Milestone 3 config
naming only quotes stays valid untouched, and so does a Milestone 4 one naming
quotes and reminders.

Quotes and reminders resolve differently on purpose. A quote collection falls
back through repository data so the bubble always has something to show; a
reminder collection never does, because showing a user an example reminder as
though it were their own would be a lie about their day.

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
from rem_bubbles.reminder_store import ReminderStore, ReminderStoreError

__all__ = [
    "APP_DIR_NAME",
    "CONFIG_FILENAME",
    "ConfigError",
    "DEFAULT_CONFIG_TEXT",
    "EXAMPLE_QUOTES",
    "LOCAL_QUOTES",
    "NOTIFICATIONS_DEFAULT",
    "QUOTES_FILENAME",
    "REMINDERS_FILENAME",
    "REPO_ROOT",
    "UserConfig",
    "default_quote_file",
    "default_reminder_file",
    "load_notification_preference",
    "load_quote_store",
    "load_reminder_store",
    "managed_quote_file",
    "managed_reminder_file",
    "notifications_enabled",
    "quote_file_candidates",
    "read_user_config",
    "reminder_file",
    "user_config_dir",
    "user_config_file",
]

#: Directory name used under the XDG config base directory.
APP_DIR_NAME = "rem-bubbles"

CONFIG_FILENAME = "config.toml"
QUOTES_FILENAME = "quotes.json"
REMINDERS_FILENAME = "reminders.json"

#: Desktop notifications are off unless a user asks for them. Updating REM
#: Bubbles must never start putting notifications on somebody's screen, so the
#: default is False and a config with no ``[notifications]`` table means False.
NOTIFICATIONS_DEFAULT = False

#: What ``rem-bubbles init`` writes when no config file exists yet. Every table
#: names its default, so the file documents itself and shows where the
#: notification switch lives; none of them is required, and an existing config
#: is never rewritten to add a table it predates.
DEFAULT_CONFIG_TEXT = (
    f'[quotes]\nfile = "{QUOTES_FILENAME}"\n'
    f'\n[reminders]\nfile = "{REMINDERS_FILENAME}"\n'
    "\n[notifications]\nenabled = false\n"
)

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


def default_reminder_file() -> Path:
    """``<config dir>/reminders.json`` — the personal reminders' default home."""
    return user_config_dir() / REMINDERS_FILENAME


# -- config.toml ------------------------------------------------------------


@dataclass(frozen=True)
class UserConfig:
    """The result of consulting ``config.toml``.

    ``quote_file`` and ``reminder_file`` are None when the file is absent or
    declares no such path; both mean "use the default location", which is not an
    error. In particular a Milestone 3 config with only ``[quotes]`` is complete:
    reminders simply live at their default path.

    ``notifications`` is a plain bool rather than an optional one, because there
    is no difference between "not configured" and "off" — both mean no desktop
    notifications, which is what every config written before Milestone 5 says.
    """

    path: Path
    exists: bool
    quote_file: Path | None = None
    reminder_file: Path | None = None
    notifications: bool = NOTIFICATIONS_DEFAULT


def _resolve_path(raw: str, config_file: Path) -> Path:
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


def _table_file(
    data: dict[str, object], table: str, config_file: Path
) -> Path | None:
    """Read ``[<table>].file`` from a decoded config, or None if not declared.

    Both recognised tables have exactly this shape, so both are validated by the
    same rules and produce the same messages. A table that is present but empty
    is fine; a table that is the wrong type, or a ``file`` that is not a
    non-blank string, is not — guessing past either could send a user's personal
    data somewhere they did not ask for.
    """
    section = data.get(table)
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ConfigError(
            f"In {config_file}: [{table}] must be a table, "
            f"but it is {type(section).__name__}."
        )

    raw_file = section.get("file")
    if raw_file is None:
        return None
    if not isinstance(raw_file, str):
        raise ConfigError(
            f'In {config_file}: [{table}] "file" must be a string, '
            f"but it is {type(raw_file).__name__}."
        )
    if not raw_file.strip():
        raise ConfigError(f'In {config_file}: [{table}] "file" is blank.')

    return _resolve_path(raw_file, config_file)


def _table_flag(
    data: dict[str, object], table: str, key: str, default: bool, config_file: Path
) -> bool:
    """Read a boolean switch out of an optional table, or return ``default``.

    Held to the same standard as ``file``: an absent table or absent key is
    fine, a table of the wrong type or a value that is not a real boolean is
    not. ``bool`` is a subclass of ``int``, but TOML has distinct types, so
    ``enabled = 1`` arrives as an int here and is refused — quietly reading it
    as true would be guessing at whether somebody wanted notifications.
    """
    section = data.get(table)
    if section is None:
        return default
    if not isinstance(section, dict):
        raise ConfigError(
            f"In {config_file}: [{table}] must be a table, "
            f"but it is {type(section).__name__}."
        )

    raw = section.get(key)
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise ConfigError(
            f'In {config_file}: [{table}] "{key}" must be true or false, '
            f"but it is {type(raw).__name__}."
        )
    return raw


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

    return UserConfig(
        path=config_file,
        exists=True,
        quote_file=_table_file(data, "quotes", config_file),
        reminder_file=_table_file(data, "reminders", config_file),
        notifications=_table_flag(
            data, "notifications", "enabled", NOTIFICATIONS_DEFAULT, config_file
        ),
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


def managed_reminder_file(config: UserConfig | None = None) -> Path:
    """The personal reminder file the CLI reads and writes.

    ``[reminders].file`` when the user declared one, otherwise
    ``<config dir>/reminders.json``. There is no repository fallback at all, in
    either direction: nothing in the checkout is ever read as real reminders and
    nothing in it is ever written to.
    """
    config = config if config is not None else read_user_config()
    return config.reminder_file or default_reminder_file()


# -- notifications ----------------------------------------------------------


def notifications_enabled(config: UserConfig | None = None) -> bool:
    """Whether the user asked for desktop notifications. Default: no.

    Raises :class:`ConfigError` when ``config.toml`` is malformed, so a
    management command still refuses to guess. The application catches that and
    falls back to :data:`NOTIFICATIONS_DEFAULT` — see
    :func:`load_notification_preference`.
    """
    config = config if config is not None else read_user_config()
    return config.notifications


def load_notification_preference() -> bool:
    """The notification switch for the running application, never raising.

    A broken config is reported to stderr and treated as "off". That direction
    is not arbitrary: guessing wrong towards *on* would put notifications on a
    screen because a config file had a typo in it, which is precisely the
    surprise the ``false`` default exists to prevent.
    """
    try:
        return notifications_enabled()
    except ConfigError as exc:
        print(f"rem-bubbles: {exc}", file=sys.stderr)
        print(
            "rem-bubbles: desktop notifications stay off until the config is fixed",
            file=sys.stderr,
        )
        return NOTIFICATIONS_DEFAULT


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


# -- runtime reminder source ------------------------------------------------


def reminder_file(explicit: Path | str | None = None) -> Path:
    """The one file reminders are read from, highest priority first.

    1. ``explicit`` — a path handed in by a caller.
    2. the file named by ``[reminders].file`` in the user's ``config.toml``.
    3. ``<config dir>/reminders.json`` — the default personal collection.

    The chain stops there. Unlike quotes there is no checkout-local file and no
    ``examples/`` fallback, because a reminder is a claim about the user's own
    life: sample data must never appear as though someone had scheduled it.

    Raises :class:`ConfigError` if ``config.toml`` is malformed.
    """
    if explicit is not None:
        return Path(explicit)
    return read_user_config().reminder_file or default_reminder_file()


def load_reminder_store(explicit: Path | str | None = None) -> ReminderStore:
    """Load the user's reminders, degrading to an empty collection on any fault.

    A missing file is the normal state before the first ``reminder add`` and is
    silent — but only at the default location. A path the user deliberately
    pointed somewhere else and which is not there is worth a line on stderr,
    since it usually means a typo rather than a fresh install.

    Every other failure is reported and then stepped over with an empty store.
    Reminders are an addition to the bubble, not a precondition for it: broken
    reminder data must never cost the user their quotes.
    """
    try:
        path = reminder_file(explicit)
        default = default_reminder_file()
    except ConfigError as exc:
        print(f"rem-bubbles: {exc}", file=sys.stderr)
        print(
            "rem-bubbles: ignoring the configured reminder path, starting with "
            "no reminders",
            file=sys.stderr,
        )
        return ReminderStore.empty()

    if not path.is_file():
        if path != default:
            print(
                f"rem-bubbles: configured reminder file not found: {path}",
                file=sys.stderr,
            )
            print("rem-bubbles: starting with no reminders", file=sys.stderr)
        return ReminderStore.empty(path)

    try:
        return ReminderStore.from_file(path)
    except ReminderStoreError as exc:
        print(f"rem-bubbles: {exc}", file=sys.stderr)
        print("rem-bubbles: starting with no reminders", file=sys.stderr)
        return ReminderStore.empty(path)
