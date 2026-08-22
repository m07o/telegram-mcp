# telegram-mcp Source Structure (Repo A — Mohamed's Fork)

> Two copies of telegram-mcp live on this machine. **This document covers
> Repo A** (`B:\for-programing\for-telegram\telegram-mcp`), the fork where
> migration features live. For the upstream baseline (Repo B), see
> `telegram-mcp-repo-layout.md` — it lacks every tool documented below.

## Modules Added in Repo A (not in upstream)

### `telegram_mcp/forum_pagination.py` (~5 kB)
Shared forum-topic helpers. Reused by both the MCP tool and the standalone
CLI so they parse topic IDs the same way.
- `iter_forum_topics` — async generator that paginates past Telegram's
  100-topic-per-request limit.
- `build_topic_index`, `get_topic_title` — fast title↔id maps for a forum.
- `extract_created_topic_id(result)` — **THE FIX** for the recurring
  "every new topic creation marked failed" bug. Parses
  `result.updates[].message.id` (Telegram actually returns the new topic's
  first message ID there), NOT the empty `result.messages` that the old
  code checked. `copy_topics.py` imports this same helper, so the bug is
  fixed in both the MCP path and the CLI path with one edit.

### `telegram_mcp/job_store.py` (~4 kB)
Per-job JSON progress persistence. Writes a small JSON file per running
job with its current step + last-processed id, so long topic migrations
can resume after a crash or `/stop` instead of restarting from zero.

### `telegram_mcp/tools/forum_forward.py` (~10.7 kB)
Registers the `forward_topics_from_group` MCP tool. Four critical fixes:
1. Send messages **oldest-first** (Telethon default is newest-first →
   collect then reverse before sending).
2. Skip bare `/` messages (would misfire as a bot command on the
   destination).
3. Validate BOTH `from_chat_id` and `to_chat_id` are forum-enabled
   megagroups before processing (otherwise dozens of cryptic "failed"
   entries appear).
4. Exclude service/action messages from `source_count` (otherwise any
   topic with a service message is wrongly marked "partial").

## Key Files (pre-existing, shared with upstream)

### `telegram_mcp/tools/messages.py` (~1854 lines)
Most important tools file. Contains `forward_message`, `forward_messages`, `send_message`, `get_messages`, `edit_message`, `delete_message`, `list_messages`, `search_messages`, `get_history`, `get_message_context`, etc.

**forward_message** (line ~899):
```python
@mcp.tool(...)
@with_account(readonly=False)
@validate_id("from_chat_id", "to_chat_id")
async def forward_message(from_chat_id, message_id, to_chat_id, account=None, expand_album=True):
    # Uses cl.forward_messages() — forces forward tag
    await cl.forward_messages(to_entity, ids_to_forward, from_entity)
```
**To add hide_sender**: Replace the `forward_messages` call with `send_file(file=msg.media)` loop when `hide_sender=True`.

**forward_messages** (line ~984): Batch version, same issue.

### `telegram_mcp/tools/chats.py` (~1110 lines)
Contains `list_topics`, `create_forum_topic`, `get_chat`, `get_chats`, `list_chats`.

**list_topics** (line ~228):
Uses `GetForumTopicsRequest` internally but works reliably (unlike standalone Telethon script which hangs on large groups).

**create_forum_topic** (line ~366):
Uses a custom `CreateForumTopicRequest` class defined locally (line ~75). Extracts topic_id via `_extract_created_topic_id()`.

### `telegram_mcp/tools/__init__.py`
Imports all tool modules so decorators register:
```python
from telegram_mcp.tools.messages import *
from telegram_mcp.tools.chats import *
# etc.
```

### `telegram_mcp/runtime.py`
Shared infrastructure:
- `get_client(account)` — returns connected Telethon client
- `resolve_entity(chat_id, client)` — resolves ID/username to entity
- `@with_account(readonly=True/False)` — decorator for account selection
- `@validate_id(*fields)` — validates chat ID parameters
- `@mcp.tool(annotations=...)` — registers MCP tool
- `format_tool_result()`, `log_and_format_error()` — output helpers

## Adding a New `copy_topic` MCP Tool

Create `telegram_mcp/tools/topics.py` (or add to `chats.py`):

```python
@mcp.tool(annotations=ToolAnnotations(title="Copy Topic (No Forward Tag)", openWorldHint=True, destructiveHint=True))
@with_account(readonly=False)
@validate_id("from_chat_id", "to_chat_id")
async def copy_topic(
    from_chat_id: Union[int, str],
    to_chat_id: Union[int, str],
    source_topic_id: int,
    topic_name: str = None,
    limit: int = 0,
    account: str = None,
) -> str:
    """Copy all messages from a source forum topic to target (no forward tag)."""
    cl = get_client(account)
    from_entity = await resolve_entity(from_chat_id, cl)
    to_entity = await resolve_entity(to_chat_id, cl)
    
    # Create target topic
    from telethon.tl.functions.messages import CreateForumTopicRequest
    result = await cl(CreateForumTopicRequest(peer=to_entity, title=topic_name or f"topic_{source_topic_id}"))
    target_topic_id = _extract_created_topic_id(result)
    
    # Copy messages
    copied, failed = 0, 0
    async for msg in cl.iter_messages(from_entity, reply_to=source_topic_id, limit=limit or None):
        if msg.media:
            kwargs = {"file": msg.media, "reply_to": target_topic_id}
            if msg.message:
                kwargs["caption"] = msg.message
            if msg.entities:
                kwargs["formatting_entities"] = msg.entities
            await cl.send_file(to_entity, **kwargs)
        elif msg.message:
            await cl.send_message(to_entity, msg.message, reply_to=target_topic_id)
        copied += 1
    
    return f"Copied {copied} messages (no forward tag) to topic '{topic_name}'."
```

Register in `__init__.py`:
```python
from telegram_mcp.tools.topics import *  # or add to chats.py
```

## Decorator Pattern

All MCP tools follow this pattern:
```python
@mcp.tool(annotations=ToolAnnotations(title="...", openWorldHint=True, ...))
@with_account(readonly=True/False)  # readonly=True for read ops, False for writes
@validate_id("chat_id")             # validates ID params
async def tool_name(param: type, ...) -> str:
    cl = get_client(account)
    entity = await resolve_entity(chat_id, cl)
    # ... do work ...
    return format_tool_result(result)
```
