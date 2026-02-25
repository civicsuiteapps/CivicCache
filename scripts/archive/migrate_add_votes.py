#!/usr/bin/env python3
"""
Database migration to add vote tracking tables and session column.

This migration:
1. Adds session column to legislation table
2. Populates session values based on introduced_date
3. Creates roll_call_votes table for vote metadata
4. Creates member_votes table for individual member votes
5. Creates necessary indexes
"""

import sqlite3
from pathlib import Path

DB_PATH = Path("data/civic_cache.db")


def create_connection() -> sqlite3.Connection:
    """Create and return a database connection."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found at {DB_PATH}")

    conn = sqlite3.Connection(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def add_session_column(conn: sqlite3.Connection) -> None:
    """Add session column to legislation table and populate values."""
    print("\n" + "=" * 60)
    print("ADDING SESSION COLUMN TO LEGISLATION")
    print("=" * 60)

    # Check if column already exists
    cursor = conn.execute("PRAGMA table_info(legislation)")
    columns = [row[1] for row in cursor.fetchall()]

    if 'session' in columns:
        print("  Session column already exists, skipping...")
        return

    # Add the column
    print("  Adding session column...")
    conn.execute("ALTER TABLE legislation ADD COLUMN session INTEGER")

    # Create index for session
    print("  Creating index on session...")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_legislation_session ON legislation(congress, session)")

    # Populate session based on introduced_date
    # Odd year = session 1, Even year = session 2
    print("  Populating session values...")
    conn.execute("""
        UPDATE legislation
        SET session = CASE
            WHEN CAST(strftime('%Y', introduced_date) AS INTEGER) % 2 = 1 THEN 1
            ELSE 2
        END
        WHERE introduced_date IS NOT NULL
    """)

    conn.commit()

    # Verify
    result = conn.execute("""
        SELECT session, COUNT(*) as count
        FROM legislation
        WHERE congress = 119
        GROUP BY session
    """).fetchall()

    print("  Session distribution for 119th Congress:")
    for session, count in result:
        print(f"    Session {session}: {count:,} bills")

    print("  Session column added successfully")


def create_vote_tables(conn: sqlite3.Connection) -> None:
    """Create roll_call_votes and member_votes tables."""
    print("\n" + "=" * 60)
    print("CREATING VOTE TRACKING TABLES")
    print("=" * 60)

    # Check if tables already exist
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('roll_call_votes', 'member_votes')"
    ).fetchall()
    existing = [t[0] for t in tables]

    if 'roll_call_votes' not in existing:
        print("  Creating roll_call_votes table...")
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
        print("    Created roll_call_votes table")
    else:
        print("  roll_call_votes table already exists")

    if 'member_votes' not in existing:
        print("  Creating member_votes table...")
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
        print("    Created member_votes table")
    else:
        print("  member_votes table already exists")

    conn.commit()
    print("  Vote tables created successfully")


def create_vote_indexes(conn: sqlite3.Connection) -> None:
    """Create indexes for vote tables."""
    print("\n" + "=" * 60)
    print("CREATING VOTE INDEXES")
    print("=" * 60)

    indexes = [
        ("idx_votes_congress", "roll_call_votes(congress)"),
        ("idx_votes_chamber", "roll_call_votes(chamber)"),
        ("idx_votes_session", "roll_call_votes(congress, session)"),
        ("idx_votes_date", "roll_call_votes(vote_date)"),
        ("idx_votes_legislation", "roll_call_votes(legislation_id)"),
        ("idx_member_votes_roll", "member_votes(roll_call_id)"),
        ("idx_member_votes_bioguide", "member_votes(bioguide_id)"),
        ("idx_member_votes_position", "member_votes(vote_position)"),
    ]

    for index_name, index_def in indexes:
        try:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {index_def}")
            print(f"    Created index: {index_name}")
        except sqlite3.Error as e:
            print(f"    Index {index_name} error: {e}")

    conn.commit()
    print("  Indexes created successfully")


def verify_migration(conn: sqlite3.Connection) -> None:
    """Verify the migration was successful."""
    print("\n" + "=" * 60)
    print("MIGRATION VERIFICATION")
    print("=" * 60)

    # Check legislation session column
    cursor = conn.execute("PRAGMA table_info(legislation)")
    columns = [row[1] for row in cursor.fetchall()]
    has_session = 'session' in columns
    print(f"  Legislation session column: {'Yes' if has_session else 'No'}")

    # Check vote tables
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('roll_call_votes', 'member_votes')"
    ).fetchall()
    print(f"  Vote tables created: {len(tables)}/2")

    # Check indexes
    indexes = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_votes%' OR name LIKE 'idx_member_votes%'"
    ).fetchall()
    print(f"  Vote indexes created: {len(indexes)}")

    print("\n  Migration verification complete")


def main():
    """Run the migration."""
    print("=" * 60)
    print("DATABASE MIGRATION - ADD VOTE TRACKING")
    print("=" * 60)

    try:
        conn = create_connection()

        add_session_column(conn)
        create_vote_tables(conn)
        create_vote_indexes(conn)
        verify_migration(conn)

        conn.close()

        print("\n" + "=" * 60)
        print("MIGRATION COMPLETE")
        print("=" * 60)

        return 0

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
