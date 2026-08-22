"""Diagnostics MCP tools.

Local-only health reporting: no Telegram API calls are made, so this works
even when disconnected and is safe to expose in read-only deployments.
"""

import os
import shutil
import time

from telegram_mcp.db_setup import get_db_path
from telegram_mcp.job_store import JobStore
from telegram_mcp.runtime import *
from telegram_mcp.runtime import _last_conn_verified  # noqa: F401  (dict ref)

_JOB_BASE_DIR_ENV = "TELEGRAM_JOBS_DIR"


def _session_file_info(session) -> dict:
    """Describe a Telethon session (string or file) without leaking values."""
    session_file = getattr(session, "session_file", None)
    info = {
        "session_type": "string" if isinstance(session, StringSession) else "file",
        "session_file": session_file if isinstance(session_file, str) else None,
        "session_file_mode": None,
        "session_file_readable_by_others": None,
    }
    if isinstance(session_file, str) and os.path.exists(session_file):
        try:
            mode = os.stat(session_file).st_mode & 0o777
        except OSError:
            return info
        info["session_file_mode"] = format(mode, "03o")
        info["session_file_readable_by_others"] = bool(mode & 0o007)
    return info


@mcp.tool(
    annotations=ToolAnnotations(
        title="Health Check",
        readOnlyHint=True,
        openWorldHint=False,
        idempotentHint=True,
    )
)
async def telegram_health_check() -> str:
    """Check the health of this Telegram MCP server (local-only, no API calls).

    Reports: configured accounts and their session configuration
    (including file permissions), how recently each account's connection was
    verified, active/persisted migration jobs, and disk space for the media
    database and allowed file roots. Use this to self-diagnose before and
    after operations.
    """
    now = time.time()

    accounts = []
    for label, cl in clients.items():
        last_verified = _last_conn_verified.get(id(cl))
        accounts.append(
            {
                "label": label,
                **_session_file_info(getattr(cl, "session", None)),
                "seconds_since_last_connection_verified": (
                    round(now - last_verified, 1) if last_verified is not None else None
                ),
            }
        )

    # Disk space for the places the server writes to.
    disk = {}
    db_dir = str(Path(get_db_path()).parent)
    try:
        usage = shutil.disk_usage(db_dir)
        disk["media_db_dir"] = {
            "path": db_dir,
            "free_gb": round(usage.free / 1024**3, 2),
            "total_gb": round(usage.total / 1024**3, 2),
        }
    except OSError:
        disk["media_db_dir"] = None
    for root in SERVER_ALLOWED_ROOTS:
        try:
            usage = shutil.disk_usage(str(root))
            disk[f"root:{root}"] = {
                "path": str(root),
                "free_gb": round(usage.free / 1024**3, 2),
                "total_gb": round(usage.total / 1024**3, 2),
            }
        except OSError:
            disk[f"root:{root}"] = None

    try:
        jobs = sorted(JobStore().list_jobs())
    except Exception:
        jobs = []

    payload = {
        "server": {
            "multi_account_mode": is_multi_mode(),
            "accounts_configured": len(clients),
            "allowed_roots_configured": len(SERVER_ALLOWED_ROOTS),
            "audit_log_enabled": bool(os.getenv("TELEGRAM_AUDIT_LOG")),
        },
        "accounts": accounts,
        "disk": disk,
        "migration_jobs": jobs,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


__all__ = ["telegram_health_check"]
