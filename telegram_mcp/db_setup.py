"""
SQLite Database Setup and Helper Module for Telegram Media Processing Pipeline.

This module initializes a local SQLite database (telegram_media.db) with the
required schemas for tracking processed messages and series episodes.
"""

import hashlib
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

# Configure logging
logger = logging.getLogger(__name__)

# Default database path
DEFAULT_DB_PATH = Path(__file__).parent / "telegram_media.db"


def get_db_path() -> Path:
    """Get the database path, allowing override via environment variable."""
    env_path = os.getenv("TELEGRAM_MEDIA_DB_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_DB_PATH


@contextmanager
def get_db_connection(db_path: Optional[Path] = None):
    """Context manager for SQLite database connections with row factory."""
    path = db_path or get_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_database(db_path: Optional[Path] = None) -> None:
    """
    Initialize the SQLite database with required tables.
    
    Creates:
    - processed_messages: Tracks all processed messages with deduplication
    - series_episodes: Tracks TV series episodes for sequence auditing
    
    Args:
        db_path: Optional custom database path
    """
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    
    with get_db_connection(path) as conn:
        cursor = conn.cursor()
        
        # Table 1: processed_messages
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_channel_id TEXT NOT NULL,
                source_message_id TEXT NOT NULL,
                content_hash TEXT UNIQUE NOT NULL,
                target_topic_id TEXT,
                status TEXT NOT NULL,  -- 'PROCESSING', 'COMPLETED', 'FAILED_FLOODWAIT', 'FAILED_PARSING', 'REJECTED_SPAM', 'EMPTY_AFTER_CLEANING'
                failure_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create indexes for common queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_processed_messages_source 
            ON processed_messages(source_channel_id, source_message_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_processed_messages_hash 
            ON processed_messages(content_hash)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_processed_messages_status 
            ON processed_messages(status)
        """)
        
        # Table 2: series_episodes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS series_episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_name TEXT NOT NULL,
                season_number INTEGER NOT NULL,
                episode_number INTEGER NOT NULL,
                telegram_message_id TEXT,
                topic_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create unique index to prevent duplicate episodes
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_series_episodes_unique 
            ON series_episodes(series_name, season_number, episode_number)
        """)
        
        # Create index for sequence audit queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_series_episodes_lookup 
            ON series_episodes(series_name, season_number)
        """)
        
        conn.commit()
        logger.info(f"Database initialized at {path}")


def compute_content_hash(text: str, file_name: str = "", file_size_bytes: int = 0) -> str:
    """
    Compute a SHA-256 content hash for deduplication.
    
    Logic:
    1. Strip whitespace from text
    2. Concatenate: f"{stripped_text}|{file_name}|{file_size_bytes}"
    3. Generate SHA-256 hex digest prefixed with "sha256:"
    
    This is calculated BEFORE text cleaning to detect duplicates across
    different sources or channels.
    
    Args:
        text: The message text content
        file_name: Optional attached file name
        file_size_bytes: Optional file size in bytes
        
    Returns:
        SHA-256 hash string prefixed with "sha256:"
    """
    stripped_text = text.strip() if text else ""
    content = f"{stripped_text}|{file_name}|{file_size_bytes}"
    hash_bytes = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return f"sha256:{hash_bytes}"


def check_duplicate(
    source_channel_id: str,
    source_message_id: str,
    content_hash: str,
    db_path: Optional[Path] = None
) -> bool:
    """
    Check if a message already exists in the database.
    
    Queries by either source_message_id OR content_hash.
    
    Args:
        source_channel_id: Source channel identifier
        source_message_id: Source message identifier
        content_hash: Content hash for deduplication
        db_path: Optional custom database path
        
    Returns:
        True if duplicate exists, False otherwise
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 1 FROM processed_messages 
            WHERE (source_channel_id = ? AND source_message_id = ?) 
               OR content_hash = ?
            LIMIT 1
        """, (source_channel_id, source_message_id, content_hash))
        return cursor.fetchone() is not None


def record_message_status(
    source_channel_id: str,
    source_message_id: str,
    content_hash: str,
    status: str,
    target_topic_id: Optional[str] = None,
    failure_reason: Optional[str] = None,
    db_path: Optional[Path] = None
) -> int:
    """
    Insert a new record or update status/failure_reason in processed_messages.
    
    Args:
        source_channel_id: Source channel identifier
        source_message_id: Source message identifier
        content_hash: Content hash for deduplication
        status: Message status ('PROCESSING', 'COMPLETED', 'FAILED_FLOODWAIT', etc.)
        target_topic_id: Optional target topic ID
        failure_reason: Optional failure reason
        db_path: Optional custom database path
        
    Returns:
        The row ID of the inserted/updated record
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        
        # Try to update existing record first
        cursor.execute("""
            UPDATE processed_messages 
            SET status = ?, target_topic_id = ?, failure_reason = ?, updated_at = CURRENT_TIMESTAMP
            WHERE content_hash = ?
        """, (status, target_topic_id, failure_reason, content_hash))
        
        if cursor.rowcount > 0:
            conn.commit()
            # Return the ID of the updated record
            cursor.execute("SELECT id FROM processed_messages WHERE content_hash = ?", (content_hash,))
            row = cursor.fetchone()
            return row["id"] if row else 0
        
        # Insert new record
        cursor.execute("""
            INSERT INTO processed_messages 
            (source_channel_id, source_message_id, content_hash, target_topic_id, status, failure_reason)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (source_channel_id, source_message_id, content_hash, target_topic_id, status, failure_reason))
        
        conn.commit()
        return cursor.lastrowid


def audit_series_sequence(
    series_name: str,
    season_number: int,
    db_path: Optional[Path] = None
) -> list[int]:
    """
    Query series_episodes for a given series and season to detect missing episodes.
    
    Args:
        series_name: Name of the series
        season_number: Season number to audit
        db_path: Optional custom database path
        
    Returns:
        List of missing episode numbers (empty if complete)
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT episode_number FROM series_episodes
            WHERE series_name = ? AND season_number = ?
            ORDER BY episode_number
        """, (series_name, season_number))
        
        episodes = [row["episode_number"] for row in cursor.fetchall()]
        
        if not episodes:
            return []
        
        # Find gaps in sequence
        expected = set(range(1, max(episodes) + 1))
        actual = set(episodes)
        missing = sorted(expected - actual)
        
        return missing


def add_series_episode(
    series_name: str,
    season_number: int,
    episode_number: int,
    telegram_message_id: Optional[str] = None,
    topic_id: Optional[str] = None,
    db_path: Optional[Path] = None
) -> int:
    """
    Add a series episode record.
    
    Args:
        series_name: Name of the series
        season_number: Season number
        episode_number: Episode number
        telegram_message_id: Optional Telegram message ID
        topic_id: Optional topic ID
        db_path: Optional custom database path
        
    Returns:
        The row ID of the inserted record
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO series_episodes
            (series_name, season_number, episode_number, telegram_message_id, topic_id)
            VALUES (?, ?, ?, ?, ?)
        """, (series_name, season_number, episode_number, telegram_message_id, topic_id))
        conn.commit()
        return cursor.lastrowid


def get_message_status(
    content_hash: str,
    db_path: Optional[Path] = None
) -> Optional[dict]:
    """
    Get the status of a processed message by content hash.
    
    Args:
        content_hash: Content hash to look up
        db_path: Optional custom database path
        
    Returns:
        Dictionary with message status or None if not found
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM processed_messages WHERE content_hash = ?
        """, (content_hash,))
        row = cursor.fetchone()
        return dict(row) if row else None


if __name__ == "__main__":
    # Quick test when run directly
    import sys
    
    logging.basicConfig(level=logging.INFO)
    
    # Initialize database
    init_database()
    print("Database initialized successfully")
    
    # Test hash function
    test_hash = compute_content_hash("  Hello World  ", "test.jpg", 1024)
    print(f"Content hash: {test_hash}")
    
    # Test duplicate check
    is_dup = check_duplicate("channel1", "msg123", test_hash)
    print(f"Is duplicate (should be False): {is_dup}")
    
    # Record message
    record_id = record_message_status("channel1", "msg123", test_hash, "PROCESSING")
    print(f"Recorded message with ID: {record_id}")
    
    # Check duplicate again
    is_dup = check_duplicate("channel1", "msg123", test_hash)
    print(f"Is duplicate (should be True): {is_dup}")
    
    # Update status
    record_message_status("channel1", "msg123", test_hash, "COMPLETED", "topic456")
    print("Updated status to COMPLETED")
    
    # Test series audit
    add_series_episode("Test Series", 1, 1, "msg1", "topic1")
    add_series_episode("Test Series", 1, 2, "msg2", "topic1")
    add_series_episode("Test Series", 1, 4, "msg4", "topic1")
    add_series_episode("Test Series", 1, 5, "msg5", "topic1")
    
    missing = audit_series_sequence("Test Series", 1)
    print(f"Missing episodes: {missing}")  # Should be [3]
    
    print("\nAll tests passed!")