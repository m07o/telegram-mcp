"""
Database MCP Tools for Telegram Media Processing Pipeline.

Provides MCP tools allowing agents to interact directly with the SQLite database.
"""

from telegram_mcp.runtime import *
from telegram_mcp.db_setup import (
    check_duplicate as db_check_duplicate,
    record_message_status as db_record_message_status,
    audit_series_sequence as db_audit_series_sequence,
    add_series_episode,
    get_message_status,
    compute_content_hash,
    init_database,
)


@mcp.tool(
    annotations=ToolAnnotations(title="Check Duplicate", readOnlyHint=True)
)
@with_account(readonly=True)
async def check_duplicate(
    source_channel_id: str,
    source_message_id: str,
    content_hash: str,
    account: str = None,
) -> str:
    """
    Check if a message already exists in the processed_messages database.
    
    Queries by either source_message_id OR content_hash.
    
    Args:
        source_channel_id: Source channel identifier
        source_message_id: Source message identifier
        content_hash: Content hash for deduplication (sha256:...)
        
    Returns:
        JSON: {"is_duplicate": true/false}
    """
    try:
        is_dup = db_check_duplicate(source_channel_id, source_message_id, content_hash)
        return json.dumps({"is_duplicate": is_dup}, default=json_serializer)
    except Exception as e:
        return log_and_format_error(
            "check_duplicate", e,
            source_channel_id=source_channel_id,
            source_message_id=source_message_id,
            content_hash=content_hash
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Record Message Status", openWorldHint=True, destructiveHint=True)
)
@with_account(readonly=False)
async def record_message_status(
    source_channel_id: str,
    source_message_id: str,
    content_hash: str,
    status: str,
    target_topic_id: str = None,
    failure_reason: str = None,
    account: str = None,
) -> str:
    """
    Insert new record or update status/failure_reason in processed_messages.
    
    Args:
        source_channel_id: Source channel identifier
        source_message_id: Source message identifier
        content_hash: Content hash for deduplication (sha256:...)
        status: Message status ('PROCESSING', 'COMPLETED', 'FAILED_FLOODWAIT', 
                'FAILED_PARSING', 'REJECTED_SPAM', 'EMPTY_AFTER_CLEANING')
        target_topic_id: Optional target topic ID
        failure_reason: Optional failure reason
        
    Returns:
        JSON: {"record_id": 123, "status": "COMPLETED"}
    """
    try:
        # Validate status
        valid_statuses = {
            "PROCESSING", "COMPLETED", "FAILED_FLOODWAIT", 
            "FAILED_PARSING", "REJECTED_SPAM", "EMPTY_AFTER_CLEANING",
            "FAILED_TOPIC_ERROR", "FAILED_NETWORK"
        }
        if status not in valid_statuses:
            return json.dumps({
                "error": f"Invalid status: {status}. Valid: {', '.join(sorted(valid_statuses))}"
            })
        
        record_id = db_record_message_status(
            source_channel_id=source_channel_id,
            source_message_id=source_message_id,
            content_hash=content_hash,
            status=status,
            target_topic_id=target_topic_id,
            failure_reason=failure_reason
        )
        return json.dumps({"record_id": record_id, "status": status}, default=json_serializer)
    except Exception as e:
        return log_and_format_error(
            "record_message_status", e,
            source_channel_id=source_channel_id,
            source_message_id=source_message_id,
            content_hash=content_hash,
            status=status
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Audit Series Sequence", readOnlyHint=True)
)
@with_account(readonly=True)
async def audit_series_sequence(
    series_name: str,
    season_number: int,
    account: str = None,
) -> str:
    """
    Query series_episodes for a given series and season to detect missing episodes.
    
    Args:
        series_name: Name of the series
        season_number: Season number to audit
        
    Returns:
        JSON: {"series_name": "...", "season_number": N, "missing_episodes": [4, 7], "total_episodes": 10}
    """
    try:
        missing = db_audit_series_sequence(series_name, season_number)
        
        # Also get total count
        from telegram_mcp.db_setup import get_db_connection
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) as cnt, MAX(episode_number) as max_ep
                FROM series_episodes
                WHERE series_name = ? AND season_number = ?
            """, (series_name, season_number))
            row = cursor.fetchone()
            total = row["cnt"] if row else 0
            max_ep = row["max_ep"] if row else 0
        
        return json.dumps({
            "series_name": series_name,
            "season_number": season_number,
            "missing_episodes": missing,
            "total_episodes": total,
            "max_episode": max_ep
        }, default=json_serializer)
    except Exception as e:
        return log_and_format_error(
            "audit_series_sequence", e,
            series_name=series_name,
            season_number=season_number
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Add Series Episode", openWorldHint=True, destructiveHint=True)
)
@with_account(readonly=False)
async def add_series_episode_tool(
    series_name: str,
    season_number: int,
    episode_number: int,
    telegram_message_id: str = None,
    topic_id: str = None,
    account: str = None,
) -> str:
    """
    Add a series episode record to the database.
    
    Args:
        series_name: Name of the series
        season_number: Season number
        episode_number: Episode number
        telegram_message_id: Optional Telegram message ID
        topic_id: Optional topic ID
        
    Returns:
        JSON: {"record_id": 123, "series_name": "...", "season": N, "episode": N}
    """
    try:
        record_id = add_series_episode(
            series_name=series_name,
            season_number=season_number,
            episode_number=episode_number,
            telegram_message_id=telegram_message_id,
            topic_id=topic_id
        )
        return json.dumps({
            "record_id": record_id,
            "series_name": series_name,
            "season": season_number,
            "episode": episode_number
        }, default=json_serializer)
    except Exception as e:
        return log_and_format_error(
            "add_series_episode_tool", e,
            series_name=series_name,
            season_number=season_number,
            episode_number=episode_number
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Get Message Status", readOnlyHint=True)
)
@with_account(readonly=True)
async def get_message_status_tool(
    content_hash: str,
    account: str = None,
) -> str:
    """
    Get the status of a processed message by content hash.
    
    Args:
        content_hash: Content hash to look up (sha256:...)
        
    Returns:
        JSON: Message record or {"error": "not found"}
    """
    try:
        status = get_message_status(content_hash)
        if status:
            return json.dumps(status, default=json_serializer)
        return json.dumps({"error": "not found"}, default=json_serializer)
    except Exception as e:
        return log_and_format_error("get_message_status_tool", e, content_hash=content_hash)


@mcp.tool(
    annotations=ToolAnnotations(title="Compute Content Hash", readOnlyHint=True)
)
@with_account(readonly=True)
async def compute_content_hash_tool(
    text: str,
    file_name: str = "",
    file_size_bytes: int = 0,
    account: str = None,
) -> str:
    """
    Compute a SHA-256 content hash for deduplication.
    
    Logic: Strip whitespace from text. Concatenate f"{stripped_text}|{file_name}|{file_size_bytes}".
    Algorithm: Generate a standard SHA-256 hex digest prefixed with sha256:.
    Timing/Purpose: Calculate this hash BEFORE text cleaning to detect duplicates 
    across different sources or channels.
    
    Args:
        text: The message text content
        file_name: Optional attached file name
        file_size_bytes: Optional file size in bytes
        
    Returns:
        JSON: {"content_hash": "sha256:..."}
    """
    try:
        content_hash = compute_content_hash(text, file_name, file_size_bytes)
        return json.dumps({"content_hash": content_hash}, default=json_serializer)
    except Exception as e:
        return log_and_format_error("compute_content_hash_tool", e, text=text[:50] if text else "")


@mcp.tool(
    annotations=ToolAnnotations(title="Initialize Database", openWorldHint=True, destructiveHint=True)
)
@with_account(readonly=False)
async def initialize_database(account: str = None) -> str:
    """
    Initialize the SQLite database with required tables.
    
    Returns:
        JSON: {"status": "initialized", "database_path": "..."}
    """
    try:
        init_database()
        from telegram_mcp.db_setup import get_db_path
        return json.dumps({
            "status": "initialized",
            "database_path": str(get_db_path())
        }, default=json_serializer)
    except Exception as e:
        return log_and_format_error("initialize_database", e)


__all__ = [
    "check_duplicate",
    "record_message_status",
    "audit_series_sequence",
    "add_series_episode_tool",
    "get_message_status_tool",
    "compute_content_hash_tool",
    "initialize_database",
]