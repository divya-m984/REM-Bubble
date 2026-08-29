# REM Bubbles

A tiny persistent desktop quote and reminder companion for Wayland.

A small bubble sits near the top-left of the desktop. Click it and it expands
into a quote card showing the day's quote; click the collapse control and it
shrinks back. When a reminder falls due the bubble takes on a warm glow, and
opening it shows the reminder instead, with **Snooze 10m** and **Dismiss**. It
lives on the layer-shell overlay layer with an exclusive zone of `0`, so it
floats above everything without reserving any space in the tiling layout.

## Status

Milestone 5 — graphical foundation, the quote engine, personal reminders with
none/daily/weekly recurrence, a terminal interface for managing both in
`~/.config/rem-bubbles/`, and the desktop integration that makes it usable as a
permanent part of a session: one instance, clean shutdown, optional desktop
notifications, an autostart helper and a diagnostic command.

Not implemented yet: a background daemon, a systemd service, sound, any editing
in the GUI (no add button, edit dialog, delete button, calendar picker, settings
window or file chooser), a `reminder edit` command, natural-language dates,
timezone support, and live reload of a running bubble.

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
rem-bubbles doctor                           # check the setup, change nothing
rem-bubbles integration hyprland             # print an autostart line

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

Three optional tables, one key each:

```toml
[quotes]
file = "quotes.json"

[reminders]
file = "reminders.json"

[notifications]
enabled = false
```

Each `file` may be relative (resolved against the directory holding
`config.toml`, so the above means `~/.config/rem-bubbles/quotes.json` and
`~/.config/rem-bubbles/reminders.json`), absolute, or start with `~`:

```toml
[quotes]
file = "/some/private/location/my-quotes.json"
```

Every table is optional. The two `file` keys default to the file beside the
config and `enabled` defaults to `false`, so **a `config.toml` written before
reminders or notifications existed is still complete** — if it contains only
`[quotes]`, reminders live at `~/.config/rem-bubbles/reminders.json` anyway,
notifications stay off, and `rem-bubbles init` will not rewrite your file to add
either section.

That is the whole format for now — no theme, position or snooze settings, and
no notification sound, urgency, timeout or per-reminder settings.

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
occurrence. There is no watchdog and no restart loop either: if it crashes, it
stays stopped, because a crash hidden by a supervisor is a bug nobody ever
fixes.

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

## Desktop notifications

Optional, and **off unless you turn them on**:

```toml
[notifications]
enabled = true
```

The default is `false` deliberately. Updating REM Bubbles must never start
putting notifications on the screen of somebody who never asked for them, so a
`config.toml` with no `[notifications]` table means exactly the same thing as
`enabled = false`.

With it on, a reminder falling due also raises a desktop notification:

```text
REM Bubbles
Submit the report.
Due Aug 30 · 6:00 PM
```

**The reminder card remains authoritative.** A notification is an extra signal
pointing at it, never a replacement: the bubble still glows, the reminder still
takes priority in the card, and **Snooze 10m** and **Dismiss** still live there
and are still what changes anything on disk.

### One notification per due episode

The scheduler runs every 30 seconds. It does not notify every 30 seconds. A
reminder is notified once per *episode*, identified by three things together —
the reminder, its current occurrence, and its current snooze. So:

- **Ticks.** A reminder that has been due for an hour has produced one
  notification, not 120.
- **Recurrence.** A daily 08:00 reminder notifies at Monday 08:00, says nothing
  at Monday 08:30, and notifies again at Tuesday 08:00. Dismissing Monday does
  not suppress Tuesday.
- **Snooze.** Snoozing takes the reminder out of the queue and does not notify
  again immediately. When the snooze expires and the same occurrence becomes due
  again, you get **one** more notification — and then no more until something
  else changes.
- **Dismissal.** A dismissed occurrence never notifies again. A recurring
  reminder still notifies for its *next* occurrence.
- **Expanding and collapsing** the bubble changes nothing about notifications.

