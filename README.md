# REM Bubbles

A tiny persistent desktop quote and reminder companion for Wayland.

A small bubble sits near the top-left of the desktop. Click it and it expands
into a quote card showing the day's quote; click the collapse control and it
shrinks back. When a reminder falls due the bubble takes on a warm glow, and
opening it shows the reminder instead, with **Snooze 10m** and **Dismiss**. It
lives on the layer-shell overlay layer with an exclusive zone of `0`, so it
floats above everything without reserving any space in the tiling layout.

## Status

Milestone 4 — graphical foundation, the quote engine, personal reminders with
none/daily/weekly recurrence, and a terminal interface for managing both in
`~/.config/rem-bubbles/`.

Not implemented yet: desktop notifications, a background daemon, autostart,
sound, any editing in the GUI (no add button, edit dialog, delete button,
calendar picker, settings window or file chooser), a `reminder edit` command,
natural-language dates, timezone support, and live reload of a running bubble.

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

rem-bubbles reminder list                    # show your reminders
rem-bubbles reminder add "Submit report" --at "2026-08-30 18:00"
rem-bubbles reminder add "Weekly review" --at "2026-08-31 09:00" --repeat weekly
rem-bubbles reminder enable weekly-review    # exact id
rem-bubbles reminder disable weekly-review
rem-bubbles reminder remove weekly-review

rem-bubbles --help
```

The GUI can also still be started directly:

```bash
.venv/bin/python -m rem_bubbles.app
```

## Your data

REM Bubbles keeps your quotes and reminders in your own configuration
directory, following the XDG Base Directory convention:

```text
~/.config/rem-bubbles/
├── config.toml
├── quotes.json
└── reminders.json
```

If `$XDG_CONFIG_HOME` is set to an absolute path, `$XDG_CONFIG_HOME/rem-bubbles/`
is used instead. Nothing in this repository is ever written to: **your quotes
and reminders live outside the checkout and outside Git.** `examples/` is
tracked sample data for reading, not personal storage.

Get started with:

```bash
rem-bubbles init
rem-bubbles quote add "Keep making weird things."
rem-bubbles reminder add "Submit report" --at "2026-08-30 18:00"
```

`init` creates the directory and a default `config.toml`. It never overwrites
an existing file, so running it twice is safe. It deliberately does not create
`quotes.json` or `reminders.json` — your first `quote add` and `reminder add`
do that, which keeps each file containing only entries you chose. `init` is
optional: either `add` alone creates everything it needs.

The directory is created `0700` and files `0600`, since this is personal data.
Files that already exist keep whatever permissions you gave them.

### config.toml

Two optional tables, one key each:

```toml
[quotes]
file = "quotes.json"

[reminders]
file = "reminders.json"
```

Each `file` may be relative (resolved against the directory holding
`config.toml`, so the above means `~/.config/rem-bubbles/quotes.json` and
`~/.config/rem-bubbles/reminders.json`), absolute, or start with `~`:

```toml
[quotes]
file = "/some/private/location/my-quotes.json"
```

Both tables are optional and both default to the file beside the config, so **a
`config.toml` written before reminders existed is still complete** — if it
contains only `[quotes]`, reminders live at
`~/.config/rem-bubbles/reminders.json` anyway, and `rem-bubbles init` will not
rewrite your file to add the section.

That is the whole format for now — no theme, position or snooze settings.

A `config.toml` that cannot be understood is never guessed past. The GUI prints
the problem to stderr and carries on with the default locations rather than
refusing to open. Management commands instead stop with an error and change
nothing, because guessing there could write your data to a file you did not
choose.

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
live reload may come later. The same applies to reminders — see below.

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

## Reminders

A reminder is a piece of text with a time. When that time arrives the bubble
takes on a warm orange glow, and opening it shows a **REMINDER** card instead of
the quote card:

```text
╭──────────────────────────────╮
│ REMINDER                   × │
│                              │
│ Submit the report.           │
│                              │
│ Overdue · Aug 30 · 6:00 PM   │
│                              │
│ [ Snooze 10m ]   [ Dismiss ] │
╰──────────────────────────────╯
```

When nothing is due, REM Bubbles behaves exactly as it always has and shows
quotes.

### Managing reminders

```bash
rem-bubbles reminder list

rem-bubbles reminder add "Submit report" \
  --at "2026-08-30 18:00"

rem-bubbles reminder add "Weekly review" \
  --at "2026-08-31 09:00" \
  --repeat weekly

