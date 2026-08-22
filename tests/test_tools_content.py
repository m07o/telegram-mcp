"""Tests for the MCP tools in telegram_mcp.tools.content.

Drives the registered tool functions end-to-end through the FakeClient and
small monkeypatches of resolve_entity / get_client / JobStore. No Telegram
network. The FakeClient already exists in tests/fakes/telethon_client.py and
is reused.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from telegram_mcp.job_store import JobStore
from tests.fakes.telethon_client import FakeClient, FakeMessage


def _run(coro: Any) -> Any:
    """Run an awaitable on a fresh loop."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Custom FakeClient that can serve different messages for different chats
# ---------------------------------------------------------------------------


class ChatFakeClient(FakeClient):
    """FakeClient that routes iter_messages based on the entity passed."""

    def __init__(
        self,
        *,
        source_messages: list[Any] | None = None,
        target_messages: list[Any] | None = None,
    ) -> None:
        super().__init__(topic_messages={None: source_messages or []})
        self._source_messages = source_messages or []
        self._target_messages = target_messages or []
        self._source_entity_id = 100
        self._target_entity_id = 200

    def set_entities(self, source_id: int, target_id: int) -> None:
        self._source_entity_id = source_id
        self._target_entity_id = target_id

    async def iter_messages(
        self,
        entity: Any,
        reply_to: Any = None,
        limit: int | None = None,
        **kwargs: Any,
    ):
        # Check if this is the target entity
        eid = getattr(entity, "id", None)
        if eid == self._target_entity_id:
            msgs = self._target_messages
        else:
            msgs = self._source_messages

        # Apply reply_to filtering (topic) if needed
        if reply_to is not None:
            # For simplicity, we don't filter by topic in this fake
            # since test messages don't have topic structure
            pass

        # Apply limit
        if limit is not None:
            msgs = msgs[:limit]

        # Return newest-first (matching Telethon)
        for m in reversed(msgs):
            yield m


# ---------------------------------------------------------------------------
# Fixtures: monkeypatch the tool module's runtime dependencies
# ---------------------------------------------------------------------------


def _install_monkeypatches(
    monkeypatch: pytest.MonkeyPatch,
    fake: ChatFakeClient,
    src_entity: Any,
    tgt_entity: Any,
    tmp_path: Path,
) -> None:
    """Wire up resolve_entity, get_client, JobStore."""

    async def fake_resolve(cid: Any, _client: Any) -> Any:
        if cid == 100:
            return src_entity
        if cid == 200:
            return tgt_entity
        return cid

    def fake_get_client(_account: Any) -> Any:
        return fake

    monkeypatch.setattr("telegram_mcp.tools.content.resolve_entity", fake_resolve)
    monkeypatch.setattr("telegram_mcp.tools.content.get_client", fake_get_client)
    monkeypatch.setattr(
        "telegram_mcp.tools.content.JobStore",
        lambda *a, **k: JobStore(base_dir=tmp_path / "jobs"),
    )


# ---------------------------------------------------------------------------
# analyze_chat_content
# ---------------------------------------------------------------------------


def test_analyze_chat_content_returns_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """analyze_chat_content reads source messages and returns structured inventory."""
    src_entity = SimpleNamespace(id=100, title="Src Channel")
    tgt_entity = SimpleNamespace(id=200, title="Tgt Group", megagroup=True)
    fake = ChatFakeClient(
        source_messages=[
            FakeMessage(id=1, message="intro v1", media=SimpleNamespace(_tag="video")),
            FakeMessage(id=2, message="intro v1", media=SimpleNamespace(_tag="video")),
            FakeMessage(id=3, message="totally different", media=SimpleNamespace(_tag="photo")),
        ],
    )
    fake.set_entities(100, 200)
    _install_monkeypatches(monkeypatch, fake, src_entity, tgt_entity, tmp_path)

    from telegram_mcp.tools import content as content_tools

    out = _run(content_tools.analyze_chat_content(100))
    payload = json.loads(out)
    assert payload["chat_id"] == 100
    assert payload["total_messages"] == 3
    assert "groups" in payload
    assert payload["duplicate_clusters_count"] >= 1


# ---------------------------------------------------------------------------
# curate_content_to_group
# ---------------------------------------------------------------------------