Snoozing or dismissing also withdraws the notification from your desktop, so it
stops showing something you have already dealt with.

### Two limitations worth knowing

**Restarting may re-notify.** Deduplication is kept in memory only — nothing
about notifications is written to `reminders.json`, and there is no
`last_notified_at` field. If you deliberately restart REM Bubbles while a
reminder is still overdue, you may get one more notification for an episode that
was already announced. The persisted reminder state — snoozed, dismissed,
enabled — is unaffected, and that is the state that matters.

**A missing backend is not an error.** If no desktop notification service is
running, the send fails, the failure is reported to stderr **once** rather than
every 30 seconds, and everything else carries on unchanged: reminders stay
scheduled, nothing is marked dismissed, and the card behaves exactly as it
always does.

### Clicking a notification

Clicking the notification activates the **already-running** REM Bubbles — it
never starts a second process — expands the card and shows that reminder, even
if it was not the one the ordering would have picked. If the reminder has since
been snoozed, dismissed or removed, the window simply opens onto whatever is
relevant now rather than resurrecting something you finished with.

## Running it permanently

### One instance

Only one REM Bubbles runs per session. This is Gio's own D-Bus uniqueness, via
the application id — there is no pidfile, no lockfile and no process-name
matching. Run it a second time and the second invocation hands over to the first
and exits `0`:

```bash
rem-bubbles      # terminal 1: the bubble appears
rem-bubbles      # terminal 2: exits immediately, the first one expands
```

The first process keeps running and keeps everything: the quote you had
navigated to, the loaded reminders, snooze and dismiss state, and its single
30-second timer. No second layer surface is created and no second scheduler
starts.

### Stopping it

`Ctrl+C` and `SIGTERM` both stop it cleanly, with no traceback:

```bash
rem-bubbles
^C
```

Signals are turned into ordinary main-loop events, so they never interrupt
Python mid-statement. Both routes — and closing the window, and an ordinary quit
— go through the same single cleanup path, which removes the 30-second timer and
drops the in-memory notification state. Nothing is written during shutdown, so
stopping REM Bubbles can never be the thing that changed a reminder.

### Starting it with your session

```bash
rem-bubbles integration hyprland
```

```text
REM Bubbles Hyprland autostart:

    exec-once = /absolute/path/to/rem-bubbles

Add that line to your Hyprland configuration, then start a new Hyprland session.
Nothing was written — your Hyprland configuration is unchanged.
```

**It prints the line. It does not add it.** Your Hyprland configuration is not
edited, `hyprctl reload` is not called, and there is no `--install` mode. Copy
the line into your `hyprland.conf` yourself — a compositor that will not start
because a tool edited its config behind your back is a much worse outcome than
one line of copy and paste.

The path is absolute on purpose: a Hyprland session does not activate a virtual
environment, so `exec-once = rem-bubbles` would not find a venv install. The
printed path names whichever installation is actually running. If it cannot be
determined, the command says so and exits `1` rather than printing a line it
knows would not work.

See `examples/hyprland.conf` for a documented example.

There is **no systemd unit, no timer and no cron job**, by design. Hyprland's
`exec-once` is the intended integration, and the scheduler inside the running
GUI is the only scheduler there is.

## Diagnostics

```bash
rem-bubbles doctor
```

```text
REM Bubbles doctor

  Version:       rem-bubbles 0.1.0
  Python:        3.14.7
  Executable:    /path/to/.venv/bin/rem-bubbles
  Config dir:    /home/you/.config/rem-bubbles

  Config:        /home/you/.config/rem-bubbles/config.toml (parses)
  Quotes:        /home/you/.config/rem-bubbles/quotes.json (12 total, 11 enabled)
  Reminders:     /home/you/.config/rem-bubbles/reminders.json (3 total, 3 enabled, 1 due)
  Notifications: disabled

  WAYLAND_DISPLAY: wayland-1
  HYPRLAND_INSTANCE_SIGNATURE: set

No problems found.
```

