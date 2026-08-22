import argparse
import asyncio
import os
import sys
import json
import random
import time
import zlib
import sqlite3
import logging
import mimetypes
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import List, Dict, Optional, Union, Any
from pathlib import Path
from urllib.parse import unquote, urlparse

# Third-party libraries
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP, Context
from mcp.types import Annotations, TextContent, ToolAnnotations
from mcp.shared.exceptions import McpError
from pythonjsonlogger.json import JsonFormatter
from telethon import TelegramClient, functions, types, utils
from telethon.sessions import StringSession
from telethon.tl.types import (
    User,
    Chat,
    Channel,
    ChatAdminRights,
    ChatBannedRights,
    ChannelParticipantsKicked,
    ChannelParticipantsAdmins,
    InputChatPhoto,
    InputChatUploadedPhoto,
    InputChatPhotoEmpty,
    InputPeerUser,
    InputPeerChat,
    InputPeerChannel,
    DialogFilter,
    DialogFilterChatlist,
    DialogFilterDefault,
    TextWithEntities,
)
import re
from functools import wraps
import telethon.errors.rpcerrorlist
from sanitize import sanitize_user_content, sanitize_name, sanitize_dict, format_tool_result
from telegram_mcp import audit
from telegram_mcp.client_identity import client_identity_kwargs


class ValidationError(Exception):
    """Custom exception for validation errors."""

    pass


