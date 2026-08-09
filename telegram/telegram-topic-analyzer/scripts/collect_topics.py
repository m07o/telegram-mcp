#!/usr/bin/env python3
"""
Collect all forum topics from masass18 group using Telethon.
Saves to ~/all_topics_masass18.json
"""

import asyncio
import json
import struct
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.tlobject import TLObject, TLRequest
from telethon.tl.types import Channel


class GetForumTopicsRequest(TLRequest):
    """Raw request for channels.getForumTopics missing in Telethon 1.42-1.43."""

    CONSTRUCTOR_ID = 0x0DE560D1
    SUBCLASS_OF_ID = 0x0

    def __init__(self, channel, offset_date, offset_id, offset_topic, limit, q=None):
        self.channel = channel
        self.q = q
        self.offset_date = offset_date
        self.offset_id = offset_id
        self.offset_topic = offset_topic
        self.limit = limit

    async def resolve(self, client, utils):
        self.channel = utils.get_input_channel(await client.get_input_entity(self.channel))

    def to_dict(self):
        return {
            "_": "GetForumTopicsRequest",
            "channel": (self.channel.to_dict() if isinstance(self.channel, TLObject) else self.channel),
            "q": self.q,
            "offset_date": self.offset_date,
            "offset_id": self.offset_id,
            "offset_topic": self.offset_topic,
            "limit": self.limit,
        }

    def _bytes(self):
        flags = 0 if self.q is None or self.q is False else 1
        return b"".join(
            (
                struct.pack("<I", self.CONSTRUCTOR_ID),
                struct.pack("<I", flags),
                self.channel._bytes(),
                b"" if self.q is None or self.q is False else self.serialize_bytes(self.q),
                struct.pack("<i", self.offset_date),
                struct.pack("<i", self.offset_id),
                struct.pack("<i", self.offset_topic),
                struct.pack("<i", self.limit),
            )
        )


# Load session
SESSION_PATH = Path(r"D:\لn8n بوت التليجرام\telethon_string.txt")
with open(SESSION_PATH, 'r', encoding='utf-8') as f:
    SESSION_STRING = f.read().strip()

API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"
CHAT_ID = -1002191043427
OUTPUT_FILE = Path.home() / "all_topics_masass18.json"


async def collect_all_topics():
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        print("❌ Not authorized!")
        return

    entity = await client.get_entity(CHAT_ID)
    print(f"📂 Group: {entity.title} ({entity.id})")

    all_topics = {}
    offset_topic = 0
    offset_id = 0
    offset_date = 0
    limit = 100
    page = 0
    seen_ids = set()

    while True:
        page += 1
        request = GetForumTopicsRequest(
            channel=entity,
            offset_date=offset_date,
            offset_id=offset_id,
            offset_topic=offset_topic,
            limit=limit,
            q=None
        )
        result = await client(request)
        topics = getattr(result, 'topics', None) or []

        if not topics:
            print(f"  Page {page}: No more topics")
            break

        new_count = 0
        for t in topics:
            tid = t.id
            if tid not in seen_ids:
                seen_ids.add(tid)
                title = getattr(t, 'title', '(no title)')
                all_topics[str(tid)] = {
                    "title": title,
                    "closed": getattr(t, 'closed', False),
                    "hidden": getattr(t, 'hidden', False),
                    "unread_count": getattr(t, 'unread_count', 0),
                    "total_messages": getattr(t, 'total_messages', 0),
                    "top_message_id": getattr(t, 'top_message', 0)
                }
                new_count += 1

        print(f"  Page {page}: {len(topics)} topics, {new_count} new, total: {len(all_topics)}")

        # Save after each page
        save_data = {
            "group_id": CHAT_ID,
            "group_title": entity.title,
            "total_count": len(all_topics),
            "topics": all_topics
        }
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)

        if len(topics) < limit:
            print(f"  ✅ Last page (got {len(topics)} < {limit})")
            break

        if new_count == 0:
            print(f"  ⚠️ All duplicates - stopping")
            break

        # Update offsets from last topic + last message
        last_topic = topics[-1]
        offset_topic = last_topic.id

        messages = getattr(result, 'messages', None) or []
        if messages:
            last_msg = messages[-1]
            offset_id = last_msg.id
            offset_date = int(last_msg.date.timestamp()) if last_msg.date else 0
        else:
            offset_id = 0
            offset_date = 0

        # Rate limit
        await asyncio.sleep(2)

    print(f"\n🎉 Done! Collected {len(all_topics)} unique topics")
    print(f"💾 Saved to {OUTPUT_FILE}")

    await client.disconnect()
    return all_topics


if __name__ == "__main__":
    asyncio.run(collect_all_topics())