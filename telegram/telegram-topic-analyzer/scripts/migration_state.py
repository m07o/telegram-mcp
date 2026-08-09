#!/usr/bin/env python3
"""
Migration State Manager for telegram-topic-analyzer
Handles deduplication, completion tracking, and resume capability.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

MIGRATION_STATE_FILE = Path.home() / "migration_state.json"
MASAS18_TOPICS_FILE = Path.home() / "all_topics_masass18.json"

def load_state():
    """Load migration state from file."""
    if MIGRATION_STATE_FILE.exists():
        with open(MIGRATION_STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        "migrated_topics": [],
        "in_progress": None,
        "last_migrated_id": 0,
        "total_migrated": 0,
        "total_failed": 0,
        "total_verified": 0,
        "total_partial": 0
    }

def save_state(state):
    """Save migration state to file."""
    with open(MIGRATION_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def is_migrated_complete(state, title):
    """Check if a topic is already migrated and verified complete."""
    for t in state['migrated_topics']:
        if t['title'] == title and t['status'] in ('COMPLETE', 'SKIPPED'):
            return True
    return False

def is_migrated_partial(state, title):
    """Check if a topic was partially migrated."""
    for t in state['migrated_topics']:
        if t['title'] == title and t['status'] == 'PARTIAL':
            return True
    return False

def get_topic_status(state, title):
    """Get the migration status of a topic."""
    for t in state['migrated_topics']:
        if t['title'] == title:
            return t['status']
    return 'NOT_STARTED'

def get_next_topic_to_migrate(state):
    """Get the next topic ID to migrate (oldest first, skipping completed)."""
    with open(MASAS18_TOPICS_FILE, 'r', encoding='utf-8') as f:
        masass = json.load(f)
    
    sorted_masass = sorted([(int(tid), t['title']) for tid, t in masass['topics'].items()])
    
    for tid, title in sorted_masass:
        # Skip only if actually completed, not just based on last_migrated_id
        if is_migrated_complete(state, title):
            continue
        return tid, title
    
    return None, None

def mark_in_progress(state, masass18_topic_id, title):
    """Mark a topic as currently being migrated."""
    state['in_progress'] = {
        'masass18_topic_id': masass18_topic_id,
        'title': title,
        'started_at': datetime.utcnow().isoformat()
    }
    save_state(state)

def mark_complete(state, masass18_topic_id, egyxos_topic_id, title, message_count, verified=True):
    """Mark a topic as completely migrated."""
    # Remove any existing entry for this title
    state['migrated_topics'] = [t for t in state['migrated_topics'] if t['title'] != title]
    
    state['migrated_topics'].append({
        'masass18_topic_id': masass18_topic_id,
        'egyxos_topic_id': egyxos_topic_id,
        'title': title,
        'status': 'COMPLETE' if verified else 'PARTIAL',
        'message_count': message_count,
        'migrated_at': datetime.utcnow().isoformat(),
        'verified': verified
    })
    
    state['in_progress'] = None
    state['last_migrated_id'] = max(state['last_migrated_id'], masass18_topic_id)
    state['total_migrated'] += 1
    if verified:
        state['total_verified'] += 1
    else:
        state['total_partial'] += 1
    save_state(state)

def mark_failed(state, masass18_topic_id, title, error):
    """Mark a topic as failed."""
    state['migrated_topics'] = [t for t in state['migrated_topics'] if t['title'] != title]
    
    state['migrated_topics'].append({
        'masass18_topic_id': masass18_topic_id,
        'egyxos_topic_id': None,
        'title': title,
        'status': 'FAILED',
        'message_count': 0,
        'migrated_at': datetime.utcnow().isoformat(),
        'error': str(error)
    })
    
    state['in_progress'] = None
    state['last_migrated_id'] = max(state['last_migrated_id'], masass18_topic_id)
    state['total_failed'] += 1
    save_state(state)

def migration_status():
    """Print migration status summary."""
    state = load_state()
    
    print("=" * 60)
    print("MIGRATION STATUS")
    print("=" * 60)
    print(f"Total Migrated:     {state['total_migrated']}")
    print(f"  Verified Complete: {state['total_verified']}")
    print(f"  Partial:           {state['total_partial']}")
    print(f"Total Failed:        {state['total_failed']}")
    print(f"Last Migrated ID:    {state['last_migrated_id']}")
    
    if state['in_progress']:
        ip = state['in_progress']
        print(f"\n⚠️  IN PROGRESS:")
        print(f"   Topic ID: {ip['masass18_topic_id']}")
        print(f"   Title:    {ip['title']}")
        print(f"   Started:  {ip['started_at']}")
    
    # Show recent migrations
    if state['migrated_topics']:
        print(f"\nRecent Migrations:")
        for t in state['migrated_topics'][-10:]:
            status_icon = "✅" if t['status'] == 'COMPLETE' else ("⚠️" if t['status'] == 'PARTIAL' else "❌")
            print(f"   {status_icon} #{t['masass18_topic_id']}: {t['title']} ({t['status']}, {t.get('message_count', 0)} msgs)")

def verify_topic(title):
    """Verify a specific topic's migration status."""
    state = load_state()
    
    for t in state['migrated_topics']:
        if t['title'] == title:
            print(f"Topic: {title}")
            print(f"  Status: {t['status']}")
            print(f"  Source ID: {t['masass18_topic_id']}")
            print(f"  Dest ID: {t['egyxos_topic_id']}")
            print(f"  Messages: {t['message_count']}")
            print(f"  Migrated: {t['migrated_at']}")
            print(f"  Verified: {t.get('verified', False)}")
            return
    
    print(f"Topic '{title}' not found in migration state")

