"""Integration tests for analyze_group MCP tool using fake Telethon client."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from typing import Any
from types import SimpleNamespace

import pytest

from telegram_mcp.tools.groups import analyze_group
from telegram_mcp.runtime import mcp
from tests.fakes.telethon_client import FakeClient, FakeMessage, FakeTopic, FakeUpdates


# Helper to create a mock ForumTopic from Telethon
def make_forum_topic(topic_id: int, title: str, **kwargs) -> SimpleNamespace:
    """Create a minimal ForumTopic-like object."""
    defaults = {
        "id": topic_id,
        "title": title,
        "total_messages": 0,
        "top_message": None,
        "icon_emoji_id": None,
        "hidden": False,
        "closed": False,
        "description": "",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def make_updates_with_topics(*topics) -> SimpleNamespace:
    """Create an Updates-like object with topics list."""
    return SimpleNamespace(topics=list(topics))


def make_entity(chat_id: int) -> SimpleNamespace:
    """Create a fake entity that passes validation."""
    return SimpleNamespace(
        id=chat_id,
        title=f"Test Group {chat_id}",
        megagroup=True,
        forum=True,
        access_hash=123456,
    )


class AnalyzeGroupFakeClient(FakeClient):
    """Extended FakeClient that supports GetForumTopicsRequest and iter_messages per topic."""

    def __init__(
        self,
        *,
        forum_topics: list[SimpleNamespace] | None = None,
        topic_messages: dict[int, list[FakeMessage]] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._forum_topics = forum_topics or []
        self._topic_messages = topic_messages or {}

    async def __call__(self, request: Any) -> Any:
        self.calls.append(request)
        req_name = type(request).__name__

        if "GetForumTopics" in req_name:
            # Simulate pagination: return up to 100 topics per call
            limit = getattr(request, "limit", 100)
            offset_topic = getattr(request, "offset_topic", 0)

            # Find starting index
            start_idx = 0
            if offset_topic:
                for i, t in enumerate(self._forum_topics):
                    if t.id == offset_topic:
                        start_idx = i + 1
                        break

            page = self._forum_topics[start_idx : start_idx + limit]
            return make_updates_with_topics(*page)

        if "CreateForumTopic" in req_name:
            self.created_topics.append({"title": getattr(request, "title", "")})
            return self.create_topic_result

        return FakeUpdates()

    async def iter_messages(
        self,
        entity: Any,
        reply_to: int | None = None,
        **kwargs: Any,
    ):
        """Yield messages for a specific topic."""
        msgs = list(self._topic_messages.get(reply_to, []))
        # Default to newest first (Telethon behavior)
        if kwargs.get("reverse"):
            msgs = sorted(msgs, key=lambda m: m.id)
        else:
            msgs = sorted(msgs, key=lambda m: m.id, reverse=True)
        limit = kwargs.get("limit", 0)
        if limit:
            msgs = msgs[:limit]
        for m in msgs:
            yield m


def _run(coro):
    return asyncio.run(coro)


# --- Tests for analyze_group ---


@pytest.fixture
def mock_client_and_entity():
    """Create a fake client with forum topics and messages, and a valid entity."""
    topics = [
        make_forum_topic(
            1,
            "General",
            total_messages=50,
            top_message="2026-01-01T00:00:00",
            icon_emoji_id=123,
            hidden=False,
            closed=False,
            description="Welcome",
        ),
        make_forum_topic(
            2,
            "Bug Reports",
            total_messages=30,
            top_message="2026-01-02T00:00:00",
            icon_emoji_id=None,
            hidden=False,
            closed=False,
            description="",
        ),
        make_forum_topic(
            3,
            "bug reports",
            total_messages=25,
            top_message="2026-01-03T00:00:00",
            icon_emoji_id=None,
            hidden=True,
            closed=False,
            description="Report bugs here",
        ),
        make_forum_topic(
            4,
            "Features",
            total_messages=10,
            top_message="2025-06-01T00:00:00",
            icon_emoji_id=456,
            hidden=False,
            closed=True,
            description="Feature requests",
        ),
        make_forum_topic(
            5,
            "Random",
            total_messages=0,
            top_message=None,
            icon_emoji_id=None,
            hidden=False,
            closed=False,
            description="",
        ),
    ]

    topic_messages = {
        1: [
            FakeMessage(id=10, message="First post", date=dt.datetime(2026, 1, 1, 0, 0, 0)),
            FakeMessage(id=11, message="Second post", date=dt.datetime(2026, 1, 1, 1, 0, 0)),
            FakeMessage(id=12, message="Third post", date=dt.datetime(2026, 1, 1, 2, 0, 0)),
        ],
        2: [
            FakeMessage(id=20, message="Bug found", date=dt.datetime(2026, 1, 2, 0, 0, 0)),
            FakeMessage(id=21, message="Another bug", date=dt.datetime(2026, 1, 2, 1, 0, 0)),
        ],
        3: [
            FakeMessage(id=30, message="Bug report 1", date=dt.datetime(2026, 1, 3, 0, 0, 0)),
        ],
        4: [
            FakeMessage(id=40, message="Feature request", date=dt.datetime(2025, 6, 1, 0, 0, 0)),
        ],
        5: [],
    }

    client = AnalyzeGroupFakeClient(
        forum_topics=topics,
        topic_messages=topic_messages,
    )
    entity = make_entity(-100123456)
    return client, entity


def _patch_analyze_group(monkeypatch, client, entity):
    """Monkeypatch get_client and resolve_entity for analyze_group tests."""
    import telegram_mcp.tools.groups as groups_mod

    def fake_get_client(account=""):
        return client

    async def fake_resolve(chat_id, cl):
        return entity

    monkeypatch.setattr(groups_mod, "get_client", fake_get_client)
    monkeypatch.setattr(groups_mod, "resolve_entity", fake_resolve)


def test_analyze_group_summary_mode(monkeypatch, mock_client_and_entity):
    """analyze_group in summary mode returns aggregated counts."""
    client, entity = mock_client_and_entity
    _patch_analyze_group(monkeypatch, client, entity)

    result = _run(analyze_group(chat_id=-100123456, mode="summary", inactivity_days=90))
    data = json.loads(result)

    assert data["chat_id"] == -100123456
    assert data["summary_stats"]["total_topics"] == 5
    assert data["summary_stats"]["total_messages"] == 115
    assert (
        data["findings"]["duplicate_topic_groups"] == 1
    )  # "Bugs" and "Bug Reports" normalize to same
    assert data["findings"]["topics_with_gaps"] >= 2  # no_description, no_icon, low_messages
    assert data["findings"]["dead_topics"] >= 1  # topic 4 is old, topic 5 has no date
    assert "summary_text" in data


def test_analyze_group_detail_mode(monkeypatch, mock_client_and_entity):
    """analyze_group in detail mode returns full arrays with topic_ids."""
    client, entity = mock_client_and_entity
    _patch_analyze_group(monkeypatch, client, entity)

    result = _run(analyze_group(chat_id=-100123456, mode="detail", inactivity_days=90))
    data = json.loads(result)

    assert "duplicates" in data
    assert "gaps" in data
    assert "dead_topics" in data
    assert "topics" in data

    # Check duplicates structure
    assert len(data["duplicates"]) == 1
    dup = data["duplicates"][0]
    assert dup["normalized_title"] == "bug reports"
    assert set(dup["topic_ids"]) == {2, 3}
    assert "Bug Reports" in dup["original_titles"]
    assert "bug reports" in dup["original_titles"]

    # Check gaps structure
    assert len(data["gaps"]) >= 2
    gap_kinds = {g["kind"] for g in data["gaps"]}
    assert "no_description" in gap_kinds
    assert "no_icon" in gap_kinds
    # low_messages may not appear if all low-message topics also have higher-priority gaps

    # Check dead topics
    assert len(data["dead_topics"]) >= 1
    assert 4 in data["dead_topics"]  # old topic
    assert 5 in data["dead_topics"]  # no date

    # Check topics array has message samples
    assert len(data["topics"]) == 5
    for topic in data["topics"]:
        assert "id" in topic
        assert "title" in topic
        assert "message_samples" in topic
        # Topic 1 should have samples
        if topic["id"] == 1:
            assert len(topic["message_samples"]) > 0


def test_analyze_group_invalid_mode(monkeypatch, mock_client_and_entity):
    """analyze_group rejects invalid mode."""
    client, entity = mock_client_and_entity
    _patch_analyze_group(monkeypatch, client, entity)

    result = _run(analyze_group(chat_id=-100123456, mode="invalid", inactivity_days=90))
    assert "Invalid mode" in result


def test_analyze_group_invalid_inactivity_days(monkeypatch, mock_client_and_entity):
    """analyze_group rejects inactivity_days <= 0."""
    client, entity = mock_client_and_entity
    _patch_analyze_group(monkeypatch, client, entity)

    result = _run(analyze_group(chat_id=-100123456, mode="summary", inactivity_days=0))
    assert "inactivity_days must be > 0" in result

    result = _run(analyze_group(chat_id=-100123456, mode="summary", inactivity_days=-1))
    assert "inactivity_days must be > 0" in result


def test_analyze_group_non_supergroup(monkeypatch):
    """analyze_group rejects non-supergroup chats."""
    import telegram_mcp.tools.groups as groups_mod

    def fake_get_client(account=""):
        return FakeClient()

    async def fake_resolve(chat_id, cl):
        return SimpleNamespace(megagroup=False, forum=False)

    monkeypatch.setattr(groups_mod, "get_client", fake_get_client)
    monkeypatch.setattr(groups_mod, "resolve_entity", fake_resolve)

    result = _run(analyze_group(chat_id=-100123456, mode="summary", inactivity_days=90))
    assert "not a supergroup" in result


def test_analyze_group_non_forum_supergroup(monkeypatch):
    """analyze_group rejects supergroup without forum enabled."""
    import telegram_mcp.tools.groups as groups_mod

    def fake_get_client(account=""):
        return FakeClient()

    async def fake_resolve(chat_id, cl):
        return SimpleNamespace(megagroup=True, forum=False)

    monkeypatch.setattr(groups_mod, "get_client", fake_get_client)
    monkeypatch.setattr(groups_mod, "resolve_entity", fake_resolve)

    result = _run(analyze_group(chat_id=-100123456, mode="summary", inactivity_days=90))
    assert "forum topics enabled" in result


def test_analyze_group_empty_topics(monkeypatch):
    """analyze_group handles groups with 0 topics gracefully."""
    import telegram_mcp.tools.groups as groups_mod

    def fake_get_client(account=""):
        return AnalyzeGroupFakeClient(forum_topics=[], topic_messages={})

    async def fake_resolve(chat_id, cl):
        return make_entity(-100123456)

    monkeypatch.setattr(groups_mod, "get_client", fake_get_client)
    monkeypatch.setattr(groups_mod, "resolve_entity", fake_resolve)

    result = _run(analyze_group(chat_id=-100123456, mode="summary", inactivity_days=90))
    data = json.loads(result)
    assert data["summary_stats"]["total_topics"] == 0
    assert data["summary_stats"]["total_messages"] == 0
    assert data["findings"]["duplicate_topic_groups"] == 0
    assert data["findings"]["topics_with_gaps"] == 0
    assert data["findings"]["dead_topics"] == 0


def test_analyze_group_message_sampling(monkeypatch, mock_client_and_entity):
    """analyze_group detail mode includes up to 5 message samples per topic."""
    client, entity = mock_client_and_entity
    _patch_analyze_group(monkeypatch, client, entity)

    result = _run(analyze_group(chat_id=-100123456, mode="detail", inactivity_days=90))
    data = json.loads(result)

    # Topic 1 has 3 messages, should get up to 5 samples
    topic1 = next(t for t in data["topics"] if t["id"] == 1)
    assert len(topic1["message_samples"]) == 3
    assert topic1["message_samples"][0]["id"] == 10
    assert "First post" in topic1["message_samples"][0]["text"]

    # Topic 5 has 0 messages
    topic5 = next(t for t in data["topics"] if t["id"] == 5)
    assert topic5["message_samples"] == []


def test_analyze_group_pagination(monkeypatch):
    """analyze_group fetches all topics via pagination (limit=100)."""
    import telegram_mcp.tools.groups as groups_mod

    # Create 150 topics to test pagination
    topics = [make_forum_topic(i, f"Topic {i}", total_messages=i) for i in range(1, 151)]

    def fake_get_client(account=""):
        return AnalyzeGroupFakeClient(forum_topics=topics, topic_messages={})

    async def fake_resolve(chat_id, cl):
        return make_entity(-100123456)

    monkeypatch.setattr(groups_mod, "get_client", fake_get_client)
    monkeypatch.setattr(groups_mod, "resolve_entity", fake_resolve)

    result = _run(analyze_group(chat_id=-100123456, mode="summary", inactivity_days=90))
    data = json.loads(result)
    assert data["summary_stats"]["total_topics"] == 150
