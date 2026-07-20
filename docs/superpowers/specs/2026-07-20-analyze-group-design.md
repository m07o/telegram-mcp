# Analyze Group (Group & Topic Analysis) — Design Spec

> **For agentic workers:** This is a design specification. The implementation plan lives at `docs/superpowers/plans/2026-07-20-analyze-group.md`.

**Goal:** Add a single MCP tool `analyze_group` that gives an AI agent a comprehensive, structured analysis of a forum-enabled supergroup — total topics, duplicate topic titles, gaps (topics with no description/icon/very low activity), dead topics, and basic per-topic statistics.

**Out of scope (Phase 2+):**
- Smart content aggregation across channels
- Quality-aware deduplication
- Reading and comparing message bodies/attachments for "is this the same content?"

These will be informed by what we learn from Phase 1, but explicitly deferred.

---

## Architecture

A two-layer design:

1. **Pure-Python helpers** in `telegram_mcp/group_analysis.py` — operate on a small dataclass `ForumTopicSummary`, no network, no Telethon types. Each helper is a single responsibility and unit-testable in isolation.

2. **MCP tool wrapper** `analyze_group` in `telegram_mcp/tools/groups.py` — calls the existing `list_topics(fetcha_all=True)` to fetch all topics, converts to `ForumTopicSummary`, runs the helpers, and returns structured JSON.

