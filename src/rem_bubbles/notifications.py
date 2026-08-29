"""Deciding when a desktop notification is warranted, and what it says.

Like :mod:`rem_bubbles.quote_store` and :mod:`rem_bubbles.reminder_store`, this
module imports no ``gi``. It holds the *policy* — which due reminders deserve a
notification, what the text is, and which reminder the card should show — while
:mod:`rem_bubbles.app` holds the *mechanism*, a :class:`Gio.Notification` handed
to :meth:`Gtk.Application.send_notification`. The seam between them is the plain
``send`` callable given to :class:`NotificationCenter`, which is what lets the
whole deduplication model be tested without a desktop notification daemon.

Desktop notifications are a secondary signal. The REM Bubbles card remains the
authoritative reminder UI, and every failure here is reported and stepped over:
a missing notification backend must never cost the user a reminder.

**Deduplication is deliberately process-local.** Nothing about notifications is
written to ``reminders.json`` — no ``last_notified_at``, no history, no counter.
The consequence is documented rather than worked around: restarting REM Bubbles
while a reminder is still overdue may notify once more for an episode that was
already announced. The persisted reminder state is what matters, and that is
untouched by anything in this module.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Iterable, Sequence

from rem_bubbles.reminder_store import (
    STATUS_OVERDUE,
    Reminder,
    ReminderStore,
    format_datetime,
)

__all__ = [
    "NOTIFICATION_TITLE",
    "EpisodeKey",
    "NotificationCenter",
    "episode_key",
    "format_occurrence",
    "notification_body",
    "notification_id",
    "select_active",
]

#: Every REM Bubbles notification carries the same title; the reminder text is
#: the body. Naming the application in the title is what makes a notification
#: recognisable at a glance in a stack from several programs.
NOTIFICATION_TITLE = "REM Bubbles"

#: What a deduplication key looks like: reminder id, occurrence, snooze episode.
EpisodeKey = tuple[str, str, str]


# -- formatting -------------------------------------------------------------


def format_occurrence(value: datetime) -> str:
    """A scheduled time as a person reads it: ``Aug 30 · 6:00 PM``.

    Written out rather than handed to ``strftime`` with ``%-d``/``%-I``, since
    those are a glibc extension and the padding they strip is the whole point.

    Shared by the reminder card and the desktop notification so that a reminder
    reads identically wherever it appears.
    """
    hour = value.hour % 12 or 12
    meridiem = "AM" if value.hour < 12 else "PM"
    return (
        f"{value.strftime('%b')} {value.day} · "  # ·
        f"{hour}:{value.minute:02d} {meridiem}"
    )


def notification_id(reminder_id: str) -> str:
    """The id a notification is sent under, so a later one *replaces* it.

    One id per reminder rather than one per occurrence: tomorrow's 08:00 should
    take the place of yesterday's on screen, not queue up behind it. It is also
    what :meth:`NotificationCenter.withdraw` needs to take a notification down
    once the reminder has been snoozed or dismissed.
    """
    return f"reminder:{reminder_id}"


def notification_body(
    store: ReminderStore, reminder: Reminder, now: datetime | None = None
) -> str:
    """The notification body: the reminder, then when it was due.

    Deliberately two short lines. A notification is a nudge towards the card,
    not a second copy of it, so recurrence, snooze state and the id are all left
    out — the card says all of that, and says it authoritatively.
    """
    occurrence = store.occurrence(reminder, now) or reminder.due_at
    overdue = store.status(reminder, now) == STATUS_OVERDUE
    lead = "Overdue" if overdue else "Due"
    return f"{reminder.text}\n{lead} {format_occurrence(occurrence)}"


# -- which reminder the card shows ------------------------------------------


def select_active(
    due: Sequence[Reminder], preferred_id: str | None = None
) -> Reminder | None:
    """The reminder to display, out of the store's ordered due list.

    Normally the first — the oldest waiting occurrence, as ordered by
    :meth:`ReminderStore.due_reminders`. ``preferred_id`` overrides that for
    one case only: the user clicked a desktop notification and asked for *that*
    reminder. The preference is honoured only while the reminder is genuinely
    still due, so a stale or already-dismissed id falls back to the normal
    ordering rather than showing something that is no longer waiting.
    """
    if not due:
        return None
    if preferred_id is not None:
        for reminder in due:
            if reminder.id == preferred_id:
                return reminder
    return due[0]


# -- deduplication ----------------------------------------------------------


def episode_key(reminder: Reminder, occurrence: datetime) -> EpisodeKey:
    """Identify the *episode* a reminder is currently in.

    Three parts, and all three are needed:

    * the reminder id, so two reminders never share an episode;
    * the active occurrence, so Tuesday's 08:00 is a different episode from
      Monday's — an id alone would silence every recurrence after the first;
    * ``snoozed_until``, so a snooze expiring starts a fresh episode and earns
      exactly one new notification, while the ticks either side of it do not.

    Datetimes are rendered to their canonical strings so the key is comparable
    and hashable, and stays stable across a reload of the same data.
    """
    return (
        reminder.id,
        format_datetime(occurrence),
        format_datetime(reminder.snoozed_until) if reminder.snoozed_until else "",
    )


class NotificationCenter:
    """Sends at most one desktop notification per due episode.

    ``send`` is called as ``send(notification_id, title, body, reminder_id)``
    and is the only way this class reaches the outside world; ``withdraw`` is
    called as ``withdraw(notification_id)`` and may be None where taking a
    notification back down is not supported. Both are plain callables, so tests
    pass lists and the application passes Gio.

    The remembered state is one episode key per reminder id, which is bounded by
    the size of the reminder collection: snoozing and dismissing replace entries
    rather than adding them, and nothing here ever reloads the file.
    """

    def __init__(
        self,
        send: Callable[[str, str, str, str], None],
        withdraw: Callable[[str], None] | None = None,
        enabled: bool = False,
        report: Callable[[str], None] | None = None,
    ) -> None:
        self._send = send
        self._withdraw = withdraw
        self._enabled = bool(enabled)
        self._report = report
        self._last: dict[str, EpisodeKey] = {}
        self._failed = False

    # -- state -------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Whether desktop notifications were switched on in ``config.toml``."""
        return self._enabled

    def notified_key(self, reminder_id: str) -> EpisodeKey | None:
        """The episode this reminder was last notified for, or None."""
        return self._last.get(reminder_id)

    def reset(self) -> None:
        """Forget every episode. Used when the application shuts down."""
        self._last.clear()
        self._failed = False

    # -- evaluation --------------------------------------------------------

    def evaluate(
        self,
        store: ReminderStore,
        due: Iterable[Reminder],
        now: datetime | None = None,
    ) -> tuple[str, ...]:
        """Notify for every due reminder now in an episode it has not announced.

        ``due`` is the store's own ordered due list — this never recomputes what
        is due, so recurrence, snooze expiry and dismissal are decided in exactly
        one place. Returns the ids actually notified, which is what tests assert
        on and what makes "the ticks in between sent nothing" observable.

        Disabled means disabled: nothing is sent and nothing is remembered, so a
        user who never opted in cannot accumulate hidden state.
        """
        if not self._enabled:
            return ()

        sent: list[str] = []
        for reminder in due:
            occurrence = store.occurrence(reminder, now) or reminder.due_at
            key = episode_key(reminder, occurrence)
            if self._last.get(reminder.id) == key:
                continue
            # Recorded before the send is attempted, and kept even if it fails.
            # A backend that is missing now will still be missing in thirty
            # seconds, and turning every tick into another failed send — and
            # another line on stderr — would be worse than staying quiet.
            self._last[reminder.id] = key
            if self._deliver(reminder, notification_body(store, reminder, now)):
                sent.append(reminder.id)
        return tuple(sent)

    def _deliver(self, reminder: Reminder, body: str) -> bool:
        try:
            self._send(notification_id(reminder.id), NOTIFICATION_TITLE, body, reminder.id)
        except Exception as exc:  # noqa: BLE001 — any backend fault, never fatal
            self._fail(f"could not send a desktop notification: {exc}")
            return False
        return True

    def withdraw(self, reminder_id: str) -> None:
        """Take a reminder's visible notification down, if that is supported.

        Called after a successful snooze or dismissal, so the desktop stops
        showing something the user has already dealt with. Failure is reported
        once and otherwise ignored: the reminder state is already saved, and a
        notification that will not go away is a cosmetic problem, not a lost
        reminder.

        The episode is deliberately *not* forgotten here. Both snoozing and
        dismissing change the episode key by themselves — a snooze through
        ``snoozed_until``, a dismissal through the occurrence — so the next
        genuine episode is already distinct without any bookkeeping.
        """
        if not self._enabled or self._withdraw is None:
            return
        try:
            self._withdraw(notification_id(reminder_id))
        except Exception as exc:  # noqa: BLE001 — cosmetic, never fatal
            self._fail(f"could not withdraw a desktop notification: {exc}")

    def _fail(self, message: str) -> None:
        """Report a backend problem the first time only.

        The reminder scheduler runs every thirty seconds. Reporting each failure
        would fill a terminal with the same line all day, which hides real
        output rather than helping anyone diagnose anything.
        """
        if self._failed:
            return
        self._failed = True
        if self._report is not None:
            self._report(message)
