#!/usr/bin/env python3
"""
Import candidate photos from Wikidata into race_candidates.

Uses the Wikidata SPARQL endpoint to find all entities with both:
  - P1839 (FEC candidate ID)
  - P18 (image)

Then downloads the Wikimedia Commons images and stores them as BLOBs
in race_candidates.image_blob, matching the pattern used for
congress_members.image_blob.

Usage:
    python3 scripts/import_candidate_photos.py
    python3 scripts/import_candidate_photos.py --cycle 2026
    python3 scripts/import_candidate_photos.py --dry-run
"""

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from config import DB_PATH as _CFG_DB
except ImportError:
    _CFG_DB = Path("data/civic_cache.db")

DB_PATH = _CFG_DB

from utils import download_image

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "CivicCache/1.0 (civic research project; https://github.com/civiccache)"

# Wikimedia thumbnail URL -- resize to 225px wide (matches unitedstates/images)
THUMB_WIDTH = 225


# ---------------------------------------------------------------------------
# Wikidata SPARQL
# ---------------------------------------------------------------------------


def fetch_wikidata_photos() -> dict[str, str]:
    """Fetch all FEC candidate IDs with photos from Wikidata.

    Returns dict mapping FEC candidate_id -> Wikimedia Commons image URL.
    """
    query = """
    SELECT ?fec_id ?image WHERE {
      ?person wdt:P1839 ?fec_id .
      ?person wdt:P18 ?image .
    }
    """

    url = SPARQL_ENDPOINT + "?" + urllib.parse.urlencode(
        {"query": query, "format": "json"}
    )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    print("  Querying Wikidata SPARQL for FEC candidates with photos...")
    resp = urllib.request.urlopen(req, timeout=60)
    data = json.loads(resp.read())

    photos = {}
    for binding in data["results"]["bindings"]:
        fec_id = binding["fec_id"]["value"]
        image_url = binding["image"]["value"]
        photos[fec_id] = image_url

    print(f"  Found {len(photos)} FEC candidates with Wikidata photos")
    return photos