def test_curate_content_to_group_reports_dupes_in_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When target already has a copy, result includes old + new for manual cleanup."""
    src_entity = SimpleNamespace(id=100, title="Src")
    tgt_entity = SimpleNamespace(id=200, title="Tgt", megagroup=True)
    fake = ChatFakeClient(
        source_messages=[
            FakeMessage(id=10, message="intro", media=SimpleNamespace(_tag="video")),
        ],
        target_messages=[
            FakeMessage(id=99, message="intro", media=SimpleNamespace(_tag="video")),
        ],
    )
    fake.set_entities(100, 200)
    _install_monkeypatches(monkeypatch, fake, src_entity, tgt_entity, tmp_path)

    from telegram_mcp.tools import content as content_tools

    out = _run(
        content_tools.curate_content_to_group(
            source_chat_id=100,
            target_chat_id=200,
            delay=0.0,
            job_id="job_curate_dup",
            force=False,
        )
    )
    payload = json.loads(out)
    assert payload["job_id"] == "job_curate_dup"
    assert payload["items_planned"] == 1
    assert payload["duplicates_count"] >= 1
    pair = payload["duplicates"][0]
    assert pair["source_id"] == 10
    assert pair["existing_id"] == 99
    assert pair["keep"] in ("source", "existing", "either")


def test_curate_uses_reply_to_when_topic_id_provided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """target_topic_id=42 -> each sent message is threaded with reply_to=42."""
    src_entity = SimpleNamespace(id=100)
    tgt_entity = SimpleNamespace(id=200, megagroup=True, forum=True)
    fake = ChatFakeClient(
        source_messages=[
            FakeMessage(id=1, message="hello", media=SimpleNamespace(_tag="photo")),
        ],
        target_messages=[],
    )
    fake.set_entities(100, 200)
    _install_monkeypatches(monkeypatch, fake, src_entity, tgt_entity, tmp_path)

    from telegram_mcp.tools import content as content_tools

    out = _run(
        content_tools.curate_content_to_group(
            source_chat_id=100,
            target_chat_id=200,
            target_topic_id=42,
            delay=0.0,
            job_id="job_topic_42",
        )
    )
    payload = json.loads(out)
    assert payload["target_topic_id"] == 42
    sent = list(fake.sent_files) + list(fake.sent_messages)
    assert sent, "expected at least one message to be sent"
    assert all(s.get("reply_to") == 42 for s in sent)


def test_curate_flat_when_no_topic_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """target_topic_id omitted -> sends flat (no reply_to threading)."""
    src_entity = SimpleNamespace(id=100)
    tgt_entity = SimpleNamespace(id=200, megagroup=True, forum=True)
    fake = ChatFakeClient(
        source_messages=[
            FakeMessage(id=1, message="hi", media=SimpleNamespace(_tag="photo")),
        ],
        target_messages=[],
    )
    fake.set_entities(100, 200)
    _install_monkeypatches(monkeypatch, fake, src_entity, tgt_entity, tmp_path)

    from telegram_mcp.tools import content as content_tools

    out = _run(
        content_tools.curate_content_to_group(
            source_chat_id=100,
            target_chat_id=200,
            delay=0.0,
            job_id="job_flat",
        )
    )
    payload = json.loads(out)
    assert payload.get("target_topic_id") is None
    sent = list(fake.sent_files) + list(fake.sent_messages)
    assert sent
    for s in sent:
        assert s.get("reply_to") in (None, 0)


def test_curate_resume_skips_already_handled_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resumable: pre-seeded JobStore entries must skip re-sending those items."""
    # Pre-seed so item 1 is already in the persisted progress.
    store = JobStore(base_dir=tmp_path / "jobs")
    progress = store.load_or_create("job_resume", from_chat_id="100", to_chat_id="200")
    store.mark_topic_complete(progress, topic_id="1", title="x", source_count=1, copied_count=1)
    store.save(progress)

    src_entity = SimpleNamespace(id=100)
    tgt_entity = SimpleNamespace(id=200, megagroup=True, forum=True)
    fake = ChatFakeClient(
        source_messages=[
            FakeMessage(id=1, message="hi", media=SimpleNamespace(_tag="photo")),
        ],
        target_messages=[],
    )
    fake.set_entities(100, 200)
    _install_monkeypatches(monkeypatch, fake, src_entity, tgt_entity, tmp_path)

    from telegram_mcp.tools import content as content_tools

    out = _run(
        content_tools.curate_content_to_group(
            source_chat_id=100,
            target_chat_id=200,
            delay=0.0,
            job_id="job_resume",
        )
    )
    payload = json.loads(out)
    assert payload["skipped"] >= 1
    assert payload["items_sent"] == 0
    assert not fake.sent_messages
    assert not fake.sent_files
