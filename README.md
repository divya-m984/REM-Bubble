# REM Bubbles

Tiny thoughts and reminders that live on your Wayland desktop.

A small floating bubble with your day's quote, and a warm glow when something is due.

## What is REM Bubbles?

REM Bubbles is a minimal, persistent desktop companion that keeps a rotating daily quote in a floating bubble and reminds you of pending tasks—all without claiming screen space or requiring a background daemon.

The bubble sits on your desktop's overlay layer. Click it to expand and see the day's quote. Navigate to other quotes with arrow keys. When a reminder is due, the bubble glows warmly and opens directly to that reminder instead. Snooze for 10 minutes or dismiss it. Your quotes and reminders live in your own configuration directory, never synced, never shared.

Hyprland + Wayland focused. Built on GTK4 + PyGObject, with single-instance behavior via Gio and optional desktop notifications.

## Features

- **Daily rotating quotes** — deterministic quote for each day; navigate to others with arrow keys
- **Personal reminders** — one-time, daily, or weekly; snooze 10m or dismiss
- **Floating bubble** — no reserved screen space, sits on the overlay layer with exclusive zone 0
- **One instance** — multiple runs hand over to the first and exit cleanly
- **Atomic persistence** — all file writes are safe even if interrupted
- **Optional notifications** — desktop notification per due reminder (off by default)
- **Terminal commands** — manage quotes and reminders from the CLI, no GUI editor needed
- **Autostart helper** — prints the Hyprland config line you need; makes no changes
- **Diagnostics** — `rem-bubbles doctor` checks your setup and session readiness
- **Clean shutdown** — Ctrl+C and SIGTERM both exit tidily with no traceback

## Screenshot

*(A screenshot here would show the floating bubble on a Hyprland desktop. For now, see the examples below and try it yourself.)*

## Requirements

- **Linux** — Hyprland + Wayland (developed and tested on Arch)
- **Python** ≥ 3.11
- **System packages** (via pacman or your distro):
  - `gtk4`
  - `gtk4-layer-shell`
  - `python-gobject`

The project is currently designed and tested for **Arch Linux + Hyprland**. It may work on other Wayland compositors and Linux distributions, but that is not a current design goal.

## Installation

### From source (development)

```bash
git clone https://github.com/divya-m984/REM-Bubble.git
cd REM-Bubble
python -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e .
```

The `--system-site-packages` flag is important: PyGObject and GTK4 bindings come from Arch system packages, not PyPI. Without it, `pip` would attempt to build PyGObject from source, which is not the goal.

Then run:

```bash
rem-bubbles
```

## Quick Start

```bash
# Set up your configuration directory
rem-bubbles init

# Add a quote
rem-bubbles quote add "Keep making weird things."

# Add a one-time reminder
rem-bubbles reminder add "Submit report" --at "2026-08-30 18:00"

# Add a recurring reminder
rem-bubbles reminder add "Weekly review" --at "2026-08-31 09:00" --repeat weekly

# Launch the bubble
rem-bubbles
```

Everything else is a terminal command:

```bash
rem-bubbles quote list
rem-bubbles quote disable some-quote-id
rem-bubbles reminder list
rem-bubbles reminder enable some-reminder-id
rem-bubbles doctor
rem-bubbles integration hyprland
```

## Your Personal Data

REM Bubbles stores your quotes and reminders in `~/.config/rem-bubbles/`:

```
~/.config/rem-bubbles/
├── config.toml
├── quotes.json
└── reminders.json
```

If `$XDG_CONFIG_HOME` is set, it uses `$XDG_CONFIG_HOME/rem-bubbles/` instead.

**Your data stays private:** Nothing in this repository is ever written to. The files in `examples/` are tracked sample data for reference only. You own your quotes and reminders completely.

## Configuration

Create or edit `~/.config/rem-bubbles/config.toml`:

```toml
[quotes]
file = "quotes.json"

[reminders]
file = "reminders.json"

[notifications]
enabled = false
```

All sections are optional. `enabled` defaults to `false`, so updating REM Bubbles never starts sending notifications without your permission.

## Quotes

Quotes are stored in JSON and managed from the CLI:

```bash
rem-bubbles quote list              # show all quotes
rem-bubbles quote add "My quote"    # add a new quote
rem-bubbles quote add "By author" --author "Author Name"
rem-bubbles quote disable quote-id  # hide it temporarily
rem-bubbles quote enable quote-id   # show it again
rem-bubbles quote remove quote-id   # delete it
```

Each day, one enabled quote is selected deterministically. In the bubble, press `<` and `>` to navigate to other quotes. Restarting returns to that day's default.

