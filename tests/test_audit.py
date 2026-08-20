<<<<<<< HEAD
"""Tests for audit logging module."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from telegram_mcp import audit


def test_audit_disabled_by_default():
    """Test that audit is disabled when TELEGRAM_AUDIT_LOG is not set."""
    with patch.dict(os.environ, {}, clear=True):
        audit._INITIALIZED = False
        assert audit._is_enabled() is False


def test_audit_enabled_with_path():
    """Test that audit is enabled when TELEGRAM_AUDIT_LOG is set."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "audit.log"
        with patch.dict(os.environ, {"TELEGRAM_AUDIT_LOG": str(log_path)}, clear=True):
            audit._INITIALIZED = False
            assert audit._is_enabled() is True
            assert audit._AUDIT_LOG_PATH == log_path


def test_audit_creates_parent_directory():
    """Test that audit creates parent directory if needed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "subdir" / "audit.log"
        with patch.dict(os.environ, {"TELEGRAM_AUDIT_LOG": str(log_path)}, clear=True):
            audit._INITIALIZED = False
            assert audit._is_enabled() is True
            assert log_path.parent.exists()


def test_audit_log_args_env():
    """Test TELEGRAM_AUDIT_LOG_ARGS parsing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "audit.log"
        with patch.dict(os.environ, {
            "TELEGRAM_AUDIT_LOG": str(log_path),
            "TELEGRAM_AUDIT_LOG_ARGS": "1"
        }, clear=True):
            audit._INITIALIZED = False
            audit._init_audit_config()
            assert audit._AUDIT_LOG_ARGS is True


def test_audit_log_all_env():
    """Test TELEGRAM_AUDIT_LOG_ALL parsing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "audit.log"
        with patch.dict(os.environ, {
            "TELEGRAM_AUDIT_LOG": str(log_path),
            "TELEGRAM_AUDIT_LOG_ALL": "true"
        }, clear=True):
            audit._INITIALIZED = False
            audit._init_audit_config()
            assert audit._AUDIT_LOG_ALL is True


def test_should_audit_readonly_excluded_by_default():
    """Test that read-only tools are excluded by default."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "audit.log"
        with patch.dict(os.environ, {"TELEGRAM_AUDIT_LOG": str(log_path)}, clear=True):
            audit._INITIALIZED = False
            # _should_audit(tier, is_readonly) - is_readonly means the TOOL is read-only
            assert audit._should_audit("read-only", True) is False  # read-only tool, excluded
            assert audit._should_audit("write", False) is True      # write tool, included
            assert audit._should_audit("admin", False) is True      # admin tool, included
            assert audit._should_audit("migration", False) is True  # migration tool, included


def test_should_audit_readonly_included_when_all():
    """Test that read-only tools are included when TELEGRAM_AUDIT_LOG_ALL=1."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "audit.log"
        with patch.dict(os.environ, {
            "TELEGRAM_AUDIT_LOG": str(log_path),
            "TELEGRAM_AUDIT_LOG_ALL": "1"
        }, clear=True):
            audit._INITIALIZED = False
            assert audit._should_audit("read-only", True) is True


def test_record_audit_writes_json_line():
    """Test that record_audit writes a JSON line to the file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "audit.log"
        with patch.dict(os.environ, {"TELEGRAM_AUDIT_LOG": str(log_path)}, clear=True):
            audit._INITIALIZED = False

            audit.record_audit(
                tool_name="send_message",
                account="default",
                tier="write",
                ok=True,
                error_category=None,
                extra={"chat_id": 123},
            )

            # Read and verify
            content = log_path.read_text(encoding="utf-8").strip()
            entry = json.loads(content)

            assert entry["tool"] == "send_message"
            assert entry["account"] == "default"
            assert entry["tier"] == "write"
            assert entry["ok"] is True
            assert "timestamp" in entry
            # extra fields are merged directly into entry
            assert entry["chat_id"] == 123


def test_record_audit_redacts_sensitive_args():
    """Test that sensitive arguments are redacted in args_summary."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "audit.log"
        with patch.dict(os.environ, {
            "TELEGRAM_AUDIT_LOG": str(log_path),
            "TELEGRAM_AUDIT_LOG_ARGS": "1"
        }, clear=True):
            audit._INITIALIZED = False

            # Use audit_tool_call which handles redaction internally
            audit.audit_tool_call(
                tool_name="send_message",
                account="default",
                tier="write",
                is_readonly=False,
                ok=True,
                error=None,
                kwargs={
                    "chat_id": 123,
                    "message": "Hello world",
                    "session_string": "secret123",
                    "api_id": 456,
                },
            )

            content = log_path.read_text(encoding="utf-8").strip()
            entry = json.loads(content)

            assert entry["args_summary"]["chat_id"] == 123
            assert entry["args_summary"]["message"] == "<str len=11>"
            assert entry["args_summary"]["session_string"] == "[REDACTED]"
            assert entry["args_summary"]["api_id"] == "[REDACTED]"


def test_record_audit_no_crash_on_io_error():
    """Test that audit doesn't crash on I/O error."""
    with patch.dict(os.environ, {"TELEGRAM_AUDIT_LOG": "/invalid/path/that/does/not/exist/audit.log"}, clear=True):
        audit._INITIALIZED = False
        # Should not raise
        audit.record_audit(
            tool_name="send_message",
            account="default",
            tier="write",
            ok=True,
        )


def test_audit_tool_call_convenience():
    """Test the audit_tool_call convenience function."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "audit.log"
        with patch.dict(os.environ, {
            "TELEGRAM_AUDIT_LOG": str(log_path),
            "TELEGRAM_AUDIT_LOG_ARGS": "1"
        }, clear=True):
            audit._INITIALIZED = False

            # Success case
            audit.audit_tool_call(
                tool_name="send_message",
                account="default",
                tier="write",
                is_readonly=False,
                ok=True,
                error=None,
                kwargs={"chat_id": 123, "message": "Hello"},
            )

            # Failure case
            audit.audit_tool_call(
                tool_name="send_message",
                account="default",
                tier="write",
                is_readonly=False,
                ok=False,
                error=Exception("CHAT-ERR-001: Failed"),
                kwargs={"chat_id": 123, "message": "Hello"},
            )

            content = log_path.read_text(encoding="utf-8").strip().split("\n")
            assert len(content) == 2

            entry1 = json.loads(content[0])
            assert entry1["ok"] is True

            entry2 = json.loads(content[1])
            assert entry2["ok"] is False
            assert "CHAT" in entry2["error_category"]


def test_redact_args_handles_various_types():
    """Test _redact_args handles various argument types."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "audit.log"
        with patch.dict(os.environ, {
            "TELEGRAM_AUDIT_LOG": str(log_path),
            "TELEGRAM_AUDIT_LOG_ARGS": "1"
        }, clear=True):
            audit._INITIALIZED = False

            result = audit._redact_args({
                "string_arg": "hello",
                "int_arg": 42,
                "list_arg": [1, 2, 3],
                "dict_arg": {"a": 1},
                "session_string": "secret",
            }, "test_tool")

            assert result["string_arg"] == "<str len=5>"
            assert result["int_arg"] == 42  # non-sensitive values preserved
            assert result["list_arg"] == "<list len=3>"
            assert result["dict_arg"] == "<dict len=1>"
            assert result["session_string"] == "[REDACTED]"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
=======
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
>>>>>>> origin/arena/01a01ce4-telegram-mcp
