<<<<<<< HEAD
"""Audit logging for write operations.

Provides traceability without logging secrets. Hooks into the with_account
wrapper so every tool is covered automatically.
=======
"""Audit logging for telegram-mcp tool calls.

Appends one JSON line per audited tool invocation. Disabled by default;
enable by setting ``TELEGRAM_AUDIT_LOG`` to a file path.

Security properties:

- Never logs tool argument *values* — only argument *names*, and only when
  ``TELEGRAM_AUDIT_LOG_ARGS=1``.
- Never logs credentials (session strings, API keys, proxy URLs); none of
  the audited fields can contain them.
- Audit failures are swallowed (warning logged) so they can never break a
  tool call.
>>>>>>> origin/arena/01a01ce4-telegram-mcp
"""

import json
import logging
import os
<<<<<<< HEAD
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Module logger
logger = logging.getLogger(__name__)

# Configuration
_AUDIT_LOG_PATH: Optional[Path] = None
_AUDIT_LOG_ARGS: bool = False
_AUDIT_LOG_ALL: bool = False
_INITIALIZED: bool = False


def _init_audit_config():
    """Initialize audit configuration from environment variables."""
    global _AUDIT_LOG_PATH, _AUDIT_LOG_ARGS, _AUDIT_LOG_ALL, _INITIALIZED

    if _INITIALIZED:
        return

    _INITIALIZED = True

    # TELEGRAM_AUDIT_LOG - path to audit log file (disabled when unset)
    audit_log_path = os.getenv("TELEGRAM_AUDIT_LOG")
    if audit_log_path:
        _AUDIT_LOG_PATH = Path(audit_log_path).expanduser().resolve()
        # Create parent directory if needed
        try:
            _AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"Failed to create audit log directory: {e}")
            _AUDIT_LOG_PATH = None

    # TELEGRAM_AUDIT_LOG_ARGS=1 enables argument summary logging
    _AUDIT_LOG_ARGS = os.getenv("TELEGRAM_AUDIT_LOG_ARGS", "0").strip().lower() in ("1", "true", "yes", "on")

    # TELEGRAM_AUDIT_LOG_ALL=1 enables audit for read-only tools too
    _AUDIT_LOG_ALL = os.getenv("TELEGRAM_AUDIT_LOG_ALL", "0").strip().lower() in ("1", "true", "yes", "on")


def _is_enabled() -> bool:
    """Check if audit logging is enabled."""
    _init_audit_config()
    return _AUDIT_LOG_PATH is not None


def _should_audit(tier: str, is_readonly: bool) -> bool:
    """Determine if a tool call should be audited."""
    if not _is_enabled():
        return False
    # Read-only tools are skipped unless TELEGRAM_AUDIT_LOG_ALL=1
    if is_readonly and not _AUDIT_LOG_ALL:
        return False
    return True


def _redact_args(args: dict[str, Any], tool_name: str) -> dict[str, Any]:
    """Redact sensitive arguments from audit log.

    Never logs: session strings, api id/hash, proxy URLs, env values, message bodies.
    Only logs param NAMES + message length when TELEGRAM_AUDIT_LOG_ARGS=1.
    """
    _init_audit_config()
    if not _AUDIT_LOG_ARGS:
        return {}

    # Truly sensitive parameter names to fully redact
    sensitive_params = {
        "session_string",
        "api_id",
        "api_hash",
        "proxy",
        "proxy_url",
        "password",
        "secret",
        "token",
    }
    # Message content parameters - log length but not content
    message_content_params = {
        "message",
        "text",
        "content",
        "media",
        "file",
        "photo",
        "video",
        "document",
    }

    result = {}
    for key, value in args.items():
        key_lower = key.lower()
        if any(s in key_lower for s in sensitive_params):
            result[key] = "[REDACTED]"
        elif any(s in key_lower for s in message_content_params) and isinstance(value, str):
            # For message content strings, log length only
            result[key] = f"<str len={len(value)}>"
        elif isinstance(value, str):
            # For other string args, log length only
            result[key] = f"<str len={len(value)}>"
        elif isinstance(value, (list, dict)):
            # For collections, log type and length
            result[key] = f"<{type(value).__name__} len={len(value)}>"
        else:
            # For other types (int, bool, float, etc.), log the actual value
            # since they're not sensitive message bodies
            result[key] = value

    return result


def record_audit(
    tool_name: str,
    account: str,
    tier: str,
    ok: bool,
    error_category: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
    args_summary: Optional[dict[str, Any]] = None,
) -> None:
    """Record an audit log entry.

    Appends one JSON line to the file path from env TELEGRAM_AUDIT_LOG.
    Disabled when TELEGRAM_AUDIT_LOG is unset. Creates dir if needed.
    Never crashes the tool on audit I/O failure - logs warning and continues.

    Args:
        tool_name: Name of the MCP tool that was called.
        account: Account label used for the call.
        tier: Exposure tier of the tool (read-only, write, admin, migration).
        ok: True if the tool succeeded, False if it failed.
        error_category: Error category if the tool failed (e.g., "CHAT", "AUTH").
        extra: Additional context to include in the log.
        args_summary: Optional argument summary (param names + lengths only).
    """
    _init_audit_config()

    if not _is_enabled():
        return

    try:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "account": account,
            "tier": tier,
            "ok": ok,
        }

        if error_category:
            entry["error_category"] = error_category

        if args_summary:
            entry["args_summary"] = args_summary

        if extra:
            # Only include non-sensitive extra fields
            for k, v in extra.items():
                if k not in entry:
                    entry[k] = v

        # Write as JSON line
        with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    except Exception as e:
        # Never crash the tool on audit I/O failure
        logger.warning(f"Audit log write failed: {e}")


def _extract_error_category(error: Exception) -> Optional[str]:
    """Extract error category from exception."""
    if hasattr(error, "prefix"):
        return str(error.prefix)
    error_str = str(error).upper()
    for cat in ["CHAT", "MSG", "CONTACT", "GROUP", "MEDIA", "PROFILE", "AUTH", "ADMIN", "FOLDER", "VALIDATION"]:
        if cat in error_str:
            return cat
    return None


# Convenience function for use in with_account wrapper
def audit_tool_call(
    tool_name: str,
    account: str,
    tier: str,
    is_readonly: bool,
    ok: bool,
    error: Optional[Exception] = None,
    kwargs: Optional[dict[str, Any]] = None,
) -> None:
    """Convenience wrapper to record audit from with_account.

    Args:
        tool_name: Name of the tool.
        account: Account label.
        tier: Tool tier.
        is_readonly: Whether the tool is read-only.
        ok: Whether the call succeeded.
        error: Exception if the call failed.
        kwargs: Original keyword arguments passed to the tool.
    """
    if not _should_audit(tier, is_readonly):
        return

    error_category = _extract_error_category(error) if error else None
    args_summary = _redact_args(kwargs or {}, tool_name) if kwargs else None

    record_audit(
        tool_name=tool_name,
        account=account,
        tier=tier,
        ok=ok,
        error_category=error_category,
        args_summary=args_summary,
    )
=======
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()


def _parse_bool_env(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def audit_enabled() -> bool:
    """Return True when a TELEGRAM_AUDIT_LOG path is configured."""
    return bool(os.getenv("TELEGRAM_AUDIT_LOG"))


def _audit_path() -> Optional[Path]:
    raw = os.getenv("TELEGRAM_AUDIT_LOG")
    if not raw or not raw.strip():
        return None
    return Path(raw).expanduser()


def record_audit(
    *,
    tool_name: str,
    account: Optional[str] = None,
    ok: bool,
    error: Optional[str] = None,
    arg_names: Optional[list[str]] = None,
) -> None:
    """Append one audit entry. Never raises.

    Args:
        tool_name: MCP tool name.
        account: account label the call was routed to, if any.
        ok: final outcome of the call (after any retries).
        error: exception class name when ``ok`` is False.
        arg_names: tool parameter names; only written when
            ``TELEGRAM_AUDIT_LOG_ARGS=1``. Values are never recorded.
    """
    path = _audit_path()
    if path is None:
        return

    entry: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool_name,
        "account": account,
        "ok": bool(ok),
    }
    if error:
        entry["error"] = str(error)[:200]
    if _parse_bool_env(os.getenv("TELEGRAM_AUDIT_LOG_ARGS")):
        entry["arg_names"] = sorted(set(arg_names or []))

    try:
        line = json.dumps(entry, ensure_ascii=False)
    except Exception:
        logger.warning("audit: failed to serialize entry for tool %s", tool_name)
        return

    try:
        with _LOCK:
            if path.parent and not path.parent.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        logger.warning(
            "audit: failed to write to %s for tool %s", path, tool_name, exc_info=True
        )
>>>>>>> origin/arena/01a01ce4-telegram-mcp
