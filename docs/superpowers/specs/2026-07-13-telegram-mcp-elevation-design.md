# Telegram MCP — Production-Grade Elevation

**Date:** 2026-07-13
**Scope:** Comprehensive quality improvement to bring the telegram-mcp project to production standards.
**Owner:** Mohamed (project maintainer)

---

## 1. Goal

Elevate the `telegram-mcp` codebase from "works well" to "production-grade":

- Comprehensive type safety across all entry points
- Professional documentation suite (security, contributing, architecture, changelog)
- Eliminated code duplication and brittle workarounds
- Linting/types/docs enforced in CI

Out of scope for this spec:
- Splitting god modules (1791+ line `messages.py`, 1272-line `chats.py`)
- Full test coverage of the 80+ tools (separately scoped)
- Refactoring `runtime.py` into smaller submodules

These are deferred — captured in the audit but not addressed here.

---

## 2. Changes

### 2.1 Type Safety (Option 3)

| File | What changes |
|---|---|
| `copy_topics.py` | Add complete type hints to all functions, return types, locals |
| `session_string_generator.py` | Add type hints to `_render_qr`, `_qr_login`, `_phone_login`, `main`; type-annotate all locals |
| `pyproject.toml` | Add `[tool.mypy]` strict section; pin known third-party ignores (telethon, mcp) |
| `.pre-commit-config.yaml` | Add `mypy` hook |
| `.github/workflows/python-lint-format.yml` | Add `mypy` step |

mypy will run in **advisory mode** (no `fail` block) initially, since adding strict types to existing legacy code in `telegram_mcp/tools/` is out of scope. The new `copy_topics.py` will be fully typed and serve as the reference for future contributors.

### 2.2 Professional Documentation (Option 4)

New files at repo root:

| File | Purpose |
|---|---|
| `CHANGELOG.md` | Version history in [Keep a Changelog 1.1.0](https://keepachangelog.com/) format |
| `SECURITY.md` | Vulnerability disclosure policy; responsible for a project that handles Telegram session credentials |
| `CONTRIBUTING.md` | Move contribution guidance from README → standalone file (referenced by GitHub PR template) |
| `ARCHITECTURE.md` | System architecture diagram (ASCII), module boundaries, data flow, account routing, file-path security design |

The README's existing "Contributing" section becomes a one-line pointer to `CONTRIBUTING.md`.

### 2.3 Code Quality ("What you see is right")

#### 2.3.1 Consolidate `iter_forum_topics`

Three implementations of the same offset-based forum topic pagination exist:

- `telegram_mcp/tools/chats.py:list_topics` (uses raw `GetForumTopicsRequest`)
- `telegram_mcp/tools/chats.py:copy_topic` (uses `functions.messages.GetForumTopicsRequest`)
- `copy_topics.py:get_all_topics` (same as above)

**Fix:** Introduce `telegram_mcp/forum_pagination.py` with a single `async def iter_forum_topics(client, entity) -> AsyncIterator[types.ForumTopic]`. Both `chats.py` callers and `copy_topics.py` consume this. The duplicate code in `copy_topics.py` is removed.

#### 2.3.2 Replace `from telegram_mcp.runtime import *`

Each tool file (`chats.py`, `messages.py`, etc.) uses star import from `runtime.py`. This hides dependencies and breaks `pyflakes`.

**Fix:** Switch each tool file to explicit imports from the new submodules (introduced in 2.3.4 below). All call sites verified.

#### 2.3.3 Remove dual-path fallback in `mute_chat`/`unmute_chat`

`chats.py:783-849` has a vestigial fallback that activates when `telethon.tl.functions` lacks `EditNotifySettingsRequest`/`ResetNotifySettingsRequest`. Telethon 1.44+ is pinned in `pyproject.toml`, so the fallback is dead code that increases complexity.

**Fix:** Remove the fallback branches, keep only the canonical path. Tests in `test_runtime.py` already cover proxy/multi-account behavior; no behavior change.

#### 2.3.4 Introduce `telegram_mcp/file_path_security.py` (optional)

If time permits, extract file-path safety helpers from `runtime.py` into a dedicated module. This makes the star-import cleanup mechanical. **Skip if scope tightens.**

### 2.4 CI / Workflow

#### `.github/workflows/python-lint-format.yml`
- Add `mypy`-equivalent step (using `python -m mypy --no-incremental .` with `|| true`)
- Existing `flake8`/`black` checks unchanged

#### `.pre-commit-config.yaml`
- Add `mypy` hook (advisory only — never blocks commits)

---

## 3. Design Decisions

### Why advisory mypy (not strict)?

The legacy code in `telegram_mcp/tools/` uses untyped call helpers from telethon. Full strict mode would touch ~70 tools and is independently scoped. By running mypy in advisory mode, we get a baseline now and a roadmap later. New code (`copy_topics.py`, refactored top-level scripts) is fully typed.

### Why a separate `forum_pagination.py` instead of inlining in `runtime.py`?

`runtime.py` is already 1176 lines (the audit flagged it as a "junk drawer"). Adding more to it makes the second cleanup (deferred) harder. A focused 30-line module with one clear responsibility is the right home.

### Why move `CONTRIBUTING.md` content out of README?

GitHub's PR template and many bots look for a top-level `CONTRIBUTING.md` file. Keeping it inline in README hides it from tooling and from casual readers browsing the repo.

---

## 4. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| mypy finds type errors that block the advisory run | Run as `\|\| true`; capture errors in CI log for later hardening |
| Removing dual-path fallback breaks users on old Telethon | Floor pinned at 1.44 in `pyproject.toml`; verify in `requirements.txt` and `poetry.lock` first |
| `iter_forum_topics` consolidation changes behavior for a tool | Run full test suite; manual smoke test of `copy_topic` against a test chat |
| README refactor loses useful info | Diff README after extracting CONTRIBUTING; preserve any unique content |

---

## 5. Verification

After implementation:

```bash
# Lint / format
uv run black --check .
uv run flake8 .

# Type check (advisory)
uv run mypy copy_topics.py session_string_generator.py

# Tests
uv run pytest
```

Doc verification (manual):
- Each new `.md` file renders correctly on GitHub.
- Links from README → CONTRIBUTING work.
- `CHANGELOG.md` entries match current version (2.0.1).

---

## 6. Out of Scope (Deferred)

- Splitting `messages.py`, `chats.py`, `runtime.py` into smaller modules
- Adding unit tests for the 70+ tools in `telegram_mcp/tools/*.py`
- Refactoring `_apply_exposed_tools_mode` to not depend on FastMCP private internals
- Migrating `telegram_mcp/install_guard.py` import to `telegram_mcp/__init__.py`

Each of these is captured in the audit and reserved for a future design cycle.

---

## 7. File Touch List

```
NEW  CHANGELOG.md
NEW  CONTRIBUTING.md
NEW  SECURITY.md
NEW  ARCHITECTURE.md
NEW  telegram_mcp/forum_pagination.py
MOD  copy_topics.py            # type hints + use simplify_sanitize
MOD  session_string_generator.py  # type hints
MOD  pyproject.toml            # add [tool.mypy]
MOD  .pre-commit-config.yaml   # add mypy hook
MOD  .github/workflows/python-lint-format.yml  # add mypy step (advisory)
MOD  README.md                 # extract CONTRIBUTING; link to new docs
MOD  telegram_mcp/tools/chats.py  # dedup pagination, remove fallback, explicit imports
MOD  .gitignore                # ignore new checkpoint files if any
```
