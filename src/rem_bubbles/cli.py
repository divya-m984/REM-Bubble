"""The ``rem-bubbles`` command line: quote and reminder management, plus the GUI.

This module must stay importable on a machine with no display server, so it
imports no GTK at module level — not ``gi``, not ``Gtk``, not
``Gtk4LayerShell``. Only :func:`run_gui` imports :mod:`rem_bubbles.app`, and it
does so inside the function body, which keeps that module's
``CDLL("libgtk4-layer-shell.so")`` running before anything pulls in libwayland.

Everything here operates on the user's personal files — the ones named by
``[quotes].file`` and ``[reminders].file`` in their ``config.toml``, or the
default XDG locations. Repository data (``examples/``, a checkout-local
``quotes.json``) is read-only fallback for the running application and is never
written to; ``examples/reminders.json`` is not even read.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import sys
import sysconfig
import unicodedata
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from rem_bubbles import __version__
from rem_bubbles.config import (
    DEFAULT_CONFIG_TEXT,
    ConfigError,
    UserConfig,
    managed_quote_file,
    managed_reminder_file,
    quote_file_candidates,
    read_user_config,
    user_config_dir,
    user_config_file,
)
from rem_bubbles.persistence import PRIVATE_DIR_MODE, write_text_atomic
from rem_bubbles.quote_store import (
    Quote,
    QuoteStoreError,
    load_quotes,
    write_quotes,
)
from rem_bubbles.reminder_store import (
    NONE,
    RECURRENCES,
    STATUS_OVERDUE,
    Reminder,
    ReminderStore,
    ReminderStoreError,
    load_reminders,
    parse_local_datetime,
    write_reminders,
)

__all__ = [
    "build_parser",
    "console_script_path",
    "generate_id",
    "hyprland_autostart_line",
    "main",
    "slugify",
]

PROGRAM = "rem-bubbles"

#: Longest generated slug, before any de-duplication suffix. Long enough to stay
#: recognisable, short enough to type back for ``remove`` / ``enable``.
MAX_SLUG_LENGTH = 48

ENABLED_MARK = "✓"  # ✓
DISABLED_MARK = "○"  # ○

#: Reminder list markers: waiting for you, recurring, ordinary.
DUE_MARK = "!"
RECURRING_MARK = "↻"  # ↻
PENDING_MARK = "·"  # ·


# -- identifiers ------------------------------------------------------------


def slugify(text: str) -> str:
    """Return a lowercase ASCII slug for ``text``, or "" if none can be made.

    Accents are folded rather than dropped (``café`` → ``cafe``), so most Latin
    text still yields something readable. Scripts with no ASCII equivalent —
    Japanese, Cyrillic, emoji — legitimately reduce to nothing; that is the
    caller's cue to fall back to a digest, not a reason to reject the quote.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    ascii_only = folded.encode("ascii", "ignore").decode("ascii").lower()

    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")
    if len(slug) > MAX_SLUG_LENGTH:
        clipped = slug[:MAX_SLUG_LENGTH]
        # Prefer cutting at a word boundary, but never return an empty slug
        # just because the first word is longer than the limit.
        slug = clipped.rsplit("-", 1)[0].strip("-") or clipped.strip("-")
    return slug


def digest_id(text: str, prefix: str = "quote") -> str:
    """A stable, readable-enough id for text that has no usable ASCII slug.

    ``prefix`` names the kind of thing, so a reminder written in a script with no
    Latin characters gets ``reminder-a13f84c2`` rather than being mislabelled a
    quote. Everything else about the strategy is shared.
    """
    return f"{prefix}-{sha256(text.encode('utf-8')).hexdigest()[:8]}"


def generate_id(text: str, taken: set[str], prefix: str = "quote") -> str:
    """Derive an id from ``text`` that is not already in ``taken``.

    Deterministic for a given text and collection: the same text added to the
    same file always produces the same id. Collisions get a numeric suffix
    rather than overwriting anything.
    """
    base = slugify(text) or digest_id(text, prefix)
    if base not in taken:
        return base
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    return f"{base}-{suffix}"


# -- shared helpers ---------------------------------------------------------


