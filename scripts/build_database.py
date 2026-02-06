#!/usr/bin/env python3
"""
Build the CivicCache SQLite database from extracted data.

This script:
1. Creates the database schema
2. Imports Congressional member data
3. Imports state reference data
4. Imports FEC candidate and committee data (sampled for mobile)
5. Creates full-text search indexes
6. Adds metadata and statistics
"""

import json
import sqlite3
import csv
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional

# Database location
DB_PATH = Path("data/civic_cache.db")

# Data directories
CONGRESS_DATA_DIR = Path("data/raw/congress")
FEC_DATA_DIR = Path("data/raw/fec")


def create_connection() -> sqlite3.Connection:
    """Create and return a database connection."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Remove existing database if it exists
    if DB_PATH.exists():
        print(f"Removing existing database: {DB_PATH}")
        DB_PATH.unlink()

    conn = sqlite3.Connection(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all database tables and indexes."""
    print("\nCreating database schema...")

    # States table
    conn.execute("""
        CREATE TABLE states (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            fips_code TEXT,
            is_territory INTEGER DEFAULT 0,
            region TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Congress members table
    conn.execute("""
        CREATE TABLE congress_members (
            id TEXT PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            full_name TEXT NOT NULL,
            nickname TEXT,
            middle_name TEXT,
            suffix TEXT,

            chamber TEXT NOT NULL,
            state_code TEXT,
            state_name TEXT NOT NULL,
            district TEXT,

            current_party TEXT NOT NULL,

            office_address TEXT,
            phone TEXT,
            website_url TEXT,
            contact_form_url TEXT,

            twitter_handle TEXT,
            facebook_url TEXT,
            youtube_url TEXT,

            birth_year INTEGER,
            gender TEXT,

            term_start_date TEXT,
            term_end_date TEXT,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (state_code) REFERENCES states(code)
        )
    """)

    # Committees table
    conn.execute("""
        CREATE TABLE committees (
            committee_id TEXT PRIMARY KEY,
            committee_name TEXT NOT NULL,
            committee_type TEXT,
            committee_type_full TEXT,
            committee_designation TEXT,
            committee_designation_full TEXT,

            party_code TEXT,
            party_full_name TEXT,

            total_receipts REAL,
            total_disbursements REAL,
            cash_on_hand REAL,
            coverage_end_date TEXT,

            connected_org_name TEXT,
            treasurer_name TEXT,

            street_1 TEXT,
            street_2 TEXT,
            city TEXT,
            state TEXT,
            zip TEXT,

            filing_frequency TEXT,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Candidates table
    conn.execute("""
        CREATE TABLE candidates (
            candidate_id TEXT PRIMARY KEY,
            candidate_name TEXT NOT NULL,

            office TEXT,
            office_state TEXT,
            office_district TEXT,

            party_code TEXT,
            party_full_name TEXT,
            incumbent_challenger_status TEXT,
            candidate_status TEXT,

            election_year INTEGER NOT NULL,
            election_cycle INTEGER NOT NULL,

            total_receipts REAL,
            total_disbursements REAL,
            cash_on_hand REAL,
            total_contributions REAL,
            candidate_contributions REAL,

            principal_committee_id TEXT,

            mailing_address_1 TEXT,
            mailing_address_2 TEXT,
            mailing_city TEXT,
            mailing_state TEXT,
            mailing_zip TEXT,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (principal_committee_id) REFERENCES committees(committee_id)
        )
    """)

    # Candidate-Committee linkages
    conn.execute("""
        CREATE TABLE candidate_committee_linkages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT NOT NULL,
            committee_id TEXT NOT NULL,
            linkage_type TEXT,
            election_year INTEGER,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id),
            FOREIGN KEY (committee_id) REFERENCES committees(committee_id),

            UNIQUE(candidate_id, committee_id, election_year)
        )
    """)

    # Roll call votes table
    conn.execute("""
        CREATE TABLE roll_call_votes (
            id TEXT PRIMARY KEY,
            congress INTEGER NOT NULL,
            chamber TEXT NOT NULL,
            session INTEGER NOT NULL,
            roll_number INTEGER NOT NULL,

            vote_date TEXT NOT NULL,
            vote_time TEXT,
            vote_question TEXT,
            vote_question_text TEXT,
            vote_type TEXT,
            vote_result TEXT,
            vote_result_text TEXT,

            yea_count INTEGER DEFAULT 0,
            nay_count INTEGER DEFAULT 0,
            present_count INTEGER DEFAULT 0,
            not_voting_count INTEGER DEFAULT 0,
            required_majority TEXT,

            legislation_id TEXT,
            amendment_number TEXT,
            source_url TEXT,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(congress, chamber, session, roll_number),
            FOREIGN KEY (legislation_id) REFERENCES legislation(id)
        )
    """)

    # Member votes table
    conn.execute("""
        CREATE TABLE member_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roll_call_id TEXT NOT NULL,
            bioguide_id TEXT NOT NULL,
            vote_position TEXT NOT NULL,
            member_name TEXT,
            party TEXT,
            state TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(roll_call_id, bioguide_id),
            FOREIGN KEY (roll_call_id) REFERENCES roll_call_votes(id),
            FOREIGN KEY (bioguide_id) REFERENCES congress_members(id)
        )
    """)

    # Congressional staff table
    conn.execute("""
        CREATE TABLE congressional_staff (
            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            full_name TEXT NOT NULL,
            suffix TEXT,
            title TEXT NOT NULL,
            title_normalized TEXT,
            office_type TEXT NOT NULL,
            chamber TEXT,
            member_bioguide_id TEXT,
            member_name TEXT,
            office_location TEXT,
            committee_code TEXT,
            committee_name TEXT,
            subcommittee_name TEXT,
            party_affiliation TEXT,
            institutional_office TEXT,
            congress_number INTEGER NOT NULL,
            source_document TEXT,
            source_section TEXT,
            effective_date TEXT,
            created_at TEXT,
            updated_at TEXT,
            is_current INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (member_bioguide_id) REFERENCES congress_members(id) ON DELETE CASCADE
        )
    """)

    # Metadata table
    conn.execute("""
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    print("✓ Schema created")


def create_indexes(conn: sqlite3.Connection) -> None:
    """Create all database indexes."""
    print("\nCreating indexes...")

    indexes = [
        "CREATE INDEX idx_members_chamber ON congress_members(chamber)",
        "CREATE INDEX idx_members_state ON congress_members(state_code)",
        "CREATE INDEX idx_members_party ON congress_members(current_party)",
        "CREATE INDEX idx_members_district ON congress_members(state_code, district)",

        "CREATE INDEX idx_candidates_office ON candidates(office)",
        "CREATE INDEX idx_candidates_year ON candidates(election_year)",
        "CREATE INDEX idx_candidates_state ON candidates(office_state)",
        "CREATE INDEX idx_candidates_party ON candidates(party_code)",
        "CREATE INDEX idx_candidates_cycle ON candidates(election_cycle)",

        "CREATE INDEX idx_committees_type ON committees(committee_type)",
        "CREATE INDEX idx_committees_party ON committees(party_code)",
        "CREATE INDEX idx_committees_state ON committees(state)",

        "CREATE INDEX idx_linkages_candidate ON candidate_committee_linkages(candidate_id)",
        "CREATE INDEX idx_linkages_committee ON candidate_committee_linkages(committee_id)",

        "CREATE INDEX idx_votes_congress ON roll_call_votes(congress)",
        "CREATE INDEX idx_votes_chamber ON roll_call_votes(chamber)",
        "CREATE INDEX idx_votes_session ON roll_call_votes(congress, session)",
        "CREATE INDEX idx_votes_date ON roll_call_votes(vote_date)",
        "CREATE INDEX idx_votes_legislation ON roll_call_votes(legislation_id)",
        "CREATE INDEX idx_member_votes_roll ON member_votes(roll_call_id)",
        "CREATE INDEX idx_member_votes_bioguide ON member_votes(bioguide_id)",
        "CREATE INDEX idx_member_votes_position ON member_votes(vote_position)",

        "CREATE INDEX idx_staff_member ON congressional_staff(member_bioguide_id)",
        "CREATE INDEX idx_staff_committee ON congressional_staff(committee_code)",
        "CREATE INDEX idx_staff_name ON congressional_staff(last_name, first_name)",
        "CREATE INDEX idx_staff_title ON congressional_staff(title_normalized)",
        "CREATE INDEX idx_staff_congress ON congressional_staff(congress_number)",
        "CREATE INDEX idx_staff_current ON congressional_staff(is_current)",
    ]

    for idx_sql in indexes:
        conn.execute(idx_sql)

    print(f"✓ Created {len(indexes)} indexes")


def create_fts_tables(conn: sqlite3.Connection) -> None:
    """Create full-text search tables."""
    print("\nCreating full-text search indexes...")

    # Congress members FTS
    conn.execute("""
        CREATE VIRTUAL TABLE congress_members_fts USING fts5(
            member_id UNINDEXED,
            full_name,
            first_name,
            last_name,
            state_name
        )
    """)

    # Populate FTS from members
    conn.execute("""
        INSERT INTO congress_members_fts(member_id, full_name, first_name, last_name, state_name)
        SELECT id, full_name, first_name, last_name, state_name
        FROM congress_members
    """)

    # Candidates FTS
    conn.execute("""
        CREATE VIRTUAL TABLE candidates_fts USING fts5(
            candidate_id UNINDEXED,
            candidate_name,
            office_state,
            party_full_name
        )
    """)

    # Populate FTS from candidates
    conn.execute("""
        INSERT INTO candidates_fts(candidate_id, candidate_name, office_state, party_full_name)
        SELECT candidate_id, candidate_name, office_state, party_full_name
        FROM candidates
    """)

    # Committees FTS
    conn.execute("""
        CREATE VIRTUAL TABLE committees_fts USING fts5(
            committee_id UNINDEXED,
            committee_name,
            connected_org_name,
            party_full_name
        )
    """)

    # Populate FTS from committees
    conn.execute("""
        INSERT INTO committees_fts(committee_id, committee_name, connected_org_name, party_full_name)
        SELECT committee_id, committee_name, connected_org_name, party_full_name
        FROM committees
    """)

    print("✓ Full-text search indexes created")


def import_states(conn: sqlite3.Connection) -> int:
    """Import state reference data."""
    print("\nImporting states data...")

    states_file = CONGRESS_DATA_DIR / "states_seed.json"
    if not states_file.exists():
        print("  ✗ states_seed.json not found")
        return 0

    with open(states_file, 'r') as f:
        data = json.load(f)

    # Handle both list and dict formats
    if isinstance(data, dict) and 'states_and_territories' in data:
        states_list = data['states_and_territories']
    elif isinstance(data, list):
        states_list = data
    else:
        print(f"  ✗ Unexpected format: {type(data)}")
        return 0

    count = 0
    for state in states_list:
        conn.execute("""
            INSERT INTO states (code, name, fips_code, is_territory)
            VALUES (?, ?, ?, ?)
        """, (
            state.get('state_abbr'),
            state.get('state_name'),
            state.get('state_code'),
            1 if state.get('is_territory', False) else 0
        ))
        count += 1

    conn.commit()
    print(f"  ✓ Imported {count} states/territories")
    return count


def import_congress_members(conn: sqlite3.Connection) -> int:
    """Import Congressional member data."""
    print("\nImporting Congressional members...")

    members_file = CONGRESS_DATA_DIR / "119th-congress-members.json"
    if not members_file.exists():
        print("  ✗ 119th-congress-members.json not found")
        return 0

    with open(members_file, 'r') as f:
        data = json.load(f)

    members = data.get('members', [])
    count = 0

    # Get state abbreviation from state name (for foreign key)
    state_map = {}
    cursor = conn.execute("SELECT name, code FROM states")
    for row in cursor:
        state_map[row[0]] = row[1]

    for member in members:
        # Map state name to state code
        state_name = member.get('state')
        state_code = state_map.get(state_name, None)

        # Try some common alternate names if not found
        if not state_code and state_name:
            # Try mapping common alternatives
            alt_names = {
                'U.S. Virgin Islands': 'US Virgin Islands',
            }
            alt_name = alt_names.get(state_name, state_name)
            state_code = state_map.get(alt_name, None)

        # Create full name
        first_name = member.get('first_name', '')
        last_name = member.get('last_name', '')
        full_name = f"{first_name} {last_name}".strip()

        conn.execute("""
            INSERT INTO congress_members (
                id, first_name, last_name, full_name,
                chamber, state_code, state_name, district,
                current_party
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            member.get('bioguide_id'),
            first_name,
            last_name,
            full_name,
            member.get('chamber'),
            state_code,
            state_name,
            str(member.get('district')) if member.get('district') is not None else None,
            member.get('party')
        ))
        count += 1

    conn.commit()
    print(f"  ✓ Imported {count} members")
    return count


def import_fec_candidates(conn: sqlite3.Connection, limit: Optional[int] = None) -> int:
    """Import FEC candidate data (sampled for mobile).

    Args:
        limit: Maximum number of candidates to import per file (None = all)
    """
    print("\nImporting FEC candidates...")

    candidates_dir = FEC_DATA_DIR / "All-Candidates"
    if not candidates_dir.exists():
        print("  ✗ All-Candidates directory not found")
        return 0

    total_count = 0

    # Process text files (pipe-delimited)
    for file_path in sorted(candidates_dir.glob("*.txt")):
        count = 0
        print(f"  Processing {file_path.name}...")

        with open(file_path, 'r', encoding='latin-1') as f:
            for line in f:
                if limit and count >= limit:
                    break

                fields = line.strip().split('|')
                if len(fields) < 15:
                    continue

                try:
                    conn.execute("""
                        INSERT OR REPLACE INTO candidates (
                            candidate_id, candidate_name,
                            office, office_state, office_district,
                            party_code, incumbent_challenger_status,
                            candidate_status, election_year, election_cycle,
                            total_receipts, total_disbursements, cash_on_hand
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        fields[0],  # candidate_id
                        fields[1],  # candidate_name
                        fields[2],  # office
                        fields[3],  # office_state
                        fields[4],  # office_district
                        fields[5],  # party_code
                        fields[6],  # incumbent_challenger_status
                        fields[7],  # candidate_status
                        int(fields[8]) if fields[8] else None,  # election_year
                        int(fields[9]) if fields[9] else None,  # election_cycle
                        float(fields[14]) if fields[14] else None,  # total_receipts
                        float(fields[15]) if fields[15] else None,  # total_disbursements
                        float(fields[16]) if fields[16] else None,  # cash_on_hand
                    ))
                    count += 1
                except (ValueError, IndexError, sqlite3.Error) as e:
                    # Skip malformed records
                    continue

        print(f"    ✓ Imported {count} candidates from {file_path.name}")
        total_count += count

        conn.commit()

    print(f"  ✓ Total candidates imported: {total_count}")
    return total_count


def add_metadata(conn: sqlite3.Connection, stats: Dict[str, int]) -> None:
    """Add database metadata and statistics."""
    print("\nAdding metadata...")

    metadata_items = [
        ('schema_version', '1.0.0'),
        ('database_created', datetime.now().isoformat()),
        ('data_source', 'CivicScope v1.0'),
        ('congress_session', '119'),
        ('fec_data_range', '1979-2026'),
        ('total_states', str(stats.get('states', 0))),
        ('total_members', str(stats.get('members', 0))),
        ('total_candidates', str(stats.get('candidates', 0))),
        ('total_committees', str(stats.get('committees', 0))),
    ]

    for key, value in metadata_items:
        conn.execute("""
            INSERT INTO metadata (key, value) VALUES (?, ?)
        """, (key, value))

    conn.commit()
    print("  ✓ Metadata added")


def print_statistics(conn: sqlite3.Connection) -> None:
    """Print database statistics."""
    print("\n" + "=" * 60)
    print("Database Statistics")
    print("=" * 60)

    tables = [
        'states',
        'congress_members',
        'candidates',
        'committees',
        'candidate_committee_linkages'
    ]

    for table in tables:
        cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"  {table:30} {count:>10,} rows")

    # Database size
    db_size = DB_PATH.stat().st_size
    print(f"\n  Database size: {db_size / (1024*1024):.2f} MB")


def main():
    """Main database build process."""
    print("=" * 60)
    print("CivicCache - Database Builder")
    print("=" * 60)

    # Check data directories exist
    if not CONGRESS_DATA_DIR.exists():
        print(f"\n✗ ERROR: Congress data not found: {CONGRESS_DATA_DIR}")
        print("  Run extract_congress_data.py first")
        return 1

    if not FEC_DATA_DIR.exists():
        print(f"\n✗ ERROR: FEC data not found: {FEC_DATA_DIR}")
        print("  Run extract_fec_data.py first")
        return 1

    # Create database connection
    print(f"\nCreating database: {DB_PATH}")
    conn = create_connection()

    try:
        # Create schema
        create_schema(conn)

        # Import data
        stats = {}
        stats['states'] = import_states(conn)
        stats['members'] = import_congress_members(conn)

        # Import sampled FEC data (limit for mobile performance)
        # Change limit=None to import all candidates
        stats['candidates'] = import_fec_candidates(conn, limit=1000)
        stats['committees'] = 0  # TODO: Implement committee import

        # Create indexes and FTS
        create_indexes(conn)
        create_fts_tables(conn)

        # Add metadata
        add_metadata(conn, stats)

        # Print statistics
        print_statistics(conn)

        print("\n" + "=" * 60)
        print("Database Build Complete!")
        print("=" * 60)
        print(f"\nDatabase location: {DB_PATH.absolute()}")

        return 0

    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        conn.close()


if __name__ == "__main__":
    exit(main())
