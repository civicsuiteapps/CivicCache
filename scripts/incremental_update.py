#!/usr/bin/env python3
"""
Smart incremental update for CivicCache
Checks database for latest data and only fetches what's needed
"""

import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR.parent / "data" / "civic_cache.db"

def print_banner(title):
    """Print a formatted banner"""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)

def print_info(label, value):
    """Print formatted info line"""
    print(f"  • {label:30s}: {value}")

def get_latest_bill_date():
    """Get the date of the most recent bill in the database"""
    if not DB_PATH.exists():
        print("Database doesn't exist - need full rebuild")
        return None

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Get most recent bill update date
        cursor.execute("""
            SELECT MAX(updateDate)
            FROM legislation
            WHERE updateDate IS NOT NULL
        """)

        result = cursor.fetchone()
        conn.close()

        if result and result[0]:
            return result[0]
        return None
    except Exception as e:
        print(f"Error checking database: {e}")
        return None

def get_database_stats():
    """Get current database statistics"""
    if not DB_PATH.exists():
        return None

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        stats = {}

        # Get member count
        cursor.execute("SELECT COUNT(*) FROM congress_members")
        stats['total_members'] = cursor.fetchone()[0]

        # Get legislation count
        cursor.execute("SELECT COUNT(*) FROM legislation")
        stats['total_legislation'] = cursor.fetchone()[0]

        # Get latest bill update
        cursor.execute("SELECT MAX(updateDate) FROM legislation WHERE updateDate IS NOT NULL")
        stats['latest_bill_date'] = cursor.fetchone()[0]

        # Get roll call vote count
        try:
            cursor.execute("SELECT COUNT(*) FROM roll_call_votes")
            stats['total_roll_calls'] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM member_votes")
            stats['total_member_votes'] = cursor.fetchone()[0]
        except sqlite3.OperationalError:
            stats['total_roll_calls'] = 0
            stats['total_member_votes'] = 0

        # Get staff count
        try:
            cursor.execute("SELECT COUNT(*) FROM congressional_staff")
            stats['total_staff'] = cursor.fetchone()[0]
        except sqlite3.OperationalError:
            stats['total_staff'] = 0

        conn.close()
        return stats
    except Exception as e:
        print(f"⚠️  Error reading database stats: {e}")
        return None

