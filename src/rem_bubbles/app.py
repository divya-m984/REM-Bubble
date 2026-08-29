"""Application entry point, process lifecycle and desktop integration.

gtk4-layer-shell must be loaded before libwayland is pulled in by GTK, so the
CDLL() call below has to stay above every ``gi`` import — including the import
of :mod:`rem_bubbles.bubble`, which imports GTK itself. Moving it produces:

    Failed to initialize layer surface, GTK4 Layer Shell may have been linked
    after libwayland.

Three things beyond opening a window live here, and all three are Gio's rather
than ours:

* **uniqueness** — a :class:`Gtk.Application` with an ``application_id`` already
  owns a D-Bus name. A second ``rem-bubbles`` finds the name taken, sends an
  activation to the first and exits. There is no pidfile and no process-name
  matching, because there does not need to be;
* **shutdown** — SIGINT and SIGTERM are turned into ordinary main-loop events by
  :func:`GLib.unix_signal_add`, so a signal never interrupts Python mid-statement
  and never surfaces as a ``KeyboardInterrupt`` traceback;
* **notifications** — :class:`Gio.Notification` through
  :meth:`Gtk.Application.send_notification`, with no subprocess and no new
  dependency. What is worth notifying about is decided in the GTK-free
  :mod:`rem_bubbles.notifications`.
"""

from ctypes import CDLL

try:
    CDLL("libgtk4-layer-shell.so")
except OSError as _exc:  # pragma: no cover — needs the library to be absent
    # A missing library is a packaging problem with a fix the user can act on,
    # so say so plainly. SystemExit prints its message and stops; the bare
    # OSError traceback this replaces explained nothing.
    raise SystemExit(
        f"rem-bubbles: could not load libgtk4-layer-shell.so: {_exc}\n"
        "rem-bubbles: the bubble needs gtk4-layer-shell to sit on the overlay "
        "layer. Install it and try again."
    ) from None

import signal
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gio, GLib, Gtk

from rem_bubbles.bubble import BubbleWindow
from rem_bubbles.config import (
    load_notification_preference,
    load_quote_store,
    load_reminder_store,
)
from rem_bubbles.notifications import NotificationCenter

APP_ID = "dev.rembubbles.RemBubbles"

#: The action a desktop notification activates when it is clicked. Registered on
#: the application, so ``app.open-reminder`` reaches whichever instance is
#: already running — clicking a notification never starts a second process.
OPEN_REMINDER_ACTION = "open-reminder"

#: Signals that mean "stop now, tidily". SIGINT is Ctrl+C in a terminal; SIGTERM
#: is a session logout, a compositor shutting down or a process supervisor.
SHUTDOWN_SIGNALS = (signal.SIGINT, signal.SIGTERM)

# Candidate locations for the stylesheet, in priority order: the repository
# checkout (editable installs / running from source) first, then a copy shipped
# alongside the package.
_CSS_CANDIDATES = (
    Path(__file__).resolve().parents[2] / "assets" / "style.css",
    Path(__file__).resolve().parent / "assets" / "style.css",
)


def find_stylesheet() -> Path | None:
    """Return the first stylesheet that exists, or None."""
    for path in _CSS_CANDIDATES:
        if path.is_file():
            return path
    return None


def _watch_signal(signum: int, handler) -> int:
    """Attach ``handler`` to ``signum`` as a main-loop source.

    GLib catches the signal in C and dispatches ``handler`` from the main loop,
    which is what makes this safe: the callback is ordinary code running between
    iterations, not signal-handler context, and Python's own handler — the one
    that raises ``KeyboardInterrupt`` at an arbitrary bytecode boundary — is
    displaced.

    ``GLib.unix_signal_add`` is deprecated in newer PyGObject in favour of
    ``GLibUnix.signal_add``; the newer spelling is preferred where the typelib
    provides it so that no deprecation warning is printed at startup.
    """
    try:
        gi.require_version("GLibUnix", "2.0")
        from gi.repository import GLibUnix

        return GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signum, handler)
    except (ImportError, ValueError, AttributeError):  # pragma: no cover
        return GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, handler)