class CommandError(Exception):
    """A user-facing failure: printed to stderr, exit status 1, nothing written."""


def _config() -> UserConfig:
    """Read ``config.toml``, letting a malformed one abort the command.

    Management commands never guess past a broken config — doing so could write
    a user's quotes to a file they did not choose.
    """
    return read_user_config()


def _target_file() -> Path:
    return managed_quote_file(_config())


def _reminder_target_file() -> Path:
    return managed_reminder_file(_config())


def _pick_id(text: str, explicit: str | None, taken: set[str], kind: str) -> str:
    """The id for a new entry: the one the user asked for, or a generated one.

    Shared by ``quote add`` and ``reminder add`` so both spell "already taken"
    the same way, and so the slug rules only ever have one implementation.
    """
    if explicit is None:
        return generate_id(text, taken, prefix=kind)

    chosen = explicit.strip()
    if not chosen:
        raise CommandError("--id cannot be blank.")
    if chosen in taken:
        raise CommandError(
            f'A {kind} with id "{chosen}" already exists — nothing was changed.\n'
            "Choose a different --id, or omit it to have one generated."
        )
    return chosen


def _read_collection(path: Path) -> list[Quote]:
    """Every quote in ``path``, or [] when the file does not exist yet."""
    if not path.exists():
        return []
    return load_quotes(path)


def _require_collection(path: Path) -> list[Quote]:
    if not path.exists():
        raise CommandError(
            f"No personal quote file yet: {path}\n"
            f'Add the first quote with: {PROGRAM} quote add "Your quote here."'
        )
    return load_quotes(path)


def _find(quotes: list[Quote], quote_id: str) -> int:
    """Index of the quote with this exact id, or a CommandError naming it."""
    for index, quote in enumerate(quotes):
        if quote.id == quote_id:
            return index
    raise CommandError(
        f'No quote with id "{quote_id}".\n'
        f"List the ids you have with: {PROGRAM} quote list"
    )


def _enabled_count(quotes: list[Quote]) -> int:
    return sum(1 for quote in quotes if quote.enabled)


def _describe(quote: Quote) -> str:
    mark = ENABLED_MARK if quote.enabled else DISABLED_MARK
    lines = [f"{mark} {quote.id}", f"  {quote.text}"]
    if quote.author:
        lines.append(f"  — {quote.author}")  # —
    return "\n".join(lines)


def _ensure_config_dir() -> Path:
    """Create the configuration directory, user-private, if it is missing."""
    directory = user_config_dir()
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
        # mkdir's mode is masked by the umask; be explicit about personal data.
        os.chmod(directory, PRIVATE_DIR_MODE)
    return directory


# -- commands ---------------------------------------------------------------


