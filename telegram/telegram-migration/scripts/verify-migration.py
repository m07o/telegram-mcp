#!/usr/bin/env python3
"""
Standalone migration verification script.

Usage:
    python scripts/verify-migration.py --job-id masass18_to_egyxos_2026
    python scripts/verify-migration.py --job-id masass18_to_egyxos_2026 --topic-id 1234
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

def load_ref_map(job_id: str):
    """Load ref_map entries for a job."""
    ref_dir = Path.home() / ".cache" / "telegram-mcp" / "jobs" / "refs"
    ref_file = ref_dir / f"{job_id}.json"
    if ref_file.exists():
        with open(ref_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def load_migration_state():
    """Load migration state."""
    state_file = Path.home() / "migration_state.json"
    if state_file.exists():
        with open(state_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def verify_topic(job_id: str, source_topic_id: int, source_chat: int, target_chat: int, tolerance: int = 5):
    """Verify a single topic's migration sync."""
    # This would call the MCP verify_topic_sync tool
    # For standalone script, we'd need to connect to MCP or use Telethon directly
    print(f"Verification for topic {source_topic_id}:")
    print("  (Requires MCP server running - call verify_topic_sync via MCP)")
    return {}

def print_job_stats(job_id: str):
    """Print migration statistics for a job."""
    ref_map = load_ref_map(job_id)
    
    if not ref_map:
        print(f"No ref_map data for job: {job_id}")
        return
    
    total_entries = sum(len(entries) for entries in ref_map.values())
    jobs = list(ref_map.keys())
    
    print(f"\n=== Job: {job_id} ===")
    print(f"Total message mappings: {total_entries}")
    print(f"Source topics tracked: {len(jobs)}")
    
    for job, entries in ref_map.items():
        print(f"\n  Job: {job}")
        print(f"    Entries: {len(entries)}")
        if entries:
            src_topics = set(e.get('source_topic_id') for e in entries if 'source_topic_id' in e)
            print(f"    Source topics: {len(src_topics)}")
            print(f"    Dest topics: {len(set(e.get('dest_topic_id') for e in entries if 'dest_topic_id' in e))}")

def print_migration_state():
    """Print migration state summary."""
    state = load_migration_state()
    
    print("\n=== Migration State ===")
    print(f"Total migrated: {state.get('total_migrated', 0)}")
    print(f"Verified: {state.get('total_verified', 0)}")
    print(f"Partial: {state.get('total_partial', 0)}")
    print(f"Failed: {state.get('total_failed', 0)}")
    print(f"Last migrated ID: {state.get('last_migrated_id', 0)}")
    
    if state.get('in_progress'):
        ip = state['in_progress']
        print(f"\n  IN PROGRESS: #{ip['masass18_topic_id']} - {ip['title']}")
    
    # Count by status
    statuses = {}
    for t in state.get('migrated_topics', []):
        status = t.get('status', 'UNKNOWN')
        statuses[status] = statuses.get(status, 0) + 1
    
    print("\n  By Status:")
    for status, count in sorted(statuses.items()):
        print(f"    {status}: {count}")

def main():
    parser = argparse.ArgumentParser(description="Verify Telegram migration")
    parser.add_argument("--job-id", default="masass18_to_egyxos_2026", help="Migration job ID")
    parser.add_argument("--topic-id", type=int, help="Verify specific topic")
    parser.add_argument("--source-chat", type=int, default=-1002191043427)
    parser.add_argument("--target-chat", type=int, default=-1002204837936)
    parser.add_argument("--tolerance", type=int, default=5)
    parser.add_argument("--stats-only", action="store_true", help="Show only statistics")
    
    args = parser.parse_args()
    
    print_migration_state()
    print_job_stats(args.job_id)
    
    if args.topic_id:
        verify_topic(args.job_id, args.topic_id, args.source_chat, args.target_chat, args.tolerance)
    
    if not args.stats_only:
        print("\nNote: Full verification requires MCP server running.")
        print("Use: mcp__telegram_mcp__verify_topic_sync(...) via MCP client")

if __name__ == "__main__":
    main()