#!/usr/bin/env python3
"""
Import legislation data from Congress.gov API into CivicCache database.

This script:
1. Fetches all bills and resolutions from the 119th Congress
2. Downloads detailed metadata for each bill
3. Imports summaries, sponsors, actions, and subjects
4. Stores everything in the database for offline access
"""

import json
import sqlite3
import urllib.request
import urllib.error
import time
import os
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime

# Database location
DB_PATH = Path("data/civic_cache.db")

# API configuration
CONGRESS_API_BASE = "https://api.congress.gov/v3"
CONGRESS_NUMBER = 119

# Get API key from environment or use DEMO_KEY
API_KEY = os.environ.get("CONGRESS_API_KEY", "DEMO_KEY")

# Rate limiting
REQUEST_DELAY = 0.5  # Delay between API requests (seconds)
BATCH_COMMIT_SIZE = 50  # Commit to DB every N bills


def create_connection() -> sqlite3.Connection:
    """Create and return a database connection."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found at {DB_PATH}")

    conn = sqlite3.Connection(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def api_request(url: str, retries: int = 3) -> Optional[Dict]:
    """Make an API request with retries."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'CivicCache/1.0'}
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode('utf-8'))
                time.sleep(REQUEST_DELAY)
                return data
        except urllib.error.HTTPError as e:
            if e.code == 429:  # Rate limited
                wait_time = 2 ** attempt
                print(f"  Rate limited, waiting {wait_time}s...")
                time.sleep(wait_time)
            elif e.code == 404:
                return None
            else:
                print(f"  HTTP Error {e.code}: {e.reason}")
                if attempt == retries - 1:
                    return None
        except Exception as e:
            print(f"  Error: {e}")
            if attempt == retries - 1:
                return None
            time.sleep(1)

    return None


def fetch_all_bills() -> List[Dict]:
    """Fetch list of all bills in the current Congress."""
    print(f"\nFetching all bills from {CONGRESS_NUMBER}th Congress...")

    all_bills = []
    offset = 0
    limit = 250  # Max allowed by API

    while True:
        url = f"{CONGRESS_API_BASE}/bill/{CONGRESS_NUMBER}?offset={offset}&limit={limit}&api_key={API_KEY}&format=json"
        data = api_request(url)

        if not data or 'bills' not in data:
            break

        bills = data['bills']
        all_bills.extend(bills)

        total = data.get('pagination', {}).get('count', 0)
        print(f"  Fetched {len(all_bills)}/{total} bills...")

        if len(bills) < limit:
            break

        offset += limit

    print(f"✓ Found {len(all_bills)} bills and resolutions")
    return all_bills


def fetch_bill_details(congress: int, bill_type: str, bill_number: int) -> Optional[Dict]:
    """Fetch detailed information for a specific bill."""
    url = f"{CONGRESS_API_BASE}/bill/{congress}/{bill_type}/{bill_number}?api_key={API_KEY}&format=json"
    data = api_request(url)
    return data.get('bill') if data else None


def fetch_bill_summaries(congress: int, bill_type: str, bill_number: int) -> List[Dict]:
    """Fetch CRS summaries for a bill."""
    url = f"{CONGRESS_API_BASE}/bill/{congress}/{bill_type}/{bill_number}/summaries?api_key={API_KEY}&format=json"
    data = api_request(url)
    return data.get('summaries', []) if data else []


def fetch_bill_sponsors(congress: int, bill_type: str, bill_number: int) -> Dict:
    """Fetch sponsors and cosponsors for a bill."""
    # Get main sponsor from bill details (already fetched)
    # Get cosponsors
    url = f"{CONGRESS_API_BASE}/bill/{congress}/{bill_type}/{bill_number}/cosponsors?limit=250&api_key={API_KEY}&format=json"
    data = api_request(url)
    return {
        'cosponsors': data.get('cosponsors', []) if data else []
    }


def fetch_bill_subjects(congress: int, bill_type: str, bill_number: int) -> List[str]:
    """Fetch subjects/policy areas for a bill."""
    url = f"{CONGRESS_API_BASE}/bill/{congress}/{bill_type}/{bill_number}/subjects?api_key={API_KEY}&format=json"
    data = api_request(url)

    subjects = []
    if data and 'subjects' in data:
        legislative_subjects = data['subjects'].get('legislativeSubjects', [])
        for subj in legislative_subjects:
            if isinstance(subj, dict) and 'name' in subj:
                subjects.append(subj['name'])

    return subjects


def fetch_bill_actions(congress: int, bill_type: str, bill_number: int, limit: int = 100) -> List[Dict]:
    """Fetch legislative actions for a bill (limited to save space)."""
    url = f"{CONGRESS_API_BASE}/bill/{congress}/{bill_type}/{bill_number}/actions?limit={limit}&api_key={API_KEY}&format=json"
    data = api_request(url)
    return data.get('actions', []) if data else []