def cmd_init(_args: argparse.Namespace) -> int:
    """Create the configuration directory and ``config.toml`` if absent.

    Never overwrites anything, so running it twice is safe — including a config
    written before reminders or notifications existed, which is left exactly as
    it is. All three tables are optional: ``[quotes]`` and ``[reminders]``
    default to the file beside the config and ``[notifications]`` defaults to
    off, so an untouched Milestone 3 or Milestone 4 config is already complete
    and adding the new table to it would change nothing.

    Neither data file is created here: leaving those to the first ``quote add``
    and ``reminder add`` means each collection only ever contains entries the
    user chose.
    """
    config_path = user_config_file()

    if config_path.exists():
        # Validate before touching anything, so a broken config fails cleanly.
        config = read_user_config(config_path)
        print(f"Config already exists, left unchanged: {config_path}")
    else:
        _ensure_config_dir()
        write_text_atomic(config_path, DEFAULT_CONFIG_TEXT)
        print(f"Created {config_path}")
        config = read_user_config(config_path)

    target = managed_quote_file(config)
    if target.exists():
        print(f"Personal quote file: {target}")
    else:
        print(f"No personal quote file yet: {target}")
        print(f'It is created by your first quote: {PROGRAM} quote add "Your quote here."')

    reminders = managed_reminder_file(config)
    if reminders.exists():
        print(f"Personal reminder file: {reminders}")
    else:
        print(f"No personal reminder file yet: {reminders}")
        print(
            f"It is created by your first reminder: {PROGRAM} reminder add "
            '"Do the thing." --at "2026-08-30 18:00"'
        )
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    """Show the personal collection, without creating or modifying anything."""
    path = _target_file()
    print(f"Quote file: {path}")

    if not path.exists():
        print()
        print("No personal quote file yet — nothing to list.")
        print(f'Add the first quote with: {PROGRAM} quote add "Your quote here."')
        return 0

    quotes = load_quotes(path)
    if not quotes:
        print()
        print("The quote file is empty.")
        print(f'Add a quote with: {PROGRAM} quote add "Your quote here."')
        return 0

    for quote in quotes:
        print()
        print(_describe(quote))

    enabled = _enabled_count(quotes)
    print()
    print(
        f"{len(quotes)} quote{'s' if len(quotes) != 1 else ''} "
        f"({enabled} enabled, {len(quotes) - enabled} disabled)"
    )
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    """Append a quote, creating the config directory and file when needed."""
    text = args.text.strip()
    if not text:
        raise CommandError("The quote text is empty.")

    path = _target_file()
    quotes = _read_collection(path)
    taken = {quote.id for quote in quotes}

    quote_id = _pick_id(text, args.id, taken, "quote")

    author = args.author.strip() if args.author else None
    enabled = not args.disabled

    if not enabled and _enabled_count(quotes) == 0:
        raise CommandError(
            "Refusing to add a disabled quote as the only quote: at least one "
            "quote must remain enabled for the bubble to have something to show."
        )

    quotes.append(Quote(id=quote_id, text=text, author=author or None, enabled=enabled))

    _ensure_config_dir()
    write_quotes(path, quotes)

    state = "enabled" if enabled else "disabled"
    print(f'Added "{quote_id}" ({state}) to {path}')
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    """Delete one quote by exact id, refusing to empty the rotation."""
    path = _target_file()
    quotes = _require_collection(path)
    index = _find(quotes, args.id)

    remaining = quotes[:index] + quotes[index + 1 :]
    if _enabled_count(remaining) == 0:
        raise CommandError(
            f'Cannot remove "{args.id}": at least one quote must remain enabled.'
        )

    write_quotes(path, remaining)
    print(f'Removed "{args.id}" from {path}')
    return 0


def _set_enabled(path: Path, quotes: list[Quote], index: int, enabled: bool) -> None:
    """Replace one entry in place, leaving order and every other field alone."""
    quotes[index] = Quote(
        id=quotes[index].id,
        text=quotes[index].text,
        author=quotes[index].author,
        enabled=enabled,
    )
    write_quotes(path, quotes)


def cmd_enable(args: argparse.Namespace) -> int:
    path = _target_file()
    quotes = _require_collection(path)
    index = _find(quotes, args.id)

    if quotes[index].enabled:
        # Already in the requested state: report it and leave the file alone
        # rather than rewriting it for no reason.
        print(f'"{args.id}" is already enabled — nothing to do.')
        return 0

    _set_enabled(path, quotes, index, True)
    print(f'Enabled "{args.id}" in {path}')
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    path = _target_file()
    quotes = _require_collection(path)
    index = _find(quotes, args.id)

    if not quotes[index].enabled:
        print(f'"{args.id}" is already disabled — nothing to do.')
        return 0

    if _enabled_count(quotes) <= 1:
        raise CommandError(
            f'Cannot disable "{args.id}": at least one quote must remain enabled.'
        )

    _set_enabled(path, quotes, index, False)
    print(f'Disabled "{args.id}" in {path}')
    return 0


# -- reminders --------------------------------------------------------------


def parse_due_at(text: str) -> datetime:
    """Parse a ``--at`` value into a local wall-clock datetime.

    ``2026-08-30 18:00`` and ``2026-08-30T18:00`` are the documented forms, with
    seconds optional. Anything else — a bare date, a timezone offset, "tomorrow"
    — is refused with an explanation. Guessing at a reminder's time is the one
    mistake a reminder tool must never make, and natural-language parsing is
    deliberately out of scope for this milestone.
    """
    try:
        return parse_local_datetime(text)
    except ValueError as exc:
        raise CommandError(
            f"{exc}.\nGive a date and a time, for example: --at \"2026-08-30 18:00\""
        ) from exc


