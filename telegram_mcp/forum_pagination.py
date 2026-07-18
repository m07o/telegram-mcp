"""Shared forum-topic pagination helper.

The Telegram API returns a maximum of 100 forum topics per `GetForumTopicsRequest` call.
Supergroups with more topics than that require iteration. This module is the single
source of truth for that pagination loop and is shared between the MCP ``copy_topic``
implementation and the standalone ``copy_topics.py`` script.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Union

from telethon import TelegramClient, functions
from telethon.tl import types

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
    return [t async for t in iter_forum_topics(
        client, entity, page_size=page_size, inter_page_delay=inter_page_delay,
    )]


__all__ = [
    "PAGE_SIZE",
    "INTER_PAGE_DELAY",
    "ChatLike",
    "iter_forum_topics",
    "list_forum_topics",
]
