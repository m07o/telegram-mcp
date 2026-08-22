"""Tests for copy_topic MCP tool - covering the 4 bug fixes."""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any
from types import SimpleNamespace

import pytest

from telegram_mcp.tools.chats import copy_topic
from tests.fakes.telethon_client import FakeClient, FakeMessage, FakeUpdates


def _run(coro):
    return asyncio.run(coro)


def make_entity(chat_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=chat_id,
        title=f"Test Group {chat_id}",
        megagroup=True,
        forum=True,
        access_hash=123456,
    )


class CopyTopicFakeClient(FakeClient):
    """Extended FakeClient for copy_topic tests."""

    def __init__(
        self,
        *,
        source_topics: list | None = None,
        source_messages: dict[int, list[FakeMessage]] | None = None,
        target_topics: list | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._source_topics = source_topics or []
        self._source_messages = source_messages or {}
        self._target_topics = target_topics or []
        self._topic_page_idx = 0

    async def __call__(self, request: Any) -> Any:
        self.calls.append(request)
        req_name = type(request).__name__

        if "GetForumTopics" in req_name:
            # Simulate pagination
            limit = getattr(request, "limit", 100)
            offset_topic = getattr(request, "offset_topic", 0)

            start_idx = 0
            if offset_topic:
                for i, t in enumerate(self._target_topics):
                    if t.id == offset_topic:
                        start_idx = i + 1
                        break

            page = self._target_topics[start_idx : start_idx + limit]
            return SimpleNamespace(topics=page, messages=[])

        if "CreateForumTopic" in req_name:
            self.created_topics.append({"title": getattr(request, "title", "")})
            # Return a fake update with the created topic ID
            topic_id = len(self._target_topics) + 1
            self._target_topics.append(
                SimpleNamespace(id=topic_id, title=getattr(request, "title", ""))
            )
            return FakeUpdates(
                updates=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            id=topic_id,
                            peer_id=SimpleNamespace(channel_id=123456),
                        )
                    )
                ],
                messages=[],
            )

        return FakeUpdates()

    async def iter_messages(
        self,
        entity: Any,
        reply_to: int | None = None,
        **kwargs: Any,
    ):
        print(f"DEBUG iter_messages: reply_to={reply_to}, kwargs={kwargs}")
        msgs = list(self._source_messages.get(reply_to, []))
        print(f"DEBUG: got {len(msgs)} messages from source")
        if kwargs.get("reverse"):
            msgs = sorted(msgs, key=lambda m: m.id)
        else:
            msgs = sorted(msgs, key=lambda m: m.id, reverse=True)
        limit = kwargs.get("limit", 0)
        if limit:
            print(f"DEBUG: applying limit={limit}")
            msgs = msgs[:limit]
        for m in msgs:
            yield m


def _patch_copy_topic(monkeypatch, client, from_entity, to_entity):
    import telegram_mcp.runtime as runtime_mod
    import telegram_mcp.tools.chats as chats_mod

    def fake_get_client(account=""):
        print(f"DEBUG fake_get_client called with account={account}")
        return client

    async def fake_resolve(chat_id, cl):
        print(f"DEBUG fake_resolve called with chat_id={chat_id}")
        if chat_id == -100111:
            return from_entity
        elif chat_id == -100222:
            return to_entity
        return make_entity(chat_id)

    monkeypatch.setattr(runtime_mod, "get_client", fake_get_client)
    monkeypatch.setattr(chats_mod, "get_client", fake_get_client)
    monkeypatch.setattr(chats_mod, "resolve_entity", fake_resolve)


# --- Test FIX 1: Uses custom GetForumTopicsRequest (channels.getForumTopics) ---


def test_copy_topic_uses_custom_getforumtopics(monkeypatch):
    """copy_topic uses the custom GetForumTopicsRequest class, not functions.messages."""
    from_entity = make_entity(-100111)
    to_entity = make_entity(-100222)

    client = CopyTopicFakeClient(
        source_messages={1: [FakeMessage(id=10, message="Hello")]},
        target_topics=[],  # No existing topics
    )
    _patch_copy_topic(monkeypatch, client, from_entity, to_entity)

    result = _run(
        copy_topic(
            from_chat_id=-100111,
            topic_id=1,
            to_chat_id=-100222,
            topic_title="Test Topic",
            limit=10,
        )
    )

    # Verify it succeeded
    assert "copied: 1" in result

    # Verify the custom GetForumTopicsRequest was used (check calls)
    # The custom request is called with `channel=` not `peer=`
    getforumtopics_calls = [c for c in client.calls if "GetForumTopics" in type(c).__name__]
    assert len(getforumtopics_calls) > 0
    # Check it was called with channel= parameter
    for call in getforumtopics_calls:
        assert hasattr(call, "channel")