See `examples/quotes.json` for the schema.

## Reminders

Reminders are scheduled from the CLI and shown in the bubble when due:

```bash
rem-bubbles reminder list           # show all reminders

# One-time reminder
rem-bubbles reminder add "Task" --at "2026-08-30 18:00"

# Daily reminder
rem-bubbles reminder add "Stand up" --at "09:00" --repeat daily

# Weekly reminder
rem-bubbles reminder add "Review" --at "2026-08-31 09:00" --repeat weekly

rem-bubbles reminder disable reminder-id    # pause it
rem-bubbles reminder enable reminder-id     # resume it
rem-bubbles reminder remove reminder-id     # delete it
```

When a reminder is due, **Snooze 10m** or **Dismiss** it directly in the bubble. Snooze hides it for 10 minutes; Dismiss closes that occurrence (for recurring reminders, the next one will still appear).

**Important:** The running bubble only picks up reminder changes on restart—no live reload yet.

See `examples/reminders.json` for the schema.

## Desktop Notifications

Optional. By default, disabled.

To enable:

```toml
[notifications]
enabled = true
```

When enabled, a reminder falling due also sends a desktop notification. The card in the bubble is still the source of truth for Snooze and Dismiss. One notification per reminder occurrence, one more if you snooze and the snooze expires. Restarting may send a notification again if a reminder is still overdue—the notification state is in-memory only.

## Hyprland Autostart

Ask REM Bubbles for the line to add:

```bash
rem-bubbles integration hyprland
```

It prints something like:

```
exec-once = /path/to/rem-bubbles
```

Copy that line into your `hyprland.conf` and start a new Hyprland session. REM Bubbles makes no changes to your config—you paste the line yourself.

See `examples/hyprland.conf` for a documented example.

## Diagnostics

```bash
rem-bubbles doctor
```

Checks your Python, executable paths, config, quote file, reminder file, notifications setting and Wayland environment. Exits `0` if everything looks good, `1` if there is a problem.

It reports **counts, not contents**—your reminders and quotes are personal, and this output is meant for bug reports.

## Limitations

- **No background daemon** — reminders only fire while REM Bubbles is running
- **No live reload** — quote and reminder file changes require restart
- **Hyprland/Wayland only** — developed for Hyprland; compatibility with other compositors untested
- **No natural-language dates** — use ISO 8601 (e.g. `2026-08-30 18:00`)
- **No GUI editor** — manage everything from the CLI
- **No timezone support** — times are local wall-clock times
- **Notification deduplication is in-memory only** — restart may re-notify for the same overdue reminder

## Architecture

```
CLI
 ├── quote management (list, add, enable, disable, remove)
 ├── reminder management (list, add, enable, disable, remove)
 ├── init (configuration directory setup)
 ├── doctor (diagnostics)
 └── integration (autostart helpers)

GUI (GTK4 + Layer Shell)
 ├── single-instance Gio.Application
 ├── floating bubble window
 ├── quote or reminder card rendering
 ├── navigation and snooze/dismiss
 ├── 30-second scheduler for due reminders
 └── optional desktop notifications

Data Layer
 ├── quote storage & selection (daily deterministic)
 ├── reminder storage & scheduling (none/daily/weekly recurrence)
 ├── XDG config directory management
 ├── atomic file writes (safe even if interrupted)
 └── in-memory notification deduplication
```

All data operations (`quote_store`, `reminder_store`, `config`, `persistence`) are GTK-free, so they can run over SSH or in a CI environment. Only `app.py` and `bubble.py` import GTK.

See the `Layout` section of the old README for a module-by-module breakdown.

## Testing

```bash
.venv/bin/python -m unittest discover -s tests
```

Tests cover the quote engine, reminder engine, notifications, configuration, CLI, and lifecycle. Most run without a display server. A small number of layer-surface and single-instance tests require a live Wayland session and are skipped with a reason when unavailable.

Tests use temporary directories and temporary `$HOME` throughout; nothing touches your real `~/.config/rem-bubbles`.

## Roadmap

Not yet planned:
- Background daemon or systemd service
- Natural-language date parsing
- Timezone support
- GUI editor or settings window
- Sound or urgency levels
- File watching and live reload
- Custom snooze durations

The focus remains on keeping REM Bubbles small and reliable, not on feature growth.

## License

See the `LICENSE` file. *(License has not yet been selected for the initial release.)*

## Contributing

See `CONTRIBUTING.md` for development setup, testing, and submitting changes.

---

**Built on:** GTK4, PyGObject, gtk4-layer-shell, Gio, GLib.

**Repository:** https://github.com/divya-m984/REM-Bubble
