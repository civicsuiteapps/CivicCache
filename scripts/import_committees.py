#!/usr/bin/env python3
"""
Import congressional committee data from the unitedstates/congress-legislators repository.

This script downloads committee data from GitHub and creates/populates:
- congressional_committees: Committee definitions (standing, select, joint, subcommittees)
- member_committee_assignments: Which members serve on which committees

Data sources:
- committees-current.yaml: Committee definitions
- committee-membership-current.yaml: Member assignments

Usage:
    python3 import_committees.py [--dry-run]
"""

import argparse
import os
import sqlite3
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip3 install pyyaml")
    exit(1)

# Configuration
COMMITTEES_URL = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main/committees-current.yaml"
MEMBERSHIP_URL = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main/committee-membership-current.yaml"

# Current congress number
CURRENT_CONGRESS = 119

# Load .env file
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value

DATABASE_PATH = Path(__file__).parent.parent / os.environ.get('DATABASE_PATH', 'data/civic_cache.db')


def download_yaml(url: str, item_name: str = "entries") -> any:
    """Download and parse YAML file from URL."""
    print(f"Downloading: {url}")
    with urllib.request.urlopen(url, timeout=60) as response:
        content = response.read().decode('utf-8')
    print(f"  Downloaded {len(content):,} bytes")
    data = yaml.safe_load(content)
    if isinstance(data, list):
        print(f"  Parsed {len(data)} {item_name}")
    elif isinstance(data, dict):
        print(f"  Parsed {len(data)} {item_name}")
    return data


def create_tables(conn: sqlite3.Connection) -> None:
    """Create committee tables if they don't exist."""

    # Congressional Committees table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS congressional_committees (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            chamber TEXT NOT NULL,
            committee_type TEXT,
            parent_committee_id TEXT,
            jurisdiction TEXT,
            url TEXT,
            is_subcommittee INTEGER DEFAULT 0,
            congress INTEGER DEFAULT 119,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Member Committee Assignments table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS member_committee_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id TEXT NOT NULL,
            committee_id TEXT NOT NULL,
            party_side TEXT,
            rank INTEGER,
            title TEXT,
            is_subcommittee INTEGER DEFAULT 0,
            congress INTEGER DEFAULT 119,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (member_id) REFERENCES congress_members(id),
            UNIQUE(member_id, committee_id, congress)
        )
    """)

    # Create indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_committees_chamber ON congressional_committees(chamber)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_committees_parent ON congressional_committees(parent_committee_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_assignments_member ON member_committee_assignments(member_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_assignments_committee ON member_committee_assignments(committee_id)")

    conn.commit()
    print("Tables and indexes created/verified")


def clear_existing_data(conn: sqlite3.Connection, dry_run: bool = False) -> None:
    """Clear existing committee data for reimport."""
    if not dry_run:
        conn.execute(f"DELETE FROM member_committee_assignments WHERE congress = {CURRENT_CONGRESS}")
        conn.execute(f"DELETE FROM congressional_committees WHERE congress = {CURRENT_CONGRESS}")
        conn.commit()
    print(f"Cleared existing data for Congress {CURRENT_CONGRESS}")


def determine_chamber(committee_type: str, committee_id: str) -> str:
    """Determine chamber from committee type or ID prefix."""
    if committee_type == 'house':
        return 'House'
    elif committee_type == 'senate':
        return 'Senate'
    elif committee_type == 'joint':
        return 'Joint'
    # Fallback: check ID prefix
    if committee_id.startswith('H'):
        return 'House'
    elif committee_id.startswith('S'):
        return 'Senate'
    elif committee_id.startswith('J'):
        return 'Joint'
    return 'Unknown'


def import_committees(conn: sqlite3.Connection, committees_data: List[Dict], dry_run: bool = False) -> Dict:
    """Import committee definitions."""
    stats = {'committees': 0, 'subcommittees': 0}

    for committee in committees_data:
        committee_id = committee.get('thomas_id') or committee.get('id')
        if not committee_id:
            continue

        name = committee.get('name', '')
        committee_type = committee.get('type', '')
        chamber = determine_chamber(committee_type, committee_id)
        jurisdiction = committee.get('jurisdiction')
        url = committee.get('url')

        if not dry_run:
            conn.execute("""
                INSERT OR REPLACE INTO congressional_committees
                (id, name, chamber, committee_type, parent_committee_id, jurisdiction, url, is_subcommittee, congress)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (committee_id, name, chamber, committee_type, None, jurisdiction, url, 0, CURRENT_CONGRESS))

        stats['committees'] += 1

        # Process subcommittees
        for subcommittee in committee.get('subcommittees', []):
            sub_id = subcommittee.get('thomas_id') or subcommittee.get('id')
            if not sub_id:
                continue

            # Full subcommittee ID includes parent
            full_sub_id = f"{committee_id}{sub_id}"
            sub_name = subcommittee.get('name', '')

            if not dry_run:
                conn.execute("""
                    INSERT OR REPLACE INTO congressional_committees
                    (id, name, chamber, committee_type, parent_committee_id, jurisdiction, url, is_subcommittee, congress)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (full_sub_id, sub_name, chamber, 'subcommittee', committee_id, None, None, 1, CURRENT_CONGRESS))

            stats['subcommittees'] += 1

    if not dry_run:
        conn.commit()

    return stats


def import_memberships(conn: sqlite3.Connection, membership_data: Dict, dry_run: bool = False) -> Dict:
    """Import member committee assignments."""
    stats = {'assignments': 0, 'members_not_found': set()}

    # Get existing member IDs for validation
    cursor = conn.execute("SELECT id FROM congress_members")
    valid_members = {row[0] for row in cursor.fetchall()}

    for committee_id, members in membership_data.items():
        # Determine if this is a subcommittee (contains a subcommittee ID after the main committee ID)
        # Main committee IDs are like "HSAG", subcommittees are like "HSAG04"
        is_subcommittee = 0
        base_committee_id = committee_id

        # Check if this looks like a subcommittee ID (main ID + numeric suffix)
        # Main IDs are 4 chars for House/Senate, subcommittees have additional chars
        if len(committee_id) > 4 and committee_id[:4].isalpha():
            # Check if last part is numeric (subcommittee number)
            potential_sub_num = committee_id[4:]
            if potential_sub_num.isdigit():
                is_subcommittee = 1

        for member in members:
            bioguide_id = member.get('bioguide')
            if not bioguide_id:
                continue

            if bioguide_id not in valid_members:
                stats['members_not_found'].add(bioguide_id)
                continue

            party = member.get('party')
            rank = member.get('rank')
            title = member.get('title')

            if not dry_run:
                try:
                    conn.execute("""
                        INSERT OR REPLACE INTO member_committee_assignments
                        (member_id, committee_id, party_side, rank, title, is_subcommittee, congress)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (bioguide_id, committee_id, party, rank, title, is_subcommittee, CURRENT_CONGRESS))
                    stats['assignments'] += 1
                except sqlite3.IntegrityError:
                    pass  # Skip duplicates
            else:
                stats['assignments'] += 1

    if not dry_run:
        conn.commit()

    return stats


