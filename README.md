# REM Bubbles

A tiny persistent desktop quote and reminder companion for Wayland.

A small bubble sits near the top-left of the desktop. Click it and it expands
into a quote card showing the day's quote; click the collapse control and it
shrinks back. It lives on the layer-shell overlay layer with an exclusive zone
of `0`, so it floats above everything without reserving any space in the tiling
layout.

## Status

Milestone 3 — graphical foundation, the quote engine, and a terminal interface
for managing your own quotes in `~/.config/rem-bubbles/`.

Not implemented yet: reminders, scheduling and notifications; any quote editing
in the GUI (no add button, edit dialog, delete button, settings window or file
chooser); live reload of a running bubble.

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
rem-bubbles
```

Run with no command and you get the bubble. Everything else is a terminal
command that never opens a window:

```bash
rem-bubbles gui                              # the same thing, explicitly
rem-bubbles init                             # set up ~/.config/rem-bubbles/
rem-bubbles quote list                       # show your quotes
rem-bubbles quote add "Keep making weird things."
rem-bubbles quote add "Read the error first." --author "Me"
rem-bubbles quote add "A quiet one." --id quiet --disabled
rem-bubbles quote remove quiet               # exact id
rem-bubbles quote enable quiet
rem-bubbles quote disable quiet
rem-bubbles --help
```

The GUI can also still be started directly:

```bash
.venv/bin/python -m rem_bubbles.app
```

## Your quotes

REM Bubbles keeps your quotes in your own configuration directory, following
the XDG Base Directory convention:

```text
~/.config/rem-bubbles/
├── config.toml
└── quotes.json
```

If `$XDG_CONFIG_HOME` is set to an absolute path, `$XDG_CONFIG_HOME/rem-bubbles/`
is used instead. Nothing in this repository is ever written to: **your quotes
live outside the checkout and outside Git.** `examples/` is tracked sample data
for reading, not personal storage.

Get started with:

```bash
rem-bubbles init
rem-bubbles quote add "Keep making weird things."
```

`init` creates the directory and a default `config.toml`. It never overwrites
an existing file, so running it twice is safe. It deliberately does not create
`quotes.json` — your first `quote add` does that, which keeps the file
containing only quotes you chose. `init` is optional: `quote add` alone creates
everything it needs.

The directory is created `0700` and files `0600`, since this is personal data.
Files that already exist keep whatever permissions you gave them.

### config.toml

One table, one key:

```toml
[quotes]
file = "quotes.json"
```

`file` may be relative (resolved against the directory holding `config.toml`,
so the above means `~/.config/rem-bubbles/quotes.json`), absolute, or start
with `~`:

```toml
[quotes]
file = "/some/private/location/my-quotes.json"
```

That is the whole format for now — no theme, position or reminder settings.

A `config.toml` that cannot be understood is never guessed past. The GUI prints
the problem to stderr and carries on with the default quote locations rather
than refusing to open. Quote-management commands instead stop with an error and
change nothing, because guessing there could write your quotes to a file you
did not choose.

### Managing quotes

`quote list` prints the file it is managing, then every quote with its id, its
state and its author if it has one:

```text
Quote file: /home/you/.config/rem-bubbles/quotes.json

✓ finish-simple-first
  Finish the simple version first.

○ old-note
  Something I no longer want in rotation.
  — Me

