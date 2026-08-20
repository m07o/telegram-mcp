"""Tests for audit logging (telegram_mcp.audit + with_account integration)."""

import json

import pytest

from telegram_mcp import audit, runtime


def _read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_audit_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_AUDIT_LOG", raising=False)

    path = tmp_path / "audit.log"
    audit.record_audit(tool_name="some_tool", ok=True)

    assert audit.audit_enabled() is False
    assert not path.exists()


def test_audit_writes_jsonl(tmp_path, monkeypatch):
    path = tmp_path / "sub" / "audit.log"
    monkeypatch.setenv("TELEGRAM_AUDIT_LOG", str(path))

    audit.record_audit(tool_name="send_message", account="main", ok=True)
    audit.record_audit(tool_name="ban_user", account="main", ok=False, error="ValueError")

    lines = _read_lines(path)
    assert len(lines) == 2
    assert lines[0]["tool"] == "send_message"
    assert lines[0]["account"] == "main"
    assert lines[0]["ok"] is True
    assert "ts" in lines[0]
    assert "arg_names" not in lines[0]
    assert lines[1]["ok"] is False
    assert lines[1]["error"] == "ValueError"


def test_audit_arg_names_only_when_enabled(tmp_path, monkeypatch):
    path = tmp_path / "audit.log"
    monkeypatch.setenv("TELEGRAM_AUDIT_LOG", str(path))
    monkeypatch.setenv("TELEGRAM_AUDIT_LOG_ARGS", "1")

    audit.record_audit(tool_name="send_message", ok=True, arg_names=["message", "chat_id"])

    raw = path.read_text()
    data = json.loads(raw)
    # Names only — sorted and de-duplicated.
    assert data["arg_names"] == ["chat_id", "message"]
    # Argument values must never appear.
    assert "hello" not in raw


def test_audit_never_raises_on_io_failure(tmp_path, monkeypatch):
    blocker = tmp_path / "blocked"
    blocker.mkdir()  # a directory where a file is expected -> open("a") fails
    monkeypatch.setenv("TELEGRAM_AUDIT_LOG", str(blocker))

    audit.record_audit(tool_name="some_tool", ok=True)  # must not raise


def test_audit_error_truncated(tmp_path, monkeypatch):
    path = tmp_path / "audit.log"
    monkeypatch.setenv("TELEGRAM_AUDIT_LOG", str(path))

    audit.record_audit(tool_name="t", ok=False, error="X" * 500)

    data = _read_lines(path)[0]
    assert len(data["error"]) <= 200


@pytest.mark.asyncio
async def test_with_account_records_successful_call(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_AUDIT_LOG", str(tmp_path / "audit.log"))

    @runtime.with_account(readonly=False)
    async def fake_write(chat_id, account=None):
        return "done"

    assert await fake_write(chat_id=5) == "done"

    lines = _read_lines(tmp_path / "audit.log")
    assert lines[0]["tool"] == "fake_write"
    assert lines[0]["ok"] is True
    assert lines[0]["account"] is None


@pytest.mark.asyncio
async def test_with_account_records_failed_call(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_AUDIT_LOG", str(tmp_path / "audit.log"))

    @runtime.with_account(readonly=False)
    async def fake_fail(chat_id, account=None):
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await fake_fail(chat_id=5)

    lines = _read_lines(tmp_path / "audit.log")
    assert lines[0]["tool"] == "fake_fail"
    assert lines[0]["ok"] is False
    assert lines[0]["error"] == "ValueError"


@pytest.mark.asyncio
async def test_with_account_audit_does_not_mask_result(tmp_path, monkeypatch):
    # A broken audit path must not break the tool itself.
    blocker = tmp_path / "blocked"
    blocker.mkdir()
    monkeypatch.setenv("TELEGRAM_AUDIT_LOG", str(blocker))

    @runtime.with_account(readonly=True)
    async def fake_read(chat_id, account=None):
        return "value"

    assert await fake_read(chat_id=5) == "value"
