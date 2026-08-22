#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cleanup_bot_noise.py — Remove bot-authored messages and `===` separators
from a Telegram supergroup after a forum-topic migration.

Usage:
    python cleanup_bot_noise.py                # dry-run by default
    python cleanup_bot_noise.py --yes          # actually delete
    python cleanup_bot_noise.py --target -1001234567890 --yes

Reads env or hard-coded defaults for session/chat. See
references/bot-noise-cleanup.md in the telegram-topic-transfer skill
for full context.
"""

import argparse
import asyncio
import os
import re
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.environ.get("TELETHON_API_ID", "37090963"))
API_HASH = os.environ.get("TELETHON_API_HASH", "9748665402b621aede54041a072df53a")
SESSION_FILE = os.environ.get(
    "TELETHON_SESSION_FILE",
    r"D:\لn8ن بوت التليجرام\telethon_string.txt",
)
DEFAULT_TARGET = -1002204837936

# Hermes bot user id — adjust per project.
BOT_USER_ID = int(os.environ.get("HERMES_BOT_USER_ID", "8661914459"))

SEPARATOR_PREFIX = "==="
SEPARATOR_RUN = re.compile(r"^=+$")  # any-length run of `=`

BOT_NOTIF_RE = re.compile(
    r"^(⚡\s*Interrupting"
    r"|⚠️\s*Your message was interrupted"
    r"|🔧\s*Processing"
    r")",
    re.IGNORECASE,
)


def message_signature(msg) -> str:
    """Stable fingerprint for dedupe; not needed for cleanup but kept for parity."""
    media = getattr(msg, "media", None)
    text = (getattr(msg, "message", "") or "").strip()
    if media:
        doc_id = getattr(media, "document", None)
        if doc_id is not None:
            doc_id = getattr(doc_id, "id", None)
            return f"doc:{doc_id}"
        photo_id = getattr(media, "photo", None)
        if photo_id is not None:
            photo_id = getattr(photo_id, "id", None)
            return f"photo:{photo_id}"
    if text:
        return f"text:{text[:200]}"
    return ""


def is_bot_notification(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if SEPARATOR_RUN.match(stripped):
        return True
    if SEPARATOR_PREFIX in stripped and len(stripped) <= 60:
        return True  # a run of `===` glued together
    if BOT_NOTIF_RE.match(stripped):
        return True
    return False


async def main_async(target_chat_id: int, do_delete: bool, limit: int):
    with open(SESSION_FILE, "r", encoding="utf-8") as f:
        session_string = f.read().strip()

    client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        print("ERROR: Telethon session not authorized", file=sys.stderr)
        return 1
    me = await client.get_me()
    print(f"Connected as: {me.first_name}")

    target = await client.get_entity(target_chat_id)
    print(f"Target: {target.title}")

    candidates = []
    async for msg in client.iter_messages(target, limit=limit):
        if msg.action is not None:
            continue
        sender = msg.sender
        text = getattr(msg, "message", "") or ""

        if sender and getattr(sender, "id", None) == BOT_USER_ID:
            candidates.append((msg.id, "B", text[:80]))
            continue

        if is_bot_notification(text):
            candidates.append((msg.id, "=", text[:80]))

    if not candidates:
        print("No noise messages found.")
        await client.disconnect()
        return 0

    by_kind = {"B": 0, "=": 0}
    for _, k, _ in candidates:
        by_kind[k] += 1

    print(f"\nCandidates: {len(candidates)} total")
    print(f"  bot-author (B): {by_kind['B']}")
    print(f"  separator/notif (=): {by_kind['=']}")

    print("\nFirst 10:")
    for mid, kind, preview in candidates[:10]:
        print(f"  [{kind}] msg {mid}: {preview!r}")
    if len(candidates) > 10:
        print(f"  ... {len(candidates) - 10} more")

    if not do_delete:
        print("\n=== DRY-RUN — pass --yes to actually delete. ===")
        await client.disconnect()
        return 0

    ids = [mid for mid, _, _ in candidates]
    deleted = 0
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        try:
            await client.delete_messages(target, chunk)
            deleted += len(chunk)
            print(f"Deleted {len(chunk)} (running total: {deleted})")
            await asyncio.sleep(1)
        except Exception as e:
            print(f"Error deleting {len(chunk)}: {e}")
            await asyncio.sleep(2)

    print(f"\nCleanup complete. Deleted: {deleted}")
    await client.disconnect()
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--target", type=int, default=DEFAULT_TARGET,
        help="Target supergroup id (negative for supergroup)",
    )
    p.add_argument(
        "--yes", action="store_true",
        help="Actually delete (default: dry-run)",
    )
    p.add_argument(
        "--limit", type=int, default=10000,
        help="Max messages to scan (default 10000)",
    )
    args = p.parse_args()

    sys.stdout.reconfigure(line_buffering=True)
    return asyncio.run(main_async(args.target, args.yes, args.limit))


if __name__ == "__main__":
    sys.exit(main())