# --- Test FIX 2: First-wins deduplication for existing topics ---


def test_copy_topic_first_wins_dedup(monkeypatch):
    """When multiple topics have same title, use the FIRST (oldest) ID."""
    from_entity = make_entity(-100111)
    to_entity = make_entity(-100222)

    # Create target with TWO topics having the same title
    target_topics = [
        SimpleNamespace(id=100, title="Existing Topic"),
        SimpleNamespace(id=200, title="Existing Topic"),  # Duplicate
        SimpleNamespace(id=300, title="Other Topic"),
    ]

    client = CopyTopicFakeClient(
        source_messages={1: [FakeMessage(id=10, message="Hello")]},
        target_topics=target_topics,
    )
    _patch_copy_topic(monkeypatch, client, from_entity, to_entity)

    result = _run(
        copy_topic(
            from_chat_id=-100111,
            topic_id=1,
            to_chat_id=-100222,
            topic_title="Existing Topic",
            limit=10,
        )
    )

    assert "copied: 1" in result

    # Verify it used the FIRST topic ID (100), not the last (200)
    # The send should use reply_to=100
    assert any(
        kwargs.get("reply_to") == 100 for call in client.sent_messages for kwargs in [call]
    ), f"Expected reply_to=100, got {[c.get('reply_to') for c in client.sent_messages]}"


# --- Test FIX 3: SKIP_PATTERNS only skips exact matches without media/entities ---


def test_copy_topic_skip_patterns_exact_match_only(monkeypatch):
    """SKIP_PATTERNS only skips exact matches with no media and no entities."""
    from_entity = make_entity(-100111)
    to_entity = make_entity(-100222)

    # Test messages: exact "." (should skip), " . " (whitespace, should skip),
    # "." with media (should NOT skip), "." with entities (should NOT skip),
    # "hello." (contains pattern but not exact, should NOT skip)
    import datetime as dt

    msgs = [
        FakeMessage(id=1, message=".", date=dt.datetime(2026, 1, 1)),  # Exact, skip
        FakeMessage(id=2, message=" . ", date=dt.datetime(2026, 1, 1)),  # Whitespace, skip
        FakeMessage(id=3, message="hello.", date=dt.datetime(2026, 1, 1)),  # Not exact, copy
        FakeMessage(id=4, message="/", date=dt.datetime(2026, 1, 1)),  # Exact, skip
        FakeMessage(id=5, message="@", date=dt.datetime(2026, 1, 1)),  # Exact, skip
        FakeMessage(id=6, message="===", date=dt.datetime(2026, 1, 1)),  # Exact, skip
    ]

    client = CopyTopicFakeClient(
        source_messages={1: msgs},
        target_topics=[],
    )
    _patch_copy_topic(monkeypatch, client, from_entity, to_entity)

    result = _run(
        copy_topic(
            from_chat_id=-100111,
            topic_id=1,
            to_chat_id=-100222,
            topic_title="Test Topic",
        )
    )

    # Should copy only message 3 ("hello."), skip 1,2,4,5,6
    assert "copied: 1" in result
    assert "5 skipped" in result


def test_copy_topic_skip_patterns_with_media_not_skipped(monkeypatch):
    """Messages with media are NOT skipped even if they match SKIP_PATTERNS."""
    from_entity = make_entity(-100111)
    to_entity = make_entity(-100222)

    import datetime as dt

    msgs = [
        FakeMessage(id=1, message=".", date=dt.datetime(2026, 1, 1), media=object()),  # Has media
    ]

    client = CopyTopicFakeClient(
        source_messages={1: msgs},
        target_topics=[],
    )
    _patch_copy_topic(monkeypatch, client, from_entity, to_entity)

    result = _run(
        copy_topic(
            from_chat_id=-100111,
            topic_id=1,
            to_chat_id=-100222,
            topic_title="Test Topic",
        )
    )

    # Should copy the message with media, not skip it
    assert "copied: 1" in result
    assert "0 skipped" in result


def test_copy_topic_skip_patterns_with_entities_not_skipped(monkeypatch):
    """Messages with formatting entities are NOT skipped."""
    from_entity = make_entity(-100111)
    to_entity = make_entity(-100222)

    import datetime as dt

    # Message with entities (bold, italic, etc.)
    msgs = [
        FakeMessage(id=1, message=".", date=dt.datetime(2026, 1, 1), entities=[object()]),
    ]

    client = CopyTopicFakeClient(
        source_messages={1: msgs},
        target_topics=[],
    )
    _patch_copy_topic(monkeypatch, client, from_entity, to_entity)

    result = _run(
        copy_topic(
            from_chat_id=-100111,
            topic_id=1,
            to_chat_id=-100222,
            topic_title="Test Topic",
        )
    )

    assert "copied: 1" in result
    assert "0 skipped" in result


