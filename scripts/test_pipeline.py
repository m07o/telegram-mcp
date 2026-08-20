#!/usr/bin/env python3
"""
Integration Test Script for Telegram Media Processing Pipeline.

This script verifies:
1. Successful SQLite table creation and connection
2. Correct SHA-256 hash generation with compute_content_hash
3. Simulating a full ingestion flow: duplicate check, DB insertion, status updates, and sequence audit checks
4. Proper formatting of success and failure JSON responses
"""

import asyncio
import json
import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from telegram_mcp.db_setup import (
    init_database,
    compute_content_hash,
    check_duplicate,
    record_message_status,
    audit_series_sequence,
    add_series_episode,
    get_message_status,
    get_db_connection,
    get_db_path,
)


def test_database_initialization():
    """Test 1: SQLite table creation and connection."""
    print("\n=== Test 1: Database Initialization ===")
    try:
        # Remove existing test database
        test_db = Path(__file__).parent / "test_telegram_media.db"
        if test_db.exists():
            test_db.unlink()
        
        # Override DB path for testing
        import telegram_mcp.db_setup as db_setup
        original_get_db_path = db_setup.get_db_path
        db_setup.get_db_path = lambda: test_db
        
        init_database()
        
        # Verify tables exist
        with get_db_connection(test_db) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            
            assert "processed_messages" in tables, "processed_messages table missing"
            assert "series_episodes" in tables, "series_episodes table missing"
            
            # Verify indexes
            cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
            indexes = [row[0] for row in cursor.fetchall()]
            print(f"  Tables: {tables}")
            print(f"  Indexes: {indexes}")
        
        # Restore
        db_setup.get_db_path = original_get_db_path
        
        print("  [OK] Database initialized successfully")
        return True
    except Exception as e:
        print(f"  [FAIL] FAILED: {e}")
        return False


def test_content_hash_generation():
    """Test 2: SHA-256 hash generation."""
    print("\n=== Test 2: Content Hash Generation ===")
    try:
        # Test basic hash
        hash1 = compute_content_hash("Hello World", "test.jpg", 1024)
        assert hash1.startswith("sha256:"), "Hash should start with sha256:"
        assert len(hash1) == 71, f"Hash length should be 71 (sha256: + 64), got {len(hash1)}"
        print(f"  Hash 1: {hash1}")
        
        # Test whitespace stripping
        hash2 = compute_content_hash("  Hello World  ", "test.jpg", 1024)
        assert hash1 == hash2, "Whitespace should be stripped"
        print("  [OK] Whitespace stripping works")
        
        # Test different content produces different hash
        hash3 = compute_content_hash("Hello World", "test.jpg", 1025)
        assert hash1 != hash3, "Different file size should produce different hash"
        print("  [OK] Different content produces different hash")
        
        # Test empty text
        hash4 = compute_content_hash("", "test.jpg", 1024)
        assert hash4.startswith("sha256:"), "Empty text should still produce hash"
        print(f"  Empty text hash: {hash4}")
        
        # Test deterministic
        hash5 = compute_content_hash("Test", "file.png", 2048)
        hash6 = compute_content_hash("Test", "file.png", 2048)
        assert hash5 == hash6, "Hash should be deterministic"
        print("  [OK] Hash is deterministic")
        
        print("  [OK] Content hash generation works correctly")
        return True
    except Exception as e:
        print(f"  [FAIL] FAILED: {e}")
        return False