def main():
    parser = argparse.ArgumentParser(description='Import congressional committee data')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without updating database')
    args = parser.parse_args()

    print("=" * 70)
    print("CONGRESSIONAL COMMITTEE DATA IMPORT")
    print("=" * 70)
    print(f"Database: {DATABASE_PATH}")
    print(f"Congress: {CURRENT_CONGRESS}")
    print(f"Dry run: {args.dry_run}")
    print()

    if not DATABASE_PATH.exists():
        print(f"ERROR: Database not found at {DATABASE_PATH}")
        return 1

    # Connect to database
    conn = sqlite3.connect(DATABASE_PATH)

    # Create tables
    create_tables(conn)
    print()

    # Clear existing data
    clear_existing_data(conn, args.dry_run)
    print()

    # Download committee definitions
    committees_data = download_yaml(COMMITTEES_URL, "committees")
    print()

    # Download membership data
    membership_data = download_yaml(MEMBERSHIP_URL, "committee memberships")
    print()

    # Import committees
    print("Importing committees...")
    committee_stats = import_committees(conn, committees_data, args.dry_run)
    print(f"  Imported {committee_stats['committees']} committees")
    print(f"  Imported {committee_stats['subcommittees']} subcommittees")
    print()

    # Import memberships
    print("Importing member assignments...")
    membership_stats = import_memberships(conn, membership_data, args.dry_run)
    print(f"  Imported {membership_stats['assignments']} assignments")
    if membership_stats['members_not_found']:
        print(f"  Skipped {len(membership_stats['members_not_found'])} members not in database")
    print()

    conn.close()

    # Summary
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Committees: {committee_stats['committees']}")
    print(f"Subcommittees: {committee_stats['subcommittees']}")
    print(f"Total committee entities: {committee_stats['committees'] + committee_stats['subcommittees']}")
    print(f"Member assignments: {membership_stats['assignments']}")

    if args.dry_run:
        print()
        print("DRY RUN - No changes were made. Run without --dry-run to apply changes.")

    return 0


if __name__ == "__main__":
    exit(main())
