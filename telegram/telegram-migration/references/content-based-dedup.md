# Content-Based Deduplication

## Why Content Fingerprints?

Telegram assigns **new message IDs** on every server-side copy (`send_file`/`send_message`). The original `message_id` from source is lost. Therefore, ID-based resume (storing `last_synced_source_msg_id`) is unreliable — the target messages have completely different IDs.

## Content Fingerprint Strategy

Create a deterministic fingerprint for each message:

```python
def _message_fingerprint(msg) -> str:
    """Create content-based fingerprint for deduplication."""
    parts = []
    
    # Text content (normalized)
    text = getattr(msg, "message", None) or ""
    if text.strip():
        # Normalize: lowercase, strip whitespace, remove zero-width chars
        normalized = text.strip().lower()
        normalized = re.sub(r'[\u200b-\u200f\ufeff]', '', normalized)  # zero-width
        parts.append(f"text:{hashlib.md5(normalized.encode()).hexdigest()[:16]}")
    
    # Media type + size (if available)
    if getattr(msg, "media", None):
        media_type = type(msg.media).__name__
        size = getattr(msg.media, "size", None) or getattr(msg.file, "size", 0)
        parts.append(f"media:{media_type}:{size}")
    
    # Date (day precision for grouping)
    if getattr(msg, "date", None):
        parts.append(f"date:{msg.date.strftime('%Y-%m-%d')}")
    
    return "|".join(parts) if parts else "empty"
```

## Comparison Logic

### Source Index
```python
def _build_source_index(messages: list) -> dict:
    """Build content fingerprint -> message mapping for source."""
    index = {}
    for msg in messages:
        if getattr(msg, "action", None):
            continue  # Skip service messages
        fp = _message_fingerprint(msg)
        if fp not in index:
            index[fp] = []
        index[fp].append(msg)
    return index
```

### Target Index
```python
def _build_target_index(messages: list) -> dict:
    """Build content fingerprint -> message mapping for target."""
    index = {}
    for msg in messages:
        if getattr(msg, "action", None):
            continue
        fp = _message_fingerprint(msg)
        if fp not in index:
            index[fp] = []
        index[fp].append(msg)
    return index
```

### Diff Calculation
```python
def _diff_topics(source_index: dict, target_index: dict) -> dict:
    """Calculate missing/extra messages by content fingerprint."""
    missing = []
    extra = []
    matched = 0
    
    for fp, source_msgs in source_index.items():
        target_msgs = target_index.get(fp, [])
        if len(target_msgs) >= len(source_msgs):
            matched += len(source_msgs)
        else:
            # Some messages missing
            missing_count = len(source_msgs) - len(target_msgs)
            for _ in range(missing_count):
                missing.append(source_msgs[len(target_msgs)].id)
    
    for fp, target_msgs in target_index.items():
        source_msgs = source_index.get(fp, [])
        if len(target_msgs) > len(source_msgs):
            extra_count = len(target_msgs) - len(source_msgs)
            for _ in range(extra_count):
                extra.append(target_msgs[len(source_msgs)].id)
    
    return {
        "missing_in_target": missing,
        "extra_in_target": extra,
        "matched_count": matched,
        "source_count": sum(len(v) for v in source_index.values()),
        "target_count": sum(len(v) for v in target_index.values())
    }
```

## Noise Filtering (Before Comparison)

Filter out noise messages **before** building indexes:

```python
_NOISE_PATTERNS = {
    ".", "===", "/", "@", "...", "....", ".....",
    "......", ".......", "........", ".........", ".........."
}

_BOT_COMMAND_PATTERN = re.compile(r"/\w+(@\w+)?$")

def _is_noise_message(msg) -> bool:
    """Check if message is noise (separator, single char, bot command)."""
    text = getattr(msg, "message", None) or ""
    stripped = text.strip()
    
    if not stripped and not getattr(msg, "media", None):
        return True  # Empty no-media message
    
    if stripped in _NOISE_PATTERNS and not getattr(msg, "media", None):
        return True
    
    if _BOT_COMMAND_PATTERN.match(stripped):
        return True
    
    return False
```

## Handling Edge Cases

| Edge Case | Handling |
|-----------|----------|
| Duplicate messages with same content | Count occurrences, match 1:1 by order |
| Media without text | Fingerprint by media type + size + date |
| Forwarded messages | Include `forwarded_from` in fingerprint if available |
| Edited messages | Use current text (edited version) |
| Messages with same text, different entities | Entities not fingerprinted (acceptable loss) |

## MCP Integration

The `compare_topics` tool implements this logic:
- Fetches all messages oldest-first
- Filters noise
- Builds content fingerprint indexes
- Returns `missing_in_target`, `extra_in_target`, counts