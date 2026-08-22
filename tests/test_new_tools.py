"""Tests for export_analyze_group_excel and get_ref_map MCP tools."""

from __future__ import annotations

import asyncio
import json
import tempfile
import os
from pathlib import Path
from typing import Any
from types import SimpleNamespace

import pytest

from telegram_mcp.tools.groups import export_analyze_group_excel, get_ref_map
from telegram_mcp.ref_map import RefMap
from tests.fakes.telethon_client import FakeClient, FakeMessage, FakeUpdates


def make_forum_topic(topic_id: int, title: str, **kwargs) -> SimpleNamespace:
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
    return SimpleNamespace(topics=list(topics))


def make_entity(chat_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=chat_id,
        title=f"Test Group {chat_id}",
        megagroup=True,
        forum=True,
        access_hash=123456,
    )


class AnalyzeGroupFakeClient(FakeClient):
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
            limit = getattr(request, "limit", 100)
            offset_topic = getattr(request, "offset_topic", 0)

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

    async def iter_messages(self, entity: Any, reply_to: int | None = None, **kwargs: Any):
        msgs = list(self._topic_messages.get(reply_to, []))
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


def _patch_groups(monkeypatch, client, entity):
    import telegram_mcp.tools.groups as groups_mod

    def fake_get_client(account=""):
        return client

    async def fake_resolve(chat_id, cl):
        return entity

    monkeypatch.setattr(groups_mod, "get_client", fake_get_client)
    monkeypatch.setattr(groups_mod, "resolve_entity", fake_resolve)


# --- Tests for export_analyze_group_excel ---


@pytest.fixture
def excel_client_and_entity():
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

    import datetime as dt

    topic_messages = {
        1: [FakeMessage(id=10, message="First post", date=dt.datetime(2026, 1, 1, 0, 0, 0))],
        2: [FakeMessage(id=20, message="Bug found", date=dt.datetime(2026, 1, 2, 0, 0, 0))],
        3: [FakeMessage(id=30, message="Bug report 1", date=dt.datetime(2026, 1, 3, 0, 0, 0))],
        4: [FakeMessage(id=40, message="Feature request", date=dt.datetime(2025, 6, 1, 0, 0, 0))],
        5: [],
    }

    client = AnalyzeGroupFakeClient(forum_topics=topics, topic_messages=topic_messages)
    entity = make_entity(-100123456)
    return client, entity


def test_export_analyze_group_excel_creates_file(monkeypatch, excel_client_and_entity):
    """export_analyze_group_excel creates a valid .xlsx file."""
    client, entity = excel_client_and_entity
    _patch_groups(monkeypatch, client, entity)

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        output_path = f.name

    try:
        result = _run(
            export_analyze_group_excel(
                chat_id=-100123456,
                inactivity_days=90,
                output_path=output_path,
            )
        )
        data = json.loads(result)

        assert data["success"] is True
        assert data["output_path"] == output_path
        assert os.path.exists(output_path)

        # Verify it's a valid zip (xlsx)
        import zipfile

        with zipfile.ZipFile(output_path, "r") as zf:
            names = zf.namelist()
            assert "[Content_Types].xml" in names
            assert "xl/workbook.xml" in names
            assert "xl/worksheets/sheet1.xml" in names  # Summary
            assert "xl/worksheets/sheet2.xml" in names  # Topics
            assert "xl/worksheets/sheet3.xml" in names  # Duplicates
            assert "xl/worksheets/sheet4.xml" in names  # Gaps
            assert "xl/worksheets/sheet5.xml" in names  # Dead Topics

    finally:
        if os.path.exists(output_path):
            os.unlink(output_path)


def test_export_analyze_group_excel_invalid_mode(monkeypatch, excel_client_and_entity):
    """export_analyze_group_excel validates inactivity_days."""
    client, entity = excel_client_and_entity
    _patch_groups(monkeypatch, client, entity)

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        output_path = f.name

    try:
        result = _run(
            export_analyze_group_excel(
                chat_id=-100123456,
                inactivity_days=0,
                output_path=output_path,
            )
        )
        assert "inactivity_days must be > 0" in result
    finally:
        if os.path.exists(output_path):
            os.unlink(output_path)


def test_export_analyze_group_excel_non_forum(monkeypatch):
    """export_analyze_group_excel rejects non-forum supergroup."""
    import telegram_mcp.tools.groups as groups_mod

    def fake_get_client(account=""):
        return FakeClient()

    async def fake_resolve(chat_id, cl):
        return SimpleNamespace(megagroup=True, forum=False)

    monkeypatch.setattr(groups_mod, "get_client", fake_get_client)
    monkeypatch.setattr(groups_mod, "resolve_entity", fake_resolve)

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        output_path = f.name

    try:
        result = _run(
            export_analyze_group_excel(
                chat_id=-100123456,
                inactivity_days=90,
                output_path=output_path,
            )
        )
        assert "forum topics enabled" in result
    finally:
        if os.path.exists(output_path):
            os.unlink(output_path)