class RemBubblesApp(Gtk.Application):
    """The single REM Bubbles instance for a user session."""

    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._window: BubbleWindow | None = None
        self._notifier: NotificationCenter | None = None
        self._signal_sources: list[int] = []
        self._signal_status: int | None = None
        self._shutting_down = False

    # -- startup ------------------------------------------------------------

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        self._install_signal_handlers()
        self._register_actions()
        self._load_css()

    def _install_signal_handlers(self) -> None:
        for signum in SHUTDOWN_SIGNALS:
            self._signal_sources.append(
                _watch_signal(signum, lambda number=signum: self._on_signal(number))
            )

    def _on_signal(self, signum: int) -> bool:
        """Begin a tidy shutdown, and remember why.

        Returns ``SOURCE_CONTINUE`` so the source stays attached: a second
        Ctrl+C while the first is still being processed then arrives here again
        and finds :meth:`request_shutdown` already done, rather than falling
        through to a default disposition that would kill the process mid-write.
        """
        if self._signal_status is None:
            # 128 + signum is the conventional status for "ended by this
            # signal", which is what a shell reports for an uncaught one.
            self._signal_status = 128 + int(signum)
        self.request_shutdown()
        return GLib.SOURCE_CONTINUE

    def _register_actions(self) -> None:
        action = Gio.SimpleAction.new(OPEN_REMINDER_ACTION, GLib.VariantType.new("s"))
        action.connect("activate", self._on_open_reminder)
        self.add_action(action)

    def _load_css(self) -> None:
        stylesheet = find_stylesheet()
        if stylesheet is None:
            print(
                "rem-bubbles: no stylesheet found, falling back to unstyled widgets",
                file=sys.stderr,
            )
            return

        provider = Gtk.CssProvider()
        provider.load_from_path(str(stylesheet))
        display = Gdk.Display.get_default()
        if display is None:
            print("rem-bubbles: no display available", file=sys.stderr)
            return
        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    # -- activation ---------------------------------------------------------

    def do_activate(self) -> None:
        """Open the bubble, or acknowledge a second launch of an existing one.

        Everything expensive happens exactly once. A second ``rem-bubbles``
        reaches this method on the *first* process, where the window already
        exists — so the quote cursor keeps its position, the reminder collection
        is not reloaded, snooze and dismissal state survive, and no second
        thirty-second timer is created.

        The one visible effect of a repeat launch is that the bubble expands. A
        layer surface is always on the overlay layer and cannot be raised, so
        presenting it again would look like nothing happened at all; expanding
        is both an acknowledgement and harmless, since collapsing loses nothing.
        """
        if self._window is None:
            self._notifier = self._build_notifier()
            # Both loaders report their own failures and always return a usable
            # store, so neither a broken quote file nor a broken reminder file
            # can stop the window opening. A missing reminder file is not even a
            # failure — it is the normal state before the first reminder.
            self._window = BubbleWindow(
                application=self,
                store=load_quote_store(),
                reminders=load_reminder_store(),
                notifier=self._notifier,
            )
            self._window.present()
            return

        self._window.expand()
        self._window.present()

    # -- notifications ------------------------------------------------------

    def _build_notifier(self) -> NotificationCenter:
        """Wire the GTK-free notification policy to Gio's delivery.

        Reading the preference here rather than at import time means the switch
        is consulted once per launch, alongside the quote and reminder files.
        """
        return NotificationCenter(
            send=self._send_notification,
            withdraw=self.withdraw_notification,
            enabled=load_notification_preference(),
            report=lambda message: print(f"rem-bubbles: {message}", file=sys.stderr),
        )

    def _send_notification(
        self, ident: str, title: str, body: str, reminder_id: str
    ) -> None:
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        # Clicking the notification body activates this action on this running
        # application. No new process, and no D-Bus service of our own.
        notification.set_default_action_and_target(
            f"app.{OPEN_REMINDER_ACTION}", GLib.Variant("s", reminder_id)
        )
        self.send_notification(ident, notification)

    def _on_open_reminder(self, _action, parameter) -> None:
        """Handle a clicked desktop notification.

        The reminder may have been snoozed, dismissed or simply overtaken by an
        older one between the notification appearing and the click. That is not
        an error: the window is brought up regardless, showing whatever is
        genuinely relevant now.
        """
        reminder_id = parameter.get_string() if parameter is not None else ""
        self.activate()
        if self._window is not None and reminder_id:
            self._window.focus_reminder(reminder_id)

    # -- shutdown -----------------------------------------------------------

    def request_shutdown(self) -> None:
        """Ask the application to stop. Safe to call more than once.

        The window is closed first so GTK tears the layer surface down the way
        it would for any other close, and only then is the main loop asked to
        finish. Transient state is not touched here — that is
        :meth:`do_shutdown`'s job, which Gio runs exactly once no matter which
        route got us here.
        """
        if self._shutting_down:
            return
        self._shutting_down = True
        if self._window is not None:
            self._window.close()
        self.quit()

    def do_shutdown(self) -> None:
        """The one cleanup path, whatever ended the process.

        Ctrl+C, SIGTERM, the window being closed and an ordinary quit all end up
        here, so the thirty-second timer and the in-memory notification state
        have exactly one place they are dropped and cannot be forgotten by a
        route somebody adds later.
        """
        self._shutting_down = True
        if self._window is not None:
            self._window.shutdown()
        if self._notifier is not None:
            self._notifier.reset()
        for source in self._signal_sources:
            GLib.source_remove(source)
        self._signal_sources.clear()
        Gtk.Application.do_shutdown(self)

    @property
    def exit_status(self) -> int | None:
        """128 + signum when a signal ended the run, otherwise None."""
        return self._signal_status


def main(argv: list[str] | None = None) -> int:
    """Run the application, translating an interruption into a status.

    The ``KeyboardInterrupt`` guard covers one narrow gap: a Ctrl+C arriving
    before ``do_startup`` has attached the GLib signal sources, while Python's
    own handler is still the one installed. Once the main loop is running the
    exception cannot occur, and nothing else is caught here — a real fault still
    produces a real traceback.
    """
    app = RemBubblesApp()
    try:
        status = app.run(argv if argv is not None else sys.argv)
    except KeyboardInterrupt:
        return 128 + int(signal.SIGINT)
    return app.exit_status if app.exit_status is not None else status


if __name__ == "__main__":
    raise SystemExit(main())