2 quotes (1 enabled, 1 disabled)
```

`✓` is enabled, `○` is disabled. Listing never creates or modifies anything; if
you have no quote file yet it says so and tells you how to add the first one.

`remove`, `enable` and `disable` take an **exact** id — the one `list` shows. A
partial or unknown id is an error that changes nothing.

Ids are generated from the text when you do not pass `--id`: lowercased, accents
folded, everything else turned into hyphens, capped at 48 characters on a word
boundary. `Finish the simple version first.` becomes `finish-the-simple-version-first`.
Text with no usable ASCII slug at all — Japanese, Cyrillic, emoji — gets a
stable short digest instead, like `quote-a13f84c2`, because valid Unicode text
should never be rejected just because it does not transliterate. An id that is
already taken gets a numeric suffix (`-2`, `-3`, …); an existing quote is never
overwritten.

At least one quote always stays enabled. Removing or disabling the last enabled
quote is refused with an explanation, and a disabled quote cannot be the only
quote in a new file — otherwise the bubble would have nothing to show.
Enabling something already enabled (or disabling something already disabled) is
a success that leaves the file untouched.

Writes are atomic: the new collection is serialised, validated, written to a
temporary file in the same directory and then moved into place with
`os.replace()`. An interrupted or failed write leaves your existing collection
exactly as it was, and no half-written JSON is ever visible. Files are UTF-8
with `ensure_ascii=False`, two-space indentation and a trailing newline, so
`café` stays `café` and the file stays readable and diffable.

### The running bubble does not reload yet

Quote changes are picked up when the application starts. If you run
`rem-bubbles quote add ...` while the bubble is already running, the running
window keeps the collection it loaded until you restart it. File watching and
live reload may come later.

## Quote file format

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
2. the file named by `[quotes].file` in your `config.toml`
3. `~/.config/rem-bubbles/quotes.json` — the default personal collection
4. `quotes.json` in the repository root — a checkout-local collection, for development
5. `examples/quotes.json` — tracked sample data
6. a single built-in emergency quote

**Your own quotes outrank anything in the repository**, so an installed copy
run from inside a checkout still shows your collection, and no path argument is
ever needed.

The root `quotes.json` is git-ignored (`/quotes.json` in `.gitignore`), so a
checkout-local collection stays out of the repository while `examples/quotes.json`
remains tracked. Only the CLI ever writes, and only to your personal file
(2 or 3 above) — never to `examples/` or the repository root.

An explicit path is used even when it does not exist, so you get a real "not
found" rather than a silent fallback. The rest are skipped when absent: not
having created a personal collection yet is the normal state before your first
`quote add`, not an error worth printing on every launch. If a file exists but
fails to load, the error is printed and the next candidate is tried. If none
work, the built-in quote keeps the window openable.

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

## Tests

Nothing in the quote engine, the configuration layer or the CLI needs GTK,
Wayland or a display server, so the whole suite runs anywhere:

```bash
.venv/bin/python -m unittest discover -s tests
```

The tests use temporary directories and a temporary `XDG_CONFIG_HOME`
throughout; they never read or write your real `~/.config/rem-bubbles`.

## Layout

```text
src/rem_bubbles/cli.py          command parsing, quote management, GUI dispatch
src/rem_bubbles/app.py          application lifecycle, CSS loading, GTK entry point
src/rem_bubbles/bubble.py       layer-shell window, collapsed/expanded UI, transitions
src/rem_bubbles/quote_store.py  quote parsing, validation, daily selection, navigation, persistence
src/rem_bubbles/config.py       XDG paths, config.toml, quote source resolution
assets/style.css                all styling
examples/config.toml            sample configuration
examples/quotes.json            sample quote collection
tests/                          quote engine, config, CLI (no display server needed)
```

`cli.py`, `config.py` and `quote_store.py` import no GTK. Presentation stays in
`bubble.py`; quote data — both reading and writing — stays in `quote_store.py`,
so the on-disk format has exactly one home.

The console script is `rem_bubbles.cli:main`, not the GTK application, because
it has to be importable with no display server. `cli.py` imports
`rem_bubbles.app` inside `run_gui()` only, which is what keeps that module's
`CDLL("libgtk4-layer-shell.so")` running before anything pulls in libwayland.
So this works on a machine with no Wayland session at all:

```bash
env -u WAYLAND_DISPLAY -u DISPLAY rem-bubbles quote list
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

The top and left margins are constructor arguments on `BubbleWindow`
(`DEFAULT_MARGIN_TOP` / `DEFAULT_MARGIN_LEFT` in `bubble.py`), so they can be
adjusted without touching the layer-shell setup.
Note that a compositor places non-exclusive layer surfaces below any exclusive
zone already claimed by a bar, so the effective on-screen `y` is the bar height
plus the top margin.
