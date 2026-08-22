# Fix: `list_topics` Pagination Bug (2026-07-12)

## Problem
The MCP tool `mcp__telegram_mcp__list_topics` could not list all topics in large supergroups (>200 topics).

### Root Causes
1. **Hard limit**: Default `limit=200`, but Telegram's `GetForumTopicsRequest` max is 100 per request
2. **Broken pagination**: `offset_topic` parameter was treated as a numeric offset (skip N records), but in Telegram's API it expects a **topic ID** to start from. Every call with any `offset_topic` returned the same first 200 topics.

## Solution
Modified `B:\for-hermes\telegram-mcp\telegram_mcp\tools\chats.py` function `list_topics`:

```python
async def list_topics(
    chat_id: int,
    limit: int = 100,           # clamped to 100 (Telegram max)
    offset_topic: int = 0,      # topic ID to start from (last topic ID from prev batch)
    fetch_all: bool = False,    # NEW: if True, auto-paginate through ALL topics
    search_query: str = None,
    account: str = None,
) -> str:
    limit = min(max(1, limit), 100)  # clamp to Telegram max
    all_records = []
    current_offset = offset_topic
    
    while True:
        result = await cl(GetForumTopicsRequest(
            channel=entity,
            offset_date=0,
            offset_id=0,
            offset_topic=current_offset,  # LAST topic's ID = correct offset
            limit=limit,
            q=search_query or None,
        ))
        
        topics = getattr(result, "topics", None) or []
        if not topics:
            break
            
        # ... process topics into records ...
        all_records.extend(records)
        
        if not fetch_all or len(topics) < limit:
            break
            
        # Use LAST topic's ID as next offset_topic
        current_offset = topics[-1].id
    
    return format_tool_result(all_records)
```

## Usage After Fix

```bash
# Get ALL topics (auto-pagination)
mcp__telegram_mcp__list_topics(chat_id=-1002191043427, fetch_all=true)

# Manual pagination with correct offset_topic
mcp__telegram_mcp__list_topics(chat_id=-1002191043427, limit=100, offset_topic=0)
# → take last topic's ID from result, use as next offset_topic
```

## Verification
After applying the patch and **restarting the MCP server**:
- `fetch_all=true` returns the complete topic list (not capped at 200)
- Manual pagination with `offset_topic=<last_topic_id>` works correctly

## Files Modified
- `B:\for-hermes\telegram-mcp\telegram_mcp\tools\chats.py` — `list_topics` function (lines ~228-314)