Two output modes:
- `"summary"` (default, low-token): aggregated counts only
- `"detail"`: full per-section raw arrays (also includes a compact `topics[]` array with sampled messages, per the user's enhancement request — directly useful for Phase 2).

All `topic_id` fields are present in every finding so the AI can take immediate action (no second tool call needed to look them up).

**Tech Stack:** Python 3.10+, Telethon (existing), no new external dependencies.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `telegram_mcp/group_analysis.py` | Pure-Python helpers + `ForumTopicSummary` + `MessageSample` dataclasses | **Create** |
| `telegram_mcp/tools/groups.py` | Adds `analyze_group` MCP tool | **Modify** |
| `tests/test_group_analysis.py` | Unit tests for every helper (no network) | **Create** |
| `tests/test_analyze_group.py` | Integration tests via fake Telethon client | **Create** |
| `CHANGELOG.md` | Document the new tool | **Modify** |

---

## Specification

### Helper module: `telegram_mcp/group_analysis.py`

Pure Python, no network, no Telethon imports in the helper bodies. Helpers receive dataclass instances and return dataclass / list / dict.

#### Data classes

```python
@dataclass
class MessageSample:
    """A small per-topic message sample for content-based Phase 2 hints."""
    id: int
    text: str  # sanitized; max 256 chars
    has_media: bool
    date_iso: str | None  # ISO-8601 string or None


@dataclass
class ForumTopicSummary:
    """Decoupled from Telethon; populated by converting raw ForumTopic."""
    id: int
    title: str
    total_messages: int
    last_activity_iso: str | None  # ISO-8601; None if unknown
    icon_emoji_id: int | None  # None means "no icon"
    hidden: bool
    closed: bool
    description: str | None  # may be empty or None
    message_samples: list[MessageSample] = field(default_factory=list)
```

#### Helper functions

| Name | Input | Output |
|---|---|---|
| `normalize_forum_title(title: str) -> str` | raw title | lowercase + stripped punctuation + collapsed whitespace + unicodedata NFKC. Unicode combining marks dropped but NOT characters of other scripts (so Arabic and Latin preserve identity). |
| `find_duplicate_forum_topics(topics: list[ForumTopicSummary]) -> list[DuplicateGroup]` | topics | groups with ≥ 2 topics under same normalized title |
| `find_topic_gaps(topics: list[ForumTopicSummary]) -> list[Gap]` | topics | each gap identifies the topic + kind |
| `find_dead_forum_topics(topics: list[ForumTopicSummary], *, inactivity_days: int)` | topics + int | topic ids whose `last_activity_iso` is older than now − inactivity_days, OR is None |
| `compute_topic_stats(topics: list[ForumTopicSummary]) -> TopicStats` | topics | total counts and median/percentile distribution of `total_messages` |
| `summarize_findings(*, stats, duplicates, gaps, dead_topics) -> str` | findings | one-paragraph English summary line (shown only when `mode="summary"`) |

#### Helper data types

```python
@dataclass
class DuplicateGroup:
    normalized_title: str
    topic_ids: list[int]
    original_titles: list[str]


@dataclass
class Gap:
    kind: str  # one of: "no_description", "no_icon", "low_messages"
    topic_id: int
    detail: str  # human-readable explanation


@dataclass
class TopicStats:
    total_topics: int
    total_messages: int
    median_messages: int
    max_messages: int
    min_messages: int
    p90_messages: int
```

#### Helpers NEVER raise on bad input
- Empty list → empty / zero result.
- Bad `inactivity_days` (≤ 0) → caller is responsible for rejecting upstream; if a helper somehow receives it, treat as `infinity` (no dead topics).
- Helpers return small / predictable types to make unit tests trivial.

---

### MCP tool: `analyze_group(chat_id, *, mode, inactivity_days, account)`

File: `telegram_mcp/tools/groups.py` — placed below the existing topic-related tools (`delete_topic`, `close/reopen/hide/unhide`) at the bottom of the forum block.

```python
@mcp.tool(annotations=ToolAnnotations(
    title="Analyze Group",
    readOnlyHint=True,
    openWorldHint=True,
))
@with_account(readonly=True)
@validate_id("chat_id")
async def analyze_group(
    chat_id: int,
    *,
    mode: str = "summary",
    inactivity_days: int = 90,
    account: Optional[str] = None,
) -> str:
    """..."""
```

#### Validation (the tool)

- `mode` must be `"summary"` or `"detail"`. Else return `'Invalid mode: ...'` string.
- `inactivity_days` must be `> 0`. Else return `'inactivity_days must be > 0'`.
- Use existing `_validate_topic_target(entity)` (lifted into `groups.py` private). If it returns non-None error string, propagate immediately.
- All other errors are caught by `log_and_format_error`.

#### Data flow

```
analyze_group(chat_id, mode, ...)
 ├── resolve_entity, get_client
 ├── _validate_topic_target(entity)         → fail fast
 ├── list_topics(chat_id, fetch_all=True)  (or our own pagination)
 │      → list[Foru­mTopic] (raw Telethon objects)
 ├── _to_summary(topic)                    → ForumTopicSummary per topic
 │      (also samples first/last N messages per topic if mode == "detail")
 ├── helpers in group_analysis.py:
 │      compute_topic_stats(...)
 │      find_duplicate_forum_topics(...)
 │      find_topic_gaps(...)
 │      find_dead_forum_topics(inactivity_days=...)
 │      summarize_findings(...)
 ├── assemble JSON per mode
 └── return JSON string
```

#### Resource for sampling (optional in summary mode, recommended in detail mode)

In `detail` mode, for each topic we collect up to 5 `MessageSample` objects — first/last/most-recent. Sampling is bounded (`max 5`), and uses `iter_messages(peer, reply_to=topic_id, reverse=False)` plus an ascending sort. Empty topic → empty list.

In `summary` mode, sampling is skipped entirely (saves bandwidth).

#### Output: `mode="summary"`

```json
{
  "chat_id": -100xxx,
  "chat_title": "My Group",
  "summary_stats": {
    "total_topics": 612,
    "total_messages": 12340,
    "median_messages_per_topic": 18,
    "max_messages": 950,
    "min_messages": 0,
    "p90_messages": 87
  },
  "findings": {
    "duplicate_topic_groups": 4,
    "topics_with_gaps": 47,
    "dead_topics": 23
  }
}
```

#### Output: `mode="detail"`

Returns everything in `summary` PLUS raw arrays. **Every finding includes `topic_id`** so the AI can act immediately.

```json
{
  "chat_id": -100xxx,
  "chat_title": "My Group",
  "summary_stats": { ... },
  "duplicates": [
    {"normalized_title": "bugs", "topic_ids": [42, 113], "original_titles": ["Bugs", "Bug Reports"]}
  ],
  "gaps": [
    {"kind": "no_description", "topic_id": 42, "detail": "..."},
    {"kind": "no_icon", "topic_id": 87, "detail": "..."}
  ],
  "dead_topics": [42, 113],
  "topics": [
    {"id": 42, "title": "Bugs", "total_messages": 150, "message_samples": [
      {"id": 999, "text": "Bug found in...", "has_media": false, "date_iso": "2026-05-01T10:30:00+00:00"}
    ]}
  ]
}
```

#### Error contract

| Failure | Returned string |
|---|---|
| Chat not found | existing helper output (via `_validate_topic_target`) |
| Chat not a megagroup | `"The specified chat is not a supergroup."` |
| Chat not forum-enabled | `"The specified supergroup does not have forum topics enabled..."` |
| `mode` invalid | `"Invalid mode: X. Must be 'summary' or 'detail'."` |
| `inactivity_days` ≤ 0 | `"inactivity_days must be > 0."` |
| RPC failure mid-tool | via `log_and_format_error(...)` |

---

## Testing strategy

### Unit tests: `tests/test_group_analysis.py`

Pure Python, no mocks. ~25 tests:

- `normalize_forum_title` (6): plain ASCII; Arabic with diacritics; punctuation-only; empty string; mixed scripts (Arabic+English); whitespace multiple collapse
- `find_duplicate_forum_topics` (5): empty; single; pair of duplicates; triple same normalized; non-duplicates; case-insensitive
- `find_topic_gaps` (4): everything OK; only no_description; only no_icon; mixed
- `find_dead_forum_topics` (4): empty dates count as dead; all recent; mixed with cutoff
- `compute_topic_stats` (4): empty; single; ten topics; ties-and-outliers
- `summarize_findings` (2): empty findings; populated

### Integration tests: `tests/test_analyze_group.py`

Uses existing `tests/fakes/` fake Telethon client. ~10 tests:

- `mode="summary"` returns the structured JSON (no `topics`, `duplicates`, etc.)
- `mode="detail"` returns raw arrays
- `mode="invalid"` rejects with clear error
- `inactivity_days=0` rejects
- `inactivity_days=-1` rejects
- non-forum chat → "forum" in error string
- non-supergroup chat → "supergroup" in error string
- Group with 0 topics → empty JSON, helpers don't blow up
- Group with 2 duplicate titles → `duplicates` array contains both
- Group with N topics, all helpers called

### Black / flake8 / mypy

- `uv run black --check .` clean (all files < 99-char lines).
- `uv run flake8 --select=E9,F63,F7,F82 --exclude=.venv .` → 0 errors.
- mypy on `group_analysis.py` and the new section in `groups.py` clean.

Targets:
- New files < 250 lines each.
- `<= 30 minute` implementation per task individually.
- Final test count: ~203 (current) + ~35 new = ~238.
