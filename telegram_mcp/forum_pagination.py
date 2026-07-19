"""Shared forum-topic pagination helper.

The Telegram API returns a maximum of 100 forum topics per `GetForumTopicsRequest` call.
Supergroups with more topics than that require iteration. This module is the single
source of truth for that pagination loop and is shared between the MCP ``copy_topic``
implementation and the standalone ``copy_topics.py`` script.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any, Union

from telethon import TelegramClient, functions
from telethon.tl import types
from telethon.tl.functions.messages import GetForumTopicsByIDRequest

#: Topic page size requested per call; matches the Telegram API limit.
PAGE_SIZE: int = 100

#: Inter-request delay to stay well under Telegram's flood thresholds.
INTER_PAGE_DELAY: float = 0.5

ChatLike = Union[types.Chat, types.Channel, types.User, types.ChatFull]


async def iter_forum_topics(
    client: TelegramClient,
    entity: ChatLike,
    *,
    page_size: int = PAGE_SIZE,
    inter_page_delay: float = INTER_PAGE_DELAY,
) -> AsyncIterator[types.ForumTopic]:
    """Yield every forum topic in a forum-enabled supergroup.

    Pages through ``GetForumTopicsRequest`` until a page returns no new topics,
    guaranteeing the caller observes the full topic list regardless of size.
    """
    offset_topic = 0
    seen: set[int] = set()

    while True:
        result = await client(
            functions.messages.GetForumTopicsRequest(
                peer=entity,
                offset_date=0,
                offset_id=0,
                offset_topic=offset_topic,
                limit=page_size,
            )
        )
        batch: list[types.Type] = list(getattr(result, "topics", []) or [])
        if not batch:
            break

        new_count = 0
        for t in batch:
            if hasattr(t, "id") and t.id not in seen:
                seen.add(t.id)
                assert isinstance(t, types.ForumTopic)
                yield t
                new_count += 1

        if new_count == 0:
            break

        offset_topic = batch[-1].id
        await asyncio.sleep(inter_page_delay)


async def list_forum_topics(
    client: TelegramClient,
    entity: ChatLike,
    *,
    page_size: int = PAGE_SIZE,
    inter_page_delay: float = INTER_PAGE_DELAY,
) -> list[types.ForumTopic]:
    """Materialize the full list of forum topics (use only when iteration isn't enough)."""
    return [
        t
        async for t in iter_forum_topics(
            client,
            entity,
            page_size=page_size,
            inter_page_delay=inter_page_delay,
        )
    ]


async def get_topic_title(
    client: TelegramClient,
    chat_id: int | str,
    topic_id: int,
) -> str:
    """Fetch the title of a single forum topic by ID.

    Uses the ``GetForumTopicsByIDRequest`` RPC to retrieve the topic title
    without loading all messages.  Returns ``"<topic {topic_id}>"`` if
    the topic cannot be found (e.g. General topic or deleted topic).
    """
    try:
        result = await client(GetForumTopicsByIDRequest(peer=chat_id, topics=[topic_id]))
        if result.messages:
            return result.messages[0].message or f"<topic {topic_id}>"
    except Exception as exc:
        logging.getLogger(__name__).debug("get_topic_title failed for topic %s: %s", topic_id, exc)
    return f"<topic {topic_id}>"


def build_topic_index(
    topics: list[types.ForumTopic],
) -> dict[int, str]:
    """Build a lookup dict mapping topic_id -> topic title.

    Useful for resolving topic IDs to human-readable names
    in progress reports and dry-run output.
    """
    return {t.id: t.title for t in topics}


def extract_created_topic_id(result: Any) -> int | None:
    """Best-effort extraction of the new topic's message id from a
    CreateForumTopicRequest response.

    Telethon returns ``Updates`` where the id lives inside
    ``updates[].message.id`` (an ``UpdateNewMessage``). Falls back to
    ``result.message.id`` for older variants. Returns None when no id
    could be extracted.

    This is the same logic as ``telegram_mcp.tools.chats._extract_created_topic_id``
    but exposed here so both the MCP tool and the standalone CLI share it.
    """
    updates = getattr(result, "updates", None) or []
    for update in updates:
        message = getattr(update, "message", None)
        message_id = getattr(message, "id", None)
        if isinstance(message_id, int):
            return message_id

        update_id = getattr(update, "id", None)
        if isinstance(update_id, int):
            return update_id

    message = getattr(result, "message", None)
    message_id = getattr(message, "id", None)
    if isinstance(message_id, int):
        return message_id

    return None


__all__ = [
    "PAGE_SIZE",
    "INTER_PAGE_DELAY",
    "ChatLike",
    "iter_forum_topics",
    "list_forum_topics",
    "get_topic_title",
    "build_topic_index",
    "extract_created_topic_id",
]