It exits `0` when nothing is likely to stop REM Bubbles behaving as intended,
and `1` for a malformed `config.toml`, malformed quote data, malformed reminder
data or a quote collection with nothing enabled. A **missing `reminders.json` is
not a problem** — having no reminders is a normal way to use it — and neither is
a missing personal `quotes.json`, because the fallback chain still has something
to show.

`doctor` reports **counts, never contents**: your quotes and reminders are
personal, and a diagnostic is the kind of output that gets pasted into a bug
report. It does not print the Hyprland instance signature either, only whether
one is set.

It imports no GTK, opens no window and writes nothing, so it works anywhere —
including over SSH with no compositor at all, which is where a diagnostic is
most useful:

```bash
env -u WAYLAND_DISPLAY -u DISPLAY rem-bubbles doctor
```

Starting the bubble normally stays quiet. Only real startup problems go to
stderr; detailed diagnostics live in `doctor`, not on every launch.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
```

Nothing in the quote engine, the reminder engine, the notification engine, the
configuration layer or the CLI needs GTK, Wayland or a display server, so almost
the whole suite runs anywhere. In particular every notification rule — one per
episode, once per recurrence, one more after a snooze expires, never after a
dismissal — is asserted **without a desktop notification daemon**, because the
policy lives in a GTK-free module that reaches the outside world through a plain
callable.

A small number of tests genuinely need a compositor: a layer surface cannot be
created without one, so the single-instance, signal-shutdown and
notification-click tests start real processes under a live Wayland session.
Those are skipped, with a reason, when there is no session — rather than faked
into passing, since a single-instance check that only compared process names
would prove nothing.

The tests use temporary directories, a temporary `HOME` and a temporary
`XDG_CONFIG_HOME` throughout; they never read or write your real
`~/.config/rem-bubbles`, and nothing touches `~/.config/hypr`. No test changes
the system clock either: every time-dependent method takes an optional `now`, so
recurrence, snooze expiry, overdue states and notification episodes are all
exercised with explicit datetimes.

## Layout

```text
src/rem_bubbles/cli.py             command parsing, quote/reminder management, doctor, integration
src/rem_bubbles/app.py             application lifecycle, signals, single instance, notification delivery
src/rem_bubbles/bubble.py          layer-shell window, bubble/quote/reminder UI, the 30s check
src/rem_bubbles/quote_store.py     quote parsing, validation, daily selection, navigation, persistence
src/rem_bubbles/reminder_store.py  reminder parsing, validation, recurrence, due state, snooze, dismissal
src/rem_bubbles/notifications.py   notification policy: deduplication, wording, which card to show
src/rem_bubbles/config.py          XDG paths, config.toml, quote/reminder/notification resolution
src/rem_bubbles/persistence.py     the atomic file replace both collections share
assets/style.css                   all styling
examples/config.toml               sample configuration
examples/hyprland.conf             sample autostart snippet (documentation only)
examples/quotes.json               sample quote collection
examples/reminders.json            sample reminder collection (schema documentation only)
tests/                             quote, reminder and notification engines, config, CLI, lifecycle
```

`cli.py`, `config.py`, `quote_store.py`, `reminder_store.py`,
`notifications.py` and `persistence.py` import no GTK. Presentation stays in
`bubble.py`; quote data stays in `quote_store.py` and reminder data in
`reminder_store.py`, both directions, so each on-disk format has exactly one
home. The window never does calendar arithmetic — it asks "what is due now?"
and renders the answer.

Notifications follow the same split. `notifications.py` decides *whether*
anything deserves announcing and *what it says*; `app.py` does the announcing,
with `Gio.Notification` and `Gtk.Application.send_notification`. No subprocess
is spawned, `notify-send` is never invoked, and no dependency was added for any
of it.

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
env -u WAYLAND_DISPLAY -u DISPLAY rem-bubbles doctor
env -u WAYLAND_DISPLAY -u DISPLAY rem-bubbles integration hyprland
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
