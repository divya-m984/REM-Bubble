# REM Bubbles

A tiny persistent desktop quote and reminder companion for Wayland.

A small bubble sits near the top-left of the desktop. Click it and it expands
into a quote card; click the collapse control and it shrinks back. It lives on
the layer-shell overlay layer with an exclusive zone of `0`, so it floats above
everything without reserving any space in the tiling layout.

## Status

Milestone 1 — graphical foundation only. The quote is hard-coded; quote
loading, reminders, configuration and the CLI come later.

## Requirements

System packages (not installable from PyPI):

- GTK 4
- PyGObject
- gtk4-layer-shell
- A Wayland compositor supporting `wlr-layer-shell` (developed on Hyprland)

Create the virtual environment with access to those system packages:

```bash
python -m venv --system-site-packages .venv
.venv/bin/pip install -e .
```

## Running

```bash
.venv/bin/python -m rem_bubbles.app
```

or, via the console script:

```bash
.venv/bin/rem-bubbles
```

## Layout

```text
src/rem_bubbles/app.py     application lifecycle, CSS loading, entry point
src/rem_bubbles/bubble.py  layer-shell window, collapsed/expanded UI, transitions
assets/style.css           all styling
```

### A note on load order

`libgtk4-layer-shell.so` must be loaded before GTK pulls in libwayland, so
`app.py` calls `CDLL("libgtk4-layer-shell.so")` as its first statement — above
every `gi` import, including its import of `bubble`. Moving it below any GTK
import produces:

```text
Failed to initialize layer surface, GTK4 Layer Shell may have been linked after libwayland.
```

## Tuning placement

The top and left margins default to 3 px and 8 px. They are constructor
arguments on `BubbleWindow` (`DEFAULT_MARGIN_TOP` / `DEFAULT_MARGIN_LEFT` in
`bubble.py`), so they can be adjusted without touching the layer-shell setup.
Note that a compositor places non-exclusive layer surfaces below any exclusive
zone already claimed by a bar, so the effective on-screen `y` is the bar height
plus the top margin.