# --- Tests for get_ref_map ---


def test_get_ref_map_list_jobs(tmp_path: Path, monkeypatch):
    """get_ref_map lists all jobs when no filters provided."""
    # Create a ref map with some jobs
    ref_dir = tmp_path / "refs"
    ref_map = RefMap(base_dir=ref_dir)
    ref_map.put("job_1", 100, 10, 200, 99)
    ref_map.put("job_2", 100, 11, 200, 100)

    # Patch at the import site
    monkeypatch.setattr("telegram_mcp.ref_map.RefMap", lambda *a, **kw: ref_map)

    result = _run(get_ref_map(job_id="job_1"))
    data = json.loads(result)
    assert "jobs" in data
    assert "job_1" in data["jobs"]
    assert "job_2" in data["jobs"]


def test_get_ref_map_get_by_source(tmp_path: Path, monkeypatch):
    """get_ref_map retrieves entry by source chat/message."""
    ref_dir = tmp_path / "refs"
    ref_map = RefMap(base_dir=ref_dir)
    ref_map.put("job_1", 100, 10, 200, 99, dest_topic_id=42)

    monkeypatch.setattr("telegram_mcp.ref_map.RefMap", lambda *a, **kw: ref_map)

    result = _run(get_ref_map(job_id="job_1", source_chat_id=100, source_msg_id=10))
    data = json.loads(result)
    # Returns entry directly when found
    assert data["source_chat_id"] == 100
    assert data["source_msg_id"] == 10
    assert data["dest_chat_id"] == 200
    assert data["dest_msg_id"] == 99
    assert data["dest_topic_id"] == 42


def test_get_ref_map_get_by_dest(tmp_path: Path, monkeypatch):
    """get_ref_map retrieves entry by destination chat/message."""
    ref_dir = tmp_path / "refs"
    ref_map = RefMap(base_dir=ref_dir)
    ref_map.put("job_1", 100, 10, 200, 99)

    monkeypatch.setattr("telegram_mcp.ref_map.RefMap", lambda *a, **kw: ref_map)

    result = _run(get_ref_map(job_id="job_1", dest_chat_id=200, dest_msg_id=99))
    data = json.loads(result)
    # Returns entry directly when found
    assert data["source_chat_id"] == 100
    assert data["source_msg_id"] == 10


def test_get_ref_map_stats(tmp_path: Path, monkeypatch):
    """get_ref_map returns stats when stats_only=True."""
    ref_dir = tmp_path / "refs"
    ref_map = RefMap(base_dir=ref_dir)
    ref_map.put("job_1", 100, 10, 200, 99, dest_topic_id=42)
    ref_map.put("job_1", 100, 11, 200, 100, dest_topic_id=42)

    monkeypatch.setattr("telegram_mcp.ref_map.RefMap", lambda *a, **kw: ref_map)

    result = _run(get_ref_map(job_id="job_1", stats_only=True))
    data = json.loads(result)
    assert data["count"] == 2
    assert 200 in data["dest_chats"]
    assert 42 in data["topics"]


def test_get_ref_map_list_all(tmp_path: Path, monkeypatch):
    """get_ref_map lists all entries when list_all=True."""
    ref_dir = tmp_path / "refs"
    ref_map = RefMap(base_dir=ref_dir)
    ref_map.put("job_1", 100, 10, 200, 99)
    ref_map.put("job_1", 100, 11, 200, 100)

    monkeypatch.setattr("telegram_mcp.ref_map.RefMap", lambda *a, **kw: ref_map)

    result = _run(get_ref_map(job_id="job_1", list_all=True))
    data = json.loads(result)
    assert data["count"] == 2
    assert len(data["entries"]) == 2


def test_get_ref_map_not_found(tmp_path: Path, monkeypatch):
    """get_ref_map returns found=false for missing entries."""
    ref_dir = tmp_path / "refs"
    ref_map = RefMap(base_dir=ref_dir)
    ref_map.put("job_1", 100, 10, 200, 99)

    monkeypatch.setattr("telegram_mcp.ref_map.RefMap", lambda *a, **kw: ref_map)

    result = _run(get_ref_map(job_id="job_1", source_chat_id=100, source_msg_id=999))
    data = json.loads(result)
    assert data["found"] is False
