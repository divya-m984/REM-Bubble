"""Application entry point for REM Bubbles.

gtk4-layer-shell must be loaded before libwayland is pulled in by GTK, so the
CDLL() call below has to stay above every ``gi`` import — including the import
of :mod:`rem_bubbles.bubble`, which imports GTK itself. Moving it produces:

    Failed to initialize layer surface, GTK4 Layer Shell may have been linked
    after libwayland.
"""

from ctypes import CDLL

CDLL("libgtk4-layer-shell.so")

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gio, Gtk

from rem_bubbles.bubble import BubbleWindow
from rem_bubbles.config import load_quote_store

APP_ID = "dev.rembubbles.RemBubbles"

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


class RemBubblesApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._window: BubbleWindow | None = None

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        self._load_css()

    def do_activate(self) -> None:
        if self._window is None:
            # load_quote_store() reports its own failures and always returns a
            # usable store, so a broken quote file cannot stop the window.
            self._window = BubbleWindow(application=self, store=load_quote_store())
        self._window.present()

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


def main(argv: list[str] | None = None) -> int:
    return RemBubblesApp().run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
