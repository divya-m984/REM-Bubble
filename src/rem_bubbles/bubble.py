"""The REM Bubbles layer-shell window and its UI states.

The window hosts exactly one child at a time and swaps it between the collapsed
bubble, the expanded quote card and the expanded reminder card. The application
keeps running across every transition — nothing is torn down but the child
widget.

Reminders take presentation priority over quotes, but this module decides
nothing about *when*. It asks a :class:`~rem_bubbles.reminder_store.ReminderStore`
"what is due now?" on a timer and renders the answer; recurrence, snooze
expiry, dismissal and ordering all live in that GTK-free engine. Desktop
notifications follow the same split: the window hands the store's own due list
to a :class:`~rem_bubbles.notifications.NotificationCenter` and lets it decide
whether anything is worth announcing.

This module imports GTK, so it must only ever be imported *after*
:mod:`rem_bubbles.app` has loaded libgtk4-layer-shell.
"""

import sys
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")

from gi.repository import GLib, Gtk
from gi.repository import Gtk4LayerShell as LayerShell

from rem_bubbles.notifications import (
    NotificationCenter,
    format_occurrence,
    select_active,
)
from rem_bubbles.quote_store import Quote, QuoteStore
from rem_bubbles.reminder_store import (
    NONE,
    STATUS_OVERDUE,
    Reminder,
    ReminderStore,
    ReminderStoreError,
)

__all__ = [
    "BUBBLE_GLYPH",
    "CHECK_INTERVAL_SECONDS",
    "DEFAULT_MARGIN_LEFT",
    "DEFAULT_MARGIN_TOP",
    "DUE_CSS_CLASS",
    "LAYER_NAMESPACE",
    "BubbleWindow",
    "format_occurrence",
]

#: Glyph shown in the collapsed state.
BUBBLE_GLYPH = "●"  # ●

#: Initial placement, in pixels. Tunable defaults, not architectural constants.
DEFAULT_MARGIN_TOP = -36
DEFAULT_MARGIN_LEFT = 400

#: Layer-shell namespace, useful for Hyprland `layerrule` matching.
LAYER_NAMESPACE = "rem-bubbles"

#: How often the window asks the reminder engine what is due. Thirty seconds is
#: fine-grained enough for minute-precision reminders and cheap enough to be
#: invisible: it is a GLib timeout on the main loop, not a thread and not a poll
#: of the filesystem.
CHECK_INTERVAL_SECONDS = 30

#: Marks the collapsed bubble while a reminder is waiting.
DUE_CSS_CLASS = "rem-bubble-due"