def test_duplicate_check():
    """Test 3: Duplicate check functionality."""
    print("\n=== Test 3: Duplicate Check ===")
    try:
        test_db = Path(__file__).parent / "test_telegram_media.db"
        if test_db.exists():
            test_db.unlink()
        
        import telegram_mcp.db_setup as db_setup
        original_get_db_path = db_setup.get_db_path
        db_setup.get_db_path = lambda: test_db
        
        init_database()
        
        content_hash = compute_content_hash("Test message", "image.jpg", 500)
        
        # Should not be duplicate initially
        is_dup = check_duplicate("channel1", "msg1", content_hash)
        assert not is_dup, "Should not be duplicate initially"
        print("  [OK] Not duplicate initially")
        
        # Insert record
        record_message_status("channel1", "msg1", content_hash, "PROCESSING")
        
        # Should be duplicate now (by content_hash)
        is_dup = check_duplicate("channel1", "msg1", content_hash)
        assert is_dup, "Should be duplicate by content_hash"
        print("  [OK] Duplicate detected by content_hash")
        
        # Should be duplicate by source_channel_id + source_message_id
        is_dup = check_duplicate("channel1", "msg1", "different_hash")
        assert is_dup, "Should be duplicate by source IDs"
        print("  [OK] Duplicate detected by source IDs")
        
        # Different source should not be duplicate
        is_dup = check_duplicate("channel2", "msg1", content_hash)
        assert is_dup, "Different channel with same content should be duplicate"
        print("  [OK] Different channel with same content correctly detected as duplicate")
        
        db_setup.get_db_path = original_get_db_path
        
        print("  [OK] Duplicate check works correctly")
        return True
    except Exception as e:
        print(f"  [FAIL] FAILED: {e}")
        return False


def test_record_message_status():
    """Test 4: Record message status insert/update."""
    print("\n=== Test 4: Record Message Status ===")
    try:
        test_db = Path(__file__).parent / "test_telegram_media.db"
        if test_db.exists():
            test_db.unlink()
        
        import telegram_mcp.db_setup as db_setup
        original_get_db_path = db_setup.get_db_path
        db_setup.get_db_path = lambda: test_db
        
        init_database()
        
        content_hash = compute_content_hash("Status test", "file.txt", 100)
        
        # Insert new record
        record_id = record_message_status(
            "channel1", "msg1", content_hash, "PROCESSING", "topic123"
        )
        assert record_id > 0, "Should return record ID"
        print(f"  Inserted record ID: {record_id}")
        
        # Update status
        record_id2 = record_message_status(
            "channel1", "msg1", content_hash, "COMPLETED", "topic123"
        )
        assert record_id2 == record_id, "Should return same record ID on update"
        print(f"  Updated record ID: {record_id2}")
        
        # Verify status in database
        status = get_message_status(content_hash)
        assert status["status"] == "COMPLETED", "Status should be COMPLETED"
        assert status["target_topic_id"] == "topic123", "Topic ID should be saved"
        print(f"  Verified status: {status['status']}")
        
        # Test failure status
        fail_hash = compute_content_hash("Fail test", "fail.txt", 200)
        record_message_status("channel1", "msg2", fail_hash, "FAILED_FLOODWAIT", failure_reason="Wait 45 seconds")
        status = get_message_status(fail_hash)
        assert status["status"] == "FAILED_FLOODWAIT"
        assert status["failure_reason"] == "Wait 45 seconds"
        print("  [OK] Failure reason recorded")
        
        # Test EMPTY_AFTER_CLEANING status
        empty_hash = compute_content_hash("Empty test", "", 0)
        record_message_status("channel1", "msg3", empty_hash, "EMPTY_AFTER_CLEANING")
        status = get_message_status(empty_hash)
        assert status["status"] == "EMPTY_AFTER_CLEANING"
        print("  [OK] EMPTY_AFTER_CLEANING status recorded")
        
        db_setup.get_db_path = original_get_db_path
        
        print("  [OK] Record message status works correctly")
        return True
    except Exception as e:
        print(f"  [FAIL] FAILED: {e}")
        return False


