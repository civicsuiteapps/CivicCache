#!/usr/bin/env python3
"""
Import birth years for all congress members from Congress.gov API.

This script fetches birthYear from the detailed member endpoint and updates
both the CivicCache and CivicScope databases.
"""

import json
import sqlite3
import urllib.request
import urllib.error
import time
import os
from pathlib import Path

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value

# Database locations
CIVICCACHE_DB = Path(__file__).parent.parent / "data/civic_cache.db"
CIVICSCOPE_DB = Path.home() / "GIT/CivicScope/CivicScope/Resources/civic_cache.db"

# API configuration
CONGRESS_API_BASE = "https://api.congress.gov/v3"
CONGRESS_API_KEY = os.environ.get("CONGRESS_GOV_API_KEY", "")

# Rate limiting - Congress.gov allows 5000 req/hour
REQUEST_DELAY = 0.75  # ~4800 requests/hour max


def api_request(url: str, retries: int = 3):
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
                wait_time = 2 ** attempt * 5
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


def fetch_birth_year(bioguide_id: str) -> int | None:
    """Fetch birth year for a member from Congress.gov API."""
    url = f"{CONGRESS_API_BASE}/member/{bioguide_id}?api_key={CONGRESS_API_KEY}&format=json"
    data = api_request(url)

    if data and 'member' in data:
        birth_year = data['member'].get('birthYear')
        if birth_year:
            try:
                return int(birth_year)
            except ValueError:
                return None
    return None


def update_databases(member_id: str, birth_year: int):
    """Update birth_year in both databases."""
    for db_path in [CIVICCACHE_DB, CIVICSCOPE_DB]:
        if db_path.exists():
            try:
                conn = sqlite3.connect(db_path)
                conn.execute(
                    "UPDATE congress_members SET birth_year = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (birth_year, member_id)
                )
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"  Error updating {db_path.name}: {e}")


def main():
    print("=" * 60)
    print("IMPORTING BIRTH YEARS FROM CONGRESS.GOV")
    print("=" * 60)

    if not CONGRESS_API_KEY:
        print("\nERROR: No Congress.gov API key found!")
        print("Set CONGRESS_GOV_API_KEY in .env")
        return 1

    print(f"API Key: {CONGRESS_API_KEY[:10]}...")
    print(f"CivicCache DB: {CIVICCACHE_DB}")
    print(f"CivicScope DB: {CIVICSCOPE_DB}")

    # Connect to primary database to get member list
    if not CIVICCACHE_DB.exists():
        print(f"\nERROR: Database not found at {CIVICCACHE_DB}")
        return 1

    conn = sqlite3.connect(CIVICCACHE_DB)

    # Get all members missing birth_year
    members = conn.execute("""
        SELECT id, full_name FROM congress_members
        WHERE birth_year IS NULL
        ORDER BY last_name, first_name
    """).fetchall()

    conn.close()

    total = len(members)
    print(f"\nFound {total} members missing birth_year")

    updated = 0
    failed = 0

    for i, (member_id, full_name) in enumerate(members, 1):
        print(f"[{i}/{total}] {full_name} ({member_id})...", end=" ")

        birth_year = fetch_birth_year(member_id)

        if birth_year:
            update_databases(member_id, birth_year)
            print(f"born {birth_year}")
            updated += 1
        else:
            print("no birth year found")
            failed += 1

    print("\n" + "=" * 60)
    print("IMPORT COMPLETE")
    print("=" * 60)
    print(f"Updated: {updated}")
    print(f"Missing: {failed}")

    # Verify
    conn = sqlite3.connect(CIVICCACHE_DB)
    result = conn.execute("SELECT COUNT(*), COUNT(birth_year) FROM congress_members").fetchone()
    conn.close()
    print(f"\nCivicCache: {result[1]}/{result[0]} members have birth_year")

    if CIVICSCOPE_DB.exists():
        conn = sqlite3.connect(CIVICSCOPE_DB)
        result = conn.execute("SELECT COUNT(*), COUNT(birth_year) FROM congress_members").fetchone()
        conn.close()
        print(f"CivicScope: {result[1]}/{result[0]} members have birth_year")

    return 0


if __name__ == "__main__":
    exit(main())
