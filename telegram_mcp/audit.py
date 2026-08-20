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
"""

import json
import logging
import os
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
