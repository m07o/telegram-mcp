"""Tests for the count_topics MCP tool — returns true topic count via pagination."""

from __future__ import annotations

import json
from typing import Any

import pytest
from tests.fakes.telethon_client import FakeTopic


class FakeTopicsResult:
    def __init__(self, topics: list[FakeTopic]) -> None:
        self.topics = topics


class FakeCountClient:
    """Returns scripted pages of topics so we can verify pagination."""

    def __init__(self, pages: list[list[FakeTopic]]) -> None:
        self.pages = pages
        self.call_count = 0

    async def __call__(self, request: Any) -> FakeTopicsResult:
        if self.call_count >= len(self.pages):
            return FakeTopicsResult(topics=[])
        page = self.pages[self.call_count]
        self.call_count += 1
        return FakeTopicsResult(topics=page)


@pytest.mark.asyncio
async def test_count_topics_returns_true_total_across_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED: count_topics must paginate past 100-topic limit and return the true total."""
    import asyncio
    from telegram_mcp.tools.chats import count_topics
    from types import SimpleNamespace

    pages = [
        [FakeTopic(i, f"t{i}") for i in range(0, 100)],
        [FakeTopic(i, f"t{i}") for i in range(100, 200)],
        [FakeTopic(i, f"t{i}") for i in range(200, 250)],
        [],
    ]
    fake_client = FakeCountClient(pages)

    valid_entity = SimpleNamespace(id=1, title="Test", megagroup=True, forum=True)

    async def fake_resolve(_chat_id: object, _client: object) -> object:
        return valid_entity

    def fake_get_client(_account: object) -> FakeCountClient:
        return fake_client

    monkeypatch.setattr("telegram_mcp.tools.chats.resolve_entity", fake_resolve)
    monkeypatch.setattr("telegram_mcp.tools.chats.get_client", fake_get_client)

    result = await count_topics(100)
    data = json.loads(result)
    # format_tool_result wraps in {"results": [...]} (or returns a list directly)
    if isinstance(data, dict) and "results" in data:
        record = data["results"][0]
    elif isinstance(data, list):
        record = data[0]
    else:
        record = data
    assert record["count"] == 250, f"expected 250 across pages, got {record['count']}"


@pytest.mark.asyncio
async def test_count_topics_rejects_non_forum_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-forum-enabled chat returns a clear error, not a list."""
    from telegram_mcp.tools.chats import count_topics
    from types import SimpleNamespace

    non_forum = SimpleNamespace(id=1, title="X", megagroup=True, forum=False)

    async def fake_resolve(_chat_id: object, _client: object) -> object:
        return non_forum

    def fake_get_client(_account: object) -> object:
        return FakeCountClient([])

    monkeypatch.setattr("telegram_mcp.tools.chats.resolve_entity", fake_resolve)
    monkeypatch.setattr("telegram_mcp.tools.chats.get_client", fake_get_client)

    result = await count_topics(100)
    assert "forum" in result.lower() or "supergroup" in result.lower()
