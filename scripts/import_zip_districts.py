#!/usr/bin/env python3
"""
Import ZIP code to Congressional District mappings.

This script:
1. Reads the zccd.csv file from the us-zipcodes-congress repository
2. Creates a zip_districts table in the database
3. Imports all ZIP-to-district mappings

Data source: https://github.com/OpenSourceActivismTech/us-zipcodes-congress
Updated for the 119th Congress (2025-2027)
"""

import csv
import sqlite3
from pathlib import Path
from typing import Dict, List, Tuple
from collections import defaultdict

try:
    from config import DB_PATH as _CFG_DB
except ImportError:
    _CFG_DB = Path("data/civic_cache.db")

from utils import create_connection

# Database location
DB_PATH = _CFG_DB

# Source CSV file
CSV_PATH = Path("data/raw/zip_to_district/zccd.csv")



def create_zip_districts_table(conn: sqlite3.Connection) -> None:
    """Create the zip_districts table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS zip_districts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zip_code TEXT NOT NULL,
            state_fips TEXT NOT NULL,
            state_abbr TEXT NOT NULL,
            district INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(zip_code, state_abbr, district)
        )
    """)

    # Create indexes for fast lookups
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_zip_districts_zip
        ON zip_districts(zip_code)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_zip_districts_state_district
        ON zip_districts(state_abbr, district)
    """)

    conn.commit()
    print("✓ Created zip_districts table")


def load_csv_data(csv_path: Path) -> List[Dict]:
    """Load and parse the CSV file."""
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found at {csv_path}")

    records = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append({
                'state_fips': row['state_fips'],
                'state_abbr': row['state_abbr'],
                'zip_code': row['zcta'],
                'district': int(row['cd'])
            })

    return records


def import_zip_districts(conn: sqlite3.Connection, records: List[Dict]) -> Dict[str, int]:
    """Import ZIP-to-district mappings into the database."""
    stats = {
        'inserted': 0,
        'skipped': 0,
        'errors': 0
    }

    # Clear existing data
    conn.execute("DELETE FROM zip_districts")
    print("✓ Cleared existing zip_districts data")

    # Insert in batches for performance
    batch_size = 1000
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        try:
            conn.executemany("""
                INSERT INTO zip_districts (zip_code, state_fips, state_abbr, district)
                VALUES (:zip_code, :state_fips, :state_abbr, :district)
            """, batch)
            stats['inserted'] += len(batch)
        except sqlite3.IntegrityError as e:
            # Handle duplicates individually
            for record in batch:
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO zip_districts (zip_code, state_fips, state_abbr, district)
                        VALUES (:zip_code, :state_fips, :state_abbr, :district)
                    """, record)
                    stats['inserted'] += 1
                except Exception:
                    stats['errors'] += 1

        # Progress indicator
        if (i + batch_size) % 10000 == 0:
            print(f"  Processed {i + batch_size:,} records...")

    conn.commit()
    return stats


def analyze_data(conn: sqlite3.Connection) -> None:
    """Analyze the imported data and print statistics."""
    print("\n" + "="*60)
    print("DATA ANALYSIS")
    print("="*60)

    # Total records
    result = conn.execute("SELECT COUNT(*) FROM zip_districts").fetchone()
    print(f"Total mappings: {result[0]:,}")

    # Unique ZIP codes
    result = conn.execute("SELECT COUNT(DISTINCT zip_code) FROM zip_districts").fetchone()
    total_zips = result[0]
    print(f"Unique ZIP codes: {total_zips:,}")

    # ZIPs with single district
    result = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT zip_code FROM zip_districts
            GROUP BY zip_code HAVING COUNT(*) = 1
        )
    """).fetchone()
    single_district = result[0]
    print(f"ZIPs with single district: {single_district:,} ({single_district/total_zips*100:.1f}%)")

    # ZIPs with multiple districts
    result = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT zip_code FROM zip_districts
            GROUP BY zip_code HAVING COUNT(*) > 1
        )
    """).fetchone()
    multi_district = result[0]
    print(f"ZIPs with multiple districts: {multi_district:,} ({multi_district/total_zips*100:.1f}%)")

    # Max districts per ZIP
    result = conn.execute("""
        SELECT zip_code, COUNT(*) as district_count
        FROM zip_districts
        GROUP BY zip_code
        ORDER BY district_count DESC
        LIMIT 5
    """).fetchall()
    print(f"\nZIPs with most districts:")
    for zip_code, count in result:
        print(f"  {zip_code}: {count} districts")

    # States coverage
    result = conn.execute("""
        SELECT state_abbr, COUNT(DISTINCT zip_code) as zip_count
        FROM zip_districts
        GROUP BY state_abbr
        ORDER BY zip_count DESC
        LIMIT 10
    """).fetchall()
    print(f"\nTop 10 states by ZIP count:")
    for state, count in result:
        print(f"  {state}: {count:,} ZIPs")

    # Sample lookups
    print(f"\nSample ZIP lookups:")
    sample_zips = ['10001', '90210', '60601', '02101', '33139']
    for zip_code in sample_zips:
        result = conn.execute("""
            SELECT state_abbr, district FROM zip_districts
            WHERE zip_code = ?
            ORDER BY state_abbr, district
        """, (zip_code,)).fetchall()
        if result:
            districts = [f"{state}-{dist}" for state, dist in result]
            print(f"  {zip_code}: {', '.join(districts)}")
        else:
            print(f"  {zip_code}: Not found")


