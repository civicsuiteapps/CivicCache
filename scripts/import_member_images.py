#!/usr/bin/env python3
"""
Import Congress member images into CivicCache database.

This script:
1. Reads member data from JSON including image URLs
2. Downloads member portrait images from congress.gov
3. Stores both URLs and image blobs in the database
"""

import json
import os
import sqlite3
import urllib.request
import time
from pathlib import Path
from typing import Dict, List, Any, Optional

try:
    from config import DB_PATH as _CFG_DB, CONGRESS_NUMBER as _CFG_CONGRESS
except ImportError:
    _CFG_DB = Path("data/civic_cache.db")
    _CFG_CONGRESS = 119

from utils import create_connection, download_image

# Database location
DB_PATH = _CFG_DB

# Source data file -- prefer env var, fall back to well-known local paths
_civicscope_data = os.environ.get("CIVICSCOPE_DATA_PATH", "")
if _civicscope_data:
    MEMBERS_JSON = Path(_civicscope_data).expanduser() / f"{_CFG_CONGRESS}th-congress-members.json"
else:
    # Try common local paths
    _candidates = [
        Path(__file__).parent.parent / "data" / f"{_CFG_CONGRESS}th-congress-members.json",
        Path(__file__).parent.parent / "data" / "raw" / "congress" / f"{_CFG_CONGRESS}th-congress-members.json",
        Path.home() / "GIT/CivicScope/CivicScope/Resources" / f"{_CFG_CONGRESS}th-congress-members.json",
    ]
    MEMBERS_JSON = next((p for p in _candidates if p.exists()), _candidates[0])


def import_member_images(conn: sqlite3.Connection) -> Dict[str, int]:
    """Import member images from JSON and download/store them."""
    print(f"\nReading member data from: {MEMBERS_JSON}")

    if not MEMBERS_JSON.exists():
        raise FileNotFoundError(f"Members JSON not found: {MEMBERS_JSON}")

    with open(MEMBERS_JSON, 'r') as f:
        data = json.load(f)

    members = data.get('members', [])
    print(f"Found {len(members)} members")

    stats = {
        'total': len(members),
        'with_url': 0,
        'downloaded': 0,
        'stored': 0,
        'failed': 0,
        'skipped_no_url': 0
    }

    for i, member in enumerate(members, 1):
        bioguide_id = member.get('bioguide_id')
        first_name = member.get('first_name', '')
        last_name = member.get('last_name', '')
        image_url = member.get('image_url')

        if not bioguide_id:
            continue

        # Progress indicator
        if i % 50 == 0 or i == len(members):
            print(f"  Processing {i}/{len(members)} members...")

        if not image_url:
            stats['skipped_no_url'] += 1
            continue

        stats['with_url'] += 1

        # Download image
        image_data = download_image(image_url)

        if image_data:
            stats['downloaded'] += 1

            # Store in database
            try:
                conn.execute("""
                    UPDATE congress_members
                    SET image_url = ?, image_blob = ?
                    WHERE id = ?
                """, (image_url, image_data, bioguide_id))

                stats['stored'] += 1

                # Commit every 25 members to avoid memory issues
                if stats['stored'] % 25 == 0:
                    conn.commit()

            except Exception as e:
                print(f"  ⚠️  Failed to store image for {first_name} {last_name} ({bioguide_id}): {e}")
                stats['failed'] += 1
        else:
            stats['failed'] += 1

        # Small delay to be respectful to congress.gov servers
        if i % 10 == 0:
            time.sleep(0.5)

    # Final commit
    conn.commit()

    return stats


def verify_import(conn: sqlite3.Connection) -> None:
    """Verify the image import."""
    print("\n" + "="*60)
    print("IMPORT VERIFICATION")
    print("="*60)

    # Count members with images
    result = conn.execute("""
        SELECT
            COUNT(*) as total,
            COUNT(image_url) as with_url,
            COUNT(image_blob) as with_blob
        FROM congress_members
    """).fetchone()

    total, with_url, with_blob = result
    print(f"Total members: {total}")
    print(f"Members with image URL: {with_url}")
    print(f"Members with image blob: {with_blob}")

    # Calculate storage
    result = conn.execute("""
        SELECT
            AVG(LENGTH(image_blob)) as avg_size,
            MIN(LENGTH(image_blob)) as min_size,
            MAX(LENGTH(image_blob)) as max_size,
            SUM(LENGTH(image_blob)) as total_size
        FROM congress_members
        WHERE image_blob IS NOT NULL
    """).fetchone()

    if result and result[0]:
        avg_size, min_size, max_size, total_size = result
        print(f"\nImage storage:")
        print(f"  Average size: {avg_size/1024:.1f} KB")
        print(f"  Min size: {min_size/1024:.1f} KB")
        print(f"  Max size: {max_size/1024:.1f} KB")
        print(f"  Total size: {total_size/1024/1024:.2f} MB")

    # Sample members with images
    print("\nSample members with images:")
    samples = conn.execute("""
        SELECT full_name, chamber, state_name,
               LENGTH(image_blob) as blob_size
        FROM congress_members
        WHERE image_blob IS NOT NULL
        LIMIT 5
    """).fetchall()

    for name, chamber, state, blob_size in samples:
        print(f"  {name} ({chamber}, {state}): {blob_size/1024:.1f} KB")

    print("\n✓ Import verification complete")


def main():
    """Main execution function."""
    print("="*60)
    print("CONGRESS MEMBER IMAGE IMPORT")
    print("="*60)

    try:
        # Connect to database
        conn = create_connection()

        # Import images
        print("\nDownloading and importing images...")
        stats = import_member_images(conn)

        # Print statistics
        print("\n" + "="*60)
        print("IMPORT STATISTICS")
        print("="*60)
        print(f"Total members: {stats['total']}")
        print(f"Members with image URLs: {stats['with_url']}")
        print(f"Images downloaded: {stats['downloaded']}")
        print(f"Images stored in DB: {stats['stored']}")
        print(f"Failed downloads: {stats['failed']}")
        print(f"Skipped (no URL): {stats['skipped_no_url']}")

        # Verify import
        verify_import(conn)

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