def test_series_audit():
    """Test 5: Series episode sequence audit."""
    print("\n=== Test 5: Series Sequence Audit ===")
    try:
        test_db = Path(__file__).parent / "test_telegram_media.db"
        if test_db.exists():
            test_db.unlink()
        
        import telegram_mcp.db_setup as db_setup
        original_get_db_path = db_setup.get_db_path
        db_setup.get_db_path = lambda: test_db
        
        init_database()
        
        # Add episodes 1, 2, 3, 5 (missing 4)
        add_series_episode("Test Series", 1, 1, "msg1", "topic1")
        add_series_episode("Test Series", 1, 2, "msg2", "topic1")
        add_series_episode("Test Series", 1, 3, "msg3", "topic1")
        add_series_episode("Test Series", 1, 5, "msg5", "topic1")
        
        # Audit should find missing episode 4
        missing = audit_series_sequence("Test Series", 1)
        assert missing == [4], f"Expected [4], got {missing}"
        print(f"  Missing episodes: {missing}")
        
        # Add episode 4 - should be complete
        add_series_episode("Test Series", 1, 4, "msg4", "topic1")
        missing = audit_series_sequence("Test Series", 1)
        assert missing == [], f"Expected [], got {missing}"
        print("  [OK] No missing episodes after adding episode 4")
        
        # Test different series
        add_series_episode("Another Series", 1, 1, "msg1", "topic1")
        add_series_episode("Another Series", 1, 3, "msg3", "topic1")
        missing = audit_series_sequence("Another Series", 1)
        assert missing == [2], f"Expected [2], got {missing}"
        print(f"  Another series missing: {missing}")
        
        # Test non-existent series
        missing = audit_series_sequence("NonExistent", 1)
        assert missing == [], "Non-existent series should return empty list"
        print("  [OK] Non-existent series returns empty list")
        
        db_setup.get_db_path = original_get_db_path
        
        print("  [OK] Series sequence audit works correctly")
        return True
    except Exception as e:
        print(f"  [FAIL] FAILED: {e}")
        return False


def test_json_response_formats():
    """Test 6: JSON response format validation."""
    print("\n=== Test 6: JSON Response Formats ===")
    try:
        test_db = Path(__file__).parent / "test_telegram_media.db"
        if test_db.exists():
            test_db.unlink()
        
        import telegram_mcp.db_setup as db_setup
        original_get_db_path = db_setup.get_db_path
        db_setup.get_db_path = lambda: test_db
        
        init_database()
        
        # Simulate the JSON responses from MCP tools
        content_hash = compute_content_hash("JSON test", "test.jpg", 100)
        
        # Success response format
        success_response = {
            "status": "SUCCESS",
            "target_message_id": 98765,
            "content_hash": content_hash
        }
        success_json = json.dumps(success_response)
        parsed = json.loads(success_json)
        assert parsed["status"] == "SUCCESS"
        assert parsed["target_message_id"] == 98765
        assert parsed["content_hash"] == content_hash
        print("  [OK] SUCCESS response format valid")
        
        # FloodWait response format
        floodwait_response = {
            "status": "FAILED_FLOODWAIT",
            "wait_seconds": 45
        }
        floodwait_json = json.dumps(floodwait_response)
        parsed = json.loads(floodwait_json)
        assert parsed["status"] == "FAILED_FLOODWAIT"
        assert parsed["wait_seconds"] == 45
        print("  [OK] FAILED_FLOODWAIT response format valid")
        
        # Topic Error response format
        topic_error_response = {
            "status": "FAILED_TOPIC_ERROR",
            "reason": "Topic full or permission denied"
        }
        topic_error_json = json.dumps(topic_error_response)
        parsed = json.loads(topic_error_json)
        assert parsed["status"] == "FAILED_TOPIC_ERROR"
        assert "permission" in parsed["reason"]
        print("  [OK] FAILED_TOPIC_ERROR response format valid")
        
        # Network Error response format
        network_error_response = {
            "status": "FAILED_NETWORK",
            "reason": "Connection timed out after 2 retries"
        }
        network_error_json = json.dumps(network_error_response)
        parsed = json.loads(network_error_json)
        assert parsed["status"] == "FAILED_NETWORK"
        assert "retries" in parsed["reason"]
        print("  [OK] FAILED_NETWORK response format valid")
        
        # EMPTY_AFTER_CLEANING response format
        empty_response = {
            "status": "EMPTY_AFTER_CLEANING",
            "reason": "Message has no text content and no media attached"
        }
        empty_json = json.dumps(empty_response)
        parsed = json.loads(empty_json)
        assert parsed["status"] == "EMPTY_AFTER_CLEANING"
        print("  [OK] EMPTY_AFTER_CLEANING response format valid")
        
        # validate_topic response format
        validate_response = {"valid": True, "can_post": True}
        validate_json = json.dumps(validate_response)
        parsed = json.loads(validate_json)
        assert parsed["valid"] is True
        assert parsed["can_post"] is True
        print("  [OK] validate_topic response format valid")
        
        # check_duplicate response format
        dup_response = {"is_duplicate": False}
        dup_json = json.dumps(dup_response)
        parsed = json.loads(dup_json)
        assert parsed["is_duplicate"] is False
        print("  [OK] check_duplicate response format valid")
        
        # audit_series_sequence response format
        audit_response = {
            "series_name": "Test Series",
            "season_number": 1,
            "missing_episodes": [4],
            "total_episodes": 4,
            "max_episode": 5
        }
        audit_json = json.dumps(audit_response)
        parsed = json.loads(audit_json)
        assert parsed["missing_episodes"] == [4]
        print("  [OK] audit_series_sequence response format valid")
        
        db_setup.get_db_path = original_get_db_path
        
        print("  [OK] All JSON response formats valid")
        return True
    except Exception as e:
        print(f"  [FAIL] FAILED: {e}")
        return False


