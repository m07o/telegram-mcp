# Telegram Topic Migration — Session Artifacts (2026-07-12)

This directory contains the key data files and scripts generated during the masass18 → egyxos topic migration session.

## Data Files

| File | Description | Status |
|------|-------------|--------|
| `masass18_topics.csv` | 612 topics exported from masass18 (topic_id, title, unread_count, total_messages, closed, hidden, created_date) | ✅ Ready |
| `masass18_topics.json` | Same data as JSON for programmatic use | ✅ Ready |
| `missing_topics.json` | 512 topics missing in egyxos (topic_id, title pairs) | ✅ Ready |
| `all_topics_masass18.json` | Full topic data from MCP `list_topics(fetch_all=true)` | ✅ Ready |

## Scripts Created

| Script | Purpose | Location |
|--------|---------|----------|
| `collect_masass18_topics.py` | Collects all 612 topics via fixed pagination (offset_date + offset_id) | `C:\Users\Mohamed\AppData\Local\hermes\skills\telegram\telegram-topic-analyzer\scripts\collect_topics.py` |
| `analyze.py` | CLI for searching, filtering, exporting topic data | `C:\Users\Mohamed\AppData\Local\hermes\skills\telegram\telegram-topic-analyzer\scripts\analyze.py` |
| `create_all_missing_topics.py` | Batch creates missing topics in target using `create_forum_topic` | `C:\tmp\create_all_missing_topics.py` |
| `cleanup_egyxos.py` | Deletes bot noise (interrupt messages, separators) from target group | `C:\tmp\cleanup_egyxos.py` |

## Migration Progress (as of 2026-07-12)

| Metric | Value |
|--------|-------|
| Source topics (masass18) | 612 |
| Target topics (egyxos, start) | ~100 |
| Target topics (egyxos, current) | ~285+ |
| Topics created this session | ~185+ |
| Topics remaining | ~327 |

## Commands Used

```bash
# Get full topic list via fixed MCP
mcp__telegram_mcp__list_topics(chat_id=-1002191043427, fetch_all=true, limit=100)

# Create topic in target
mcp__telegram_mcp__create_forum_topic(chat_id=-1002204837936, title="اسم التوبك")

# Analyze topic data
python scripts/analyze.py stats
python scripts/analyze.py search "الكبير"
python scripts/analyze.py unread --min 100
python scripts/analyze.py export --format csv --output ~/masass18_topics.csv
```

## Key Learnings

1. **Pagination fix was essential** — without it, only 104/612 topics were visible
2. **Always fetch live** — cached JSON becomes stale within hours
3. **Create topics first** — `copy_topic` fails if target topic doesn't exist
4. **Cleanup before user sees target** — user explicitly wants no bot noise
5. **Rate limits** — 1-2s between topic creation, 0.5-3s between message copies depending on media