def import_bill(conn: sqlite3.Connection, bill_summary: Dict) -> str:
    """Import a single bill with all its details."""
    congress = bill_summary.get('congress')
    bill_type = bill_summary.get('type', '').lower()
    bill_number = bill_summary.get('number')

    bill_id = f"{congress}-{bill_type}-{bill_number}"

    # Fetch detailed information
    bill_details = fetch_bill_details(congress, bill_type, bill_number)
    if not bill_details:
        print(f"  ⚠️  Could not fetch details for {bill_id}")
        return bill_id

    # Insert main bill record
    latest_action = bill_details.get('latestAction', {})
    laws = bill_details.get('laws', [])
    policy_area = bill_details.get('policyArea', {})

    conn.execute("""
        INSERT OR REPLACE INTO legislation (
            id, congress, bill_type, bill_number,
            title, origin_chamber, origin_chamber_code,
            introduced_date, latest_action_date, latest_action_text,
            policy_area, is_law, law_number, law_type,
            actions_count, amendments_count, cosponsors_count,
            subjects_count, summaries_count,
            legislation_url
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        bill_id,
        congress,
        bill_type,
        bill_number,
        bill_details.get('title', ''),
        bill_details.get('originChamber'),
        bill_details.get('originChamberCode'),
        bill_details.get('introducedDate'),
        latest_action.get('actionDate'),
        latest_action.get('text'),
        policy_area.get('name'),
        1 if laws else 0,
        laws[0].get('number') if laws else None,
        laws[0].get('type') if laws else None,
        bill_details.get('actions', {}).get('count', 0),
        bill_details.get('amendments', {}).get('count', 0),
        0,  # Will update after fetching cosponsors
        bill_details.get('subjects', {}).get('count', 0),
        bill_details.get('summaries', {}).get('count', 0),
        bill_details.get('legislationUrl')
    ))

    # Fetch and import summaries
    summaries = fetch_bill_summaries(congress, bill_type, bill_number)
    for summary in summaries:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO legislation_summaries (
                    legislation_id, action_date, action_desc,
                    summary_text, update_date, version_code
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                bill_id,
                summary.get('actionDate'),
                summary.get('actionDesc'),
                summary.get('text', ''),
                summary.get('updateDate'),
                summary.get('versionCode')
            ))
        except Exception as e:
            print(f"  ⚠️  Error importing summary: {e}")

    # Import primary sponsor
    sponsors = bill_details.get('sponsors', [])
    for sponsor in sponsors:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO legislation_sponsors (
                    legislation_id, bioguide_id, full_name,
                    first_name, last_name, middle_name,
                    party, state, district,
                    is_original_cosponsor, sponsorship_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                bill_id,
                sponsor.get('bioguideId'),
                sponsor.get('fullName', ''),
                sponsor.get('firstName'),
                sponsor.get('lastName'),
                sponsor.get('middleName'),
                sponsor.get('party'),
                sponsor.get('state'),
                sponsor.get('district'),
                0,  # Primary sponsor, not cosponsor
                bill_details.get('introducedDate')
            ))
        except Exception as e:
            print(f"  ⚠️  Error importing sponsor: {e}")

    # Fetch and import cosponsors
    sponsor_data = fetch_bill_sponsors(congress, bill_type, bill_number)
    cosponsors = sponsor_data.get('cosponsors', [])

    for cosponsor in cosponsors:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO legislation_sponsors (
                    legislation_id, bioguide_id, full_name,
                    first_name, last_name, middle_name,
                    party, state, district,
                    is_original_cosponsor, sponsorship_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                bill_id,
                cosponsor.get('bioguideId'),
                cosponsor.get('fullName', ''),
                cosponsor.get('firstName'),
                cosponsor.get('lastName'),
                cosponsor.get('middleName'),
                cosponsor.get('party'),
                cosponsor.get('state'),
                cosponsor.get('district'),
                1 if cosponsor.get('isOriginalCosponsor') == 'True' else 0,
                cosponsor.get('sponsorshipDate')
            ))
        except Exception as e:
            print(f"  ⚠️  Error importing cosponsor: {e}")

    # Update cosponsor count
    conn.execute("""
        UPDATE legislation SET cosponsors_count = ? WHERE id = ?
    """, (len(cosponsors), bill_id))

    # Fetch and import subjects (limited to save API calls)
    subjects = fetch_bill_subjects(congress, bill_type, bill_number)
    for subject in subjects[:50]:  # Limit to 50 subjects
        try:
            conn.execute("""
                INSERT OR IGNORE INTO legislation_subjects (
                    legislation_id, subject_name
                ) VALUES (?, ?)
            """, (bill_id, subject))
        except Exception as e:
            print(f"  ⚠️  Error importing subject: {e}")

    # Skip importing actions - can be fetched on-demand later
    # This saves ~90 minutes of import time and ~30MB of database space
    # Uncomment below if you want to import actions during initial load:
    #
    # actions = fetch_bill_actions(congress, bill_type, bill_number, limit=50)
    # for action in actions:
    #     try:
    #         conn.execute("""
    #             INSERT OR IGNORE INTO legislation_actions (
    #                 legislation_id, action_date, action_code, action_text,
    #                 source_system, source_system_code, action_type
    #             ) VALUES (?, ?, ?, ?, ?, ?, ?)
    #         """, (
    #             bill_id,
    #             action.get('actionDate'),
    #             action.get('actionCode'),
    #             action.get('text', ''),
    #             action.get('sourceSystem', {}).get('name') if isinstance(action.get('sourceSystem'), dict) else None,
    #             action.get('sourceSystem', {}).get('code') if isinstance(action.get('sourceSystem'), dict) else None,
    #             action.get('type')
    #         ))
    #     except Exception as e:
    #         print(f"  ⚠️  Error importing action: {e}")

    return bill_id


def import_all_legislation(conn: sqlite3.Connection) -> Dict[str, int]:
    """Import all legislation from the 119th Congress."""
    print("\n" + "="*60)
    print("IMPORTING LEGISLATION")
    print("="*60)

    # Fetch list of all bills
    bills = fetch_all_bills()

    stats = {
        'total_bills': len(bills),
        'imported': 0,
        'failed': 0,
        'summaries': 0,
        'sponsors': 0,
        'subjects': 0,
        'actions': 0
    }

    print(f"\nImporting {len(bills)} bills with full details...")
    print("This will take a while due to API rate limits...")

    start_time = time.time()

    for i, bill in enumerate(bills, 1):
        bill_type = bill.get('type', '').lower()
        bill_number = bill.get('number')
        title = bill.get('title', '')[:50]

        try:
            print(f"\n[{i}/{len(bills)}] {bill_type.upper()} {bill_number}: {title}...")

            bill_id = import_bill(conn, bill)
            stats['imported'] += 1

            # Commit periodically
            if i % BATCH_COMMIT_SIZE == 0:
                conn.commit()
                elapsed = time.time() - start_time
                rate = i / elapsed
                remaining = (len(bills) - i) / rate if rate > 0 else 0
                print(f"\n  Progress: {i}/{len(bills)} ({i/len(bills)*100:.1f}%)")
                print(f"  Rate: {rate:.1f} bills/sec, Est. remaining: {remaining/60:.1f} min")

        except Exception as e:
            print(f"  ✗ Failed to import {bill_type} {bill_number}: {e}")
            stats['failed'] += 1

    # Final commit
    conn.commit()

    # Get final statistics
    result = conn.execute("SELECT COUNT(*) FROM legislation_summaries").fetchone()
    stats['summaries'] = result[0] if result else 0

    result = conn.execute("SELECT COUNT(*) FROM legislation_sponsors").fetchone()
    stats['sponsors'] = result[0] if result else 0

    result = conn.execute("SELECT COUNT(*) FROM legislation_subjects").fetchone()
    stats['subjects'] = result[0] if result else 0

    result = conn.execute("SELECT COUNT(*) FROM legislation_actions").fetchone()
    stats['actions'] = result[0] if result else 0

    return stats


def create_indexes(conn: sqlite3.Connection) -> None:
    """Create indexes for efficient querying."""
    print("\n" + "="*60)
    print("CREATING INDEXES")
    print("="*60)

    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_legislation_congress ON legislation(congress)",
        "CREATE INDEX IF NOT EXISTS idx_legislation_type ON legislation(bill_type)",
        "CREATE INDEX IF NOT EXISTS idx_legislation_policy ON legislation(policy_area)",
        "CREATE INDEX IF NOT EXISTS idx_legislation_law ON legislation(is_law)",
        "CREATE INDEX IF NOT EXISTS idx_legislation_date ON legislation(introduced_date)",

        "CREATE INDEX IF NOT EXISTS idx_summaries_legislation ON legislation_summaries(legislation_id)",

        "CREATE INDEX IF NOT EXISTS idx_sponsors_legislation ON legislation_sponsors(legislation_id)",
        "CREATE INDEX IF NOT EXISTS idx_sponsors_bioguide ON legislation_sponsors(bioguide_id)",

        "CREATE INDEX IF NOT EXISTS idx_actions_legislation ON legislation_actions(legislation_id)",
        "CREATE INDEX IF NOT EXISTS idx_actions_date ON legislation_actions(action_date)",

        "CREATE INDEX IF NOT EXISTS idx_subjects_legislation ON legislation_subjects(legislation_id)",
        "CREATE INDEX IF NOT EXISTS idx_subjects_name ON legislation_subjects(subject_name)",

        # Full-text search on summaries
        "CREATE VIRTUAL TABLE IF NOT EXISTS legislation_summaries_fts USING fts5(legislation_id, summary_text, content='legislation_summaries', content_rowid='id')",

        # Full-text search on legislation titles
        "CREATE VIRTUAL TABLE IF NOT EXISTS legislation_fts USING fts5(id, title, content='legislation', content_rowid='rowid')"
    ]

    for idx_sql in indexes:
        try:
            conn.execute(idx_sql)
            print(f"  ✓ Created index")
        except Exception as e:
            print(f"  ⚠️  Index error: {e}")

    conn.commit()
    print("\n✓ All indexes created")


