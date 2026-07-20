"""Tests for telegram_mcp.tools.groups.delete_topic.

Note: Telegram does NOT have a single RPC to delete a forum topic.
The convention used by mobile clients is:
  1. Delete every message (service + user) currently in the topic.
  2. Hide the topic from the tab bar (so a new copy can replace it).
Our ``delete_topic`` MCP tool wraps that two-step convention.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from tests.fakes.telethon_client import FakeMessage, FakeUpdates


class _FakeChannel:
    def __init__(self, megagroup: bool = True, forum: bool = True) -> None:
        self.megagroup = megagroup
        self.forum = forum


class _FakeClient:
    def __init__(self, topic_messages: list[FakeMessage]) -> None:
        self.topic_messages = topic_messages
        self.calls: list[Any] = []

    async def __call__(self, request: Any) -> FakeUpdates:
        self.calls.append(request)
        return FakeUpdates()

    async def iter_messages(self, entity: Any, *args: Any, **kwargs: Any) -> Any:
        topic_id = kwargs.get("reply_to", 0)
        if topic_id == 7:  # the topic_id we use in tests
            for m in self.topic_messages:
                yield m


def _impl():
    from telegram_mcp.tools.groups import delete_topic

    return delete_topic


@pytest.mark.asyncio
async def test_delete_topic_rejects_non_forum_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-forum megagroup -> error before any RPC."""
    fake = _FakeClient([])

    async def fake_resolve(_chat_id: object, _client: object) -> object:
        return _FakeChannel(megagroup=True, forum=False)

    monkeypatch.setattr("telegram_mcp.tools.groups.resolve_entity", fake_resolve)
    monkeypatch.setattr("telegram_mcp.tools.groups.get_client", lambda *_: fake)

    result = await _impl()(100, 7)
    assert "forum" in result.lower()
    assert fake.calls == [], "should not have hit Telegram"


@pytest.mark.asyncio
async def test_delete_topic_rejects_non_supergroup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Small/non-supergroup -> clear error."""
    fake = _FakeClient([])

    async def fake_resolve(_chat_id: object, _client: object) -> object:
        return _FakeChannel(megagroup=False, forum=False)

    monkeypatch.setattr("telegram_mcp.tools.groups.resolve_entity", fake_resolve)
    monkeypatch.setattr("telegram_mcp.tools.groups.get_client", lambda *_: fake)

    result = await _impl()(100, 7)
    assert "supergroup" in result.lower()
    assert fake.calls == []


@pytest.mark.asyncio
async def test_delete_topic_empty_topic_sends_no_delete_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the topic has no messages, the tool only sends an EditForumTopic (hide)."""
    fake = _FakeClient([])

    async def fake_resolve(_chat_id: object, _client: object) -> object:
        return _FakeChannel(megagroup=True, forum=True)

    monkeypatch.setattr("telegram_mcp.tools.groups.resolve_entity", fake_resolve)
    monkeypatch.setattr("telegram_mcp.tools.groups.get_client", lambda *_: fake)

    result = await _impl()(100, 7)

    # Exactly one RPC: the EditForumTopic hide.
    assert len(fake.calls) == 1, f"expected just hide RPC, got {len(fake.calls)} calls"
    type_name = type(fake.calls[0]).__name__
    assert "EditForumTopic" in type_name, f"expected EditForumTopic, got {type_name}"
    assert getattr(fake.calls[0], "hidden", None) is True
    assert getattr(fake.calls[0], "topic_id", None) == 7

    parsed = json.loads(result)
    assert parsed["topic_id"] == 7
    assert parsed["messages_deleted"] == 0
    assert parsed["hidden"] is True


@pytest.mark.asyncio
async def test_delete_topic_with_messages_deletes_then_hides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: delete all messages, then hide the topic."""
    msgs = [
        FakeMessage(id=10, message="a"),
        FakeMessage(id=11, message="b"),
        FakeMessage(id=12, message="c", action="pin_added"),  # service, still counted
    ]
    fake = _FakeClient(msgs)

    async def fake_resolve(_chat_id: object, _client: object) -> object:
        return _FakeChannel(megagroup=True, forum=True)

    monkeypatch.setattr("telegram_mcp.tools.groups.resolve_entity", fake_resolve)
    monkeypatch.setattr("telegram_mcp.tools.groups.get_client", lambda *_: fake)

    result = await _impl()(100, 7)

    # Two RPC calls: DeleteMessagesRequest first, then EditForumTopic(hide).
    assert len(fake.calls) == 2, f"expected 2 RPC calls, got {len(fake.calls)}"

    from telethon.tl.functions.messages import DeleteMessagesRequest

    # First RPC must be DeleteMessagesRequest with all 3 message ids.
    del_req = fake.calls[0]
    assert isinstance(del_req, DeleteMessagesRequest)
    assert del_req.id == [10, 11, 12]
    assert del_req.revoke is True  # revoke so other clients don't see them

    # Second RPC must be EditForumTopic with hidden=True.
    edit_req = fake.calls[1]
    type_name = type(edit_req).__name__
    assert "EditForumTopic" in type_name
    assert getattr(edit_req, "hidden", None) is True
    assert getattr(edit_req, "topic_id", None) == 7

    parsed = json.loads(result)
    assert parsed["topic_id"] == 7
    assert parsed["messages_deleted"] == 3
    assert parsed["hidden"] is True
