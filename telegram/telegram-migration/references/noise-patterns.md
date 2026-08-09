# Noise Identification Patterns

## Known Noise Patterns

### Separator Lines
```python
_NOISE_PATTERNS = {
    ".",           # Single dot
    "===",         # Triple equals
    "/",           # Single slash
    "@",           # Single at-sign
    "...",         # Three dots
    "....",        # Four dots
    ".....",       # Five dots
    "......",      # Six dots
    ".......",     # Seven dots
    "........",    # Eight dots
    ".........",   # Nine dots
    "..........",  # Ten dots
}
```

### Bot Commands
```python
_BOT_COMMAND_PATTERN = re.compile(r"/\w+(@\w+)?$")
# Matches: /start, /help@botname, /command@bot
```

### Bot Interrupt Messages
```python
_BOT_INTERRUPT_PATTERNS = [
    r"^(⚡|⚠️|🔧)?\s*Interrupting",
    r"^(⚡|⚠️|🔧)?\s*interrupted",
    r"^(⚡|⚠️|🔧)?\s*processing",
    r"^(⚡|⚠️|🔧)?\s*respond",
]
# Matches bot status messages like:
# "⚡ Interrupting current task. I'll respond to your message shortly."
# "⚠️ Your message was interrupted."
```

### Empty/Whitespace Messages
```python
# Messages with no text AND no media
if not text.strip() and not getattr(msg, "media", None):
    return True
```

## Detection Function

```python
def _is_noise_message(msg) -> bool:
    """Check if message is noise (separator, single char, bot command, bot interrupt)."""
    text = getattr(msg, "message", None) or ""
    stripped = text.strip()
    
    # Empty message with no media
    if not stripped and not getattr(msg, "media", None):
        return True
    
    # Known noise patterns (without media)
    if stripped in _NOISE_PATTERNS and not getattr(msg, "media", None):
        return True
    
    # Bot commands
    if _BOT_COMMAND_PATTERN.match(stripped):
        return True
    
    # Bot interrupt/status messages
    for pattern in _BOT_INTERRUPT_PATTERNS:
        if re.search(pattern, stripped, re.IGNORECASE):
            return True
    
    return False
```

## Cleanup Strategy

### Dry-Run First
```python
cleanup = mcp__telegram_mcp__cleanup_topic_noise(
    chat_id=TARGET_CHAT_ID,
    topic_id=target.topic_id,
    dry_run=True  # Preview what would be deleted
)
# Returns: deleted_count, failed_count, would_delete_ids
```

### Actual Cleanup
```python
cleanup = mcp__telegram_mcp__cleanup_topic_noise(
    chat_id=TARGET_CHAT_ID,
    topic_id=target.topic_id,
    dry_run=False
)
# Returns: deleted_count, failed_count, deleted_ids
```

## When to Run Cleanup

| Timing | Reason |
|--------|--------|
| **Before migration** | Prevents noise being treated as "missing" messages |
| **After migration** | Removes bot interrupts that occurred during migration |
| **Periodic** | Cleans up noise from ongoing bot activity |

## Patterns to NOT Delete

| Pattern | Reason |
|---------|--------|
| Single dot with media (e.g., "." + photo) | Could be intentional |
| Bot command with media | Could be intentional |
| User messages containing "===" as part of text | Not a pure separator |