def verify_member_coverage(conn: sqlite3.Connection) -> None:
    """Verify that ZIP districts can be matched to congress members."""
    print("\n" + "="*60)
    print("MEMBER COVERAGE VERIFICATION")
    print("="*60)

    # Check if we can join with congress_members
    result = conn.execute("""
        SELECT z.state_abbr, z.district, COUNT(DISTINCT z.zip_code) as zip_count,
               cm.full_name, cm.id
        FROM zip_districts z
        LEFT JOIN congress_members cm
            ON z.state_abbr = cm.state_code
            AND CAST(z.district AS TEXT) = cm.district
            AND cm.chamber = 'House'
        GROUP BY z.state_abbr, z.district
        ORDER BY z.state_abbr, z.district
        LIMIT 20
    """).fetchall()

    matched = 0
    unmatched = 0
    print(f"\nSample district-to-member matches:")
    for state, district, zip_count, name, member_id in result[:10]:
        if name:
            print(f"  {state}-{district}: {zip_count:,} ZIPs → {name}")
            matched += 1
        else:
            print(f"  {state}-{district}: {zip_count:,} ZIPs → NO MEMBER FOUND")
            unmatched += 1

    # Total coverage stats
    result = conn.execute("""
        SELECT COUNT(DISTINCT z.state_abbr || '-' || z.district) as total_districts,
               COUNT(DISTINCT CASE WHEN cm.id IS NOT NULL THEN z.state_abbr || '-' || z.district END) as matched_districts
        FROM zip_districts z
        LEFT JOIN congress_members cm
            ON z.state_abbr = cm.state_code
            AND CAST(z.district AS TEXT) = cm.district
            AND cm.chamber = 'House'
    """).fetchone()

    total, matched = result
    print(f"\nTotal unique state-district pairs: {total}")
    print(f"Matched to House members: {matched} ({matched/total*100:.1f}%)")


def main():
    """Main execution function."""
    print("="*60)
    print("ZIP CODE TO CONGRESSIONAL DISTRICT IMPORT")
    print("="*60)
    print(f"Source: {CSV_PATH}")
    print(f"Database: {DB_PATH}")

    try:
        # Connect to database
        conn = create_connection()

        # Create table
        create_zip_districts_table(conn)

        # Load CSV data
        print(f"\nLoading CSV data from {CSV_PATH}...")
        records = load_csv_data(CSV_PATH)
        print(f"✓ Loaded {len(records):,} records from CSV")

        # Import data
        print("\nImporting data...")
        stats = import_zip_districts(conn, records)
        print(f"✓ Inserted {stats['inserted']:,} records")
        if stats['errors'] > 0:
            print(f"⚠ Errors: {stats['errors']}")

        # Analyze data
        analyze_data(conn)

        # Verify member coverage
        verify_member_coverage(conn)

        conn.close()

        print("\n" + "="*60)
        print("✓ IMPORT COMPLETE")
        print("="*60)

        return 0

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
