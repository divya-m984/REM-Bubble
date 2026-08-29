"""Reminder data: parsing, validation, scheduling, snooze, dismissal, storage.

Like :mod:`rem_bubbles.quote_store` this module holds no widgets and imports no
``gi``, so the whole scheduling model is unit-testable without a display server.
The graphical layer asks a :class:`ReminderStore` *what is due now* and renders
the answer; it never does calendar arithmetic and never touches JSON.

Times are **local wall-clock** and stored as naive ISO datetimes
(``2026-08-30T18:00:00``). A daily reminder at 08:00 is at 08:00 whatever the
date, which is what "every morning" means to a person; naive arithmetic gives
exactly that. Timezone-aware values are rejected rather than converted, because
silently moving a user's 18:00 to 17:00 would be worse than refusing to load.

Two rules distinguish reminders from quotes:

* an empty collection is completely valid — there is no "at least one enabled"
  invariant, because having no reminders is a normal state;
* nothing in the repository is ever a runtime fallback. A user must never be
  shown an example reminder as though it were their own.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from rem_bubbles.persistence import write_text_atomic

__all__ = [
    "NONE",
    "DAILY",
    "OVERDUE_AFTER",
    "RECURRENCES",
    "SNOOZE",
    "STATUS_DISMISSED",
    "STATUS_DISABLED",
    "STATUS_DUE",
    "STATUS_OVERDUE",
    "STATUS_SNOOZED",
    "STATUS_UPCOMING",
    "WEEKLY",
    "Reminder",
    "ReminderStore",
    "ReminderStoreError",
    "current_occurrence",
    "decode_reminders",
    "format_datetime",
    "load_reminders",
    "next_occurrence",
    "parse_local_datetime",
    "parse_reminders",
    "reminder_to_dict",
    "reminders_to_json",
    "write_reminders",
]

# -- recurrence -------------------------------------------------------------

NONE = "none"
DAILY = "daily"
WEEKLY = "weekly"

#: Every recurrence this milestone understands. Deliberately short: monthly,
#: yearly and cron-style rules are not implemented.
RECURRENCES: tuple[str, ...] = (NONE, DAILY, WEEKLY)

_STEPS: dict[str, timedelta] = {
    DAILY: timedelta(days=1),
    WEEKLY: timedelta(days=7),
}

# -- states -----------------------------------------------------------------

STATUS_UPCOMING = "upcoming"
STATUS_DUE = "due"
STATUS_OVERDUE = "overdue"
STATUS_SNOOZED = "snoozed"
STATUS_DISMISSED = "dismissed"
STATUS_DISABLED = "disabled"

#: The fixed snooze interval. Not configurable in this milestone.
SNOOZE = timedelta(minutes=10)

#: How long after its occurrence a reminder still reads as "due" rather than
#: "overdue". The scheduler only looks every 30 seconds, so without a small
#: grace window every reminder would be reported overdue the moment it fired.
OVERDUE_AFTER = timedelta(minutes=1)


class ReminderStoreError(Exception):
    """Raised for any unusable reminder source: missing or malformed.

    Carries a message meant to be shown to a human as-is, so neither the CLI nor
    the bubble ever has to surface a traceback for ordinary bad data.
    """


# -- datetimes --------------------------------------------------------------

#: Accepted local wall-clock form: a date, a separator, and at least hh:mm.
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?$")

#: A trailing UTC designator or numeric offset, detected only so that it can be
#: rejected with an explanation rather than a generic "malformed" message.
_ZONE_RE = re.compile(r"(Z|z|[+-]\d{2}:?\d{2})$")

#: What a value is normalised to on disk and in messages.
DATETIME_FORMAT = "YYYY-MM-DDTHH:MM:SS"


def parse_local_datetime(text: str) -> datetime:
    """Parse a local wall-clock datetime, raising ValueError with a reason.

    Accepts ``2026-08-30 18:00``, ``2026-08-30T18:00`` and either with seconds.
    A date on its own is refused: assuming midnight would be guessing at a time
    the user did not write.
    """
    candidate = text.strip()
    if not candidate:
        raise ValueError("the time is empty")
    if _ZONE_RE.search(candidate):
        raise ValueError(
            "reminder times are local wall-clock times, so a timezone offset "
            f"is not supported here — write it as {DATETIME_FORMAT}"
        )
    if not _DATETIME_RE.match(candidate):
        raise ValueError(
            f'"{candidate}" is not a date and time — expected {DATETIME_FORMAT}, '
            'for example "2026-08-30 18:00"'
        )
    try:
        return datetime.fromisoformat(candidate.replace(" ", "T"))
    except ValueError as exc:
        raise ValueError(f'"{candidate}" is not a real date and time ({exc})') from exc


def format_datetime(value: datetime) -> str:
    """Render a datetime in the canonical on-disk form, to the second."""
    return value.replace(microsecond=0).isoformat(sep="T", timespec="seconds")


# -- model ------------------------------------------------------------------


@dataclass(frozen=True)
class Reminder:
    """One reminder and everything remembered about its current occurrence.

    ``snoozed_until`` and ``dismissed_occurrence`` are the only mutable-feeling
    fields, and both are replaced rather than edited: the store swaps in a new
    frozen instance once the change has reached disk.
    """

    id: str
    text: str
    due_at: datetime
    recurrence: str = NONE
    enabled: bool = True
    snoozed_until: datetime | None = None
    dismissed_occurrence: datetime | None = None


# -- scheduling -------------------------------------------------------------


def current_occurrence(reminder: Reminder, now: datetime) -> datetime | None:
    """The most recent scheduled occurrence at or before ``now``, or None.

    None means the reminder has not come round yet. For a recurring reminder
    that was missed while the application was closed, this collapses the whole
    missed history to a single occurrence: a daily 08:00 reminder first seen on
    Thursday at 10:00 has one active occurrence, Thursday 08:00, not one per
    skipped day. A backlog of identical cards helps nobody.
    """
    if now < reminder.due_at:
        return None
    step = _STEPS.get(reminder.recurrence)
    if step is None:
        return reminder.due_at
    # Naive arithmetic keeps the wall-clock time of day fixed across the span.
    elapsed = now - reminder.due_at
    return reminder.due_at + step * (elapsed // step)


def next_occurrence(reminder: Reminder, now: datetime) -> datetime | None:
    """The first scheduled occurrence strictly after ``now``, or None.

    A non-recurring reminder whose time has passed has no next occurrence.
    """
    if now < reminder.due_at:
        return reminder.due_at
    step = _STEPS.get(reminder.recurrence)
    if step is None:
        return None
    current = current_occurrence(reminder, now)
    assert current is not None  # now >= due_at, so there is always one
    return current + step


# -- parsing / validation ---------------------------------------------------


def _where(source: str | None) -> str:
    return f" in {source}" if source else ""


def _parse_datetime_field(
    entry: dict[str, Any],
    field: str,
    at: str,
    *,
    required: bool,
) -> datetime | None:
    if field not in entry or entry[field] is None:
        if required:
            raise ReminderStoreError(f'{at} is missing the required "{field}" field.')
        return None

    raw = entry[field]
    if not isinstance(raw, str):
        raise ReminderStoreError(
            f'{at} has a non-string "{field}" field: expected a '
            f"{DATETIME_FORMAT} string"
            f'{"" if required else " or null"}.'
        )
    try:
        return parse_local_datetime(raw)
    except ValueError as exc:
        raise ReminderStoreError(f'{at} has an invalid "{field}" field: {exc}.') from exc


def _parse_one(entry: Any, index: int, source: str | None) -> Reminder:
    at = f"Reminder at index {index}{_where(source)}"

    if not isinstance(entry, dict):
        raise ReminderStoreError(f"{at} is not a JSON object.")

    if "id" not in entry:
        raise ReminderStoreError(f'{at} is missing the required "id" field.')
    raw_id = entry["id"]
    if not isinstance(raw_id, str):
        raise ReminderStoreError(f'{at} has a non-string "id" field.')
    reminder_id = raw_id.strip()
    if not reminder_id:
        raise ReminderStoreError(f'{at} has an empty "id" field.')

    # Past the id, name the reminder rather than its position: far easier to
    # find in a file than "index 7".
    at = f'Reminder "{reminder_id}"{_where(source)}'

    if "text" not in entry:
        raise ReminderStoreError(f'{at} is missing the required "text" field.')
    raw_text = entry["text"]
    if not isinstance(raw_text, str):
        raise ReminderStoreError(f'{at} has a non-string "text" field.')
    text = raw_text.strip()
    if not text:
        raise ReminderStoreError(f'{at} has an empty "text" field.')

    due_at = _parse_datetime_field(entry, "due_at", at, required=True)
    assert due_at is not None  # required=True either returns or raises

    raw_recurrence = entry.get("recurrence", NONE)
    if raw_recurrence is None:
        recurrence = NONE
    elif not isinstance(raw_recurrence, str):
        raise ReminderStoreError(
            f'{at} has a non-string "recurrence" field: expected one of '
            f"{', '.join(RECURRENCES)}."
        )
    else:
        recurrence = raw_recurrence.strip().lower()
        if recurrence not in RECURRENCES:
            raise ReminderStoreError(
                f'{at} has an unsupported "recurrence" value "{raw_recurrence}": '
                f"expected one of {', '.join(RECURRENCES)}."
            )

    raw_enabled = entry.get("enabled", True)
    # bool is a subclass of int, so this rejects 0/1 and "true" as intended.
    if not isinstance(raw_enabled, bool):
        raise ReminderStoreError(
            f'{at} has an invalid "enabled" field: expected true or false.'
        )

    snoozed_until = _parse_datetime_field(entry, "snoozed_until", at, required=False)
    dismissed = _parse_datetime_field(entry, "dismissed_occurrence", at, required=False)

    return Reminder(
        id=reminder_id,
        text=text,
        due_at=due_at,
        recurrence=recurrence,
        enabled=raw_enabled,
        snoozed_until=snoozed_until,
        dismissed_occurrence=dismissed,
    )


def parse_reminders(raw: Any, source: str | None = None) -> list[Reminder]:
    """Validate a decoded JSON document and return every reminder it declares.

    An empty array is valid and yields an empty list. Nothing is silently
    dropped: the first problem raises :class:`ReminderStoreError`, because a
    reminder quietly skipped is a reminder that never fires.
    """
    if not isinstance(raw, list):
        raise ReminderStoreError(
            f"The reminder file{_where(source)} must contain a JSON array of "
            f"reminder objects, but the root value is {type(raw).__name__}."
        )

    reminders: list[Reminder] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        reminder = _parse_one(entry, index, source)
        if reminder.id in seen:
            raise ReminderStoreError(
                f'Duplicate reminder id: "{reminder.id}"{_where(source)}'
            )
        seen.add(reminder.id)
        reminders.append(reminder)

    return reminders


def decode_reminders(text: str, source: str | None = None) -> list[Reminder]:
    """Parse JSON ``text`` into reminders."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReminderStoreError(
            f"The reminder file{_where(source)} is not valid JSON: {exc.msg} "
            f"(line {exc.lineno}, column {exc.colno})."
        ) from exc
    return parse_reminders(raw, source)


