# Post-Migration Cleanup — Bot Noise + Separators in Target Group

## Why this exists

When the migration script runs, the **agent itself** (via MCP or in-chat
interruption while a Telegram session is active) sometimes writes control
messages into the target supergroup. These look like:

- `⚡ Interrupting current task. I'll respond to your message shortly.`
- `⚠️ Your message was interrupted.`
- `🔧 Processing your request...`
- `===========================` (left as section separators by Hermes UI)

They contain no user content, confuse anyone reviewing the target group,
and the user explicitly asked to delete them: "امسح الرسائل بتاعت البوت
اللي اسمه mo عشان كان بيبعت حجات في التوبكات اللي انت بتعملها زي دي".

## What to delete

| Match kind | Pattern | Action |
|---|---|---|
| Bot author | `msg.sender.id == BOT_USER_ID` (Hermes bot id) | DELETE |
| Separator | exact-text `===` / `===========================` (any length of run) | DELETE |
| Bot notification text | regex `^(⚡\s*Interrupting\|^⚠️\s*Your message was interrupted\|^🔧\s*Processing...)` | DELETE |
| Real user content | anything else | KEEP |

## Implementation pattern

```python
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = ...
API_HASH = '...'
SESSION_FILE = r"D:\لn8ن بوت التليجرام\telethon_string.txt"
TARGET_CHAT_ID = -1002204837936

with open(SESSION_FILE, "r", encoding="utf-8") as f:
    SESSION_STRING = f.read().strip()
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

BOT_USER_ID = 8661914459  # mo / Hermes bot

SEPARATOR = "==========================="
BOT_NOTIF_RE = (
    r"^(⚡\s*Interrupting"
    r"|⚠️\s*Your message was interrupted"
    r"|🔧\s*Processing"
    r").*"
)

import re
BOT_RE = re.compile(BOT_NOTIF_RE, re.IGNORECASE | re.DOTALL)

async def main():
    await client.connect()
    target = await client.get_entity(TARGET_CHAT_ID)

    msgs_to_delete = []
    async for msg in client.iter_messages(target, limit=10000):
        sender = msg.sender
        text = getattr(msg, "message", "") or ""

        if sender and getattr(sender, "id", None) == BOT_USER_ID:
            msgs_to_delete.append(msg.id)
            continue

        stripped = text.strip()
        if stripped == SEPARATOR or stripped.startswith("====="):
            msgs_to_delete.append(msg.id)
            continue

        if BOT_RE.match(stripped):
            msgs_to_delete.append(msg.id)

    if msgs_to_delete:
        # Telegram RPC limit: max 100 messages per delete_messages call
        for i in range(0, len(msgs_to_delete), 100):
            await client.delete_messages(
                target, msgs_to_delete[i:i + 100])
            await asyncio.sleep(1)  # avoid flood
        print(f"Deleted {len(msgs_to_delete)} noise messages")

    await client.disconnect()

asyncio.run(main())
```

## When to run

- **AFTER** `transfer_all_topics.py` completes (all topics done).
- **BEFORE** the user inspects the target group for review.
- Optionally again after any subsequent sync that ran while the agent
  was processing a message (Hermes can re-inject interrupt text).

## Sanity check before deleting

Always print the first 10 matches with their text preview so the user
(or you, reviewing on resume) can confirm before bulk-delete:

```python
for mid in msgs_to_delete[:10]:
    print(f"  msg {mid}: {text[:80]}")
if len(msgs_to_delete) > 10:
    print(f"  ... {len(msgs_to_delete) - 10} more")
```

If the breakdown looks wrong, fix the heuristic and re-run; do not
delete messages that look like user content.

## User-provided t.me link anchors

User often provides links like:
```
https://t.me/c/2204837936/15389/28108
```

**Decoding:**
- `chat_id` = `2204837936` → add `-100` prefix = `-1002204837936` (Egyxos)
- `topic_id` = `15389` (افلام عربي)
- `msg_id` = `28108` (first polluted message — delete this and all newer in that topic)

**Cleanup with anchor:**
```python
topic_id = 15389
from_msg_id = 28108  # Delete this and newer in this topic

msgs_to_delete = []
async for msg in client.iter_messages(target, reply_to=topic_id):
    if msg.id < from_msg_id:
        break
    msgs_to_delete.append(msg.id)

# Delete in chunks of 80 (safer than 100 for targeted deletes)
for i in range(0, len(msgs_to_delete), 80):
    chunk = msgs_to_delete[i:i+80]
    await client.delete_messages(target, chunk)
```

**Why this pattern:** When a buggy sync re-sends messages into an already-migrated topic, pollution always accumulates at the END of the topic (the buggy sync appends after existing tail). The user's link marks the exact start of the pollution zone.

**Caution:** Always show the user what will be deleted before actually deleting when count > 50.

## Sources

- MASASS18 → EGYXOS cleanup, 2026-07-12 (deleted 189 `===` separators
  and any bot-authored lines from `مسرحيات`, `افلام كرتون`,
  `افلام اجنبي`, etc.)
- See pitfall #21 in SKILL.md.