def main():
    print_banner("CIVICCACHE SMART INCREMENTAL UPDATE")

    print("\n📋 COMMAND: python3 scripts/incremental_update.py")
    print(f"📁 DATABASE: {DB_PATH}")
    print(f"🕐 STARTED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Check if database exists
    if not DB_PATH.exists():
        print("\n❌ ERROR: Database not found")
        print("   Please run full rebuild first:")
        print("   → python3 scripts/build_database.py")
        print("   → python3 scripts/import_legislation.py --congress 119 --all")
        sys.exit(1)

    # Get database stats
    print_banner("DATABASE STATUS")
    stats = get_database_stats()

    if not stats:
        print("\n❌ ERROR: Could not read database")
        sys.exit(1)

    print_info("Total Members", f"{stats['total_members']:,}")
    print_info("Total Legislation", f"{stats['total_legislation']:,}")
    print_info("Total Roll Call Votes", f"{stats.get('total_roll_calls', 0):,}")
    print_info("Total Member Votes", f"{stats.get('total_member_votes', 0):,}")
    print_info("Total Staff Records", f"{stats.get('total_staff', 0):,}")
    print_info("Latest Bill Update", stats.get('latest_bill_date', 'N/A'))

    # Current Congress number (119th = 2025-2026)
    current_congress = 119

    print_banner("INCREMENTAL UPDATE PLAN")
    print_info("Target Congress", f"{current_congress}th (2025-2026)")
    print_info("Strategy", "Run all import scripts with existing data handling")

    # List of scripts to run (in order, skipping missing ones)
    scripts_to_run = [
        ("import_congress_legislators.py", [], "Import/update legislators"),
        ("import_member_contact_info.py", [], "Import member contact info"),
        ("import_member_images.py", [], "Import member portraits"),
        ("import_member_positions.py", [], "Update member positions/activity"),
        ("import_legislation.py", ["--congress", str(current_congress)], f"Import {current_congress}th Congress bills"),
        ("import_votes.py", ["--congress", str(current_congress)], f"Import {current_congress}th Congress roll call votes"),
        ("import_congressional_staff.py", ["--import-house-sod"], "Import House staff (Statement of Disbursements)"),
        ("import_fec_committees.py", [], "Import FEC committee data"),
    ]

    print("\n  Scripts to execute:")
    for i, (name, args, desc) in enumerate(scripts_to_run, 1):
        script_path = SCRIPT_DIR / name
        status = "✓" if script_path.exists() else "✗"
        print(f"    {i}. [{status}] {name:35s} - {desc}")

    # Execute scripts
    completed = 0
    for script_name, args, description in scripts_to_run:
        script_path = SCRIPT_DIR / script_name

        if not script_path.exists():
            print_banner(f"⏭️  SKIPPING: {script_name}")
            print(f"   File not found: {script_path}")
            continue

        print_banner(f"STAGE {completed + 1}: {description}")

        cmd = ["python3", str(script_path)] + args
        print(f"  COMMAND: {' '.join(cmd)}")
        print(f"  WORKING DIR: {SCRIPT_DIR.parent}\n")

        result = subprocess.run(
            cmd,
            cwd=SCRIPT_DIR.parent,
            stdout=sys.stdout,
            stderr=sys.stderr
        )

        if result.returncode != 0:
            print_banner(f"❌ FAILED: {script_name}")
            print(f"   Exit code: {result.returncode}")
            sys.exit(result.returncode)

        completed += 1
        print(f"\n  ✅ Completed {completed}/{len(scripts_to_run)}\n")

    # Get final stats
    print_banner("✅ ALL INCREMENTAL UPDATES COMPLETED")

    final_stats = get_database_stats()
    if final_stats:
        print("\n  BEFORE:")
        print_info("  Members", f"{stats['total_members']:,}")
        print_info("  Legislation", f"{stats['total_legislation']:,}")
        print_info("  Roll Call Votes", f"{stats.get('total_roll_calls', 0):,}")
        print_info("  Member Votes", f"{stats.get('total_member_votes', 0):,}")
        print_info("  Staff Records", f"{stats.get('total_staff', 0):,}")

        print("\n  AFTER:")
        print_info("  Members", f"{final_stats['total_members']:,}")
        print_info("  Legislation", f"{final_stats['total_legislation']:,}")
        print_info("  Roll Call Votes", f"{final_stats.get('total_roll_calls', 0):,}")
        print_info("  Member Votes", f"{final_stats.get('total_member_votes', 0):,}")
        print_info("  Staff Records", f"{final_stats.get('total_staff', 0):,}")

        print("\n  CHANGES:")
        member_diff = final_stats['total_members'] - stats['total_members']
        leg_diff = final_stats['total_legislation'] - stats['total_legislation']
        vote_diff = final_stats.get('total_roll_calls', 0) - stats.get('total_roll_calls', 0)
        staff_diff = final_stats.get('total_staff', 0) - stats.get('total_staff', 0)
        print_info("  Members added", f"+{member_diff}" if member_diff >= 0 else str(member_diff))
        print_info("  Legislation added", f"+{leg_diff}" if leg_diff >= 0 else str(leg_diff))
        print_info("  Roll calls added", f"+{vote_diff}" if vote_diff >= 0 else str(vote_diff))
        print_info("  Staff added", f"+{staff_diff}" if staff_diff >= 0 else str(staff_diff))

    print(f"\n🕐 FINISHED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    sys.exit(0)

if __name__ == "__main__":
    main()