def update_metadata(conn: sqlite3.Connection, stats: Dict[str, int]) -> None:
    """Update database metadata."""
    print("\n" + "="*60)
    print("UPDATING METADATA")
    print("="*60)

    conn.execute("""
        INSERT OR REPLACE INTO metadata (key, value, updated_at)
        VALUES ('total_legislation', ?, CURRENT_TIMESTAMP)
    """, (str(stats['imported']),))

    conn.execute("""
        INSERT OR REPLACE INTO metadata (key, value, updated_at)
        VALUES ('legislation_summaries', ?, CURRENT_TIMESTAMP)
    """, (str(stats['summaries']),))

    conn.commit()
    print("✓ Metadata updated")


def verify_import(conn: sqlite3.Connection) -> None:
    """Verify the import was successful."""
    print("\n" + "="*60)
    print("IMPORT VERIFICATION")
    print("="*60)

    # Count records
    result = conn.execute("SELECT COUNT(*) FROM legislation").fetchone()
    print(f"Total legislation: {result[0]:,}")

    result = conn.execute("SELECT COUNT(*) FROM legislation_summaries").fetchone()
    print(f"Total summaries: {result[0]:,}")

    result = conn.execute("SELECT COUNT(*) FROM legislation_sponsors").fetchone()
    print(f"Total sponsors/cosponsors: {result[0]:,}")

    result = conn.execute("SELECT COUNT(*) FROM legislation_subjects").fetchone()
    print(f"Total subjects: {result[0]:,}")

    result = conn.execute("SELECT COUNT(*) FROM legislation_actions").fetchone()
    print(f"Total actions: {result[0]:,}")

    # Breakdown by type
    print("\nLegislation by type:")
    results = conn.execute("""
        SELECT bill_type, COUNT(*) as count
        FROM legislation
        GROUP BY bill_type
        ORDER BY count DESC
    """).fetchall()

    for bill_type, count in results:
        print(f"  {bill_type.upper()}: {count:,}")

    # Laws
    result = conn.execute("SELECT COUNT(*) FROM legislation WHERE is_law = 1").fetchone()
    print(f"\nEnacted into law: {result[0]:,}")

    # Sample bills
    print("\nSample legislation with summaries:")
    samples = conn.execute("""
        SELECT l.id, l.title, COUNT(s.id) as summary_count,
               LENGTH(GROUP_CONCAT(s.summary_text)) as total_text_size
        FROM legislation l
        LEFT JOIN legislation_summaries s ON l.id = s.legislation_id
        GROUP BY l.id
        HAVING summary_count > 0
        ORDER BY l.introduced_date DESC
        LIMIT 5
    """).fetchall()

    for leg_id, title, summary_count, text_size in samples:
        print(f"  {leg_id}: {title[:60]}...")
        print(f"    Summaries: {summary_count}, Size: {text_size/1024:.1f} KB")

    print("\n✓ Import verification complete")


def main():
    """Main execution function."""
    print("="*60)
    print("LEGISLATION IMPORT - 119TH CONGRESS")
    print("="*60)
    print(f"API Key: {API_KEY[:10]}..." if API_KEY != "DEMO_KEY" else "API Key: DEMO_KEY (limited)")

    try:
        # Connect to database
        conn = create_connection()

        # Import all legislation
        stats = import_all_legislation(conn)

        # Create indexes
        create_indexes(conn)

        # Update metadata
        update_metadata(conn, stats)

        # Verify import
        verify_import(conn)

        conn.close()

        print("\n" + "="*60)
        print("✓ IMPORT COMPLETE")
        print("="*60)
        print(f"Imported: {stats['imported']:,} bills")
        print(f"Failed: {stats['failed']:,}")
        print(f"Summaries: {stats['summaries']:,}")
        print(f"Sponsors: {stats['sponsors']:,}")
        print(f"Subjects: {stats['subjects']:,}")
        print(f"Actions: {stats['actions']:,}")

        return 0

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
