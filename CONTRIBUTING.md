# Contributing to REM Bubbles

Thanks for your interest in contributing to REM Bubbles! This document covers development setup and expectations.

## Development Setup

### Prerequisites

- Arch Linux (or similar Wayland-capable Linux)
- Hyprland or another Wayland compositor
- Python ≥ 3.11
- System packages: `gtk4`, `gtk4-layer-shell`, `python-gobject`, `git`

### Environment

```bash
git clone https://github.com/divya-m984/REM-Bubble.git
cd REM-Bubble
python -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e .
```

The `--system-site-packages` flag is critical: PyGObject and GTK4 come from Arch, not PyPI.

### Running

```bash
# Launch the bubble
rem-bubbles

# Run all tests
.venv/bin/python -m unittest discover -s tests

# Run tests with verbose output
.venv/bin/python -m unittest discover -s tests -v

# Run a specific test module
.venv/bin/python -m unittest tests.test_cli

# Run a specific test case
.venv/bin/python -m unittest tests.test_quote_store.QuoteStoreTests.test_load_quotes
```

### Code Style

Keep it simple:

- Follow the existing code style (docstrings, imports, structure)
- Maintain the split: data logic (GTK-free modules) vs. presentation (GTK modules)
- Keep CLI headless — no imports of GTK at module level in `cli.py`
- Preserve the load-order requirement: `libgtk4-layer-shell.so` before libwayland

### Key Design Principles

- **Single instance** — one REM Bubbles per session, via Gio
- **Atomic persistence** — all file writes go through `persistence.write_text_atomic()`
- **No daemon** — everything runs in the main process
- **No live reload** — users must restart for file changes to take effect
- **XDG compliant** — use `~/.config/rem-bubbles/` and respect `$XDG_CONFIG_HOME`
- **Private data** — never write to `.venv`, `examples/`, or the repository root

### Testing Expectations

- Run `unittest discover -s tests` before committing
- All existing tests must pass
- Add tests for new functionality where practical
- Use temporary directories and `$HOME` for file operations; never touch the user's real `~/.config/rem-bubbles`

### Commit Messages

Keep them short and clear:

```
feat: add feature description
fix: describe what was broken and how
refactor: reorganize this part
docs: update documentation
test: add or fix tests
```

### What Not to Do

- Do not add platform-specific dependencies without strong justification
- Do not import GTK in modules that need to stay headless
- Do not change the persistent file format without considering backward compatibility
- Do not modify Hyprland configuration from within REM Bubbles
- Do not commit personal `~/.config/rem-bubbles` data
- Do not commit `.claude`, `.venv`, `__pycache__`, or build artifacts

### Submitting Changes

1. Create a focused branch from `main`
2. Make your changes and test locally
3. Ensure all tests pass: `python -m unittest discover -s tests`
4. Keep commits focused; one change per commit when possible
5. Push and open a pull request with a clear description

### Questions?

Open an issue or start a discussion on GitHub.

---

**Welcome, and thank you for contributing!**