def json_serializer(obj):
    """Helper function to convert non-serializable objects for JSON serialization."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    # Add other non-serializable types as needed
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def get_entity_type(entity: Any) -> str:
    """Return a normalized, human-readable chat/entity type."""
    if isinstance(entity, User):
        return "User"
    if isinstance(entity, Chat):
        return "Group (Basic)"
    if isinstance(entity, Channel):
        if getattr(entity, "megagroup", False):
            return "Supergroup"
        return "Channel" if getattr(entity, "broadcast", False) else "Group"
    return type(entity).__name__


def get_marked_id(entity: Any) -> int:
    """Return a Telethon-compatible marked ID for an entity."""
    if isinstance(entity, Channel):
        return -1000000000000 - entity.id
    if isinstance(entity, Chat):
        return -entity.id
    return entity.id


def get_entity_filter_type(entity: Any) -> Optional[str]:
    """Return list_chats-compatible filter type: user/group/channel."""
    entity_type = get_entity_type(entity)
    if entity_type == "User":
        return "user"
    if entity_type in ("Group (Basic)", "Group", "Supergroup"):
        return "group"
    if entity_type == "Channel":
        return "channel"
    return None


try:
    load_dotenv()
except Exception as e:
    print(f"WARNING: Failed to load .env file: {e}", file=sys.stderr)

def _require_env_int(name: str) -> int:
    """Read a required integer env var with a friendly failure message."""
    raw = os.getenv(name)
    if not raw or not raw.strip():
        raise SystemExit(
            f"Missing required environment variable {name}. "
            "Get TELEGRAM_API_ID and TELEGRAM_API_HASH from "
            "https://my.telegram.org/apps and set them in your environment or .env."
        )
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(
            f"Environment variable {name} must be an integer, got {raw!r}."
        ) from None


TELEGRAM_API_ID = _require_env_int("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
if not TELEGRAM_API_HASH or not TELEGRAM_API_HASH.strip():
    raise SystemExit(
        "Missing required environment variable TELEGRAM_API_HASH. "
        "Get it from https://my.telegram.org/apps and set it in your "
        "environment or .env."
    )

mcp = FastMCP("telegram")

# Annotate all tool results with audience=["user"] so MCP clients know
# the content is user-generated data, not instructions for the model.
# We wrap the low-level request handler (after FastMCP registers it) to inject
# annotations into the final CallToolResult, preserving structured output.
_USER_AUDIENCE = Annotations(audience=["user"])


_RESULT_WARN_CHARS = 150_000
_RESULT_TRUNCATION_MARKER = "\n…[truncated by TELEGRAM_MAX_RESULT_CHARS]"


def _get_max_result_chars() -> Optional[int]:
    """Optional hard cap on tool result size (``TELEGRAM_MAX_RESULT_CHARS``)."""
    raw = os.getenv("TELEGRAM_MAX_RESULT_CHARS")
    if not raw or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid TELEGRAM_MAX_RESULT_CHARS %r; ignoring it", raw)
        return None
    return value if value > 0 else None


def _apply_result_size_policy(content: list, tool_name: str = "unknown") -> list:
    """Warn on oversized results; truncate when TELEGRAM_MAX_RESULT_CHARS is set.

    By default the result is left intact (so structured JSON stays valid) and
    only a warning is logged. Setting ``TELEGRAM_MAX_RESULT_CHARS`` truncates
    every TextContent block above the cap and appends a visible marker.
    """
    total_chars = sum(len(b.text) for b in content if isinstance(b, TextContent))
    if total_chars > _RESULT_WARN_CHARS:
        logger.warning(
            "Tool %s returned a large result (%d chars; warn threshold %d); "
            "consider pagination or a result limit",
            tool_name,
            total_chars,
            _RESULT_WARN_CHARS,
        )
    cap = _get_max_result_chars()
    if not cap:
        return content
    truncated = []
    for block in content:
        if isinstance(block, TextContent) and len(block.text) > cap:
            truncated.append(
                block.model_copy(
                    update={"text": block.text[:cap] + _RESULT_TRUNCATION_MARKER}
                )
            )
        else:
            truncated.append(block)
    return truncated


def _install_annotation_hook() -> None:
    from mcp.types import CallToolRequest, ServerResult, CallToolResult

    original_handler = mcp._mcp_server.request_handlers[CallToolRequest]

    async def annotated_handler(req):
        response = await original_handler(req)
        if isinstance(response, ServerResult) and isinstance(response.root, CallToolResult):
            content = response.root.content
            if content:
                annotated = [
                    (
                        block.model_copy(update={"annotations": _USER_AUDIENCE})
                        if isinstance(block, TextContent) and block.annotations is None
                        else block
                    )
                    for block in content
                ]
                tool_name = getattr(req.params, "name", None) or "unknown"
                response.root.content = _apply_result_size_policy(annotated, tool_name)
        return response

    mcp._mcp_server.request_handlers[CallToolRequest] = annotated_handler


_install_annotation_hook()


_EXPOSED_TOOLS_MODES = {"all", "read-only", "write", "admin", "migration"}

# Admin-tier tools: group administration operations with elevated impact.
# Kept as an explicit, auditable list (not annotation-based) so the exact
# tools a "admin" deployment can call are always visible here.
ADMIN_TOOLS: frozenset = frozenset(
    {
        "promote_admin",
        "demote_admin",
        "edit_admin_rights",
        "ban_user",
        "unban_user",
        "ban_users_bulk",
        "unban_users_bulk",
        "set_default_chat_permissions",
        "toggle_slow_mode",
    }
)


def _get_exposed_tools_mode(value: Optional[str] = None) -> list[str]:
    """Return the configured MCP tool exposure tiers, validated.

    ``TELEGRAM_EXPOSED_TOOLS`` accepts a single tier or a comma-separated
    list, e.g. ``read-only,write``. Tiers:

    - ``all``: every registered tool (default, backward compatible).
    - ``read-only``: tools annotated ``readOnlyHint=True``.
    - ``write``: all other non-read-only tools.
    - ``admin``: elevated group-administration tools (see ``ADMIN_TOOLS``).
    - ``migration``: tools defined in ``telegram_mcp.tools.migration``.

    Multiple tiers are combined by union. Unknown values fail fast at
    startup with ``SystemExit``.
    """
    raw_value = os.getenv("TELEGRAM_EXPOSED_TOOLS", "all") if value is None else value
    tiers = [part.strip().lower() for part in raw_value.split(",") if part.strip()]
    if not tiers:
        tiers = ["all"]
    invalid = [tier for tier in tiers if tier not in _EXPOSED_TOOLS_MODES]
    if invalid:
        accepted = ", ".join(sorted(_EXPOSED_TOOLS_MODES))
        raise SystemExit(
            f"Invalid TELEGRAM_EXPOSED_TOOLS '{raw_value}'. "
            f"Expected one of (or a comma-separated list of): {accepted}."
        )
    # De-duplicate while preserving order.
    seen = set()
    ordered = []
    for tier in tiers:
        if tier not in seen:
            seen.add(tier)
            ordered.append(tier)
    return ordered


def _tool_tier(tool) -> str:
    """Classify a registered tool into exactly one exposure tier.

    Classification is least-privilege first: a read-only tool is always
    ``read-only`` even if defined in the migration module, and an admin
    tool is never ``write``.
    """
    annotations = getattr(tool, "annotations", None)
    if getattr(annotations, "readOnlyHint", False):
        return "read-only"
    if tool.name in ADMIN_TOOLS:
        return "admin"
    fn = getattr(tool, "fn", None)
    module = getattr(fn, "__module__", "") or ""
    if module == "telegram_mcp.tools.migration":
        return "migration"
    return "write"


def _apply_exposed_tools_mode(server: FastMCP = mcp, mode: Optional[str] = None) -> list[str]:
    """Prune registered MCP tools according to the configured exposure tiers.

    A tool is kept when its tier (see ``_tool_tier``) is among the selected
    tiers, or when ``all`` is selected. Returns the names of removed tools.
    """
    selected_tiers = (
        _get_exposed_tools_mode() if mode is None else _get_exposed_tools_mode(mode)
    )
    if "all" in selected_tiers:
        return []

    allowed = set(selected_tiers)
    removed: list[str] = []
    for tool in list(server._tool_manager.list_tools()):
        if _tool_tier(tool) not in allowed:
            server._tool_manager.remove_tool(tool.name)
            removed.append(tool.name)
    return removed


# ---------------------------------------------------------------------------
# Multi-account configuration
# ---------------------------------------------------------------------------


_PROXY_TYPES_SOCKS_HTTP = {"socks5", "socks4", "http"}
_PROXY_TYPES_ALL = _PROXY_TYPES_SOCKS_HTTP | {"mtproxy"}


def _get_proxy_env(name: str, label: str) -> Optional[str]:
    """Resolve a TELEGRAM_PROXY_* env var with optional ``_<LABEL>`` suffix.

    Per-account values override the unsuffixed defaults so a global proxy can
    coexist with per-label overrides.
    """
    suffixed = os.getenv(f"TELEGRAM_PROXY_{name}_{label.upper()}")
    if suffixed:
        return suffixed
    return os.getenv(f"TELEGRAM_PROXY_{name}") or None


def _parse_bool_env(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    v = value.strip().lower()
    if v in {"1", "true", "yes", "on"}:
        return True
    if v in {"0", "false", "no", "off"}:
        return False
    return default


def _build_proxy_for_label(label: str) -> tuple[Optional[Any], Optional[Any]]:
    """Return ``(proxy, connection)`` kwargs for ``TelegramClient`` for a label.

    Reads ``TELEGRAM_PROXY_*`` env vars (with optional ``_<LABEL>`` suffix).
    Returns ``(None, None)`` when no proxy is configured. Raises
    :class:`ValidationError` for malformed configuration so the server fails
    fast instead of silently bypassing the proxy.
    """
    proxy_type = _get_proxy_env("TYPE", label)
    if not proxy_type:
        return None, None

    proxy_type = proxy_type.strip().lower()
    if proxy_type not in _PROXY_TYPES_ALL:
        raise ValidationError(
            f"Invalid TELEGRAM_PROXY_TYPE '{proxy_type}'. "
            f"Expected one of: {', '.join(sorted(_PROXY_TYPES_ALL))}."
        )

    host = _get_proxy_env("HOST", label)
    port_raw = _get_proxy_env("PORT", label)
    if not host or not port_raw:
        raise ValidationError(
            "TELEGRAM_PROXY_HOST and TELEGRAM_PROXY_PORT are required when "
            "TELEGRAM_PROXY_TYPE is set."
        )
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ValidationError(
            f"TELEGRAM_PROXY_PORT must be an integer, got '{port_raw}'."
        ) from exc

    if proxy_type == "mtproxy":
        secret = _get_proxy_env("SECRET", label)
        if not secret:
            raise ValidationError("TELEGRAM_PROXY_SECRET is required for mtproxy.")
        try:
            from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate
        except ImportError as exc:  # pragma: no cover - defensive guard
            raise ValidationError(
                "Telethon MTProxy connection class is unavailable; upgrade telethon."
            ) from exc
        return (host, port, secret), ConnectionTcpMTProxyRandomizedIntermediate

    # SOCKS4/SOCKS5/HTTP via python-socks (Telethon's optional dependency).
    try:
        import python_socks  # noqa: F401
    except ImportError as exc:
        raise ValidationError(
            f"Proxy type '{proxy_type}' requires the 'python-socks' package. "
            "Install it with `pip install python-socks` or `uv sync --extra proxy`."
        ) from exc

    proxy: dict[str, Any] = {
        "proxy_type": proxy_type,
        "addr": host,
        "port": port,
        "rdns": _parse_bool_env(_get_proxy_env("RDNS", label), default=True),
    }
    username = _get_proxy_env("USERNAME", label)
    password = _get_proxy_env("PASSWORD", label)
    if username:
        proxy["username"] = username
    if password:
        proxy["password"] = password
    return proxy, None


def _build_client(session: Any, label: str) -> TelegramClient:
    """Construct a ``TelegramClient`` honoring per-label proxy configuration."""
    proxy, connection = _build_proxy_for_label(label)
    kwargs: dict[str, Any] = {}
    if proxy is not None:
        kwargs["proxy"] = proxy
    if connection is not None:
        kwargs["connection"] = connection
    kwargs.update(client_identity_kwargs())
    return TelegramClient(session, TELEGRAM_API_ID, TELEGRAM_API_HASH, **kwargs)


def _discover_accounts() -> dict[str, TelegramClient]:
    """Scan env vars to build account label -> TelegramClient mapping.

    Detection rules:
    - TELEGRAM_SESSION_STRING_<LABEL> / TELEGRAM_SESSION_NAME_<LABEL> -> multi-mode
    - Unsuffixed TELEGRAM_SESSION_STRING / TELEGRAM_SESSION_NAME -> label "default"
    - If both suffixed and unsuffixed exist -> unsuffixed becomes "default"

    Each client is constructed via :func:`_build_client`, which applies any
    matching ``TELEGRAM_PROXY_*`` configuration (optionally per-label).
    """
    accounts: dict[str, TelegramClient] = {}

    prefix_str = "TELEGRAM_SESSION_STRING_"
    prefix_name = "TELEGRAM_SESSION_NAME_"

    for key, value in os.environ.items():
        if key.startswith(prefix_str) and value:
            label = key[len(prefix_str) :].lower()
            accounts[label] = _build_client(StringSession(value), label)
        elif key.startswith(prefix_name) and value:
            label = key[len(prefix_name) :].lower()
            accounts[label] = _build_client(value, label)

    # Backward-compatible unsuffixed variables
    session_string = os.getenv("TELEGRAM_SESSION_STRING")
    session_name = os.getenv("TELEGRAM_SESSION_NAME")

    if session_string and "default" not in accounts:
        accounts["default"] = _build_client(StringSession(session_string), "default")
    elif session_name and "default" not in accounts:
        accounts["default"] = _build_client(session_name, "default")

    if not accounts:
        print(
            "Error: No Telegram session configured. "
            "Set TELEGRAM_SESSION_STRING or TELEGRAM_SESSION_STRING_<LABEL> in .env",
            file=sys.stderr,
        )
        sys.exit(1)

    return accounts


def _warn_world_readable_sessions() -> None:
    """Warn at startup when a file-based session is readable by group/others.

    A session file is effectively the account's password; 0644 means anyone
    on the host can read it. Warning (not failing) so an operator can fix
    the permissions without losing access to a running deployment.
    """
    for label, cl in clients.items():
        session = getattr(cl, "session", None)
        session_file = getattr(session, "session_file", None)
        if not isinstance(session_file, str):
            continue
        try:
            mode = os.stat(session_file).st_mode & 0o777
        except OSError:
            continue
        if mode & 0o007:
            logger.warning(
                "Session file for account %r (%s) is readable by group/others "
                "(mode %03o). A session file is the account's credential — "
                "run: chmod 600 %s",
                label,
                session_file,
                mode,
                session_file,
            )


clients: dict[str, TelegramClient] = _discover_accounts()
_warn_world_readable_sessions()


def get_client(account: str = None) -> TelegramClient:
    """Resolve account label to TelegramClient."""
    # Handle empty string from MCP tool calls (treated as "use default")
    if account is None or account == "":
        if len(clients) == 1:
            return next(iter(clients.values()))
        raise ValueError(f"Account is required. Available accounts: {', '.join(clients.keys())}")
    label = account.lower()
    if label not in clients:
        raise ValueError(
            f"Unknown account '{account}'. Available accounts: {', '.join(clients.keys())}"
        )
    return clients[label]


def is_multi_mode() -> bool:
    """Return True when more than one account is configured."""
    return len(clients) > 1


# Connection-level errors eligible for optional transient retry. Kept
# deliberately narrow: server-side RPC errors are NOT retried here because
# they are unambiguous outcomes, and retrying send-type tools on them could
# duplicate side effects.
_TRANSIENT_EXCEPTIONS = (ConnectionError, TimeoutError)


def _get_transient_retry_count() -> int:
    """Return how many times transient connection errors may be retried.

    ``TELEGRAM_RETRY_TRANSIENT`` (default ``0`` = disabled). Capped at 5.
    Opt-in by design: a reset mid-request may mean the operation already
    reached Telegram, so automatic retries are a safety valve for flaky
    networks, not the default behavior.
    """
    raw = os.getenv("TELEGRAM_RETRY_TRANSIENT", "0")
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid TELEGRAM_RETRY_TRANSIENT '%s'; defaulting to 0", raw)
        return 0
    return max(0, min(value, 5))


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff (1s, 2s, 4s, ...) capped at 10s, plus jitter."""
    return min(2 ** (attempt - 1), 10.0) + random.uniform(0, 0.5)