rem-bubbles reminder enable <id>
rem-bubbles reminder disable <id>
rem-bubbles reminder remove <id>
```

`--at` accepts `2026-08-30 18:00` or `2026-08-30T18:00`, with seconds optional,
and stores `2026-08-30T18:00:00`. `--repeat` is `none` (the default), `daily` or
`weekly`. `--id` sets the id explicitly and `--disabled` adds a reminder without
scheduling it, exactly as for quotes.

Ids are generated from the text the same way quotes' are —
`Submit report` becomes `submit-report`, collisions get `-2`, `-3`, and text
with no usable ASCII slug gets a stable digest like `reminder-a13f84c2`.
`remove`, `enable` and `disable` take an **exact** id.

`reminder list` prints the file it is managing, then each reminder with its id,
text, scheduled time, recurrence and current status:

```text
Reminder file: /home/you/.config/rem-bubbles/reminders.json

! submit-report
  Submit the report.
  Due: 2026-08-30 18:00
  Status: overdue

↻ review-week
  Review my week.
  Due: Weekly · 2026-08-31 09:00
  Status: upcoming

2 reminders (2 enabled, 0 disabled, 1 due)
```

`!` is waiting for you, `↻` recurring, `·` scheduled and quiet. Statuses are
`upcoming`, `due`, `overdue`, `snoozed`, `dismissed` and `disabled`. Listing
never creates or modifies anything; with no reminder file it says
`No reminders yet.` and exits successfully.

Adding a reminder whose time has already passed is allowed — it simply shows up
overdue. There is no `reminder edit` yet: to reschedule, remove it and add it
again.

Unlike quotes, **an empty reminder collection is completely valid.** Zero
reminders, all of them disabled, all of them dismissed — none of these is an
error, and nothing forces one reminder to stay enabled.

### Scheduling

Times are **local wall-clock times**. A reminder at `08:00` is at 08:00 by the
clock on your wall, whatever date it is. There is no timezone support in this
milestone: a `due_at` carrying an offset or a `Z` is rejected rather than
converted, because silently moving your 18:00 to 17:00 would be worse than
refusing to load the file.

Recurrence is `none`, `daily` or `weekly` and nothing else — no monthly, yearly
or cron syntax.

- `none` — one occurrence, at `due_at`. Dismiss it and it is finished.
- `daily` — first at `due_at`, then every calendar day at the same time.
- `weekly` — first at `due_at`, then every seven days at the same time.

**Missed occurrences collapse.** If a daily 08:00 reminder went unseen Monday
through Wednesday and you start REM Bubbles on Thursday at 10:00, there is one
reminder waiting — Thursday's 08:00 — not four. A backlog of identical cards
helps nobody.

A reminder is waiting for you when it is enabled, its most recent occurrence is
at or before now, that occurrence has not been dismissed, and it is not
currently snoozed. An occurrence more than a minute old is described as
**overdue** rather than **due now**.

### Snooze and dismiss

**Snooze 10m** sets `snoozed_until` to ten minutes from now and hides the
reminder until then. The occurrence itself does not move: when the snooze
expires the same occurrence becomes due again. Ten minutes is fixed in this
milestone.

**Dismiss** records the occurrence you just dealt with in
`dismissed_occurrence` and clears any snooze. For a one-time reminder that is
the end of it. For a recurring one it dismisses **only that occurrence** — the
reminder stays enabled and comes back at its next scheduled time.

The card's `×` only collapses the bubble. It does not dismiss, snooze or
disable anything; the reminder is still waiting when you open it again.

`enable` and `disable` do not touch `snoozed_until` or `dismissed_occurrence`
either. Disabling is a pause, not a reset, so re-enabling resumes the schedule
where it was rather than replaying something you already handled.

### While REM Bubbles is running

The window asks the reminder engine what is due every 30 seconds — a GLib
timeout on the main loop, no worker thread and no filesystem polling. That is
enough to notice a reminder falling due and a snooze expiring while you work.

If more than one reminder is due they are ordered by occurrence, oldest first,
with the id breaking ties, and one is shown at a time. Snoozing or dismissing
the one on screen immediately re-evaluates the queue: the next waiting reminder
appears straight away, and when none are left the quote card comes back at
whatever quote you had navigated to.

**There is no daemon.** While REM Bubbles is not running, nothing wakes up and
nothing notifies you. Reminders that fell due in the meantime are evaluated the
next time you start it, and appear then — collapsed to their most recent
occurrence.

There are no desktop notifications, no sound, and no autostart in this
milestone. Reminder presentation stays inside the REM Bubbles window.

### Reminder file format

Reminders live in a JSON array of objects:

```json
[
  {
    "id": "submit-report",
    "text": "Submit the report.",
    "due_at": "2026-08-30T18:00:00",
    "recurrence": "none",
    "enabled": true,
    "snoozed_until": null,
    "dismissed_occurrence": null
  }
]
```

| Field                  | Required | Notes                                                     |
| ---------------------- | -------- | --------------------------------------------------------- |
| `id`                   | yes      | Non-empty string, unique within the file                   |
| `text`                 | yes      | Non-empty after trimming                                   |
| `due_at`               | yes      | Local `YYYY-MM-DDTHH:MM:SS`; the first occurrence          |
| `recurrence`           | no       | `none`, `daily` or `weekly`; defaults to `none`            |
| `enabled`              | no       | `true` or `false`, defaults to `true`                      |
| `snoozed_until`        | no       | Local datetime or `null`; hides it until then              |
| `dismissed_occurrence` | no       | Local datetime or `null`; the occurrence already dealt with |

`[]` is a valid file. Malformed data is reported as a plain message on stderr
rather than a traceback — bad JSON, a non-array root, a missing or blank `id`,
`text` or `due_at`, a malformed or timezone-aware datetime, an unsupported
recurrence, a non-boolean `enabled`, duplicate ids. Nothing is silently
discarded: the first problem stops that file from loading, because a reminder
quietly skipped is a reminder that never fires.

### Where reminders are read from

Highest priority first, and the chain is short:

1. a path supplied explicitly by a caller (`config.load_reminder_store(path)`)
2. the file named by `[reminders].file` in your `config.toml`
3. `~/.config/rem-bubbles/reminders.json` — the default personal collection
4. no reminders

Unlike quotes there is **no repository fallback at all**. There is no
checkout-local reminder file in the chain and `examples/reminders.json` is never
loaded: it documents the schema, nothing more. You must never be shown a sample
reminder as though you had scheduled it.

A missing reminder file is the normal state before your first `reminder add`
and is silent. A file you deliberately pointed somewhere else and which is not
there gets a line on stderr. Malformed data is reported and then stepped over
with an empty collection: reminders are an addition to the bubble, not a
precondition for it, so broken reminder data never costs you your quotes. The
GUI always opens.

Management commands are stricter — malformed configuration or malformed
reminder data exits 1 and writes nothing, because there the risk is corrupting
personal data rather than showing one fewer thing.

### The running bubble does not reload reminders either

The Milestone 3 limitation applies to reminders too. `rem-bubbles reminder add`
while the bubble is already running does not reach that running instance;
restart it to pick the change up. Snooze and Dismiss performed *by* the running
window do update its own collection and are persisted immediately. There is no
filesystem watching yet.

## Tests

Nothing in the quote engine, the reminder engine, the configuration layer or the
CLI needs GTK, Wayland or a display server, so the whole suite runs anywhere:

```bash
.venv/bin/python -m unittest discover -s tests
```

The tests use temporary directories and a temporary `XDG_CONFIG_HOME`
throughout; they never read or write your real `~/.config/rem-bubbles`. No test
changes the system clock either: every time-dependent method takes an optional
`now`, so recurrence, snooze expiry and overdue states are all exercised with
explicit datetimes.

## Layout

```text
src/rem_bubbles/cli.py             command parsing, quote and reminder management, GUI dispatch
src/rem_bubbles/app.py             application lifecycle, CSS loading, GTK entry point
src/rem_bubbles/bubble.py          layer-shell window, bubble/quote/reminder UI, the 30s check
src/rem_bubbles/quote_store.py     quote parsing, validation, daily selection, navigation, persistence
src/rem_bubbles/reminder_store.py  reminder parsing, validation, recurrence, due state, snooze, dismissal
src/rem_bubbles/config.py          XDG paths, config.toml, quote and reminder source resolution
src/rem_bubbles/persistence.py     the atomic file replace both collections share
assets/style.css                   all styling
examples/config.toml               sample configuration
examples/quotes.json               sample quote collection
examples/reminders.json            sample reminder collection (schema documentation only)
tests/                             quote engine, reminder engine, config, CLI (no display server needed)
```

`cli.py`, `config.py`, `quote_store.py`, `reminder_store.py` and
`persistence.py` import no GTK. Presentation stays in `bubble.py`; quote data
stays in `quote_store.py` and reminder data in `reminder_store.py`, both
directions, so each on-disk format has exactly one home. The window never does
calendar arithmetic — it asks "what is due now?" and renders the answer.

Writes go through `persistence.write_text_atomic()`: serialise, re-parse to
prove the result would survive a reload, write to a temporary file in the same
directory, then `os.replace()` it into place. An interrupted or failed write
leaves the existing file exactly as it was. The reminder store persists
*before* it mutates in memory, so a dismiss that cannot reach the disk leaves
the reminder visibly still due rather than pretending it was handled.

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