def _read_reminders(path: Path) -> list[Reminder]:
    """Every reminder in ``path``, or [] when the file does not exist yet."""
    if not path.exists():
        return []
    return load_reminders(path)


def _require_reminders(path: Path) -> list[Reminder]:
    if not path.exists():
        raise CommandError(
            f"No personal reminder file yet: {path}\n"
            f'Add the first reminder with: {PROGRAM} reminder add "Do the thing." '
            '--at "2026-08-30 18:00"'
        )
    return load_reminders(path)


def _find_reminder(reminders: list[Reminder], reminder_id: str) -> int:
    for index, reminder in enumerate(reminders):
        if reminder.id == reminder_id:
            return index
    raise CommandError(
        f'No reminder with id "{reminder_id}".\n'
        f"List the ids you have with: {PROGRAM} reminder list"
    )


def _human_time(value: datetime) -> str:
    """A reminder time as a person would read it: ``2026-08-30 18:00``."""
    return value.strftime("%Y-%m-%d %H:%M")


def _describe_reminder(store: ReminderStore, reminder: Reminder, now: datetime) -> str:
    """One reminder as three or four lines: id, text, schedule, status."""
    status = store.status(reminder, now)
    occurrence = store.occurrence(reminder, now) or reminder.due_at

    if status in ("due", STATUS_OVERDUE):
        mark = DUE_MARK
    elif reminder.recurrence != NONE:
        mark = RECURRING_MARK
    else:
        mark = PENDING_MARK

    schedule = _human_time(occurrence)
    if reminder.recurrence != NONE:
        schedule = f"{reminder.recurrence.capitalize()} · {schedule}"  # ·

    lines = [
        f"{mark} {reminder.id}",
        f"  {reminder.text}",
        f"  Due: {schedule}",
        f"  Status: {status}",
    ]
    if status == "snoozed" and reminder.snoozed_until is not None:
        lines.append(f"  Snoozed until: {_human_time(reminder.snoozed_until)}")
    return "\n".join(lines)


def cmd_reminder_list(_args: argparse.Namespace) -> int:
    """Show the personal reminders, without creating or modifying anything."""
    path = _reminder_target_file()
    print(f"Reminder file: {path}")

    if not path.exists():
        print()
        print("No reminders yet.")
        print(
            f'Add the first one with: {PROGRAM} reminder add "Do the thing." '
            '--at "2026-08-30 18:00"'
        )
        return 0

    reminders = load_reminders(path)
    if not reminders:
        print()
        print("No reminders yet.")
        return 0

    # One "now" for the whole listing, so two reminders either side of a minute
    # boundary cannot be described inconsistently.
    now = datetime.now()
    store = ReminderStore(reminders, str(path), path)
    for reminder in reminders:
        print()
        print(_describe_reminder(store, reminder, now))

    enabled = sum(1 for reminder in reminders if reminder.enabled)
    waiting = len(store.due_reminders(now))
    print()
    print(
        f"{len(reminders)} reminder{'s' if len(reminders) != 1 else ''} "
        f"({enabled} enabled, {len(reminders) - enabled} disabled, {waiting} due)"
    )
    return 0


def cmd_reminder_add(args: argparse.Namespace) -> int:
    """Append a reminder, creating the config directory and file when needed."""
    text = args.text.strip()
    if not text:
        raise CommandError("The reminder text is empty.")

    due_at = parse_due_at(args.at)

    path = _reminder_target_file()
    reminders = _read_reminders(path)
    taken = {reminder.id for reminder in reminders}

    reminder_id = _pick_id(text, args.id, taken, "reminder")

    reminders.append(
        Reminder(
            id=reminder_id,
            text=text,
            due_at=due_at,
            recurrence=args.repeat,
            enabled=not args.disabled,
        )
    )

    _ensure_config_dir()
    write_reminders(path, reminders)

    state = "disabled" if args.disabled else "enabled"
    schedule = _human_time(due_at)
    if args.repeat != NONE:
        schedule = f"{schedule}, repeating {args.repeat}"
    print(f'Added "{reminder_id}" ({state}) for {schedule} to {path}')
    # A time already gone is allowed on purpose — it simply shows up overdue.
    if due_at < datetime.now():
        print("That time has already passed, so it is due now.")
    return 0