def with_account(readonly=False):
    """Decorator that adds multi-account support to MCP tools.

    - In single-mode: always uses the sole client, no output tagging.
    - In multi-mode with explicit account: uses that account's client.
    - In multi-mode without account + readonly: fans out to all accounts
      concurrently, prefixes each result with [label], concatenates.
    - In multi-mode without account + NOT readonly: returns an error.

    The wrapped function must accept ``account: str = None`` and use
    ``get_client(account)`` internally to obtain the TelegramClient.
    """

    def decorator(fn):
        tool_name = getattr(fn, "__name__", "unknown")

        async def _invoke(account_label, *args, **kwargs):
            """Single invocation with optional transient retry and audit.

            Retry is OPT-IN (``TELEGRAM_RETRY_TRANSIENT``, default 0) because
            a connection reset mid-request is ambiguous — the operation may
            already have reached Telegram, and a blind retry of a send-type
            tool could duplicate messages. Only connection-level errors
            (``ConnectionError``/``TimeoutError``) are eligible; Telethon
            already handles FloodWait and server-level retries internally.
            """
            arg_names = [k for k in kwargs if k != "account"]
            max_retries = _get_transient_retry_count()
            attempt = 0
            started = time.monotonic()
            while True:
                try:
                    result = await fn(*args, **kwargs)
                    audit.record_audit(
                        tool_name=tool_name,
                        account=account_label,
                        ok=True,
                        arg_names=arg_names,
                        duration_ms=(time.monotonic() - started) * 1000,
                    )
                    return result
                except Exception as exc:
                    if isinstance(exc, _TRANSIENT_EXCEPTIONS) and attempt < max_retries:
                        attempt += 1
                        delay = _backoff_delay(attempt)
                        logger.warning(
                            "Transient error in %s (%s); retry %d/%d in %.1fs",
                            tool_name,
                            type(exc).__name__,
                            attempt,
                            max_retries,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    audit.record_audit(
                        tool_name=tool_name,
                        account=account_label,
                        ok=False,
                        error=type(exc).__name__,
                        arg_names=arg_names,
                        duration_ms=(time.monotonic() - started) * 1000,
                    )
                    raise

        @wraps(fn)
        async def wrapper(*args, **kwargs):
            account = kwargs.get("account")

            # Explicit account OR single-mode -> call once
            if account is not None or not is_multi_mode():
                return await _invoke(account, *args, **kwargs)

            # account is None AND multi-mode
            if not readonly:
                labels = ", ".join(clients.keys())
                return f"Error: 'account' is required. Available accounts: {labels}"

            # Read-only fan-out to all accounts concurrently
            async def _call_for(label):
                kw = dict(kwargs)
                kw["account"] = label
                return label, await _invoke(label, *args, **kw)

            results = await asyncio.gather(*(_call_for(label) for label in clients))
            return "\n\n".join(f"[{label}]\n{result}" for label, result in results)

        return wrapper

    return decorator


_last_conn_verified: dict[int, float] = {}
_CONN_VERIFY_INTERVAL: float = 30.0  # seconds between live pings


async def _force_reconnect(cl: TelegramClient):
    """Force disconnect + reconnect regardless of is_connected() state."""
    reconnect_logger = logging.getLogger("telegram_mcp")
    reconnect_logger.warning("Forcing reconnect...")
    try:
        await cl.disconnect()
    except Exception:
        pass
    await cl.connect()
    if not await cl.is_user_authorized():
        reconnect_logger.warning("Client not authorized after reconnect, calling start()...")
        await cl.start()
    _last_conn_verified[id(cl)] = time.time()
    reconnect_logger.warning("Forced reconnect successful")


async def ensure_connected(cl: TelegramClient = None):
    """Verify Telegram connection is alive, reconnect if needed.

    is_connected() can return True when the underlying TCP socket is dead.
    We periodically send a lightweight request to verify the connection
    actually works, and force-reconnect on any failure.

    Accepts an explicit client; falls back to the default single-account
    client when called without one.
    """
    if cl is None:
        cl = get_client()

    key = id(cl)

    if not cl.is_connected():
        await _force_reconnect(cl)
        return

    # Skip verification if recently confirmed alive
    now = time.time()
    if now - _last_conn_verified.get(key, 0.0) < _CONN_VERIFY_INTERVAL:
        return

    # Verify with a lightweight Telegram API call
    try:
        await asyncio.wait_for(
            cl(functions.help.GetNearestDcRequest()),
            timeout=5.0,
        )
        _last_conn_verified[key] = now
    except (ConnectionError, OSError, asyncio.TimeoutError, Exception):
        await _force_reconnect(cl)


# Setup robust logging with both file and console output
logger = logging.getLogger("telegram_mcp")
logger.setLevel(logging.ERROR)  # Set to ERROR for production, INFO for debugging

# Create console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.ERROR)  # Set to ERROR for production, INFO for debugging

# Create file handler with absolute path. Keep the legacy location next to
# top-level main.py, even though runtime code now lives inside telegram_mcp/.
package_dir = os.path.dirname(os.path.abspath(__file__))
script_dir = os.path.dirname(package_dir)
log_file_path = os.path.join(script_dir, "mcp_errors.log")

try:
    file_handler = logging.FileHandler(log_file_path, mode="a")  # Append mode
    file_handler.setLevel(logging.ERROR)

    # Create formatters
    # Console formatter remains in the old format
    console_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    console_handler.setFormatter(console_formatter)

    # File formatter is now JSON
    json_formatter = JsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    file_handler.setFormatter(json_formatter)

    # Add handlers to logger
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.info(f"Logging initialized to {log_file_path}")
except Exception as log_error:
    print(f"WARNING: Error setting up log file: {log_error}", file=sys.stderr)
    # Fallback to console-only logging
    logger.addHandler(console_handler)
    logger.error(f"Failed to set up log file handler: {log_error}")


# File-path tool security configuration
SERVER_ALLOWED_ROOTS: list[Path] = []
DEFAULT_DOWNLOAD_SUBDIR = "downloads"
DISALLOWED_PATH_PATTERNS = ("*", "?", "[", "]", "{", "}", "~", "\x00")
EXTENSION_ALLOWLISTS: dict[str, set[str]] = {
    "send_voice": {".ogg", ".opus"},
    "send_sticker": {".webp"},
    "set_profile_photo": {".jpg", ".jpeg", ".png", ".webp"},
    "edit_chat_photo": {".jpg", ".jpeg", ".png", ".webp"},
}
MAX_FILE_BYTES: dict[str, int] = {
    "send_file": 200 * 1024 * 1024,  # 200 MB
    "upload_file": 200 * 1024 * 1024,
    "send_voice": 100 * 1024 * 1024,
    "send_sticker": 10 * 1024 * 1024,
    "set_profile_photo": 50 * 1024 * 1024,
    "edit_chat_photo": 50 * 1024 * 1024,
}
ROOTS_UNSUPPORTED_ERROR_CODES = {-32601}
ROOTS_STATUS_READY = "ready"
ROOTS_STATUS_NOT_CONFIGURED = "not_configured"
ROOTS_STATUS_UNSUPPORTED_FALLBACK = "unsupported_fallback"
ROOTS_STATUS_CLIENT_DENY_ALL = "client_deny_all"
ROOTS_STATUS_SERVER_FALLBACK = "server_fallback"
ROOTS_STATUS_ERROR = "error"


# Error code prefix mapping for better error tracing
class ErrorCategory(str, Enum):
    CHAT = "CHAT"
    MSG = "MSG"
    CONTACT = "CONTACT"
    GROUP = "GROUP"
    MEDIA = "MEDIA"
    PROFILE = "PROFILE"
    AUTH = "AUTH"
    ADMIN = "ADMIN"
    FOLDER = "FOLDER"


def log_and_format_error(
    function_name: str,
    error: Exception,
    prefix: Optional[Union[ErrorCategory, str]] = None,
    user_message: str = None,
    **kwargs,
) -> str:
    """
    Centralized error handling function.

    Logs an error and returns a formatted, user-friendly message.

    Args:
        function_name: Name of the function where the error occurred.
        error: The exception that was raised.
        prefix: Error code prefix (e.g., ErrorCategory.CHAT, "VALIDATION-001").
            If None, it will be derived from the function_name.
        user_message: A custom user-facing message to return. If None, a generic one is created.
        **kwargs: Additional context parameters to include in the log.

    Returns:
        A user-friendly error message with an error code.
    """
    # Generate a consistent error code
    if isinstance(prefix, str) and prefix == "VALIDATION-001":
        # Special case for validation errors
        error_code = prefix
    else:
        if prefix is None:
            # Try to derive prefix from function name
            for category in ErrorCategory:
                if category.name.lower() in function_name.lower():
                    prefix = category
                    break

        prefix_str = prefix.value if isinstance(prefix, ErrorCategory) else (prefix or "GEN")
        # crc32 (unlike hash()) is stable across processes and restarts, so
        # error codes can be correlated between log files and runs.
        error_code = (
            f"{prefix_str}-ERR-{zlib.crc32(function_name.encode('utf-8')) % 1000:03d}"
        )

    # Format the additional context parameters
    context = ", ".join(f"{k}={v}" for k, v in kwargs.items())

    # Log the full technical error
    logger.error(f"Error in {function_name} ({context}) - Code: {error_code}", exc_info=True)

    # Return a user-friendly message
    if user_message:
        return user_message

    return f"An error occurred (code: {error_code}). Check mcp_errors.log for details."


def validate_id(*param_names_to_validate):
    """
    Decorator to validate chat_id and user_id parameters, including lists of IDs.
    It checks for valid integer ranges, string representations of integers,
    and username formats.
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for param_name in param_names_to_validate:
                if param_name not in kwargs or kwargs[param_name] is None:
                    continue

                param_value = kwargs[param_name]

                def validate_single_id(value, p_name):
                    # Handle integer IDs
                    if isinstance(value, int):
                        if not (-(2**63) <= value <= 2**63 - 1):
                            return (
                                None,
                                f"Invalid {p_name}: {value}. ID is out of the valid integer range.",
                            )
                        return value, None

                    # Handle string IDs
                    if isinstance(value, str):
                        try:
                            int_value = int(value)
                            if not (-(2**63) <= int_value <= 2**63 - 1):
                                return (
                                    None,
                                    f"Invalid {p_name}: {value}. ID is out of the valid integer range.",
                                )
                            return int_value, None
                        except ValueError:
                            # Support usernames (@username), phone numbers (+1234567890), and bare usernames
                            if re.match(r"^@?[a-zA-Z0-9_]{5,}$", value) or re.match(r"^\+?[0-9]{7,}$", value):
                                return value, None
                            else:
                                return (
                                    None,
                                    f"Invalid {p_name}: '{value}'. Must be a valid integer ID, username, or phone number.",
                                )

                    # Handle other invalid types
                    return (
                        None,
                        f"Invalid {p_name}: {value}. Type must be an integer or a string.",
                    )

                if isinstance(param_value, list):
                    validated_list = []
                    for item in param_value:
                        validated_item, error_msg = validate_single_id(item, param_name)
                        if error_msg:
                            return log_and_format_error(
                                func.__name__,
                                ValidationError(error_msg),
                                prefix="VALIDATION-001",
                                user_message=error_msg,
                                **{param_name: param_value},
                            )
                        validated_list.append(validated_item)
                    kwargs[param_name] = validated_list
                else:
                    validated_value, error_msg = validate_single_id(param_value, param_name)
                    if error_msg:
                        return log_and_format_error(
                            func.__name__,
                            ValidationError(error_msg),
                            prefix="VALIDATION-001",
                            user_message=error_msg,
                            **{param_name: param_value},
                        )
                    kwargs[param_name] = validated_value

            return await func(*args, **kwargs)

        return wrapper

    return decorator


def format_entity(entity) -> Dict[str, Any]:
    """Helper function to format entity information consistently.

    Names and titles are sanitized to prevent prompt injection.
    """
    result = {"id": get_marked_id(entity)}

    if hasattr(entity, "title"):
        result["name"] = sanitize_name(entity.title)
        result["type"] = "group" if isinstance(entity, Chat) else "channel"
    elif hasattr(entity, "first_name"):
        name_parts = []
        if entity.first_name:
            name_parts.append(entity.first_name)
        if hasattr(entity, "last_name") and entity.last_name:
            name_parts.append(entity.last_name)
        result["name"] = sanitize_name(" ".join(name_parts))
        result["type"] = "user"
        if hasattr(entity, "username") and entity.username:
            result["username"] = entity.username
        if hasattr(entity, "phone") and entity.phone:
            result["phone"] = entity.phone

    return result


def _marked_id_candidates(identifier: Union[int, str]) -> list[int]:
    """Return marked chat/channel ID variants for a bare positive integer ID."""
    if not isinstance(identifier, int) or identifier <= 0:
        return []

    return [
        -1000000000000 - identifier,
        -identifier,
    ]


async def resolve_entity(identifier: Union[int, str], client=None) -> Any:
    """Resolve entity with automatic cache warming, marked-ID fallback, and reconnect.

    StringSession has no persistent entity cache. If get_entity() fails
    because the cache is cold (ValueError on PeerUser lookup for group IDs),
    warm the cache via get_dialogs() and retry.

    If the value is a bare positive channel/chat ID, try Telethon's marked
    channel/chat ID variants before raising.

    On ConnectionError, reconnects and retries once.
    """
    if client is None:
        client = get_client()
    await ensure_connected(client)
    last_error = None
    try:
        try:
            return await client.get_entity(identifier)
        except ValueError as error:
            last_error = error
            await client.get_dialogs()
            try:
                return await client.get_entity(identifier)
            except ValueError as error:
                last_error = error
    except ConnectionError:
        await ensure_connected(client)
        try:
            return await client.get_entity(identifier)
        except ValueError as error:
            last_error = error
            await client.get_dialogs()
            try:
                return await client.get_entity(identifier)
            except ValueError as error:
                last_error = error

    for candidate in _marked_id_candidates(identifier):
        try:
            return await client.get_entity(candidate)
        except ValueError as error:
            last_error = error

    raise ValueError(
        f"Could not resolve entity for {identifier!r}, "
        f"including marked variants {_marked_id_candidates(identifier)}"
    ) from last_error


async def resolve_input_entity(identifier: Union[int, str], client=None) -> Any:
    """Like resolve_entity() but returns an InputPeer.

    Uses the same cache warming, marked-ID fallback, and reconnect behavior.
    """
    if client is None:
        client = get_client()
    await ensure_connected(client)
    last_error = None
    try:
        try:
            return await client.get_input_entity(identifier)
        except ValueError as error:
            last_error = error
            await client.get_dialogs()
            try:
                return await client.get_input_entity(identifier)
            except ValueError as error:
                last_error = error
    except ConnectionError:
        await ensure_connected(client)
        try:
            return await client.get_input_entity(identifier)
        except ValueError as error:
            last_error = error
            await client.get_dialogs()
            try:
                return await client.get_input_entity(identifier)
            except ValueError as error:
                last_error = error

    for candidate in _marked_id_candidates(identifier):
        try:
            return await client.get_input_entity(candidate)
        except ValueError as error:
            last_error = error

    raise ValueError(
        f"Could not resolve input entity for {identifier!r}, "
        f"including marked variants {_marked_id_candidates(identifier)}"
    ) from last_error


def format_message(message) -> Dict[str, Any]:
    """Helper function to format message information consistently.

    Message text is sanitized to prevent prompt injection.
    """
    result = {
        "id": message.id,
        "date": message.date.isoformat(),
        "text": sanitize_user_content(message.message),
    }

    if message.from_id:
        result["from_id"] = utils.get_peer_id(message.from_id)

    if message.media:
        result["has_media"] = True
        result["media_type"] = type(message.media).__name__

    return result


def get_sender_name(message) -> str:
    """Helper function to get sender name from a message.

    Returns a sanitized single-line display name to prevent prompt injection
    via crafted Telegram display names.
    """
    if not message.sender:
        return "Unknown"

    # Check for group/channel title first
    if hasattr(message.sender, "title") and message.sender.title:
        return sanitize_name(message.sender.title)
    elif hasattr(message.sender, "first_name"):
        # User sender
        first_name = getattr(message.sender, "first_name", "") or ""
        last_name = getattr(message.sender, "last_name", "") or ""
        full_name = f"{first_name} {last_name}".strip()
        return sanitize_name(full_name) if full_name else "Unknown"
    else:
        return "Unknown"


def get_sender_username(message) -> Optional[str]:
    """Public @username of the message sender, if any (sanitized)."""
    sender = getattr(message, "sender", None)
    username = getattr(sender, "username", None) if sender else None
    return sanitize_name(username) if username else None


def get_sender_info(message) -> str:
    """Sender display string: name (@username) [id=NNN].

    Always exposes a numeric id (sender or from_id) so a user can be reached via
    tg://user?id=<id> even when no public @username exists.
    """
    name = get_sender_name(message)
    username = get_sender_username(message)
    sid = getattr(message, "sender_id", None)
    suffix = ""
    if username:
        suffix += f" (@{username})"
    if sid:
        suffix += f" [id={sid}]"
    return f"{name}{suffix}"


def get_engagement_info(message) -> str:
    """Helper function to get engagement metrics (views, forwards, reactions) from a message."""
    engagement_parts = []
    views = getattr(message, "views", None)
    if views is not None:
        engagement_parts.append(f"views:{views}")
    forwards = getattr(message, "forwards", None)
    if forwards is not None:
        engagement_parts.append(f"forwards:{forwards}")
    reactions = getattr(message, "reactions", None)
    if reactions is not None:
        results = getattr(reactions, "results", None)
        total_reactions = sum(getattr(r, "count", 0) or 0 for r in results) if results else 0
        engagement_parts.append(f"reactions:{total_reactions}")
    return f" | {', '.join(engagement_parts)}" if engagement_parts else ""


def get_engagement_dict(message) -> Optional[Dict[str, Any]]:
    """Return engagement metrics as a dict for JSON-formatted tool results."""
    result = {}
    views = getattr(message, "views", None)
    if views is not None:
        result["views"] = views
    forwards = getattr(message, "forwards", None)
    if forwards is not None:
        result["forwards"] = forwards
    reactions = getattr(message, "reactions", None)
    if reactions is not None:
        results = getattr(reactions, "results", None)
        result["reactions"] = sum(getattr(r, "count", 0) or 0 for r in results) if results else 0
    return result if result else None


def _dedupe_paths(paths: List[Path]) -> List[Path]:
    seen: set[str] = set()
    result: List[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _contains_forbidden_path_patterns(raw_path: str) -> Optional[str]:
    value = raw_path.strip()
    if not value:
        return "Path must not be empty."
    if any(token in value for token in DISALLOWED_PATH_PATTERNS):
        return "Path contains disallowed wildcard/shell patterns."
    if ".." in Path(value).parts:
        return "Path traversal is not allowed."
    return None


def _coerce_root_uri_to_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"Unsupported root URI scheme: {parsed.scheme}")

    decoded_path = unquote(parsed.path or "")
    if parsed.netloc and parsed.netloc not in ("", "localhost"):
        decoded_path = f"//{parsed.netloc}{decoded_path}"
    if os.name == "nt" and decoded_path.startswith("/") and len(decoded_path) > 2:
        # file:///C:/tmp -> C:/tmp on Windows
        if decoded_path[2] == ":":
            decoded_path = decoded_path[1:]
    return Path(decoded_path).resolve(strict=True)


def _path_is_within_root(candidate: Path, root: Path) -> bool:
    root = root.resolve()
    if root.is_file():
        return candidate == root
    return candidate == root or root in candidate.parents


def _path_is_within_any_root(candidate: Path, roots: List[Path]) -> bool:
    return any(_path_is_within_root(candidate, root) for root in roots)


def _first_resolution_root(roots: List[Path]) -> Path:
    first = roots[0]
    return first if first.is_dir() else first.parent


def _ensure_extension_allowed(tool_name: str, candidate: Path) -> Optional[str]:
    allowlist = EXTENSION_ALLOWLISTS.get(tool_name)
    if not allowlist:
        return None
    if candidate.suffix.lower() not in allowlist:
        allowed = ", ".join(sorted(allowlist))
        return f"File extension is not allowed for {tool_name}. Allowed: {allowed}."
    return None


def _ensure_size_within_limit(tool_name: str, candidate: Path) -> Optional[str]:
    max_bytes = MAX_FILE_BYTES.get(tool_name)
    if not max_bytes:
        return None
    size = candidate.stat().st_size
    if size > max_bytes:
        return f"File is too large for {tool_name}: {size} bytes " f"(limit: {max_bytes} bytes)."
    return None


async def _get_effective_allowed_roots(ctx: Optional[Context]) -> List[Path]:
    roots, _status = await _get_effective_allowed_roots_with_status(ctx)
    return roots


def _is_roots_unsupported_error(error: Exception) -> bool:
    if isinstance(error, McpError):
        error_code = getattr(getattr(error, "error", None), "code", None)
        error_message = (
            getattr(getattr(error, "error", None), "message", None) or str(error)
        ).lower()
        if error_code in ROOTS_UNSUPPORTED_ERROR_CODES:
            return True
        return "method not found" in error_message or "not implemented" in error_message

    if isinstance(error, NotImplementedError):
        return True
    if isinstance(error, AttributeError):
        return "list_roots" in str(error)
    return False


def _server_roots_fallback_enabled(value: Optional[str] = None) -> bool:
    """Whether an empty client roots list should fall back to server CLI roots.

    Opt-in via the ``TELEGRAM_ALLOW_SERVER_ROOTS_FALLBACK`` environment variable.
    Defaults to ``False`` to preserve the safe deny-all behavior.
    """
    raw_value = os.getenv("TELEGRAM_ALLOW_SERVER_ROOTS_FALLBACK") if value is None else value
    return _parse_bool_env(raw_value, False)


async def _get_effective_allowed_roots_with_status(
    ctx: Optional[Context],
) -> tuple[List[Path], str]:
    fallback_roots = list(SERVER_ALLOWED_ROOTS)
    if ctx is None:
        if fallback_roots:
            return fallback_roots, ROOTS_STATUS_READY
        return [], ROOTS_STATUS_NOT_CONFIGURED

    try:
        list_roots_result = await ctx.session.list_roots()
    except Exception as error:
        if _is_roots_unsupported_error(error):
            if fallback_roots:
                return fallback_roots, ROOTS_STATUS_UNSUPPORTED_FALLBACK
            return [], ROOTS_STATUS_NOT_CONFIGURED
        logger.error(
            "MCP roots request failed; disabling file-path tools for safety.", exc_info=True
        )
        return [], ROOTS_STATUS_ERROR

    client_roots: List[Path] = []
    for root in list_roots_result.roots:
        try:
            client_roots.append(_coerce_root_uri_to_path(str(root.uri)))
        except Exception:
            # Ignore invalid root entries supplied by a client.
            continue

    if client_roots:
        return _dedupe_paths(client_roots), ROOTS_STATUS_READY

    # Roots API succeeded but returned an empty list. By default this is an
    # explicit deny-all. Some clients (e.g. ones that implement the Roots
    # capability but expose no roots) advertise an empty list even though the
    # operator configured server-side CLI roots; for those, an opt-in lets the
    # server-side roots take effect instead of disabling file tools entirely.
    if fallback_roots and _server_roots_fallback_enabled():
        return fallback_roots, ROOTS_STATUS_SERVER_FALLBACK
    return [], ROOTS_STATUS_CLIENT_DENY_ALL


async def _ensure_allowed_roots(
    ctx: Optional[Context], tool_name: str
) -> tuple[List[Path], Optional[str]]:
    roots, status = await _get_effective_allowed_roots_with_status(ctx)
    if not roots:
        if status == ROOTS_STATUS_CLIENT_DENY_ALL:
            return (
                [],
                (
                    f"{tool_name} is disabled because the client provided an empty "
                    "MCP Roots list (deny-all)."
                ),
            )
        if status == ROOTS_STATUS_ERROR:
            return (
                [],
                (
                    f"{tool_name} is disabled because MCP Roots could not be verified safely. "
                    "Check MCP client/server logs."
                ),
            )
        return (
            [],
            (
                f"{tool_name} is disabled until allowed roots are configured. "
                "Provide server CLI roots and/or client MCP Roots."
            ),
        )
    return roots, None


async def _resolve_readable_file_path(
    *,
    raw_path: str,
    ctx: Optional[Context],
    tool_name: str,
) -> tuple[Optional[Path], Optional[str]]:
    roots, error = await _ensure_allowed_roots(ctx, tool_name)
    if error:
        return None, error

    pattern_error = _contains_forbidden_path_patterns(raw_path)
    if pattern_error:
        return None, pattern_error

    candidate = Path(raw_path.strip())
    if not candidate.is_absolute():
        candidate = _first_resolution_root(roots) / candidate

    try:
        candidate = candidate.resolve(strict=True)
    except FileNotFoundError:
        return None, f"File not found: {raw_path}"

    if not _path_is_within_any_root(candidate, roots):
        return None, "Path is outside allowed roots."
    if not candidate.is_file():
        return None, f"Path is not a file: {candidate}"
    if not os.access(candidate, os.R_OK):
        return None, f"File is not readable: {candidate}"

    extension_error = _ensure_extension_allowed(tool_name, candidate)
    if extension_error:
        return None, extension_error

    size_error = _ensure_size_within_limit(tool_name, candidate)
    if size_error:
        return None, size_error

    return candidate, None


async def _resolve_writable_file_path(
    *,
    raw_path: Optional[str],
    default_filename: str,
    ctx: Optional[Context],
    tool_name: str,
) -> tuple[Optional[Path], Optional[str]]:
    roots, error = await _ensure_allowed_roots(ctx, tool_name)
    if error:
        return None, error

    if raw_path and raw_path.strip():
        pattern_error = _contains_forbidden_path_patterns(raw_path)
        if pattern_error:
            return None, pattern_error
        candidate = Path(raw_path.strip())
        if not candidate.is_absolute():
            candidate = _first_resolution_root(roots) / candidate
    else:
        safe_name = Path(default_filename).name
        candidate = _first_resolution_root(roots) / DEFAULT_DOWNLOAD_SUBDIR / safe_name

    candidate = candidate.resolve(strict=False)
    parent = candidate.parent.resolve(strict=False)
    if not _path_is_within_any_root(candidate, roots) or not _path_is_within_any_root(
        parent, roots
    ):
        return None, "Path is outside allowed roots."

    extension_error = _ensure_extension_allowed(tool_name, candidate)
    if extension_error:
        return None, extension_error

    parent.mkdir(parents=True, exist_ok=True)
    if not os.access(parent, os.W_OK):
        return None, f"Directory not writable: {parent}"

    return candidate, None


def _configure_allowed_roots_from_cli(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="telegram-mcp",
        add_help=False,
        description=(
            "Optional positional arguments define server-side allowed roots "
            "for file-path tools."
        ),
    )
    parser.add_argument("allowed_roots", nargs="*")
    parsed, _unknown = parser.parse_known_args(argv or [])

    resolved_roots: List[Path] = []
    for raw_root in parsed.allowed_roots:
        root = Path(raw_root).expanduser()
        if not root.exists():
            raise SystemExit(f"Allowed root does not exist: {root}")
        resolved = root.resolve(strict=True)
        resolved_roots.append(resolved)

    global SERVER_ALLOWED_ROOTS
    SERVER_ALLOWED_ROOTS = _dedupe_paths(resolved_roots)


# Re-export shared runtime names for tool modules that use star imports.
__all__ = [name for name in globals() if not name.startswith("__")]
