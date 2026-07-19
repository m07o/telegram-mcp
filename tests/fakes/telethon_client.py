"""A fake TelegramClient that records calls and returns scripted responses.

Used by tests/test_forum_forward.py to drive the real _copy_single_topic
and forward_topics_from_group code paths without touching the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class FakeMessage:
    """Minimal stand-in for telethon's Message with the fields the tool reads."""

    id: int
    message: str = ""
    media: Any = None
    entities: list[Any] = field(default_factory=list)
    action: Any = None
    video: Any = None
    date: Any = None


@dataclass
class FakeTopic:
    """Minimal stand-in for telethon's ForumTopic."""

    id: int
    title: str


@dataclass
class FakeUpdates:
    """Stand-in for the Updates object returned by CreateForumTopicRequest."""

    updates: list[Any] = field(default_factory=list)
    messages: list[FakeMessage] = field(default_factory=list)


def make_topic(topic_id: int, title: str) -> FakeTopic:
    return FakeTopic(id=topic_id, title=title)


class FakeClient:
    """Records __call__s and iter_messages calls; returns scripted results."""

    def __init__(
        self,
        *,
        create_topic_result: FakeUpdates | None = None,
        topic_messages: dict[int, list[FakeMessage]] | None = None,
        iter_messages_order: str = "newest_first",
    ) -> None:
        self.create_topic_result = create_topic_result or FakeUpdates()
        self.topic_messages = topic_messages or {}
        # iter_messages by default returns newest-first (matching Telethon)
        self.iter_messages_order = iter_messages_order
        self.calls: list[Any] = list()
        self.sent_messages: list[dict[str, Any]] = list()
        self.sent_files: list[dict[str, Any]] = list()
        self.created_topics: list[dict[str, Any]] = list()

    async def __call__(self, request: Any) -> FakeUpdates:
        self.calls.append(request)
        # Heuristic: if request looks like CreateForumTopicRequest, return scripted result
        if "CreateForumTopic" in type(request).__name__:
            self.created_topics.append({"title": getattr(request, "title", "")})
            return self.create_topic_result
        # Default: return empty updates
        return FakeUpdates()

    async def iter_messages(
        self,
        entity: Any,
        reply_to: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[FakeMessage]:
        msgs = list(self.topic_messages.get(reply_to, []))
        if self.iter_messages_order == "newest_first":
            # Telethon returns newest first (highest id first)
            msgs = sorted(msgs, key=lambda m: m.id, reverse=True)
        else:
            msgs = sorted(msgs, key=lambda m: m.id)
        for m in msgs:
            yield m

    async def send_message(self, entity: Any, text: str, **kwargs: Any) -> None:
        self.sent_messages.append({"entity": entity, "text": text, **kwargs})

    async def send_file(self, entity: Any, file: Any = None, **kwargs: Any) -> None:
        self.sent_files.append({"entity": entity, "file": file, **kwargs})