def cmd_reminder_remove(args: argparse.Namespace) -> int:
    """Delete one reminder by exact id. Emptying the collection is fine."""
    path = _reminder_target_file()
    reminders = _require_reminders(path)
    index = _find_reminder(reminders, args.id)

    write_reminders(path, reminders[:index] + reminders[index + 1 :])
    print(f'Removed "{args.id}" from {path}')
    return 0


def _set_reminder_enabled(
    path: Path, reminders: list[Reminder], index: int, enabled: bool
) -> None:
    """Replace one entry in place, leaving order and every other field alone.

    ``snoozed_until`` and ``dismissed_occurrence`` are carried across
    deliberately: disabling is a pause, not a reset, so re-enabling resumes the
    schedule exactly where it was rather than replaying an occurrence the user
    already dealt with.
    """
    current = reminders[index]
    reminders[index] = Reminder(
        id=current.id,
        text=current.text,
        due_at=current.due_at,
        recurrence=current.recurrence,
        enabled=enabled,
        snoozed_until=current.snoozed_until,
        dismissed_occurrence=current.dismissed_occurrence,
    )
    write_reminders(path, reminders)


def cmd_reminder_enable(args: argparse.Namespace) -> int:
    path = _reminder_target_file()
    reminders = _require_reminders(path)
    index = _find_reminder(reminders, args.id)

    if reminders[index].enabled:
        print(f'"{args.id}" is already enabled — nothing to do.')
        return 0

    _set_reminder_enabled(path, reminders, index, True)
    print(f'Enabled "{args.id}" in {path}')
    return 0


def cmd_reminder_disable(args: argparse.Namespace) -> int:
    path = _reminder_target_file()
    reminders = _require_reminders(path)
    index = _find_reminder(reminders, args.id)

    if not reminders[index].enabled:
        print(f'"{args.id}" is already disabled — nothing to do.')
        return 0

    # Unlike quotes there is no "one must stay enabled" rule: a collection with
    # nothing enabled simply means nothing is scheduled, which is a valid way to
    # use the application.
    _set_reminder_enabled(path, reminders, index, False)
    print(f'Disabled "{args.id}" in {path}')
    return 0


# -- desktop integration ----------------------------------------------------


def console_script_path() -> Path | None:
    """The absolute path of the ``rem-bubbles`` executable, or None.

    Autostart cannot assume the user's Hyprland session activates a virtual
    environment, so ``exec-once = rem-bubbles`` is not good enough: the line has
    to name the installation that is actually running. Three sources are tried,
    most direct first.

    1. ``sys.argv[0]``, when this process *was* started as the console script.
       Nothing describes "the installation that is running" better than the file
       that is running.
    2. the script directory of the interpreter executing us, which is the right
       answer for ``python -m rem_bubbles.cli`` and for any virtual environment.
    3. whatever ``PATH`` resolves, as a last resort.

    Paths are made absolute without resolving symlinks: a venv reached through a
    symlinked directory should keep the name the user knows it by. None means
    "could not be determined", and the caller says so rather than printing a
    line that would not work.
    """
    candidates: list[Path] = []

    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 and Path(argv0).name == PROGRAM:
        candidates.append(Path(os.path.abspath(argv0)))

    scripts = sysconfig.get_path("scripts")
    if scripts:
        candidates.append(Path(os.path.abspath(scripts)) / PROGRAM)

    found = shutil.which(PROGRAM)
    if found:
        candidates.append(Path(os.path.abspath(found)))

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def hyprland_autostart_line(executable: Path | str) -> str:
    """The ``exec-once`` line for this installation.

    ``exec-once`` is handed to a shell, so a path containing spaces has to be
    quoted or Hyprland would try to run the first word and pass the rest as
    arguments. :func:`shlex.quote` leaves an ordinary path untouched.
    """
    return f"exec-once = {shlex.quote(str(executable))}"


