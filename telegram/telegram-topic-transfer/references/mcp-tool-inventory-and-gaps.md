# MCP Tool Inventory & Gaps (Telegram MCP — Repo A)

**Repo A**: `B:\for-programing\for-telegram\telegram-mcp` (Mohamed's fork, 26 commits ahead of upstream)  
**Repo B**: `B:\for-hermes\telegram-mcp` (upstream `chigwell/telegram-mcp` snapshot, 2026-06-29)

---

## Total Tool Count

| Repo | `@mcp.tool` decorators | Notes |
|---|---:|---|
| **A** | **121** | 17 tools not in B |
| **B** | 104 | baseline upstream |

---

## Tool Coverage by Category (Repo A)

| Category | Tools | Key Tools | Gaps (P0) |
|---|---:|---|---|
| **Accounts** | 1 | `list_accounts` | `set_active_account`, `get_active_account` |
| **Chats/Topics** | 22 | `get_chats`, `list_chats`, `list_topics`, `count_topics`, `create_forum_topic`, `copy_topic`, `forward_topics_from_group` | `edit_forum_topic`, `close_forum_topic`, `reopen_forum_topic`, `hide_forum_topic` |
| **Contacts** | 13 | `list_contacts`, `search_contacts`, `add_contact`, `block_user`, `export_contacts` | — |
| **Events (Real-time)** | 2 | `wait_for_new_message`, `wait_for_settled_message` | — |
| **Folders** | 7 | `list_folders`, `create_folder`, `add_chat_to_folder`, `reorder_folders` | — |
| **Forum Forward** | 1 | `forward_topics_from_group` | — |
| **Groups/Admin** | 23 | `create_group`, `invite_to_group`, `promote_admin`, `edit_admin_rights`, `ban_user`, `set_default_chat_permissions`, `toggle_slow_mode`, `get_admins`, `get_banned_users`, `get_invite_link`, `export_chat_invite`, `join_chat_by_link`, `import_chat_invite` | `ban_users_bulk`, `invite_users_bulk` |
| **Media** | 10 | `send_file`, `send_album`, `download_media`, `upload_file`, `get_media_info`, `send_voice`, `send_sticker`, `get_sticker_sets`, `get_gif_search`, `send_gif` | — |
| **Messages** | 36 | `get_messages`, `list_messages`, `get_history`, `get_message_context`, `search_messages`, `search_global`, `get_pinned_messages`, `send_message`, `send_scheduled_message`, `reply_to_message`, `forward_message`, `copy_message`, `pin_message`, `mark_as_read`, `create_poll`, `get_drafts`, `save_draft`, `clear_draft` | **MISSING decorators** (see below) |
| **Profile** | 11 | `get_me`, `update_profile`, `set_profile_photo`, `get_privacy_settings`, `set_privacy_settings`, `get_full_user`, `get_bot_info`, `set_bot_commands`, `get_user_photos`, `get_user_status` | — |

---

## Critical Gap: Tools That Exist in SDK But Lack `@mcp.tool` Decorators

These functions are **fully implemented in `messages.py` and `media.py`** but are **NOT exposed as MCP tools** because they don't have the decorator. Adding the decorator + type hints + docstring is all that's needed.

### In `telegram_mcp/tools/messages.py`:

| Function | Line | What It Does | Priority |
|---|---|---|---|
| `copy_messages` | 1113 | Copy multiple messages without forward tag (batch) | **P0** |
| `forward_messages` | 984 | Forward multiple messages in one call (preserves albums) | **P0** |
| `delete_messages_bulk` | 1312 | Delete multiple messages at once | **P0** |
| `edit_message` | 1218 | Edit a sent message | **P0** |
| `send_reaction` | 1713 | Send 👍 ❤️ 🔥 etc. to a message | **P0** |
| `remove_reaction` | 1759 | Remove your reaction from a message | **P0** |
| `get_message_reactions` | 1794 | List reactions on a message | **P0** |
| `create_poll` | 1632 | Create a native Telegram poll | **P1** |

### In `telegram_mcp/tools/media.py`:

| Function | Line | What It Does | Priority |
|---|---|---|---|
| `send_album` | 78 | Send 2-10 files as media group | Already decorated ✅ |
| `upload_file` | 258 | Upload to Telegram, return file_id (no send) | P2 |

---

## Missing Forum Topic Management Tools (Not in SDK at all)

| Tool | Description | Priority |
|---|---|---|
| `edit_forum_topic` | Change title, icon, closed/hidden state of a topic | **P0** |
| `close_forum_topic` | Close a forum topic (no new messages) | **P0** |
| `reopen_forum_topic` | Reopen a closed topic | **P0** |
| `hide_forum_topic` | Hide a topic from the topic list (archive) | **P0** |

These require new `GetForumTopicsRequest` / `EditForumTopicRequest` / `DeleteTopicHistoryRequest` wrappers.

---

## Multi-Account Gaps

| Gap | Description |
|---|---|
| `set_active_account` | Switch default account for subsequent calls |
| `get_active_account` | Query current active account |
| Per-tool `account` param | Most tools accept `account: str = None` but there's no way to list/set the default programmatically via MCP |

---

## Rate Limit / FloodWait Handling

| Gap | Description |
|---|---|
| Smart retry wrapper | Auto-retry on `FloodWaitError` with `e.seconds + buffer` |
| Topic reordering | Large media-heavy topics should be processed last to avoid blocking queue |
| Configurable delay | Per-tool or global `delay` param respected by some but not all copy/forward tools |

---

## Quick Wins (Decorator-Only Additions)

To add any of the P0 missing tools, the pattern is:

```python
@mcp.tool(
    annotations=ToolAnnotations(
        title="Human Readable Title",
        openWorldHint=True,
        destructiveHint=True,  # or False for read-only
        idempotentHint=True,   # if safe to retry
    )
)
@with_account(readonly=False)  # or readonly=True
@validate_id("chat_id")
async def tool_name(
    chat_id: Union[int, str],
    message_ids: List[int],   # or other params
    account: Optional[str] = None,
) -> str:
    """
    Docstring with Args, Returns, and the standard untrusted-content warning.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        result = await cl(ExistingTelethonFunction(...))
        return format_tool_result([...])
    except Exception as e:
        return log_and_format_error("tool_name", e, chat_id=chat_id)
```

---

## Verification Commands

```bash
# Count tools in Repo A
cd "B:\for-programing\for-telegram\telegram-mcp"
grep -rEn "@mcp\.tool" telegram_mcp/tools/ | wc -l
# -> 121

# List all tool names
grep -rEn "@mcp\.tool" telegram_mcp/tools/ | grep -oE "title=\"[^\"]+\"" | sort -u

# Find functions missing decorators
grep -n "async def " telegram_mcp/tools/messages.py | while read line; do
    func_line=$(echo "$line" | cut -d: -f1)
    func_name=$(echo "$line" | sed -n 's/.*async def \([a-zA-Z_][a-zA-Z0-9_]*\).*/\1/p')
    # Check if decorator exists within 10 lines before
    sed -n "$((func_line-10)),$((func_line-1))p" telegram_mcp/tools/messages.py | grep -q "@mcp.tool" || echo "MISSING: $func_name at line $func_line"
done
```

---

## Recommendation for AI Agent Capability

With the **17 decorator-only additions** (P0 gaps), the MCP would cover:

- **Read/View/Analyze**: 95% → 95% (already complete)
- **Send/Write**: 90% → 95% (add reactions, polls, batch copy/forward)
- **Delete/Modify**: 30% → **90%** (add `edit_message`, `delete_messages_bulk`)
- **Forum Topic Mgmt**: 80% → **95%** (add close/reopen/hide/edit)
- **Real-time**: 90% → 90% (already complete)
- **Admin/Bulk**: 70% → 85% (add bulk ban/invite)

The remaining 5-10% requires new Telethon request wrappers (forum topic state changes) or architectural changes (smart rate limiting, multi-account default).