# REM Bubbles

A tiny persistent desktop quote and reminder companion for Wayland.

A small bubble sits near the top-left of the desktop. Click it and it expands
into a quote card showing the day's quote; click the collapse control and it
shrinks back. It lives on the layer-shell overlay layer with an exclusive zone
of `0`, so it floats above everything without reserving any space in the tiling
layout.

## Status

Milestone 2 — graphical foundation plus the quote engine. Quotes are read from
JSON, one is picked deterministically per day, and `‹` / `›` step through the
collection.

Not implemented yet: reminders and notifications, a quote editing UI, a CLI,
`~/.config/rem-bubbles/` configuration, and any persisted state.

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

## Tests

The quote engine has no GTK, Wayland or display-server dependency, so its tests
run anywhere:

```bash
.venv/bin/python -m unittest discover -s tests
```

## Quotes

Quotes live in a JSON array of objects:

```json
[
  {
    "id": "keep-making-weird-things",
    "text": "Keep making weird things.",
    "author": null,
    "enabled": true
  },
  {
    "id": "read-the-error",
    "text": "Read the error message before guessing.",
    "author": "REM Bubbles"
  }
]
```

| Field     | Required | Notes                                                        |
| --------- | -------- | ------------------------------------------------------------ |
| `id`      | yes      | Non-empty string, unique within the file                     |
| `text`    | yes      | Non-empty after trimming                                     |
| `author`  | no       | String or `null`; blank counts as absent and renders no byline |
| `enabled` | no       | `true` or `false`, defaults to `true`                        |

Disabled quotes are validated but never selected or navigated to.

Malformed data is reported as a plain message on stderr rather than a
traceback — bad JSON, a non-array root, a missing or blank `id` or `text`, a
non-string `author`, a non-boolean `enabled`, duplicate ids, and a collection
with nothing enabled. Nothing is silently discarded: the first problem stops
that file from loading.

### Where quotes are read from

Highest priority first:

1. a path supplied explicitly by a caller (`config.load_quote_store(path)`)
2. `quotes.json` in the repository root — your own collection
3. `examples/quotes.json` — tracked sample data, the development fallback

The root `quotes.json` is git-ignored (`/quotes.json` in `.gitignore`), so a
personal collection stays out of the repository while `examples/quotes.json`
remains tracked. Nothing is ever written to `~/.config/`; XDG support comes
later.

If a file fails to load, the error is printed and the next candidate is tried.
If none work, a single built-in quote keeps the window openable.

### Daily quote

The quote for a day is chosen by taking SHA-256 of the date plus the ids of the
enabled quotes in order, and reducing it modulo the collection size. It is
deterministic: starting REM Bubbles at 09:00 and again at 16:00 on the same day
shows the same quote, with no state written to disk.

Because the collection's ids are part of the digest, **adding, removing,
enabling, disabling or reordering quotes may change today's quote.** The
guarantee is stability across restarts of an unchanged collection, which is
sufficient for this milestone.

### Navigation

`‹` and `›` on the expanded card step to the previous and next quote, wrapping
at both ends. Only enabled quotes are in the rotation. Navigating updates the
card in place — the application is not restarted — and collapsing then
re-expanding the bubble keeps whatever quote you navigated to. Restarting the
application returns to that day's deterministic quote.

## Layout

```text
src/rem_bubbles/app.py          application lifecycle, CSS loading, entry point
src/rem_bubbles/bubble.py       layer-shell window, collapsed/expanded UI, transitions
src/rem_bubbles/quote_store.py  quote parsing, validation, daily selection, navigation
src/rem_bubbles/config.py       quote source resolution and load-with-fallback
assets/style.css                all styling
examples/quotes.json            sample quote collection
tests/                          quote engine tests (no display server needed)
```

`quote_store.py` and `config.py` import no GTK. Presentation stays in
`bubble.py`; quote data stays in the store.

### A note on load order

`libgtk4-layer-shell.so` must be loaded before GTK pulls in libwayland, so
`app.py` calls `CDLL("libgtk4-layer-shell.so")` as its first statement — above
every `gi` import, including its import of `bubble`. Moving it below any GTK
import produces:

```text
Failed to initialize layer surface, GTK4 Layer Shell may have been linked after libwayland.
```

## Tuning placement

The top and left margins are constructor arguments on `BubbleWindow`
(`DEFAULT_MARGIN_TOP` / `DEFAULT_MARGIN_LEFT` in `bubble.py`), so they can be
adjusted without touching the layer-shell setup.
Note that a compositor places non-exclusive layer surfaces below any exclusive
zone already claimed by a bar, so the effective on-screen `y` is the bar height
plus the top margin.
