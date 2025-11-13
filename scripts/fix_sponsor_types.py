#!/usr/bin/env python3
"""
Fix sponsor type distinction in legislation_sponsors table.

This script:
1. Adds a new 'is_primary_sponsor' column
2. Marks the first sponsor for each bill as the primary sponsor
3. All other sponsors are marked as cosponsors
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


def add_primary_sponsor_column(conn: sqlite3.Connection) -> None:
    """Add is_primary_sponsor column to legislation_sponsors table."""
    print("Adding is_primary_sponsor column...")

    try:
        conn.execute("""
            ALTER TABLE legislation_sponsors
            ADD COLUMN is_primary_sponsor INTEGER DEFAULT 0
        """)
        print("✓ Column added")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("⊘ Column already exists")
        else:
            raise


def mark_primary_sponsors(conn: sqlite3.Connection) -> int:
    """Mark the first sponsor for each bill as the primary sponsor."""
    print("\nMarking primary sponsors...")

    # Get the first sponsor ID for each legislation_id (ordered by insertion)
    primary_sponsors = conn.execute("""
        SELECT MIN(id) as primary_id, legislation_id
        FROM legislation_sponsors
        GROUP BY legislation_id
    """).fetchall()

    print(f"Found {len(primary_sponsors)} bills with sponsors")

    # Update primary sponsors
    updated = 0
    for primary_id, legislation_id in primary_sponsors:
        conn.execute("""
            UPDATE legislation_sponsors
            SET is_primary_sponsor = 1
            WHERE id = ?
        """, (primary_id,))
        updated += 1

    conn.commit()
    return updated


def verify_fix(conn: sqlite3.Connection) -> None:
    """Verify the sponsor type fix."""
    print("\n" + "="*60)
    print("VERIFICATION")
    print("="*60)

    # Count primary sponsors vs cosponsors
    result = conn.execute("""
        SELECT
            SUM(CASE WHEN is_primary_sponsor = 1 THEN 1 ELSE 0 END) as primary_count,
            SUM(CASE WHEN is_primary_sponsor = 0 THEN 1 ELSE 0 END) as cosponsor_count
        FROM legislation_sponsors
    """).fetchone()

    primary_count, cosponsor_count = result
    print(f"Primary sponsors: {primary_count:,}")
    print(f"Cosponsors: {cosponsor_count:,}")
    print(f"Total: {primary_count + cosponsor_count:,}")

    # Check that each bill has exactly one primary sponsor
    result = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT legislation_id, SUM(is_primary_sponsor) as primary_count
            FROM legislation_sponsors
            GROUP BY legislation_id
            HAVING primary_count != 1
        )
    """).fetchone()

    bills_with_wrong_sponsor_count = result[0]
    if bills_with_wrong_sponsor_count > 0:
        print(f"\n⚠️  Warning: {bills_with_wrong_sponsor_count} bills have != 1 primary sponsor")
    else:
        print("\n✓ All bills have exactly 1 primary sponsor")

    # Sample bills
    print("\nSample bills with sponsors:")
    samples = conn.execute("""
        SELECT
            l.id,
            l.title,
            ps.full_name as primary_sponsor,
            COUNT(cs.id) as cosponsor_count
        FROM legislation l
        JOIN legislation_sponsors ps ON l.id = ps.legislation_id AND ps.is_primary_sponsor = 1
        LEFT JOIN legislation_sponsors cs ON l.id = cs.legislation_id AND cs.is_primary_sponsor = 0
        WHERE l.bill_type = 'hr'
        GROUP BY l.id
        ORDER BY RANDOM()
        LIMIT 5
    """).fetchall()

    for leg_id, title, primary, cosponsor_count in samples:
        print(f"\n  {leg_id}: {title[:50]}...")
        print(f"    Primary: {primary}")
        print(f"    Cosponsors: {cosponsor_count}")


def main():
    """Main execution function."""
    print("="*60)
    print("FIX SPONSOR TYPE DISTINCTION")
    print("="*60)

    try:
        conn = create_connection()

        # Add column
        add_primary_sponsor_column(conn)

        # Mark primary sponsors
        updated = mark_primary_sponsors(conn)
        print(f"✓ Updated {updated:,} primary sponsors")

        # Verify
        verify_fix(conn)

        conn.close()

        print("\n" + "="*60)
        print("✓ FIX COMPLETE")
        print("="*60)

        return 0

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