def reset_topic(title):
    """Reset a topic to NOT_STARTED (for re-migration)."""
    state = load_state()
    original_len = len(state['migrated_topics'])
    state['migrated_topics'] = [t for t in state['migrated_topics'] if t['title'] != title]
    
    if len(state['migrated_topics']) < original_len:
        if any(t['status'] == 'COMPLETE' for t in state['migrated_topics'] if t['title'] == title):
            state['total_verified'] -= 1
        elif any(t['status'] == 'PARTIAL' for t in state['migrated_topics'] if t['title'] == title):
            state['total_partial'] -= 1
        elif any(t['status'] == 'FAILED' for t in state['migrated_topics'] if t['title'] == title):
            state['total_failed'] -= 1
        state['total_migrated'] -= 1
        save_state(state)
        print(f"✅ Reset '{title}' for re-migration")
    else:
        print(f"Topic '{title}' not in migration state")

def resume_migration():
    """Get next topic to migrate."""
    state = load_state()
    
    # Check if there's an interrupted migration
    if state['in_progress']:
        ip = state['in_progress']
        print(f"Interrupted migration detected:")
        print(f"  Topic: #{ip['masass18_topic_id']} - {ip['title']}")
        print(f"  Started: {ip['started_at']}")
        print("  You may want to verify this topic first, then resume.")
        return ip['masass18_topic_id'], ip['title']
    
    # Get next topic
    tid, title = get_next_topic_to_migrate(state)
    if tid:
        print(f"Next topic to migrate:")
        print(f"  Topic ID: {tid}")
        print(f"  Title:    {title}")
        return tid, title
    else:
        print("All topics migrated! 🎉")
        return None, None

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python migration_state.py status")
        print("  python migration_state.py verify <topic_title>")
        print("  python migration_state.py reset <topic_title>")
        print("  python migration_state.py resume")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "status":
        migration_status()
    elif cmd == "verify" and len(sys.argv) > 2:
        verify_topic(sys.argv[2])
    elif cmd == "reset" and len(sys.argv) > 2:
        reset_topic(sys.argv[2])
    elif cmd == "resume":
        resume_migration()
    else:
        print("Unknown command")
        sys.exit(1)