def cmd_integration_hyprland(_args: argparse.Namespace) -> int:
    """Print the recommended Hyprland autostart line. Writes nothing.

    Deliberately print-only. Editing somebody's compositor configuration is not
    a thing a quote bubble should do behind their back, and a session that will
    not start because a tool appended a line to ``hyprland.conf`` is a far worse
    outcome than a line the user pastes themselves. There is no ``--install``,
    nothing is reloaded, and ``hyprctl`` is never called.
    """
    executable = console_script_path()
    if executable is None:
        raise CommandError(
            "Could not work out where the rem-bubbles executable lives, so "
            "there is no autostart line worth printing.\n"
            "Install it with 'pip install -e .' (or 'pip install rem-bubbles') "
            "and run this again."
        )

    print("REM Bubbles Hyprland autostart:")
    print()
    print(f"    {hyprland_autostart_line(executable)}")
    print()
    print(
        "Add that line to your Hyprland configuration, then start a new "
        "Hyprland session."
    )
    print("Nothing was written — your Hyprland configuration is unchanged.")
    return 0


# -- doctor -----------------------------------------------------------------

#: Environment variables ``doctor`` reports on, and how much of each it shows.
#: The Hyprland signature is a session identifier rather than a setting, so its
#: presence is reported but its value is not printed.
_WAYLAND_VAR = "WAYLAND_DISPLAY"
_HYPRLAND_VAR = "HYPRLAND_INSTANCE_SIGNATURE"


def _doctor_line(label: str, value: str) -> str:
    return f"  {label:<14} {value}"


def _doctor_config(problems: list[str]) -> UserConfig | None:
    """Report on ``config.toml``. None means it could not be understood."""
    path = user_config_file()
    if not path.exists():
        print(_doctor_line("Config:", f"{path} (not created yet)"))
        print(_doctor_line("", f"Create it with: {PROGRAM} init"))
        return UserConfig(path=path, exists=False)

    try:
        config = read_user_config(path)
    except ConfigError as exc:
        print(_doctor_line("Config:", f"{path} (MALFORMED)"))
        print(_doctor_line("", str(exc)))
        problems.append("config.toml could not be parsed")
        return None

    print(_doctor_line("Config:", f"{path} (parses)"))
    return config


def _doctor_quotes(config: UserConfig | None, problems: list[str]) -> None:
    """Report on the personal quote file, without printing any quote."""
    if config is None:
        print(_doctor_line("Quotes:", "unknown — the config could not be parsed"))
        return

    path = managed_quote_file(config)
    if not path.exists():
        # Not a fault. The runtime chain falls through to a checkout-local
        # collection, then the tracked examples, then one built-in quote, so the
        # bubble always has something to show.
        fallbacks = [candidate for candidate in quote_file_candidates() if candidate != path]
        source = str(fallbacks[0]) if fallbacks else "the built-in quote"
        print(_doctor_line("Quotes:", f"{path} (not created yet)"))
        print(_doctor_line("", f"Falling back to: {source}"))
        return

    try:
        quotes = load_quotes(path)
    except QuoteStoreError as exc:
        print(_doctor_line("Quotes:", f"{path} (MALFORMED)"))
        print(_doctor_line("", str(exc)))
        problems.append("the quote file could not be parsed")
        return

    enabled = _enabled_count(quotes)
    print(_doctor_line("Quotes:", f"{path} ({len(quotes)} total, {enabled} enabled)"))
    if enabled == 0 and quotes:
        print(_doctor_line("", "No quote is enabled — the bubble has nothing to show."))
        problems.append("no quote is enabled")


def _doctor_reminders(config: UserConfig | None, problems: list[str]) -> None:
    """Report on the personal reminder file, without printing any reminder."""
    if config is None:
        print(_doctor_line("Reminders:", "unknown — the config could not be parsed"))
        return

    path = managed_reminder_file(config)
    if not path.exists():
        # Having no reminders is a normal way to use REM Bubbles.
        print(_doctor_line("Reminders:", f"{path} (none yet)"))
        return

    try:
        reminders = load_reminders(path)
    except ReminderStoreError as exc:
        print(_doctor_line("Reminders:", f"{path} (MALFORMED)"))
        print(_doctor_line("", str(exc)))
        problems.append("the reminder file could not be parsed")
        return

    store = ReminderStore(reminders, str(path), path)
    enabled = sum(1 for reminder in reminders if reminder.enabled)
    due = len(store.due_reminders(datetime.now()))
    print(
        _doctor_line(
            "Reminders:",
            f"{path} ({len(reminders)} total, {enabled} enabled, {due} due)",
        )
    )


