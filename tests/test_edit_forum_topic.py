"""Tests for telegram_mcp.tools.groups.edit_forum_topic and the chat-id validation helper.

Strict TDD: every test below is RED until the implementation is written.
See the plan `docs/superpowers/plans/2026-07-19-forward-tool-critical-fixes.md`
for the broader context; edit_forum_topic is the first new tool added after
that plan.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from tests.fakes.telethon_client import FakeUpdates


# ----- fakes for the validation helper -----


class _FakeChannel:
    def __init__(
        self,
        megagroup: bool = True,
        forum: bool = True,
        title: str = "Group",
    ) -> None:
        self.megagroup = megagroup
        self.forum = forum
        self.title = title


class _FakeRequests:
    """Records Telethon RPC requests handed in via __call__."""

    def __init__(self) -> None:
        self.calls: list[Any] = []


# ----- helper import path -----


def _helper():
    """Lazy import so the test file fails RED clearly before implementation."""
    from telegram_mcp.tools.groups import _validate_topic_target

    return _validate_topic_target


def _impl():
    from telegram_mcp.tools.groups import edit_forum_topic

    return edit_forum_topic


# ----- helper tests -----


def test_validate_topic_target_accepts_forum_megagroup() -> None:
    """Forum-enabled supergroup is valid."""
    entity = _FakeChannel(megagroup=True, forum=True)
    err = _helper()(entity)
    assert err is None


def test_validate_topic_target_rejects_small_chat() -> None:
    """megagroup=False must be rejected with 'not a supergroup' message."""
    entity = _FakeChannel(megagroup=False, forum=False)
    err = _helper()(entity)
    assert err is not None
    assert "supergroup" in err.lower()


def test_validate_topic_target_rejects_non_forum_megagroup() -> None:
    """megagroup=True with forum=False must be rejected with 'forum not enabled'."""
    entity = _FakeChannel(megagroup=True, forum=False)
    err = _helper()(entity)
    assert err is not None
    assert "forum" in err.lower()


# ----- fake Telethon wiring for the orchestrator tests -----


class _FakeClient:
    def __init__(self) -> None:
        self.requests = _FakeRequests()
        self.responses: list[Any] = []
        self._idx = 0

    async def __call__(self, request: Any) -> Any:
        # Record the request so RED tests can inspect what was sent.
        self.requests.calls.append(request)
        if self.responses:
            return self.responses.pop(0)
        return FakeUpdates()  # default empty result for EditForumTopic


# ----- edit_forum_topic orchestrator tests (RED) -----


def _wire(monkeypatch: pytest.MonkeyPatch, fake_client: _FakeClient) -> None:
    """monkeypatch the groups module to return our fake client and fake entity."""

    async def fake_resolve(_chat_id: object, _client: object) -> object:
        return _FakeChannel(megagroup=True, forum=True)

    def fake_get_client(_account: object) -> _FakeClient:
        return fake_client

    monkeypatch.setattr("telegram_mcp.tools.groups.resolve_entity", fake_resolve)
    monkeypatch.setattr("telegram_mcp.tools.groups.get_client", fake_get_client)


@pytest.mark.asyncio
async def test_edit_topic_changes_only_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED: title-only edit must produce an EditForumTopicRequest with only the new title."""
    fake = _FakeClient()
    _wire(monkeypatch, fake)

    result = await _impl()(100, 1, title="New Title")
    # The fake records exactly one RPC call. Inspect what envelope it carried.
    assert len(fake.requests.calls) == 1, f"expected 1 RPC call, got {len(fake.requests.calls)}"
    req = fake.requests.calls[0]
    type_name = type(req).__name__
    assert "EditForumTopic" in type_name, f"wrong RPC type: {type_name}"
    assert getattr(req, "title", None) == "New Title"
    assert getattr(req, "closed", "sentinel") is None
    assert getattr(req, "hidden", "sentinel") is None
    assert getattr(req, "icon_emoji_id", "sentinel") is None
    assert result == "ok"


@pytest.mark.asyncio
async def test_edit_topic_closes_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED: closed=True emits EditForumTopicRequest with closed=True."""
    fake = _FakeClient()
    _wire(monkeypatch, fake)

    result = await _impl()(100, 1, closed=True)
    assert len(fake.requests.calls) == 1
    req = fake.requests.calls[0]
    assert getattr(req, "closed", None) is True
    assert getattr(req, "title", "sentinel") is None
    assert result == "ok"


@pytest.mark.asyncio
async def test_edit_topic_reopens_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED: closed=False opens a closed topic."""
    fake = _FakeClient()
    _wire(monkeypatch, fake)
    await _impl()(100, 1, closed=False)
    req = fake.requests.calls[0]
    assert getattr(req, "closed", None) is False
    assert getattr(req, "title", "sentinel") is None


@pytest.mark.asyncio
async def test_edit_topic_hides_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED: hidden=True emits EditForumTopicRequest with hidden=True."""
    fake = _FakeClient()
    _wire(monkeypatch, fake)
    await _impl()(100, 1, hidden=True)
    req = fake.requests.calls[0]
    assert getattr(req, "hidden", None) is True


@pytest.mark.asyncio
async def test_edit_topic_unhides_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED: hidden=False unhides the topic."""
    fake = _FakeClient()
    _wire(monkeypatch, fake)
    await _impl()(100, 1, hidden=False)
    req = fake.requests.calls[0]
    assert getattr(req, "hidden", None) is False


@pytest.mark.asyncio
async def test_edit_topic_changes_icon_emoji(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED: icon_emoji_id=123 sets icon_emoji_id on the request."""
    fake = _FakeClient()
    _wire(monkeypatch, fake)
    await _impl()(100, 1, icon_emoji_id=123)
    req = fake.requests.calls[0]
    assert getattr(req, "icon_emoji_id", None) == 123


@pytest.mark.asyncio
async def test_edit_topic_combines_title_and_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED: combined edit emits one call with both fields populated."""
    fake = _FakeClient()
    _wire(monkeypatch, fake)
    await _impl()(100, 1, title="New", closed=True)
    req = fake.requests.calls[0]
    assert getattr(req, "title", None) == "New"
    assert getattr(req, "closed", False) is True


# ----- failure tests -----


@pytest.mark.asyncio
async def test_edit_topic_rejects_non_supergroup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED: small chat -> clear error, NO RPC call."""
    fake = _FakeClient()

    async def fake_resolve(_chat_id: object, _client: object) -> object:
        return _FakeChannel(megagroup=False, forum=False)

    monkeypatch.setattr("telegram_mcp.tools.groups.resolve_entity", fake_resolve)
    monkeypatch.setattr("telegram_mcp.tools.groups.get_client", lambda *_: fake)

    result = await _impl()(100, 1, title="x")
    assert "supergroup" in result.lower()
    assert fake.requests.calls == [], "should not have hit Telegram"


@pytest.mark.asyncio
async def test_edit_topic_rejects_non_forum_megagroup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED: supergroup without forum -> 'forum not enabled' error, NO RPC call."""
    fake = _FakeClient()

    async def fake_resolve(_chat_id: object, _client: object) -> object:
        return _FakeChannel(megagroup=True, forum=False)

    monkeypatch.setattr("telegram_mcp.tools.groups.resolve_entity", fake_resolve)
    monkeypatch.setattr("telegram_mcp.tools.groups.get_client", lambda *_: fake)

    result = await _impl()(100, 1, title="x")
    assert "forum" in result.lower()
    assert fake.requests.calls == [], "should not have hit Telegram"
