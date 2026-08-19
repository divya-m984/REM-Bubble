"""The REM Bubbles layer-shell window and its two UI states.

The window hosts exactly one child at a time and swaps it between the collapsed
bubble and the expanded quote card. The application keeps running across the
transition — nothing is torn down but the child widget.

This module imports GTK, so it must only ever be imported *after*
:mod:`rem_bubbles.app` has loaded libgtk4-layer-shell.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")

from gi.repository import Gtk
from gi.repository import Gtk4LayerShell as LayerShell

from rem_bubbles.quote_store import Quote, QuoteStore

#: Glyph shown in the collapsed state.
BUBBLE_GLYPH = "●"  # ●

#: Initial placement, in pixels. Tunable defaults, not architectural constants.
DEFAULT_MARGIN_TOP = -36
DEFAULT_MARGIN_LEFT = 400

#: Layer-shell namespace, useful for Hyprland `layerrule` matching.
LAYER_NAMESPACE = "rem-bubbles"


class BubbleWindow(Gtk.ApplicationWindow):
    """A tiny always-on-top overlay that toggles between bubble and card.

    The window renders whatever quote the :class:`~rem_bubbles.quote_store.QuoteStore`
    it was given currently points at; it never reads JSON itself. The cursor
    lives in the store, so collapsing and re-expanding redisplays the same
    quote rather than snapping back to today's.
    """

    def __init__(
        self,
        application: Gtk.Application,
        store: QuoteStore,
        margin_top: int = DEFAULT_MARGIN_TOP,
        margin_left: int = DEFAULT_MARGIN_LEFT,
    ) -> None:
        super().__init__(application=application, title="REM Bubbles")

        self._store = store

        self.set_decorated(False)
        self.set_resizable(False)
        self.add_css_class("rem-window")

        self._init_layer_shell(margin_top, margin_left)

        self._bubble = self._build_bubble()
        self._card = self._build_card()
        self._expanded = False

        self._show_quote(self._store.current)

        self.set_child(self._bubble)

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
        self._swap_child(self._card)

    def collapse(self) -> None:
        if not self._expanded:
            return
        self._expanded = False
        self._swap_child(self._bubble)

    def toggle(self) -> None:
        self.collapse() if self._expanded else self.expand()

    def _swap_child(self, child: Gtk.Widget) -> None:
        self.set_child(child)
        # Layer surfaces keep their last committed size unless the default is
        # reset, which would leave the collapsed bubble in a card-sized surface.
        self.set_default_size(-1, -1)
