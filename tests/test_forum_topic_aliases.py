"""Tests for the close/reopen/hide/unhide_forum_topic aliases.

These are thin wrappers around edit_forum_topic. The tests stub the
edit_forum_topic implementation via monkeypatch and verify each alias
delegates correctly.
"""

from __future__ import annotations

from typing import Any

import pytest


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace edit_forum_topic with a recorder. Returns a dict used as a side channel."""
    captured: dict[str, Any] = {}

    async def fake_edit_forum_topic(
        chat_id: Any,
        topic_id: Any,
        *,
        title: Any = None,
        icon_emoji_id: Any = None,
        closed: Any = None,
        hidden: Any = None,
        account: Any = None,
    ) -> str:
        captured["chat_id"] = chat_id
        captured["topic_id"] = topic_id
        captured["title"] = title
        captured["icon_emoji_id"] = icon_emoji_id
        captured["closed"] = closed
        captured["hidden"] = hidden
        captured["account"] = account
        return "ok"

    monkeypatch.setattr(
        "telegram_mcp.tools.groups.edit_forum_topic", fake_edit_forum_topic
    )
    return captured


@pytest.mark.asyncio
async def test_close_forum_topic_delegates_correctly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """close_forum_topic calls edit_forum_topic with closed=True, all other fields None."""
    from telegram_mcp.tools.groups import close_forum_topic

    cap = _capture(monkeypatch)
    result = await close_forum_topic(chat_id=100, topic_id=42)

    assert result == "ok"
    assert cap == {
        "chat_id": 100,
        "topic_id": 42,
        "title": None,
        "icon_emoji_id": None,
        "closed": True,
        "hidden": None,
        "account": None,
    }


@pytest.mark.asyncio
async def test_reopen_forum_topic_delegates_correctly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reopen_forum_topic calls edit_forum_topic with closed=False."""
    from telegram_mcp.tools.groups import reopen_forum_topic

    cap = _capture(monkeypatch)
    result = await reopen_forum_topic(chat_id=100, topic_id=42)

    assert result == "ok"
    assert cap["closed"] is False
    assert cap["hidden"] is None
    assert cap["title"] is None


@pytest.mark.asyncio
async def test_hide_forum_topic_delegates_correctly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """hide_forum_topic calls edit_forum_topic with hidden=True."""
    from telegram_mcp.tools.groups import hide_forum_topic

    cap = _capture(monkeypatch)
    result = await hide_forum_topic(chat_id=100, topic_id=42)

    assert result == "ok"
    assert cap["hidden"] is True
    assert cap["closed"] is None
    assert cap["title"] is None


@pytest.mark.asyncio
async def test_unhide_forum_topic_delegates_correctly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """unhide_forum_topic calls edit_forum_topic with hidden=False."""
    from telegram_mcp.tools.groups import unhide_forum_topic

    cap = _capture(monkeypatch)
    result = await unhide_forum_topic(chat_id=100, topic_id=42)

    assert result == "ok"
    assert cap["hidden"] is False
    assert cap["closed"] is None


@pytest.mark.asyncio
async def test_close_forum_topic_passes_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """account parameter is forwarded to edit_forum_topic."""
    from telegram_mcp.tools.groups import close_forum_topic

    cap = _capture(monkeypatch)
    await close_forum_topic(chat_id=100, topic_id=42, account="acc1")
    assert cap["account"] == "acc1"
