"""Tests for ban_users_bulk and unban_users_bulk MCP tools.

Each ban/unban uses Telegram's ``EditBannedRequest`` which only accepts
a single participant at a time; the bulk variants iterate the user list
and send one request per user, returning per-user results.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from tests.fakes.telethon_client import FakeUpdates


@dataclass
class _RecordedCall:
    type_name: str
    channel: Any = None
    participant: Any = None
    banned_rights: Any = None


class _FakeChannel:
    def __init__(self, megagroup: bool = True) -> None:
        self.megagroup = megagroup
        self.title = "G"


class _FakeUser:
    def __init__(self, uid: int) -> None:
        self.id = uid
        self.first_name = f"u{uid}"


class _IterUsers:
    """Stand-in for client.iter_messages-style iter over result of resolve_entity.

    We monkey-patch resolve_entity to return lists of these directly.
    """

    def __init__(self, users: list[_FakeUser]) -> None:
        self.users = users

    def __aiter__(self) -> Any:
        async def _gen() -> Any:
            for u in self.users:
                yield u

        return _gen()


class _FakeClient:
    def __init__(self, channel: _FakeChannel, users: list[_FakeUser]) -> None:
        self.channel = channel
        self.users = users
        self.calls: list[_RecordedCall] = []

    async def __call__(self, request: Any) -> Any:
        # Record type/key fields based on request type.
        type_name = type(request).__name__
        call = _RecordedCall(type_name=type_name)
        if hasattr(request, "channel"):
            call.channel = getattr(request, "channel")
        if hasattr(request, "participant"):
            call.participant = getattr(request, "participant")
        if hasattr(request, "banned_rights"):
            call.banned_rights = getattr(request, "banned_rights")
        self.calls.append(call)
        return FakeUpdates()

    async def get_input_entity(self, e: Any) -> Any:
        return e


# ----- helpers -----


def _impl_ban():
    from telegram_mcp.tools.groups import ban_users_bulk

    return ban_users_bulk


def _impl_unban():
    from telegram_mcp.tools.groups import unban_users_bulk

    return unban_users_bulk


def _wire(
    monkeypatch: pytest.MonkeyPatch, channel: _FakeChannel, users: list[_FakeUser]
) -> _FakeClient:
    fake = _FakeClient(channel, users)

    async def fake_resolve(entity_id: Any, _client: Any) -> Any:
        # chat-id is the first resolve, then each user id.
        if isinstance(entity_id, str) and entity_id.startswith("user-"):
            return users[int(entity_id.split("-")[1])]
        if isinstance(entity_id, int) and entity_id < 100:
            return channel
        # resolve user_ids in the bulk list:
        if isinstance(entity_id, list):
            return _IterUsers(users)
        return users[0]

    monkeypatch.setattr("telegram_mcp.tools.groups.resolve_entity", fake_resolve)
    monkeypatch.setattr("telegram_mcp.tools.groups.get_client", lambda *_: fake)
    return fake


# ----- ban_users_bulk tests -----


@pytest.mark.asyncio
async def test_ban_users_bulk_sends_one_editbanned_per_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _wire(
        monkeypatch,
        _FakeChannel(),
        [_FakeUser(1001), _FakeUser(1002), _FakeUser(1003)],
    )

    result = await _impl_ban()(chat_id=1, user_ids=[1001, 1002, 1003])
    parsed = json.loads(result)
    assert parsed["banned"] == [1001, 1002, 1003]
    assert parsed["failed"] == []
    assert len(parsed["banned"]) == len(fake.calls)
    for c in fake.calls:
        assert c.type_name == "EditBannedRequest"


@pytest.mark.asyncio
async def test_ban_users_bulk_rejects_non_megagroup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _wire(
        monkeypatch,
        _FakeChannel(megagroup=False),
        [_FakeUser(1001)],
    )

    result = await _impl_ban()(chat_id=1, user_ids=[1001])
    assert "supergroup" in result.lower()
    assert fake.calls == [], "should not have hit Telegram"


@pytest.mark.asyncio
async def test_ban_users_bulk_partial_failure_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If one ban fails (exception), the tool should record the failure
    in the JSON output and continue with the rest of the users."""
    channel = _FakeChannel()
    users = [_FakeUser(2001), _FakeUser(2002), _FakeUser(2003)]

    call_count = {"n": 0}

    class BoomClient(_FakeClient):
        async def __call__(self, request: Any) -> Any:
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("simulated flood wait")
            return await super().__call__(request)

    fake = BoomClient(channel, users)

    async def fake_resolve(entity_id: Any, _client: Any) -> Any:
        if isinstance(entity_id, int) and entity_id == 1:
            return channel
        if isinstance(entity_id, list):
            return _IterUsers(users)
        return users[entity_id - 2001]

    monkeypatch.setattr("telegram_mcp.tools.groups.resolve_entity", fake_resolve)
    monkeypatch.setattr("telegram_mcp.tools.groups.get_client", lambda *_: fake)

    result = await _impl_ban()(chat_id=1, user_ids=[2001, 2002, 2003])
    parsed = json.loads(result)
    assert 2001 in parsed["banned"]
    assert 2003 in parsed["banned"]
    assert len(parsed["failed"]) == 1
    assert parsed["failed"][0]["id"] == 2002
    assert "flood wait" in parsed["failed"][0]["error"].lower()


# ----- unban_users_bulk tests -----


@pytest.mark.asyncio
async def test_unban_users_bulk_sends_unban_rights(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _wire(
        monkeypatch,
        _FakeChannel(),
        [_FakeUser(3001), _FakeUser(3002)],
    )
    result = await _impl_unban()(chat_id=1, user_ids=[3001, 3002])
    parsed = json.loads(result)
    assert parsed["unbanned"] == [3001, 3002]
    assert parsed["failed"] == []
    # Each call must carry the empty/unban rights pattern.
    from telethon.tl.types import ChatBannedRights

    for c in fake.calls:
        assert c.type_name == "EditBannedRequest"
        assert isinstance(c.banned_rights, ChatBannedRights)
        # All flags False (unbanned).
        for attr in (
            "view_messages",
            "send_messages",
            "send_media",
            "embed_links",
            "invite_users",
        ):
            assert getattr(c.banned_rights, attr, True) is False


@pytest.mark.asyncio
async def test_unban_users_bulk_rejects_non_megagroup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _wire(
        monkeypatch,
        _FakeChannel(megagroup=False),
        [_FakeUser(3001)],
    )
    result = await _impl_unban()(chat_id=1, user_ids=[3001])
    assert "supergroup" in result.lower()
    assert fake.calls == []
