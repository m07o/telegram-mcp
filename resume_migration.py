#!/usr/bin/env python3
"""
Resume migration from migration_state.json using the Telethon script directly.
"""

import os
import sys
import json
import asyncio
import argparse
from pathlib import Path

# Add the telegram-mcp directory to path
sys.path.insert(0, r"B:/for-hermes/telegram-mcp")

from dotenv import load_dotenv
from telethon import TelegramClient, functions
from telethon.sessions import StringSession
from telethon.tl import types
from telegram_mcp.forum_pagination import ChatLike, extract_created_topic_id, iter_forum_topics, list_forum_topics

load_dotenv()

# Load migration state
migration_state_path = Path.home() / "migration_state.json"
with open(migration_state_path, "r", encoding="utf-8") as f:
    state = json.load(f)

MIGRATED = {item["masass18_topic_id"] for item in state["migrated_topics"] if item["status"] in ("COMPLETE", "PARTIAL")}
print(f"Already migrated {len(MIGRATED)} topics")

API_ID = int(os.getenv("TELEGRAM_API_ID"))
API_HASH = os.getenv("TELEGRAM_API_HASH")
SESSION_STRING = os.getenv("TELEGRAM_SESSION_STRING")

SOURCE_CHAT = -1002191043427
DEST_CHAT = -1002204837936

async def get_all_topics(client, entity):
    return await list_forum_topics(client, entity)

def get_topic_title(topic):
    title = getattr(topic, "title", None) or ""
    if not title.strip():
        return f"topic_{topic.id}"
    return title.strip()

async def count_topic_messages(client, entity, topic_id):
    count = 0
    async for _ in client.iter_messages(entity, reply_to=topic_id):
        count += 1
    return count

async def get_target_topics_map(client, entity):
    topics_map = {}
    async for t in iter_forum_topics(client, entity):
        if isinstance(t, types.ForumTopic):
            topics_map[get_topic_title(t)] = t.id
        await asyncio.sleep(0.0)
    return topics_map

async def main():
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()
    
    if not await client.is_user_authorized():
        print("Session string is not valid/authorized!")
        await client.disconnect()
        return
    
    from_entity = await client.get_entity(SOURCE_CHAT)
    to_entity = await client.get_entity(DEST_CHAT)
    
    print("Fetching all topics from source...")
    all_topics = await get_all_topics(client, from_entity)
    print(f"Total topics in source: {len(all_topics)}")
    
    print("Fetching existing topics in target...")
    target_map = await get_target_topics_map(client, to_entity)
    print(f"Total topics in target: {len(target_map)}")
    
    # Find topics to migrate (not already migrated, not in target)
    to_migrate = []
    for topic in all_topics:
        if topic.id in MIGRATED:
            continue
        title = get_topic_title(topic)
        if title in target_map:
            print(f"  Skipping (exists in target): {title} (id={topic.id})")
            continue
        to_migrate.append(topic)
    
    print(f"\nTopics to migrate: {len(to_migrate)}")
    for t in to_migrate:
        print(f"  [{t.id}] {get_topic_title(t)}")
    
    # Auto-proceed (non-interactive)
    print("\nAuto-proceeding with migration...")
    
    # Migrate each topic
    for i, topic in enumerate(to_migrate, 1):
        title = get_topic_title(topic)
        print(f"\n[{i}/{len(to_migrate)}] Migrating: [{topic.id}] {title}")
        
        # Create topic in target
        create_result = await client(
            functions.messages.CreateForumTopicRequest(
                peer=to_entity,
                title=title,
                random_id=topic.id,  # Use source topic ID as random_id for uniqueness
            )
        )
        
        target_topic_id = extract_created_topic_id(create_result)
        if not target_topic_id:
            print(f"  FAILED: Could not create topic")
            continue
        
        print(f"  Created topic with ID: {target_topic_id}")
        
        # Copy messages
        copied = 0
        failed = 0
        async for msg in client.iter_messages(from_entity, reply_to=topic.id):
            if getattr(msg, "action", None):
                continue
            raw_text = getattr(msg, "message", None) or ""
            if raw_text.strip() in {".", "===", "/", "@"} and not getattr(msg, "media", None):
                continue
            
            try:
                send_kwargs = {"reply_to": target_topic_id}
                if getattr(msg, "media", None):
                    send_kwargs["file"] = msg.media
                    if raw_text:
                        send_kwargs["caption"] = raw_text
                        entities = getattr(msg, "entities", None)
                        if entities:
                            send_kwargs["formatting_entities"] = entities
                    if hasattr(msg, "video") and msg.video:
                        send_kwargs["supports_streaming"] = True
                    await client.send_file(to_entity, **send_kwargs)
                elif raw_text:
                    entities = getattr(msg, "entities", None)
                    if entities:
                        send_kwargs["formatting_entities"] = entities
                    await client.send_message(to_entity, raw_text, **send_kwargs)
                else:
                    continue
                
                copied += 1
                await asyncio.sleep(2.0)  # Delay between messages
            except Exception as e:
                failed += 1
                print(f"  Failed to copy message {msg.id}: {e}")
                await asyncio.sleep(1)
        
        print(f"  Done: {copied} copied, {failed} failed")
    
    await client.disconnect()
    print("\nMigration complete!")

if __name__ == "__main__":
    asyncio.run(main())