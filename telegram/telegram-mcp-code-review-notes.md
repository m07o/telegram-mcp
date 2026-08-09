# Telegram MCP — Code Review Notes

**Repo:** [m07o/telegram-mcp](https://github.com/m07o/telegram-mcp)  
**Review date:** 2026-08-09  
**Scope:** Code-level issues, bugs, edge cases, and design concerns identified during a source review.

---

## Note to the maintainer

Here is an organized list of suggested improvements and observations about the current codebase in `m07o/telegram-mcp`. No critical vulnerabilities were found, but there are several points that may be worth reviewing.

---

## 1. `nest_asyncio` import without usage

**File:** `telegram_mcp/runtime.py`  
**Line:** `import nest_asyncio`

`nest_asyncio` is imported at the top of the file, but `nest_asyncio.apply()` is never called anywhere. `nest_asyncio` is typically used for nested event loops (e.g., Jupyter notebooks). In a standard MCP server, it does not appear to be needed.

- **Impact:** Low — an unused import does not cause an error, but it is unnecessary and may be a leftover from development.
- **Suggestion:** Remove the import unless there is a genuine need for it.

---

## 2. `.env` loaded at module level without error handling

**File:** `telegram_mcp/runtime.py`  
**Line:** `load_dotenv()` at top of module

`python-dotenv`'s `load_dotenv()` is called at module import time without `try/except` or logging. If the `.env` file has syntax errors (e.g., malformed values or encoding issues), errors may surface later in confusing ways when reading environment variables.

- **Impact:** Low-Medium — failures may be hard to trace.
- **Suggestion:** Wrap `load_dotenv()` in `try/except` or log a warning if loading fails.

---

## 3. `_parse_bool_env` does not recognize explicit `false` values

**File:** `telegram_mcp/runtime.py`

```python
def _parse_bool_env(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
```

The function only recognizes positive boolean values (`1`, `true`, `yes`, `on`) but does not explicitly handle `false` values (`0`, `false`, `no`, `off`). If an environment variable is set to an explicit `false` value, it will be treated as truthy because it is a non-empty string.

- **Impact:** Medium — may lead to incorrect assumptions about user configuration.
- **Suggestion:** Add negative values to the lookup set, or use explicit `!= "0"` / `!= "false"` logic.

---

## 4. `install_guard.py` message points to upstream, not the fork

**File:** `telegram_mcp/install_guard.py`

```python
'pip install "git+https://github.com/chigwell/telegram-mcp.git"'
```

In `_format_unsafe_installation_message`, the error message recommends installing `chigwell/telegram-mcp` (the original upstream project) instead of the current fork (`m07o/telegram-mcp`). Since users may legitimately install from the fork, this guidance can be misleading.

- **Impact:** Low — inaccurate error message.
- **Suggestion:** Replace the URL with the relevant one (the current fork or a placeholder that reminds the owner to update it).

---

## 5. `validate_id` regex does not support international phone numbers

**File:** `telegram_mcp/runtime.py`

```python
re.match(r"^@?[a-zA-Z0-9_]{5,}$", value)
```

The regex only accepts an optional `@` followed by 5+ alphanumeric characters. It does not support international phone numbers (`+1234567890`), even though Telegram allows messaging by phone number.

- **Impact:** Medium — if there is a tool that sends messages to phone numbers, it will fail under this validation.
- **Suggestion:** Separate validation logic for usernames vs. phone numbers, or extend the regex if phone number support is desired.

---

## 6. `resolve_entity` / `resolve_input_entity` — overlapping retry logic with unbounded `get_dialogs()`

**File:** `telegram_mcp/runtime.py`

Both functions contain nested retry logic:
- Try `get_entity` → on `ValueError` → warm cache with `get_dialogs()` → retry.
- On `ConnectionError` → reconnect → repeat the same logic.

The concern: `get_dialogs()` itself may take a long time or fail if the connection is unstable, and there is no explicit timeout on it. In worst cases, it could cause hangs.

- **Impact:** Medium — may affect response time and connection reliability.
- **Suggestion:** Add a timeout to `get_dialogs()`, or simplify the retry nesting to avoid overly complex logic.

---

## 7. Custom `TLRequest` classes (GetForumTopicsRequest, CreateForumTopicRequest) — risk of future breakage

**File:** `telegram_mcp/tools/chats.py`

Custom `TLRequest` classes are used because Telethon versions 1.42–1.43 did not include `channels.getForumTopics` and `messages.createForumTopic` natively. The code performs manual serialize/deserialize via `_bytes()`.

- **Risk:** If Telethon later adds these methods as native APIs (or changes the TL schema), the custom classes may conflict with or become redundant compared to the library's built-in support.
- **Suggestion:** Check whether newer versions of Telethon now include these methods and use them if available. If the custom classes are still needed, ensure they are tested against each Telethon update.

---

## 8. `_handle_flood_wait` — last attempt outside try/catch

**File:** `telegram_mcp/tools/chats.py`

```python
async def _handle_flood_wait(func, *args, max_retries: int = 3, **kwargs):
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except FloodWaitError as e:
            wait_time = e.seconds + 5
            if wait_time > 1800:
                logger.error(...)
                raise
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error(...)
            raise
    # ← after retries exhausted, call again without try/catch
    return await func(*args, **kwargs)
```

After all retries are exhausted, `func` is called once more without any `try/except`. If the call still fails after three attempts, the exception will propagate without centralized handling.

- **Impact:** Medium — may lead to uncontrolled error propagation when the retry limit is reached.
- **Suggestion:** The final attempt should also be inside a try block, or a proper exception should be raised after failure.

---

## 9. `download_media` — potential race in post-download path validation

**File:** `telegram_mcp/tools/media.py`

The flow is:
1. Determine `out_path` without a fixed extension.
2. Let Telethon download the media.
3. After download, verify that `final_path.resolve()` is within allowed roots.

The concern: Telethon may rename the file or write it to a temporary location and then rename. The post-write validation may be momentary if the file was temporarily outside allowed roots during the process.

- **Impact:** Low-Medium — depends on Telethon's behavior, but theoretically possible.
- **Suggestion:** Review Telethon's behavior for this case, or move validation earlier if feasible.

---

## 10. `format_entity` — `hasattr` on Telethon entities may be fragile

**File:** `telegram_mcp/runtime.py`

```python
if hasattr(entity, "title"):
    result["name"] = sanitize_name(entity.title)
    result["type"] = "group" if isinstance(entity, Chat) else "channel"
```

Using `hasattr` to detect attributes relies on Telethon's internal implementation details, which may change between versions. A type-check first is more reliable.

- **Impact:** Low — depends on Telethon stability.
- **Suggestion:** Replace `hasattr` with `isinstance` checks where possible.

---

## 11. `_install_annotation_hook` — access to private FastMCP attributes

**File:** `telegram_mcp/runtime.py`

```python
original_handler = mcp._mcp_server.request_handlers[CallToolRequest]
```

The code accesses `_mcp_server.request_handlers`, which is a private internal API of `FastMCP`. This is not guaranteed to remain stable across `mcp` / `FastMCP` versions.

- **Impact:** Medium — functionality may break on library updates.
- **Suggestion:** Monitor changes in FastMCP, or use an official extension mechanism if one becomes available.

---

## 12. Tools return errors as `str` instead of structured results

Many tools use `log_and_format_error`, which returns a plain **string**. This is inconsistent with tools that return JSON via `format_tool_result`, making it harder for users (or an AI agent) to distinguish success from failure in a uniform way.

- **Impact:** Low-Medium — interface consistency.
- **Suggestion:** Consider returning a uniform structure (e.g., `{"success": true/false, "data": ..., "error": ...}`) across all tools.

---

## 13. `_sanitize_topic_title` — preservation of bidirectional Unicode marks

**File:** `telegram_mcp/tools/chats.py`

The code preserves directional formatting marks (LTR/RTL embeddings, overrides) to support Arabic and Hebrew display correctly. This is a deliberate and reasonable choice, but theoretically it could be used for display spoofing — where a message appears different from its actual content.

- **Impact:** Low — unlikely in practice, but worth noting.
- **Suggestion:** No change needed now, but a documentation note about this limited risk may be helpful.

---

## 14. Logging timezone offset may be empty

**File:** `telegram_mcp/runtime.py`

```python
jsonlogger.JsonFormatter("...", datefmt="%Y-%m-%dT%H:%M:%S%z")
```

If the system's timezone data is not properly configured, `%z` may produce an empty string, resulting in log entries without a timezone offset.

- **Impact:** Low — depends on the deployment environment.
- **Suggestion:** Ensure timezone is set at the system level, or use explicit `datetime.now(timezone.utc).isoformat()`.

---

## Summary

| # | Category | Severity |
|---|---|---|
| 1 | `nest_asyncio` unused import | Low |
| 2 | `.env` loading without error handling | Low-Medium |
| 3 | `_parse_bool_env` does not recognize explicit false | Medium |
| 4 | `install_guard` message points to upstream instead of fork | Low |
| 5 | `validate_id` does not support international phone numbers | Medium |
| 6 | `resolve_entity` retry logic with unbounded `get_dialogs()` | Medium |
| 7 | Custom `TLRequest` classes — future breakage risk | Medium |
| 8 | `_handle_flood_wait` final attempt outside try/catch | Medium |
| 9 | `download_media` post-download path validation race | Low-Medium |
| 10 | `format_entity` `hasattr` fragility | Low |
| 11 | `_install_annotation_hook` private attribute access | Medium |
| 12 | Tools return errors as `str` instead of uniform structure | Low-Medium |
| 13 | `_sanitize_topic_title` bidirectional Unicode preservation | Low (informational) |
| 14 | Logging `%z` may produce empty offset | Low |

---

**Final note:** The codebase is overall well-organized and shows clear attention to security (sanitization, file path restrictions, read-only mode, install guard). The list above does not indicate major problems — rather, points that could benefit from review over time.