# --- Test FIX 4: Pagination for existing topics lookup ---


def test_copy_topic_pagination(monkeypatch):
    """copy_topic paginates through ALL target topics (not just first 100)."""
    from_entity = make_entity(-100111)
    to_entity = make_entity(-100222)

    # Create 150 target topics - topic we want is at position 120
    target_topics = [SimpleNamespace(id=i, title=f"Topic {i}") for i in range(1, 151)]
    # Add the target topic at position 120
    target_topics[119] = SimpleNamespace(id=120, title="Target Topic")

    client = CopyTopicFakeClient(
        source_messages={1: [FakeMessage(id=10, message="Hello")]},
        target_topics=target_topics,
    )
    _patch_copy_topic(monkeypatch, client, from_entity, to_entity)

    result = _run(
        copy_topic(
            from_chat_id=-100111,
            topic_id=1,
            to_chat_id=-100222,
            topic_title="Target Topic",
            limit=10,
        )
    )

    assert "copied: 1" in result
    # Should have found the topic at position 120 (second page)
    assert any(kwargs.get("reply_to") == 120 for call in client.sent_messages for kwargs in [call])


# --- Regression tests for existing functionality ---


def test_copy_topic_creates_new_topic(monkeypatch):
    """copy_topic creates new topic when it doesn't exist."""
    from_entity = make_entity(-100111)
    to_entity = make_entity(-100222)

    client = CopyTopicFakeClient(
        source_messages={1: [FakeMessage(id=10, message="Hello")]},
        target_topics=[],
    )
    _patch_copy_topic(monkeypatch, client, from_entity, to_entity)

    result = _run(
        copy_topic(
            from_chat_id=-100111,
            topic_id=1,
            to_chat_id=-100222,
            topic_title="New Topic",
            limit=10,
        )
    )

    assert "copied: 1" in result
    assert len(client.created_topics) == 1
    assert client.created_topics[0]["title"] == "New Topic"


def test_copy_topic_uses_source_topic_title_when_not_provided(monkeypatch):
    """When topic_title is None, uses 'topic_{topic_id}' as title."""
    from_entity = make_entity(-100111)
    to_entity = make_entity(-100222)

    client = CopyTopicFakeClient(
        source_messages={42: [FakeMessage(id=10, message="Hello")]},
        target_topics=[],
    )
    _patch_copy_topic(monkeypatch, client, from_entity, to_entity)

    result = _run(
        copy_topic(
            from_chat_id=-100111,
            topic_id=42,
            to_chat_id=-100222,
            # topic_title omitted
        )
    )

    assert "copied: 1" in result
    assert len(client.created_topics) == 1
    assert client.created_topics[0]["title"] == "topic_42"


def test_copy_topic_respects_limit(monkeypatch):
    """copy_topic respects the limit parameter."""
    from_entity = make_entity(-100111)
    to_entity = make_entity(-100222)

    import datetime as dt

    msgs = [
        FakeMessage(id=i, message=f"Msg {i}", date=dt.datetime(2026, 1, 1)) for i in range(1, 11)
    ]

    client = CopyTopicFakeClient(
        source_messages={1: msgs},
        target_topics=[],
    )
    _patch_copy_topic(monkeypatch, client, from_entity, to_entity)

    result = _run(
        copy_topic(
            from_chat_id=-100111,
            topic_id=1,
            to_chat_id=-100222,
            topic_title="Test",
            limit=3,  # Only copy 3 messages
        )
    )

    assert "copied: 3" in result


def test_copy_topic_skips_service_messages(monkeypatch):
    """copy_topic skips service messages (action messages)."""
    from_entity = make_entity(-100111)
    to_entity = make_entity(-100222)

    import datetime as dt

    msgs = [
        FakeMessage(id=1, message="Real message", date=dt.datetime(2026, 1, 1)),
        FakeMessage(
            id=2, message="", date=dt.datetime(2026, 1, 1), action=object()
        ),  # Service msg
        FakeMessage(id=3, message="Another real", date=dt.datetime(2026, 1, 1)),
    ]

    client = CopyTopicFakeClient(
        source_messages={1: msgs},
        target_topics=[],
    )
    _patch_copy_topic(monkeypatch, client, from_entity, to_entity)

    result = _run(
        copy_topic(
            from_chat_id=-100111,
            topic_id=1,
            to_chat_id=-100222,
            topic_title="Test",
        )
    )

    # Should copy 2 real messages, skip 1 service message
    assert "copied: 2" in result