def load_reminders(path: Path | str) -> list[Reminder]:
    """Read and validate every reminder in ``path``, enabled or not."""
    path = Path(path)
    source = str(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ReminderStoreError(f"Reminder file not found: {source}") from exc
    except OSError as exc:
        raise ReminderStoreError(
            f"Could not read reminder file {source}: {exc.strerror}"
        ) from exc
    return decode_reminders(text, source)


# -- persistence ------------------------------------------------------------


def reminder_to_dict(reminder: Reminder) -> dict[str, Any]:
    """Serialise one reminder in the canonical, fully explicit field order."""
    return {
        "id": reminder.id,
        "text": reminder.text,
        "due_at": format_datetime(reminder.due_at),
        "recurrence": reminder.recurrence,
        "enabled": reminder.enabled,
        "snoozed_until": (
            format_datetime(reminder.snoozed_until) if reminder.snoozed_until else None
        ),
        "dismissed_occurrence": (
            format_datetime(reminder.dismissed_occurrence)
            if reminder.dismissed_occurrence
            else None
        ),
    }


def reminders_to_json(reminders: Sequence[Reminder]) -> str:
    """Render a collection as pretty UTF-8 JSON with a trailing newline.

    ``ensure_ascii=False`` keeps accented and non-Latin reminders readable in
    the file instead of turning them into ``\\uXXXX`` escapes.
    """
    payload = [reminder_to_dict(reminder) for reminder in reminders]
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def write_reminders(path: Path | str, reminders: Sequence[Reminder]) -> None:
    """Persist ``reminders`` to ``path`` atomically, refusing to write bad data.

    The rendered JSON is parsed back before the destination is touched, so a
    collection that would not survive a reload — a duplicate id introduced by a
    caller, say — raises instead of overwriting a working file.
    """
    path = Path(path)
    text = reminders_to_json(reminders)
    decode_reminders(text, str(path))
    write_text_atomic(path, text)


# -- store ------------------------------------------------------------------


class ReminderStore:
    """A reminder collection that can answer "what is due?" and record answers.

    Every method that depends on the current time takes an optional ``now``, so
    the whole scheduling model can be tested with explicit datetimes and no test
    ever has to touch the system clock. Application code omits it and gets
    :meth:`datetime.now`.

    :meth:`snooze` and :meth:`dismiss` persist before they mutate: if the write
    fails the exception propagates and the in-memory collection is exactly what
    it was, so a reminder that could not be dismissed stays visibly due rather
    than vanishing from a UI while remaining on disk.
    """

    def __init__(
        self,
        reminders: Iterable[Reminder] = (),
        source: str | None = None,
        path: Path | str | None = None,
    ) -> None:
        self._reminders: tuple[Reminder, ...] = tuple(reminders)
        self.source = source
        self.path = Path(path) if path is not None else None

    # -- constructors ------------------------------------------------------

    @classmethod
    def from_json(cls, text: str, source: str | None = None) -> "ReminderStore":
        return cls(decode_reminders(text, source), source)

    @classmethod
    def from_file(cls, path: Path | str) -> "ReminderStore":
        path = Path(path)
        return cls(load_reminders(path), str(path), path)

    @classmethod
    def empty(cls, path: Path | str | None = None) -> "ReminderStore":
        """A store with no reminders — the normal state before the first one."""
        return cls((), None, path)

    # -- collection --------------------------------------------------------

    @property
    def reminders(self) -> tuple[Reminder, ...]:
        """Every reminder, in file order, enabled or not."""
        return self._reminders

    def __len__(self) -> int:
        return len(self._reminders)

    def reminder_by_id(self, reminder_id: str) -> Reminder | None:
        for reminder in self._reminders:
            if reminder.id == reminder_id:
                return reminder
        return None

    # -- scheduling --------------------------------------------------------

    @staticmethod
    def _now(now: datetime | None) -> datetime:
        return now if now is not None else datetime.now()

    def occurrence(self, reminder: Reminder, now: datetime | None = None) -> datetime | None:
        """The reminder's active occurrence at ``now``. See :func:`current_occurrence`."""
        return current_occurrence(reminder, self._now(now))

    def status(self, reminder: Reminder, now: datetime | None = None) -> str:
        """One of the ``STATUS_*`` constants, describing ``reminder`` at ``now``.

        The order of the checks is the order of the rules: disabled reminders are
        never eligible; a reminder that has not come round yet is upcoming; a
        dismissed occurrence is finished with even if a snooze is still recorded;
        an unexpired snooze hides an otherwise due occurrence.
        """
        moment = self._now(now)

        if not reminder.enabled:
            return STATUS_DISABLED

        occurrence = current_occurrence(reminder, moment)
        if occurrence is None:
            return STATUS_UPCOMING
        if reminder.dismissed_occurrence == occurrence:
            return STATUS_DISMISSED
        if reminder.snoozed_until is not None and reminder.snoozed_until > moment:
            return STATUS_SNOOZED
        if occurrence <= moment - OVERDUE_AFTER:
            return STATUS_OVERDUE
        return STATUS_DUE

    def is_due(self, reminder: Reminder, now: datetime | None = None) -> bool:
        """Whether ``reminder`` is waiting for the user right now."""
        return self.status(reminder, now) in (STATUS_DUE, STATUS_OVERDUE)

    def due_reminders(self, now: datetime | None = None) -> tuple[Reminder, ...]:
        """Every currently due reminder, oldest occurrence first.

        Ordering is (active occurrence, id): the occurrence so the reminder that
        has been waiting longest is answered first, the id to break ties so that
        two reminders scheduled for the same minute always appear in the same
        order rather than depending on file order or dict iteration.
        """
        moment = self._now(now)
        due = [
            (current_occurrence(reminder, moment), reminder.id, reminder)
            for reminder in self._reminders
            if self.is_due(reminder, moment)
        ]
        due.sort(key=lambda item: (item[0], item[1]))
        return tuple(item[2] for item in due)

    def next_due(self, now: datetime | None = None) -> Reminder | None:
        """The reminder that should be shown, or None when nothing is waiting."""
        due = self.due_reminders(now)
        return due[0] if due else None

    # -- mutation ----------------------------------------------------------

    def _require(self, reminder_id: str) -> int:
        for index, reminder in enumerate(self._reminders):
            if reminder.id == reminder_id:
                return index
        raise ReminderStoreError(f'No reminder with id "{reminder_id}".')

    def _commit(self, index: int, updated: Reminder) -> Reminder:
        """Write the collection with ``index`` replaced, then adopt it.

        Persisting first is the whole point: until :func:`write_reminders`
        returns, ``self._reminders`` still describes what is on disk, so a caller
        that catches the error is looking at consistent state.
        """
        if self.path is None:
            raise ReminderStoreError(
                "This reminder collection has no file to save to, so it cannot "
                "be modified."
            )
        candidate = list(self._reminders)
        candidate[index] = updated
        write_reminders(self.path, candidate)
        self._reminders = tuple(candidate)
        return updated

    def snooze(
        self,
        reminder_id: str,
        now: datetime | None = None,
        interval: timedelta = SNOOZE,
    ) -> Reminder:
        """Push this reminder's current occurrence out by ``interval``.

        The occurrence itself is untouched — only hidden until the snooze
        expires, after which the same occurrence becomes due again.
        """
        moment = self._now(now)
        index = self._require(reminder_id)
        reminder = self._reminders[index]
        return self._commit(
            index,
            replace(reminder, snoozed_until=(moment + interval).replace(microsecond=0)),
        )

    def dismiss(self, reminder_id: str, now: datetime | None = None) -> Reminder:
        """Record that this reminder's current occurrence has been dealt with.

        For a one-time reminder that finishes it. For a recurring one it clears
        only this occurrence: the reminder stays enabled and comes back at its
        next scheduled time. Any snooze is cleared, since it can only refer to
        the occurrence just dismissed.
        """
        moment = self._now(now)
        index = self._require(reminder_id)
        reminder = self._reminders[index]
        occurrence = current_occurrence(reminder, moment)
        if occurrence is None:
            raise ReminderStoreError(
                f'Reminder "{reminder_id}" is not due yet, so there is no '
                "occurrence to dismiss."
            )
        return self._commit(
            index,
            replace(reminder, dismissed_occurrence=occurrence, snoozed_until=None),
        )