def _doctor_session() -> None:
    """Report the Wayland session variables, without initialising GTK.

    Reading two environment variables is the whole test. Actually opening a
    display to find out would mean importing GTK, which would cost ``doctor``
    the one property that makes it useful when things are broken: it runs
    anywhere, including over SSH with no compositor at all.
    """
    # Not column-aligned with the block above: these names are long enough that
    # padding to them would leave the whole report mostly whitespace.
    display = os.environ.get(_WAYLAND_VAR, "")
    print(f"  {_WAYLAND_VAR}: {display or 'not set (no Wayland session)'}")
    # The signature is a session identifier, not a setting; whether it is there
    # is the diagnostic, and printing it would put a session token in a log.
    signature = os.environ.get(_HYPRLAND_VAR, "")
    print(f"  {_HYPRLAND_VAR}: {'set' if signature else 'not set'}")


def cmd_doctor(_args: argparse.Namespace) -> int:
    """Explain whether this REM Bubbles environment makes sense.

    Headless by construction: it imports no GTK, opens no window and touches no
    display, so it keeps working in exactly the situation where a diagnostic
    matters most. It also writes nothing — not the config directory, not a data
    file — so running it can never be what "fixed" a problem.

    Counts are reported, never contents. Somebody's quotes and reminders are
    personal, and a diagnostic is the kind of output that gets pasted into a bug
    report.
    """
    problems: list[str] = []

    print("REM Bubbles doctor")
    print()
    print(_doctor_line("Version:", f"{PROGRAM} {__version__}"))
    print(_doctor_line("Python:", sys.version.split()[0]))
    executable = console_script_path()
    print(_doctor_line("Executable:", str(executable) if executable else "not found"))
    print(_doctor_line("Config dir:", str(user_config_dir())))
    print()

    config = _doctor_config(problems)
    _doctor_quotes(config, problems)
    _doctor_reminders(config, problems)
    notifications = "enabled" if (config is not None and config.notifications) else "disabled"
    if config is None:
        notifications = "disabled (the config could not be parsed)"
    print(_doctor_line("Notifications:", notifications))
    print()

    _doctor_session()
    print()

    if problems:
        for problem in problems:
            print(f"Problem: {problem}", file=sys.stderr)
        return 1

    print("No problems found.")
    return 0


# -- GUI --------------------------------------------------------------------


def run_gui() -> int:
    """Launch the bubble.

    The import is deliberately inside the function: :mod:`rem_bubbles.app`
    loads libgtk4-layer-shell and then GTK, which must not happen for a
    headless command. Only the program name is passed on, so a subcommand of
    ours is never handed to ``Gtk.Application.run``.
    """
    from rem_bubbles.app import main as app_main

    return app_main([sys.argv[0]])


