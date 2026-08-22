# MCP Server Modifications (telegram-mcp)

## Location
`B:\for-programing\for-telegram\telegram-mcp\`

## Structure
```
telegram-mcp/
├── main.py                          # Entry point
├── telegram_mcp/
│   ├── runtime.py                   # Shared runtime (get_client, resolve_entity, decorators)
│   ├── tools/
│   │   ├── __init__.py              # Registers all tool modules (import * from each)
│   │   ├── messages.py             # forward_message, copy_message, copy_messages, send_message, get_messages
│   │   ├── chats.py                 # list_topics, create_forum_topic, copy_topic, get_chat
│   │   ├── media.py                 # download_media (disabled), send_file
│   │   └── ...
│   └── ...
└── tests/
```

## Tools Added (2026-07-12)

### copy_message (messages.py, after forward_messages ~line 1028)
Server-side copy of single message. No download, no forward tag.
- Key: `send_kwargs["file"] = msg.media` (server-side pointer copy)
- Preserves: caption (msg.message), formatting_entities, supports_streaming for videos
- Filters: service messages, noise patterns {".", "===", "/", "@"}, bot commands

### copy_messages (messages.py, after copy_message)
Batch version of copy_message. Iterates message_ids, copies each server-side.
- Has configurable delay (default 0.5s) between copies
- Reports: copied count, failed count, skipped count, first 5 errors

### copy_topic (chats.py, after get_message_link ~line 1090)
Full topic transfer: creates target topic if needed, copies all messages.
- Uses `iter_messages(reply_to=topic_id)` to fetch from source topic
- Reverses to oldest-first ordering
- Creates topic via `CreateForumTopicRequest` with `secrets.randbits(63)` random_id
- Checks existing target topics via `GetForumTopicsRequest` (limit=100) — may hang on large groups

## How to Add New MCP Tools

1. **Choose the right file**: messages for message operations, chats for chat/topic operations
2. **Add the decorator**: `@mcp.tool(annotations=ToolAnnotations(title="...", openWorldHint=True, destructiveHint=True))`
3. **Add decorators**: `@with_account(readonly=False)` and `@validate_id("chat_id", ...)`
4. **Implement the function**: Use `cl = get_client(account)`, `await ensure_connected(cl)`, `entity = await resolve_entity(chat_id, cl)`
5. **Add to `__all__`**: At the bottom of the file, add the function name
6. **Restart MCP**: In Hermes, restart the telegram-mcp server for changes to take effect

## Key Patterns

### Server-side copy (no forward tag)
```python
msg = await cl.get_messages(from_entity, ids=message_id)
send_kwargs = {}
if msg.media:
    send_kwargs["file"] = msg.media  # Server pointer copy
    if msg.message:
        send_kwargs["caption"] = msg.message
        if msg.entities:
            send_kwargs["formatting_entities"] = msg.entities
    await cl.send_file(to_entity, **send_kwargs)
elif msg.message:
    await cl.send_message(to_entity, msg.message, formatting_entities=msg.entities)
```

### Create topic with proper random_id
```python
import secrets  # MUST be at module level, not inside __main__!
result = await cl(CreateForumTopicRequest(
    peer=target_entity,
    title=topic_title,
    random_id=secrets.randbits(63)  # NEVER use 0 or conditional fallback
))
# Extract topic_id from result.updates
```

## Pitfalls When Modifying MCP

1. **`import secrets` at module level** — not inside `if __name__ == "__main__":`. The `secrets` module must be imported at the top of the file where `CreateForumTopicRequest` is called.

2. **`random_id=secrets.randbits(63) if 'secrets' in dir() else 0`** — This DOES NOT WORK. `'secrets'` is not in `dir()` at call time when it's imported at module level. Use bare `secrets.randbits(63)`.

3. **`GetForumTopicsRequest` inside MCP copy_topic** — May hang on large groups (same issue as standalone Telethon). Consider using MCP `list_topics` for discovery instead.

4. **MCP blocks during FloodWait** — If MCP's copy_topic hits FloodWait, the entire MCP server hangs. For bulk operations, use the standalone script instead.

5. **`EditForumTopicRequest` is in `messages` not `channels`** — Import from `telethon.tl.functions.messages`.