def commons_thumb_url(file_page_url: str, width: int = THUMB_WIDTH) -> str:
    """Convert a Wikimedia Commons Special:FilePath URL to a thumbnail URL.

    Input:  http://commons.wikimedia.org/wiki/Special:FilePath/John%20Boehner%20official%20portrait.jpg
    Output: https://commons.wikimedia.org/w/thumb.php?f=John%20Boehner%20official%20portrait.jpg&w=225
    """
    # Extract filename from the URL
    filename = file_page_url.rsplit("/", 1)[-1]
    return f"https://commons.wikimedia.org/w/thumb.php?f={filename}&w={width}"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def get_race_candidate_ids(conn: sqlite3.Connection, cycle: int | None) -> set[str]:
    """Get all candidate_ids in race_candidates, optionally filtered by cycle."""
    if cycle:
        rows = conn.execute(
            "SELECT DISTINCT candidate_id FROM race_candidates WHERE election_cycle = ?",
            (cycle,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT candidate_id FROM race_candidates"
        ).fetchall()
    return {row[0] for row in rows}


def store_photo(
    conn: sqlite3.Connection, candidate_id: str, image_url: str, image_blob: bytes
) -> int:
    """Store photo BLOB for all race_candidates rows with this candidate_id.

    A candidate can appear in multiple races (across cycles), so update all rows.
    Returns number of rows updated.
    """
    result = conn.execute(
        """
        UPDATE race_candidates
        SET image_url = ?, image_blob = ?
        WHERE candidate_id = ?
        """,
        (image_url, image_blob, candidate_id),
    )
    return result.rowcount


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Import candidate photos from Wikidata")
    parser.add_argument(
        "--cycle",
        type=int,
        default=None,
        help="Only update candidates in this election cycle (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be downloaded without actually doing it",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help=f"Database path (default: {DB_PATH})",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("CivicCache - Candidate Photo Import (Wikidata)")
    print("=" * 60)
    print(f"  Database: {args.db}")
    print(f"  Cycle:    {args.cycle or 'all'}")

    if not args.db.exists():
        print(f"\nERROR: Database not found: {args.db}")
        return 1

    # Phase 1: Fetch Wikidata mapping
    print("\n--- Phase 1: Wikidata SPARQL Query ---")
    wikidata_photos = fetch_wikidata_photos()

    # Phase 2: Cross-reference with our candidates
    print("\n--- Phase 2: Cross-reference with database ---")
    conn = sqlite3.connect(str(args.db))
    our_candidates = get_race_candidate_ids(conn, args.cycle)
    print(f"  race_candidates in DB: {len(our_candidates)}")

    # Find overlap
    matchable = our_candidates & set(wikidata_photos.keys())
    print(f"  Matched with Wikidata photos: {len(matchable)}")

    if args.dry_run:
        # Show sample matches
        print("\n  Sample matches (dry run):")
        for i, cid in enumerate(sorted(matchable)[:20]):
            url = wikidata_photos[cid]
            fname = url.rsplit("/", 1)[-1][:50]
            print(f"    {cid}  ->  {fname}")
        if len(matchable) > 20:
            print(f"    ... and {len(matchable) - 20} more")
        conn.close()
        return 0

    # Phase 3: Download and store photos
    print(f"\n--- Phase 3: Downloading {len(matchable)} photos ---")

    stats = {"downloaded": 0, "failed": 0, "rows_updated": 0, "skipped": 0}
    sorted_ids = sorted(matchable)

    for i, cid in enumerate(sorted_ids, 1):
        source_url = wikidata_photos[cid]
        thumb_url = commons_thumb_url(source_url)

        # Progress
        if i % 100 == 0 or i == len(sorted_ids):
            print(
                f"  Progress: {i}/{len(sorted_ids)} "
                f"({stats['downloaded']} downloaded, {stats['failed']} failed)"
            )

        image_data = download_image(thumb_url, user_agent=USER_AGENT)

        if image_data:
            rows = store_photo(conn, cid, source_url, image_data)
            stats["downloaded"] += 1
            stats["rows_updated"] += rows
        else:
            # Try full-size URL as fallback
            image_data = download_image(source_url, user_agent=USER_AGENT)
            if image_data:
                rows = store_photo(conn, cid, source_url, image_data)
                stats["downloaded"] += 1
                stats["rows_updated"] += rows
            else:
                stats["failed"] += 1

        # Commit every 50 photos
        if stats["downloaded"] % 50 == 0:
            conn.commit()

        # Rate limit: 100ms between requests
        if i % 10 == 0:
            time.sleep(1)

    conn.commit()

    # Phase 4: Also populate principal_committee_id while we're here
    print("\n--- Phase 4: Backfilling principal_committee_id ---")
    result = conn.execute("""
        UPDATE race_candidates
        SET principal_committee_id = (
            SELECT c.principal_committee_id
            FROM candidates c
            WHERE c.candidate_id = race_candidates.candidate_id
              AND c.election_cycle = race_candidates.election_cycle
        )
        WHERE principal_committee_id IS NULL
    """)
    conn.commit()
    print(f"  Updated {result.rowcount} rows with principal_committee_id")

    # Verification
    print("\n--- Verification ---")
    total_with_photos = conn.execute(
        "SELECT COUNT(*) FROM race_candidates WHERE image_blob IS NOT NULL"
    ).fetchone()[0]
    total_rows = conn.execute("SELECT COUNT(*) FROM race_candidates").fetchone()[0]

    photo_size = conn.execute(
        """
        SELECT AVG(LENGTH(image_blob)), MIN(LENGTH(image_blob)),
               MAX(LENGTH(image_blob)), SUM(LENGTH(image_blob))
        FROM race_candidates WHERE image_blob IS NOT NULL
        """
    ).fetchone()

    print(f"  race_candidates with photos: {total_with_photos} / {total_rows}")

    if photo_size[0]:
        print(f"  Average photo size: {photo_size[0]/1024:.1f} KB")
        print(f"  Min: {photo_size[1]/1024:.1f} KB, Max: {photo_size[2]/1024:.1f} KB")
        print(f"  Total storage: {photo_size[3]/1024/1024:.1f} MB")

    if args.cycle:
        cycle_photos = conn.execute(
            "SELECT COUNT(*) FROM race_candidates WHERE election_cycle = ? AND image_blob IS NOT NULL",
            (args.cycle,),
        ).fetchone()[0]
        cycle_total = conn.execute(
            "SELECT COUNT(*) FROM race_candidates WHERE election_cycle = ?",
            (args.cycle,),
        ).fetchone()[0]
        print(f"  Cycle {args.cycle}: {cycle_photos} / {cycle_total} with photos")

    conn.close()

    print("\n" + "=" * 60)
    print("Import Complete")
    print("=" * 60)
    print(f"  Photos downloaded: {stats['downloaded']}")
    print(f"  Failed downloads:  {stats['failed']}")
    print(f"  DB rows updated:   {stats['rows_updated']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