# -- parser -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description=(
            "A tiny persistent desktop quote and reminder companion. "
            "Run with no command to launch the bubble."
        ),
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {__version__}")

    commands = parser.add_subparsers(dest="command", metavar="command")

    commands.add_parser("gui", help="launch the bubble (the default)")
    commands.add_parser(
        "init",
        help="create the configuration directory and config.toml",
        description=(
            "Create ~/.config/rem-bubbles/ and a default config.toml. Existing "
            "files are never overwritten; the quote and reminder files are "
            "created by your first 'quote add' and 'reminder add'."
        ),
    )
    commands.add_parser(
        "doctor",
        help="check the configuration and session, changing nothing",
        description=(
            "Report where REM Bubbles reads its configuration and data from and "
            "whether all of it parses. Never opens a window, never imports GTK "
            "and never writes anything. Counts are shown, never the contents of "
            "your quotes or reminders. Exits 1 if something is malformed."
        ),
    )

    integration = commands.add_parser(
        "integration",
        help="print desktop autostart configuration",
        description=(
            "Print the configuration needed to start REM Bubbles with your "
            "desktop session. Nothing is ever written or reloaded — the "
            "snippet is yours to add."
        ),
    )
    integration_actions = integration.add_subparsers(
        dest="integration_command", metavar="target", required=True
    )
    integration_actions.add_parser(
        "hyprland",
        help="print the exec-once line for hyprland.conf",
        description=(
            "Print an 'exec-once' line naming this installation's executable. "
            "It is printed only: your Hyprland configuration is not edited and "
            "Hyprland is not reloaded."
        ),
    )

    quote = commands.add_parser("quote", help="manage your personal quotes")
    actions = quote.add_subparsers(dest="quote_command", metavar="action", required=True)

    actions.add_parser("list", help="show your personal quotes")

    add = actions.add_parser("add", help="add a quote")
    add.add_argument("text", help="the quote text")
    add.add_argument("--author", help="who said it (optional)")
    add.add_argument("--id", help="explicit id (default: generated from the text)")
    add.add_argument(
        "--disabled",
        action="store_true",
        help="add the quote without putting it into the rotation",
    )

    for name, help_text in (
        ("remove", "delete a quote by its exact id"),
        ("enable", "put a quote back into the rotation"),
        ("disable", "take a quote out of the rotation, keeping it in the file"),
    ):
        action = actions.add_parser(name, help=help_text)
        action.add_argument("id", help="the exact quote id, as shown by 'quote list'")

    reminder = commands.add_parser("reminder", help="manage your personal reminders")
    reminder_actions = reminder.add_subparsers(
        dest="reminder_command", metavar="action", required=True
    )

    reminder_actions.add_parser("list", help="show your personal reminders")

    reminder_add = reminder_actions.add_parser("add", help="add a reminder")
    reminder_add.add_argument("text", help="what to be reminded of")
    reminder_add.add_argument(
        "--at",
        required=True,
        metavar="WHEN",
        help='when it is due, local time: "2026-08-30 18:00" or "2026-08-30T18:00"',
    )
    reminder_add.add_argument(
        "--repeat",
        choices=RECURRENCES,
        default=NONE,
        help="how often it comes back (default: none)",
    )
    reminder_add.add_argument("--id", help="explicit id (default: generated from the text)")
    reminder_add.add_argument(
        "--disabled",
        action="store_true",
        help="add the reminder without scheduling it",
    )

    for name, help_text in (
        ("remove", "delete a reminder by its exact id"),
        ("enable", "schedule a reminder again"),
        ("disable", "stop scheduling a reminder, keeping it in the file"),
    ):
        action = reminder_actions.add_parser(name, help=help_text)
        action.add_argument(
            "id", help="the exact reminder id, as shown by 'reminder list'"
        )

    return parser


_QUOTE_HANDLERS = {
    "list": cmd_list,
    "add": cmd_add,
    "remove": cmd_remove,
    "enable": cmd_enable,
    "disable": cmd_disable,
}

_REMINDER_HANDLERS = {
    "list": cmd_reminder_list,
    "add": cmd_reminder_add,
    "remove": cmd_reminder_remove,
    "enable": cmd_reminder_enable,
    "disable": cmd_reminder_disable,
}

_INTEGRATION_HANDLERS = {
    "hyprland": cmd_integration_hyprland,
}

#: Commands with no sub-action of their own.
_HANDLERS = {
    "init": cmd_init,
    "doctor": cmd_doctor,
}


def _handler(args: argparse.Namespace):
    if args.command == "quote":
        return _QUOTE_HANDLERS[args.quote_command]
    if args.command == "reminder":
        return _REMINDER_HANDLERS[args.reminder_command]
    if args.command == "integration":
        return _INTEGRATION_HANDLERS[args.integration_command]
    return _HANDLERS[args.command]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)

    if args.command in (None, "gui"):
        return run_gui()

    try:
        return _handler(args)(args)
    except (CommandError, ConfigError, QuoteStoreError, ReminderStoreError) as exc:
        print(f"{PROGRAM}: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        # Permissions, a full disk, a read-only mount: report, do not traceback.
        location = getattr(exc, "filename", None) or "the file"
        print(f"{PROGRAM}: could not write {location}: {exc.strerror}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
