#!/usr/bin/env python3
"""
Import congressional district boundary data into CivicCache database.

This script:
1. Reads district boundary data from JSON file
2. Maps FIPS codes to state codes
3. Imports district polygons into the database
4. Creates indexes for efficient spatial queries
"""

import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional

try:
    from config import DB_PATH as _CFG_DB
except ImportError:
    _CFG_DB = Path("data/civic_cache.db")

from utils import create_connection

# Database location
DB_PATH = _CFG_DB

# Source data file -- prefer env var, fall back to well-known local paths
_civicscope_data = os.environ.get("CIVICSCOPE_DATA_PATH", "")
if _civicscope_data:
    DISTRICT_JSON_PATH = Path(_civicscope_data).expanduser() / "DistrictData.json"
else:
    _candidates = [
        Path(__file__).parent.parent / "data" / "DistrictData.json",
        Path(__file__).parent.parent / "data" / "raw" / "congress" / "DistrictData.json",
        Path.home() / "GIT/CivicScope/CivicScope/Resources/DistrictData.json",
    ]
    DISTRICT_JSON_PATH = next((p for p in _candidates if p.exists()), _candidates[0])

# FIPS to state code mapping
FIPS_TO_STATE = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA",
    "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
    "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN",
    "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
    "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS",
    "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY", "60": "AS", "66": "GU", "69": "MP", "72": "PR",
    "78": "VI"
}



def import_districts(conn: sqlite3.Connection) -> None:
    """Import district boundary data from JSON file."""
    print(f"\nReading district data from: {DISTRICT_JSON_PATH}")

    if not DISTRICT_JSON_PATH.exists():
        raise FileNotFoundError(f"District JSON file not found at {DISTRICT_JSON_PATH}")

    with open(DISTRICT_JSON_PATH, 'r') as f:
        data = json.load(f)

    metadata = data.get('metadata', {})
    districts = data.get('districts', [])

    print(f"Found {len(districts)} districts to import")
    print(f"Expected: {metadata.get('expected_count', 'unknown')}")
    print(f"Data source: {metadata.get('data_source', 'unknown')}")
    print(f"Generated: {metadata.get('generation_date', 'unknown')}")

    # Clear existing district data
    print("\nClearing existing district data...")
    conn.execute("DELETE FROM congressional_districts")
    conn.commit()

    # Import districts
    print("\nImporting districts...")
    imported_count = 0

    for district in districts:
        state_fips = district.get('state')
        district_code = district.get('district_code')
        name = district.get('name')
        geoid = district.get('geoid')
        center_lat = district.get('center_latitude')
        center_lon = district.get('center_longitude')
        population = district.get('population', 0)
        polygons = district.get('polygons', [])

        # Map FIPS to state code
        state_code = FIPS_TO_STATE.get(state_fips)

        # Parse district number (00 for at-large)
        try:
            district_number = int(district_code) if district_code != "00" else 0
        except ValueError:
            district_number = 0

        # Store polygons as JSON
        polygons_json = json.dumps(polygons)

        try:
            conn.execute("""
                INSERT INTO congressional_districts (
                    state_fips, state_code, district_code, district_number,
                    name, geoid, center_latitude, center_longitude,
                    population, polygons
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                state_fips, state_code, district_code, district_number,
                name, geoid, center_lat, center_lon, population, polygons_json
            ))
            imported_count += 1

            if imported_count % 50 == 0:
                print(f"  Imported {imported_count} districts...")
                conn.commit()
        except sqlite3.IntegrityError as e:
            print(f"  Warning: Skipping duplicate district {geoid}: {e}")

    conn.commit()
    print(f"\n✓ Successfully imported {imported_count} districts")


def create_indexes(conn: sqlite3.Connection) -> None:
    """Create indexes for efficient querying."""
    print("\nCreating indexes...")

    # Index on state and district for lookups
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_districts_state_district
        ON congressional_districts(state_code, district_number)
    """)

    # Index on FIPS code
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_districts_fips
        ON congressional_districts(state_fips)
    """)

    # Index on geoid for unique lookups
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_districts_geoid
        ON congressional_districts(geoid)
    """)

    conn.commit()
    print("✓ Indexes created")


def update_metadata(conn: sqlite3.Connection) -> None:
    """Update database metadata with district count."""
    print("\nUpdating metadata...")

    # Count districts
    result = conn.execute("SELECT COUNT(*) FROM congressional_districts").fetchone()
    district_count = result[0] if result else 0

    # Update or insert metadata
    conn.execute("""
        INSERT OR REPLACE INTO metadata (key, value, updated_at)
        VALUES ('total_districts', ?, CURRENT_TIMESTAMP)
    """, (str(district_count),))

    conn.commit()
    print(f"✓ Metadata updated (total_districts: {district_count})")


def verify_import(conn: sqlite3.Connection) -> None:
    """Verify the import was successful."""
    print("\n" + "="*60)
    print("IMPORT VERIFICATION")
    print("="*60)

    # Count total districts
    result = conn.execute("SELECT COUNT(*) FROM congressional_districts").fetchone()
    total = result[0] if result else 0
    print(f"Total districts: {total}")

    # Count by state
    result = conn.execute("""
        SELECT state_code, COUNT(*)
        FROM congressional_districts
        GROUP BY state_code
        ORDER BY state_code
    """).fetchall()

    print(f"\nDistricts by state: {len(result)} states/territories")

    # Show a few examples
    print("\nSample districts:")
    samples = conn.execute("""
        SELECT state_code, district_number, name, geoid, center_latitude, center_longitude
        FROM congressional_districts
        ORDER BY state_code, district_number
        LIMIT 5
    """).fetchall()

    for state, district, name, geoid, lat, lon in samples:
        print(f"  {state}-{district:02d}: {name} (geoid: {geoid}, center: {lat:.4f}, {lon:.4f})")

    print("\n✓ Import verification complete")


def main():
    """Main execution function."""
    print("="*60)
    print("CONGRESSIONAL DISTRICT BOUNDARY IMPORT")
    print("="*60)

    try:
        # Connect to database
        conn = create_connection()

        # Import districts
        import_districts(conn)

        # Create indexes
        create_indexes(conn)

        # Update metadata
        update_metadata(conn)

        # Verify import
        verify_import(conn)

        conn.close()

        print("\n" + "="*60)
        print("✓ IMPORT COMPLETE")
        print("="*60)

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