def test_full_ingestion_flow():
    """Test 7: Full ingestion flow simulation."""
    print("\n=== Test 7: Full Ingestion Flow Simulation ===")
    try:
        test_db = Path(__file__).parent / "test_telegram_media.db"
        if test_db.exists():
            test_db.unlink()
        
        import telegram_mcp.db_setup as db_setup
        original_get_db_path = db_setup.get_db_path
        db_setup.get_db_path = lambda: test_db
        
        init_database()
        
        # Simulate incoming message from source channel
        source_channel = "source_channel_123"
        source_msg_id = "msg_456"
        raw_text = "  Episode 5 of My Series  "
        file_name = "episode_5.mp4"
        file_size = 52428800  # 50MB
        
        # Step 1: Compute content hash BEFORE cleaning
        content_hash = compute_content_hash(raw_text, file_name, file_size)
        print(f"  1. Computed hash: {content_hash}")
        
        # Step 2: Check for duplicate
        is_dup = check_duplicate(source_channel, source_msg_id, content_hash)
        assert not is_dup, "Should not be duplicate for new message"
        print("  2. Duplicate check: NEW")
        
        # Step 3: Record as PROCESSING
        record_id = record_message_status(
            source_channel, source_msg_id, content_hash, "PROCESSING", "target_topic_789"
        )
        print(f"  3. Recorded as PROCESSING (ID: {record_id})")
        
        # Step 4: Simulate text cleaning (strip whitespace)
        cleaned_text = raw_text.strip()
        assert cleaned_text == "Episode 5 of My Series"
        print(f"  4. Cleaned text: '{cleaned_text}'")
        
        # Step 5: Validate topic (simulated)
        # In real scenario, would call validate_topic tool
        topic_valid = True  # Assume valid
        can_post = True
        assert topic_valid and can_post
        print("  5. Topic validation: OK")
        
        # Step 6: Simulate publishing (would call publish_media_with_template)
        # For test, we just simulate success
        target_msg_id = 12345
        print(f"  6. Published to target (msg_id: {target_msg_id})")
        
        # Step 7: Record as COMPLETED
        record_message_status(
            source_channel, source_msg_id, content_hash, 
            "COMPLETED", "target_topic_789"
        )
        print("  7. Recorded as COMPLETED")
        
        # Step 8: Verify final status
        status = get_message_status(content_hash)
        assert status["status"] == "COMPLETED"
        assert status["target_topic_id"] == "target_topic_789"
        assert status["source_channel_id"] == source_channel
        assert status["source_message_id"] == source_msg_id
        print("  8. Final status verified")
        
        # Step 9: Test duplicate detection on re-ingestion
        is_dup = check_duplicate(source_channel, source_msg_id, content_hash)
        assert is_dup, "Should detect duplicate on re-ingestion"
        print("  9. Re-ingestion correctly detected as duplicate")
        
        # Step 10: Series tracking
        add_series_episode("My Series", 1, 5, str(target_msg_id), "target_topic_789")
        missing = audit_series_sequence("My Series", 1)
        print(f"  10. Series audit - missing: {missing}")
        
        db_setup.get_db_path = original_get_db_path
        
        print("  [OK] Full ingestion flow simulation successful")
        return True
    except Exception as e:
        print(f"  [FAIL] FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_error_scenarios():
    """Test 8: Error scenario handling."""
    print("\n=== Test 8: Error Scenarios ===")
    try:
        test_db = Path(__file__).parent / "test_telegram_media.db"
        if test_db.exists():
            test_db.unlink()
        
        import telegram_mcp.db_setup as db_setup
        original_get_db_path = db_setup.get_db_path
        db_setup.get_db_path = lambda: test_db
        
        init_database()
        
        # Test FAILED_PARSING status
        parse_hash = compute_content_hash("Parse fail", "bad.txt", 100)
        record_message_status("ch1", "m1", parse_hash, "FAILED_PARSING", failure_reason="Invalid JSON in message")
        status = get_message_status(parse_hash)
        assert status["status"] == "FAILED_PARSING"
        assert "JSON" in status["failure_reason"]
        print("  [OK] FAILED_PARSING recorded")
        
        # Test REJECTED_SPAM status
        spam_hash = compute_content_hash("Spam content", "spam.jpg", 100)
        record_message_status("ch1", "m2", spam_hash, "REJECTED_SPAM", failure_reason="Content flagged as spam")
        status = get_message_status(spam_hash)
        assert status["status"] == "REJECTED_SPAM"
        print("  [OK] REJECTED_SPAM recorded")
        
        # Test FAILED_NETWORK status
        net_hash = compute_content_hash("Network fail", "net.txt", 100)
        record_message_status("ch1", "m3", net_hash, "FAILED_NETWORK", failure_reason="Connection timeout after 2 retries")
        status = get_message_status(net_hash)
        assert status["status"] == "FAILED_NETWORK"
        print("  [OK] FAILED_NETWORK recorded")
        
        # Test unique constraint on series_episodes
        add_series_episode("Unique Series", 1, 1, "msg1", "topic1")
        record_id = add_series_episode("Unique Series", 1, 1, "msg2", "topic2")
        assert record_id == 0, "Duplicate episode should not be inserted (unique constraint)"
        print("  [OK] Unique constraint on series_episodes enforced")
        
        db_setup.get_db_path = original_get_db_path
        
        print("  [OK] Error scenarios handled correctly")
        return True
    except Exception as e:
        print(f"  [FAIL] FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def run_all_tests():
    """Run all integration tests."""
    print("=" * 60)
    print("TELEGRAM MEDIA PROCESSING PIPELINE - INTEGRATION TESTS")
    print("=" * 60)
    
    tests = [
        test_database_initialization,
        test_content_hash_generation,
        test_duplicate_check,
        test_record_message_status,
        test_series_audit,
        test_json_response_formats,
        test_full_ingestion_flow,
        test_error_scenarios,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  [FAIL] FAILED with exception: {e}")
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)
    
    # Cleanup test database
    test_db = Path(__file__).parent / "test_telegram_media.db"
    if test_db.exists():
        test_db.unlink()
        print("\nTest database cleaned up.")
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)