class BubbleWindow(Gtk.ApplicationWindow):
    """A tiny always-on-top overlay that toggles between bubble and card.

    The window renders whatever quote the :class:`~rem_bubbles.quote_store.QuoteStore`
    it was given currently points at; it never reads JSON itself. The cursor
    lives in the store, so collapsing and re-expanding redisplays the same
    quote rather than snapping back to today's — and so does a reminder arriving
    and being dealt with, since the reminder card is a separate widget and the
    quote cursor is never touched on the way through.

    ``poll_seconds`` of 0 disables the periodic check, which is what automated
    verification uses: it drives :meth:`refresh_reminders` with explicit
    datetimes instead of waiting for wall-clock time to pass.

    ``notifier`` is an optional
    :class:`~rem_bubbles.notifications.NotificationCenter`. Without one the
    window behaves exactly as it did in Milestone 4, which is also what happens
    whenever ``[notifications].enabled`` is false.
    """

    def __init__(
        self,
        application: Gtk.Application,
        store: QuoteStore,
        reminders: ReminderStore | None = None,
        margin_top: int = DEFAULT_MARGIN_TOP,
        margin_left: int = DEFAULT_MARGIN_LEFT,
        poll_seconds: int = CHECK_INTERVAL_SECONDS,
        notifier: NotificationCenter | None = None,
    ) -> None:
        super().__init__(application=application, title="REM Bubbles")

        self._store = store
        self._reminders = reminders if reminders is not None else ReminderStore.empty()
        self._notifier = notifier
        self._active: Reminder | None = None
        self._preferred_id: str | None = None
        self._tick_source: int | None = None

        self.set_decorated(False)
        self.set_resizable(False)
        self.add_css_class("rem-window")

        self._init_layer_shell(margin_top, margin_left)

        self._bubble = self._build_bubble()
        self._card = self._build_card()
        self._reminder_card = self._build_reminder_card()
        self._expanded = False

        self._show_quote(self._store.current)

        self.set_child(self._bubble)

        # Evaluate once before anything is shown, so a reminder that came due
        # while the application was closed is already marked on first paint.
        self.refresh_reminders()
        self._start_scheduler(poll_seconds)

    # -- layer shell ------------------------------------------------------

    def _init_layer_shell(self, margin_top: int, margin_left: int) -> None:
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, LAYER_NAMESPACE)
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)

        LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
        LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
        LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, False)
        LayerShell.set_anchor(self, LayerShell.Edge.BOTTOM, False)

        LayerShell.set_margin(self, LayerShell.Edge.TOP, margin_top)
        LayerShell.set_margin(self, LayerShell.Edge.LEFT, margin_left)

        # 0 means "float above everything without reserving tiling space".
        LayerShell.set_exclusive_zone(self, 0)
        # Never steal keyboard focus from the focused window.
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)

    # -- UI construction --------------------------------------------------

    def _build_bubble(self) -> Gtk.Widget:
        button = Gtk.Button(label=BUBBLE_GLYPH)
        button.add_css_class("rem-bubble")
        button.set_has_frame(False)
        button.set_tooltip_text("REM Bubbles")
        button.set_halign(Gtk.Align.START)
        button.set_valign(Gtk.Align.START)
        button.connect("clicked", lambda _button: self.expand())
        return button

    def _build_card(self) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.add_css_class("rem-card")
        card.set_halign(Gtk.Align.START)
        card.set_valign(Gtk.Align.START)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        heading = Gtk.Label(label="REM")
        heading.add_css_class("rem-heading")
        heading.set_xalign(0.0)
        heading.set_hexpand(True)
        header.append(heading)

        collapse = Gtk.Button(label="×")  # ×
        collapse.add_css_class("rem-collapse")
        collapse.set_has_frame(False)
        collapse.set_tooltip_text("Collapse")
        collapse.set_valign(Gtk.Align.CENTER)
        collapse.connect("clicked", lambda _button: self.collapse())
        header.append(collapse)

        card.append(header)

        self._quote_label = Gtk.Label()
        self._quote_label.add_css_class("rem-quote")
        self._quote_label.set_xalign(0.0)
        self._quote_label.set_wrap(True)
        self._quote_label.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._quote_label.set_max_width_chars(34)
        card.append(self._quote_label)

        # Hidden rather than blank when a quote is unattributed, so the card
        # closes up instead of leaving an empty byline row.
        self._author_label = Gtk.Label()
        self._author_label.add_css_class("rem-author")
        self._author_label.set_xalign(0.0)
        self._author_label.set_wrap(True)
        self._author_label.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._author_label.set_max_width_chars(34)
        card.append(self._author_label)

        card.append(self._build_nav())

        return card

    def _build_nav(self) -> Gtk.Widget:
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        nav.add_css_class("rem-nav")
        nav.set_halign(Gtk.Align.CENTER)

        previous = Gtk.Button(label="‹")  # ‹
        previous.add_css_class("rem-nav-button")
        previous.set_has_frame(False)
        previous.set_tooltip_text("Previous quote")
        previous.connect("clicked", lambda _button: self.show_previous_quote())
        nav.append(previous)

        next_ = Gtk.Button(label="›")  # ›
        next_.add_css_class("rem-nav-button")
        next_.set_has_frame(False)
        next_.set_tooltip_text("Next quote")
        next_.connect("clicked", lambda _button: self.show_next_quote())
        nav.append(next_)

        # Nothing to navigate to with a single quote (e.g. the emergency one).
        navigable = len(self._store) > 1
        previous.set_sensitive(navigable)
        next_.set_sensitive(navigable)

        return nav

    def _build_reminder_card(self) -> Gtk.Widget:
        """The card shown instead of the quote card while a reminder is due.

        Deliberately the same shape as the quote card — heading, body, collapse
        control — so it reads as the same object in a different mood rather than
        as a notification centre bolted onto the side.
        """
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.add_css_class("rem-card")
        card.add_css_class("rem-reminder-card")
        card.set_halign(Gtk.Align.START)
        card.set_valign(Gtk.Align.START)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        heading = Gtk.Label(label="REMINDER")
        heading.add_css_class("rem-heading")
        heading.add_css_class("rem-reminder-heading")
        heading.set_xalign(0.0)
        heading.set_hexpand(True)
        header.append(heading)

        collapse = Gtk.Button(label="×")  # ×
        collapse.add_css_class("rem-collapse")
        collapse.set_has_frame(False)
        # Named so nobody mistakes it for "done": the reminder stays due.
        collapse.set_tooltip_text("Collapse — the reminder stays due")
        collapse.set_valign(Gtk.Align.CENTER)
        collapse.connect("clicked", lambda _button: self.collapse())
        header.append(collapse)

        card.append(header)

        self._reminder_label = Gtk.Label()
        self._reminder_label.add_css_class("rem-reminder-text")
        self._reminder_label.set_xalign(0.0)
        self._reminder_label.set_wrap(True)
        self._reminder_label.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._reminder_label.set_max_width_chars(34)
        card.append(self._reminder_label)

        self._reminder_meta = Gtk.Label()
        self._reminder_meta.add_css_class("rem-reminder-meta")
        self._reminder_meta.set_xalign(0.0)
        self._reminder_meta.set_wrap(True)
        self._reminder_meta.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._reminder_meta.set_max_width_chars(34)
        card.append(self._reminder_meta)

        card.append(self._build_reminder_actions())

        return card

    def _build_reminder_actions(self) -> Gtk.Widget:
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        actions.add_css_class("rem-reminder-actions")
        actions.set_halign(Gtk.Align.END)

        self._snooze_button = Gtk.Button(label="Snooze 10m")
        self._snooze_button.add_css_class("rem-snooze")
        self._snooze_button.set_tooltip_text("Come back in ten minutes")
        self._snooze_button.connect("clicked", lambda _button: self.snooze_reminder())
        actions.append(self._snooze_button)

        self._dismiss_button = Gtk.Button(label="Dismiss")
        self._dismiss_button.add_css_class("rem-dismiss")
        self._dismiss_button.set_tooltip_text("Done with this one")
        self._dismiss_button.connect("clicked", lambda _button: self.dismiss_reminder())
        actions.append(self._dismiss_button)

        return actions

    # -- the periodic check -------------------------------------------------

    def _start_scheduler(self, poll_seconds: int) -> None:
        """Ask the reminder engine what is due, every ``poll_seconds``.

        A GLib timeout on the main loop: no worker thread, no busy loop, and no
        work at all between ticks. Registered exactly once, from ``__init__``,
        so the timer's lifetime is the window's — activating an already-running
        instance reuses that window and cannot add a second timer.
        """
        if poll_seconds <= 0:
            return
        self._tick_source = GLib.timeout_add_seconds(poll_seconds, self._on_tick)
        self.connect("destroy", lambda _window: self.shutdown())

    @property
    def scheduler_running(self) -> bool:
        """Whether the periodic check is currently registered."""
        return self._tick_source is not None

    def _stop_scheduler(self) -> None:
        if self._tick_source is not None:
            GLib.source_remove(self._tick_source)
            self._tick_source = None

    def shutdown(self) -> None:
        """Drop everything transient this window owns. Safe to call twice.

        The single place the timer is removed, whatever ended the process:
        Ctrl+C, SIGTERM, the window being destroyed, or an ordinary quit all
        arrive here. Idempotence is what makes that safe — the application calls
        it during shutdown, and GTK calls it again through ``destroy``.

        Only in-memory state is touched. Nothing is written, so a shutdown can
        never be the thing that changes a reminder.
        """
        self._stop_scheduler()
        if self._notifier is not None:
            self._notifier.reset()

    def _on_tick(self) -> bool:
        self.refresh_reminders()
        return GLib.SOURCE_CONTINUE

    # -- reminder presentation ---------------------------------------------

    @property
    def active_reminder(self) -> Reminder | None:
        """The reminder currently taking priority, or None in quote mode."""
        return self._active

    def refresh_reminders(self, now: datetime | None = None) -> None:
        """Re-ask what is due and bring the window into line with the answer.

        Everything time-dependent happens inside the store: this asks it once
        for its ordered due list and uses that one answer for both the card and
        the desktop notifications, so the two can never disagree about what is
        waiting. Called on startup, on every tick, and immediately after a
        snooze or a dismissal so the next waiting reminder appears without
        another wait.
        """
        due = self._reminders.due_reminders(now)
        self._active = select_active(due, self._preferred_id)
        if self._active is None or self._active.id != self._preferred_id:
            # The reminder a notification asked for is no longer the one being
            # shown — it was dealt with, or something older came round. Drop the
            # preference rather than letting it override the ordering forever.
            self._preferred_id = None

        if self._active is None:
            self._bubble.remove_css_class(DUE_CSS_CLASS)
            self._bubble.set_tooltip_text("REM Bubbles")
        else:
            self._bubble.add_css_class(DUE_CSS_CLASS)
            self._bubble.set_tooltip_text("REM Bubbles — a reminder is waiting")
            self._show_reminder(self._active, now)

        if self._notifier is not None:
            self._notifier.evaluate(self._reminders, due, now)

        self._present()

    def focus_reminder(self, reminder_id: str, now: datetime | None = None) -> bool:
        """Show this reminder's card, if it is still waiting. Returns whether.

        This is what a clicked desktop notification arrives at. The reminder may
        well be gone by the time the click happens — snoozed, dismissed, or its
        occurrence passed — so the id is a request, never an instruction: an
        unknown or no-longer-due one simply expands the window onto whatever is
        currently relevant instead of resurrecting something finished.
        """
        self._preferred_id = reminder_id
        self.refresh_reminders(now)
        self.expand()
        return self._active is not None and self._active.id == reminder_id

    def _show_reminder(self, reminder: Reminder, now: datetime | None = None) -> None:
        self._reminder_label.set_label(reminder.text)

        occurrence = self._reminders.occurrence(reminder, now) or reminder.due_at
        overdue = self._reminders.status(reminder, now) == STATUS_OVERDUE

        parts = ["Overdue" if overdue else "Due now"]
        if reminder.recurrence != NONE:
            parts.append(reminder.recurrence.capitalize())
        parts.append(format_occurrence(occurrence))
        self._reminder_meta.set_label(" · ".join(parts))  # ·

    def snooze_reminder(self, now: datetime | None = None) -> None:
        """Hide the active reminder for ten minutes, if that reaches the disk."""
        self._mutate_active(self._reminders.snooze, "snooze", now)

    def dismiss_reminder(self, now: datetime | None = None) -> None:
        """Finish with the active reminder's occurrence, if that reaches the disk."""
        self._mutate_active(self._reminders.dismiss, "dismiss", now)

    def _mutate_active(self, operation, verb: str, now: datetime | None) -> None:
        """Run a store mutation and re-evaluate, or report and change nothing.

        The store persists before it mutates, so a failure here means the file
        and the collection both still say the reminder is waiting — and leaving
        it on screen is then the honest thing to do. Silently clearing the card
        would tell the user something was handled when it was not. The desktop
        notification is taken down only after the change has reached the disk,
        for the same reason.
        """
        reminder = self._active
        if reminder is None:
            return
        try:
            operation(reminder.id, now)
        except (ReminderStoreError, OSError) as exc:
            print(
                f"rem-bubbles: could not {verb} \"{reminder.id}\": {exc}",
                file=sys.stderr,
            )
            print(
                "rem-bubbles: the reminder is unchanged and still due",
                file=sys.stderr,
            )
            return
        if self._notifier is not None:
            self._notifier.withdraw(reminder.id)
        self.refresh_reminders(now)

    # -- quote presentation -----------------------------------------------

    def _show_quote(self, quote: Quote) -> None:
        self._quote_label.set_label(quote.text)

        if quote.author:
            self._author_label.set_label(f"— {quote.author}")  # —
            self._author_label.set_visible(True)
        else:
            self._author_label.set_label("")
            self._author_label.set_visible(False)

    def show_next_quote(self) -> None:
        self._show_quote(self._store.next())
        self._resize_to_content()

    def show_previous_quote(self) -> None:
        self._show_quote(self._store.previous())
        self._resize_to_content()

    def _resize_to_content(self) -> None:
        # A shorter quote would otherwise keep the taller surface committed by
        # the previous one.
        if self._expanded:
            self.set_default_size(-1, -1)

    # -- state transitions ------------------------------------------------

    def expand(self) -> None:
        if self._expanded:
            return
        self._expanded = True
        self._present()

    def collapse(self) -> None:
        """Shrink back to the bubble. This is *only* a collapse.

        A waiting reminder is not dismissed, snoozed, disabled or touched in any
        way by closing the card — it is still due, and expanding again shows it
        again. Anything else would let a stray click lose a reminder.
        """
        if not self._expanded:
            return
        self._expanded = False
        self._present()

    def toggle(self) -> None:
        self.collapse() if self._expanded else self.expand()

    def _present(self) -> None:
        """Show whichever child the current state calls for.

        One place decides, so "a reminder is due" and "the card is open" can
        change independently: a reminder falling due behind an open quote card
        swaps it for the reminder, and dismissing the last due reminder puts the
        quote card back, at whatever quote the user had navigated to.
        """
        if not self._expanded:
            wanted = self._bubble
        elif self._active is not None:
            wanted = self._reminder_card
        else:
            wanted = self._card

        if self.get_child() is not wanted:
            self._swap_child(wanted)

    def _swap_child(self, child: Gtk.Widget) -> None:
        self.set_child(child)
        # Layer surfaces keep their last committed size unless the default is
        # reset, which would leave the collapsed bubble in a card-sized surface.
        self.set_default_size(-1, -1)
