# Changelog

All notable changes to REM Bubbles are documented in this file.

## [0.1.0] - Unreleased

### Initial Public Release

#### Features

**Bubble & UI**
- Floating bubble window using GTK4 layer-shell, exclusive zone 0
- Deterministic daily quote selection
- Quote navigation with keyboard arrows, wrapping at both ends
- Quote persistence across bubble collapse and restart
- Reminder card with warm orange glow when due
- Snooze 10m and Dismiss actions
- Single-instance behavior via Gio.Application
- Clean shutdown on SIGINT and SIGTERM
- Optional desktop notifications when reminders fall due

**Quote Management**
- Personal quote storage in `~/.config/rem-bubbles/quotes.json`
- Quote add/remove/enable/disable CLI commands
- Auto-generated IDs with collision handling (stable digest fallback)
- Author field support
- Atomic file persistence (safe even if interrupted)
- Fallback chain for quote files (personal → checkout → examples → built-in)

**Reminder Management**
- Personal reminder storage in `~/.config/rem-bubbles/reminders.json`
- Reminder add/remove/enable/disable CLI commands
- One-time, daily, and weekly recurrence
- Local wall-clock times (no timezone support)
- Snooze state and dismissed-occurrence tracking
- 30-second scheduler poll for due reminders
- Overdue state indication
- Missed occurrence collapse (no backlogs)

**Configuration**
- XDG Base Directory compliant
- TOML configuration with optional `[quotes]`, `[reminders]`, `[notifications]` sections
- File path customization per section
- Flexible defaults (sections and keys all optional)
- Backward compatibility (older configs remain valid)

**Desktop Integration**
- `rem-bubbles integration hyprland` prints autostart line without modifying config
- Desktop notification support (Gio.Notification)
- Per-episode notification deduplication (in-memory)
- One notification per reminder occurrence, per snooze expiry

**Utilities**
- `rem-bubbles doctor` for setup diagnostics and validation
- `rem-bubbles init` to create configuration directory
- Headless CLI (no display server needed for management commands)
- Clear error messages for user mistakes (no tracebacks for expected errors)

#### Architecture

- Modular design: data logic separate from GUI
- GTK-free modules: `config`, `quote_store`, `reminder_store`, `notifications`, `persistence`, `cli`
- Layer-shell loaded before GTK (safe load order)
- Single entry point in `cli.py` for portability
- Atomic file writes via `persistence.write_text_atomic()`
- No background daemon or systemd service
- No file watching or live reload

#### Tests

- 760 unit tests covering quote engine, reminder engine, notifications, configuration, CLI, and lifecycle
- Temporary directories and temporary `$HOME` for file operations
- Layer-surface and single-instance tests require live Wayland session (skipped if unavailable)
- No tests modify user configuration

#### Documentation

- Comprehensive README with architecture, configuration, and usage guide
- Example files for configuration, quotes, reminders, and Hyprland autostart
- Inline code comments and docstrings
- API documentation via Python docstrings

#### Not Included (Planned for Later)

- Background daemon or systemd service
- Natural-language date parsing
- Timezone support
- GUI editor (add/edit/delete dialog)
- Custom snooze durations
- Sound or urgency levels
- File watching and live reload
- Animation or theme customization

---

See the repository for detailed feature descriptions, usage examples, and the complete architecture.
