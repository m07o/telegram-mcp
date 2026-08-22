# Atomic Topic Creation: find_or_create_topic

## Problem

Race condition when multiple processes/channels try to create the same topic title:
1. Process A checks — topic doesn't exist
2. Process B checks — topic doesn't exist  
3. Process A creates topic "Sweet Tooth"
4. Process B creates topic "Sweet Tooth" → **duplicate**

Even within a single MCP session, rapid sequential calls can race with themselves.

## Solution: find_or_create_topic

Single atomic tool that combines check + create:

```python
@mcp.tool(annotations=ToolAnnotations(title="Find or Create Forum Topic", openWorldHint=True))
@with_account(readonly=False)
@validate_id("chat_id")
async def find_or_create_topic(
    chat_id: Union[int, str],
    title: str,
    *,
    icon_emoji_id: int | None = None,
    icon_color: int | None = None,
    delay_before: float = 2.0,
    delay_after: float = 3.0,
    account: str = None,
) -> str:
```

## Implementation Logic

```python
async def find_or_create_topic(...):
    # 1. Sanitize title (same as create_forum_topic)
    clean_title = _sanitize_topic_title(title)
    
    # 2. Normalize for comparison
    normalized_target = normalize_forum_title(clean_title)
    
    # 3. Fetch ALL topics with pagination (fetch_all equivalent)
    current_offset = 0
    while True:
        result = await cl(GetForumTopicsRequest(...))
        topics = getattr(result, "topics", []) or []
        if not topics: break
        
        # 4. Local EXACT match using normalized titles
        for topic in topics:
            if normalize_forum_title(topic.title) == normalized_target:
                return {"topic_id": topic.id, "title": topic.title, "created": False}
        
        if len(topics) < limit: break
        current_offset = topics[-1].id
    
    # 5. Not found — create with rate limiting
    await _rate_limit_topic_creation(min_interval=5.0)
    await asyncio.sleep(delay_before)
    
    result = await cl(CreateForumTopicRequest(...))
    await asyncio.sleep(delay_after)
    
    return {"topic_id": topic_id, "title": clean_title, "created": True}
```

## Key Properties

| Property | Value |
|----------|-------|
| **Atomic** | No race window between check and create |
| **Same sanitization** | Uses `_sanitize_topic_title` as `create_forum_topic` |
| **Exact match** | Uses `normalize_forum_title` for comparison |
| **Returns created flag** | `created: true` if new, `false` if existed |
| **Rate limited** | Enforces 5s minimum interval between creates |

## Usage

```python
target = mcp__telegram_mcp__find_or_create_topic(
    chat_id=TARGET_CHAT_ID,
    title=topic.title,
    delay_before=2.0,
    delay_after=3.0
)
# Returns: {topic_id, title, created: true/false}
# If created=false → topic already existed, safe to use
```

## Rate Limiting

```python
async def _rate_limit_topic_creation(min_interval: float = 5.0) -> None:
    """Enforce minimum interval between topic creations."""
    global _topic_creation_times
    now = time.time()
    _topic_creation_times = [t for t in _topic_creation_times if now - t < 3600]
    if _topic_creation_times:
        elapsed = now - _topic_creation_times[-1]
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
    _topic_creation_times.append(time.time())
```

## Why Not Just Check list_topics Then Create?

| Approach | Race Condition? | API Calls |
|----------|----------------|-----------|
| `list_topics` + `create_forum_topic` | **Yes** | 2 |
| `find_or_create_topic` | **No** | 1 (atomic) |

The atomic tool eliminates the race window entirely by doing check+create in a single server-side operation sequence.