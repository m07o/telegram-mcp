# telegram-topic-analyzer

Analyze and search 612 Telegram forum topics from the **masass18** group (-1002191043427).

## Quick Start

```bash
# 1. First, collect topics (run once)
python scripts/collect_topics.py

# 2. Then analyze
python scripts/analyze.py stats
python scripts/analyze.py search "الكبير"
python scripts/analyze.py unread --min 100
python scripts/analyze.py export --format csv
```

## Commands

| Command | Description | Example |
|---------|-------------|---------|
| `stats` | Overall statistics | `python analyze.py stats` |
| `search <query>` | Search by title | `python analyze.py search "طاقة"` |
| `unread [--min N]` | Topics with unread messages | `python analyze.py unread --min 50` |
| `oldest [N]` | Oldest N topics | `python analyze.py oldest 20` |
| `newest [N]` | Newest N topics | `python analyze.py newest 20` |
| `export [--format csv\|json]` | Export topic list | `python analyze.py export --format csv` |
| `topic <id>` | Show one topic details | `python analyze.py topic 12` |
| `by-language <ar\|en\|mixed>` | Filter by title language | `python analyze.py by-language ar` |

## Data Source

The skill uses `~/all_topics_masass18.json` which contains:

```json
{
  "group_id": -1002191043427,
  "group_title": "masass18",
  "total_count": 612,
  "topics": {
    "12": {"title": "طاقة نور", "unread_count": 0, "total_messages": 0, "top_message_id": 1382, ...},
    "32022": {"title": "الكبير 8", "unread_count": 802, "total_messages": 0, "top_message_id": 71234, ...}
  }
}
```

## Key Statistics (from 612 topics)

- **Total topics**: 612
- **Arabic titles**: 466
- **English titles**: 144
- **Mixed/Other**: 2
- **Topics with unread**: 133
- **Total unread messages**: 6,847+
- **Closed topics**: 2
- **Hidden topics**: 0

## Top Active Topics (by unread)

| Topic ID | Title | Unread |
|----------|-------|--------|
| 32022 | الكبير 8 | 802 |
| 62932 | Teen Wolf | 335 |
| 68318 | شارع 9 | 301 |
| 23630 | نقطه سوده | 244 |
| 52015 | وسط البلد | 220 |

## Oldest Topics (by top_message_id)

| Topic ID | Title | Top Msg ID |
|----------|-------|------------|
| 12 | طاقة نور | 1,382 |
| 1384 | قمر هادي | 1,430 |
| 1386 | هذا المساء | 1,490 |

## Newest Topics (by top_message_id)

| Topic ID | Title | Top Msg ID |
|----------|-------|------------|
| 6495 | برنامج Top chef | 71,386 |
| 70832 | ابلة فاهيتا دراما كوين | 71,361 |
| 70236 | حين لا يرانا احد | 71,352 |

## Use Cases

1. **Migration planning** — Export topic IDs for `telegram-topic-transfer`
2. **Cleanup** — Find inactive/empty topics
3. **Content audit** — Identify topics with most content
4. **Language analysis** — Arabic vs English content distribution

## Requirements

- Python 3.8+
- Telethon (for `collect_topics.py`)
- Topic data file at `~/all_topics_masass18.json`

## Related

- [telegram-topic-transfer](../telegram-topic-transfer) — Migrate topics between groups
- [telegram-mcp](../../plugins/telegram-mcp) — Live Telegram operations