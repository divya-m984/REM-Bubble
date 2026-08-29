"""The REM Bubbles layer-shell window and its UI states.

The window hosts exactly one child at a time and swaps it between the collapsed
bubble, the expanded quote card and the expanded reminder card. The application
keeps running across every transition — nothing is torn down but the child
widget.

Reminders take presentation priority over quotes, but this module decides
nothing about *when*. It asks a :class:`~rem_bubbles.reminder_store.ReminderStore`
"what is due now?" on a timer and renders the answer; recurrence, snooze
expiry, dismissal and ordering all live in that GTK-free engine.

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

from rem_bubbles.quote_store import Quote, QuoteStore
from rem_bubbles.reminder_store import (
    NONE,
    STATUS_OVERDUE,
    Reminder,
    ReminderStore,
    ReminderStoreError,
)

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


def format_occurrence(value: datetime) -> str:
    """A scheduled time as a person reads it: ``Aug 30 · 6:00 PM``.

    Written out rather than handed to ``strftime`` with ``%-d``/``%-I``, since
    those are a glibc extension and the padding they strip is the whole point.
    """
    hour = value.hour % 12 or 12
    meridiem = "AM" if value.hour < 12 else "PM"
    return (
        f"{value.strftime('%b')} {value.day} · "  # ·
        f"{hour}:{value.minute:02d} {meridiem}"
    )


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
    """

    def __init__(
        self,
        application: Gtk.Application,
        store: QuoteStore,
        reminders: ReminderStore | None = None,
        margin_top: int = DEFAULT_MARGIN_TOP,
        margin_left: int = DEFAULT_MARGIN_LEFT,
        poll_seconds: int = CHECK_INTERVAL_SECONDS,
    ) -> None:
        super().__init__(application=application, title="REM Bubbles")

        self._store = store
        self._reminders = reminders if reminders is not None else ReminderStore.empty()
        self._active: Reminder | None = None
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
        work at all between ticks. It is removed when the window goes away so a
        closed bubble leaves nothing running.
        """
        if poll_seconds <= 0:
            return
        self._tick_source = GLib.timeout_add_seconds(poll_seconds, self._on_tick)
        self.connect("destroy", lambda _window: self._stop_scheduler())

    def _stop_scheduler(self) -> None:
        if self._tick_source is not None:
            GLib.source_remove(self._tick_source)
            self._tick_source = None

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

        Everything time-dependent happens inside the store: this picks the first
        of its ordered due reminders, which is the oldest waiting occurrence.
        Called on startup, on every tick, and immediately after a snooze or a
        dismissal so the next waiting reminder appears without another wait.
        """
        self._active = self._reminders.next_due(now)

        if self._active is None:
            self._bubble.remove_css_class(DUE_CSS_CLASS)
            self._bubble.set_tooltip_text("REM Bubbles")
        else:
            self._bubble.add_css_class(DUE_CSS_CLASS)
            self._bubble.set_tooltip_text("REM Bubbles — a reminder is waiting")
            self._show_reminder(self._active, now)

        self._present()

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
        would tell the user something was handled when it was not.
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
