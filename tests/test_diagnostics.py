"""Tests for the telegram_health_check diagnostics tool."""

import os

import json

import pytest

import main  # noqa: F401  (registers every tool on the shared server)
from telegram_mcp import runtime
from telegram_mcp.tools.diagnostics import (
    _session_file_info,
    telegram_health_check,
)


def test_health_check_registered_read_only():
    tools = {t.name: t for t in runtime.mcp._tool_manager.list_tools()}

    assert "telegram_health_check" in tools
    assert tools["telegram_health_check"].annotations.readOnlyHint is True


@pytest.mark.asyncio
async def test_health_check_payload_shape():
    payload = json.loads(await telegram_health_check())

    assert payload["server"]["accounts_configured"] >= 1
    assert "default" in {a["label"] for a in payload["accounts"]}
    for account in payload["accounts"]:
        assert account["session_type"] in {"string", "file"}
        assert "session_file_readable_by_others" in account
    assert "disk" in payload
    assert isinstance(payload["migration_jobs"], list)


def test_session_file_info_for_missing_file():
    class FakeSession:
        session_file = "/nonexistent/path/that/does/not/exist.session"

    info = _session_file_info(FakeSession())

    assert info["session_type"] == "file"
    assert info["session_file_mode"] is None
    assert info["session_file_readable_by_others"] is None


@pytest.mark.skipif(os.name == "nt",
                    reason="Windows chmod does not affect stat modes; POSIX-only check")
def test_session_file_info_detects_world_readable(tmp_path):
    session_path = tmp_path / "s.session"
    session_path.write_text("x")
    session_path.chmod(0o644)

    class FakeSession:
        session_file = str(session_path)

    info = _session_file_info(FakeSession())

    assert info["session_file_mode"] == "644"
    assert info["session_file_readable_by_others"] is True

    session_path.chmod(0o600)
    info = _session_file_info(FakeSession())
    assert info["session_file_readable_by_others"] is False
