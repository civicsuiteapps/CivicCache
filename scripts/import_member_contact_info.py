#!/usr/bin/env python3
"""
Import Congressional member contact information from ProPublica and Congress.gov APIs.

This script:
1. Fetches member contact info from ProPublica Congress API (primary source)
2. Falls back to Congress.gov API for missing data or rate limits
3. Updates the congress_members table with office addresses, phone numbers, websites, etc.
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

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value

# Database location
DB_PATH = Path("data/civic_cache.db")

# API configuration
CONGRESS_API_BASE = "https://api.congress.gov/v3"

# Get API key from environment
CONGRESS_API_KEY = os.environ.get("CONGRESS_GOV_API_KEY", "")

# Current Congress session
CONGRESS_NUMBER = 119

# Rate limiting
REQUEST_DELAY = 1.0  # Delay between API requests (seconds)


def create_connection() -> sqlite3.Connection:
    """Create and return a database connection."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found at {DB_PATH}")

    conn = sqlite3.Connection(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def api_request_congress(url: str, retries: int = 3) -> Optional[Dict]:
    """Make a Congress.gov API request with retries."""
    if not CONGRESS_API_KEY:
        print("  ⚠️  Congress.gov API key not found")
        return None

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


def fetch_congress_gov_member_detail(bioguide_id: str) -> Optional[Dict]:
    """Fetch detailed member information from Congress.gov API."""
    url = f"{CONGRESS_API_BASE}/member/{bioguide_id}?api_key={CONGRESS_API_KEY}&format=json"
    data = api_request_congress(url)
    return data.get('member') if data else None


def calculate_service_years(terms: List[Dict]) -> tuple:
    """Calculate first elected year, total years served, and total terms."""
    if not terms:
        return None, None, None

    # Sort terms by start year
    sorted_terms = sorted(terms, key=lambda t: t.get('startYear', 9999))

    first_elected_year = sorted_terms[0].get('startYear')
    total_terms = len(terms)

    # Calculate total years (rough estimate based on term count and chamber)
    # House terms are 2 years, Senate terms are 6 years
    total_years = 0
    for term in terms:
        start = term.get('startYear', 0)
        end = term.get('endYear', datetime.now().year)
        if start and end:
            total_years += (end - start)

    return first_elected_year, total_years, total_terms


def extract_leadership_role(leadership: List[Dict], current_congress: int = CONGRESS_NUMBER) -> Optional[str]:
    """Extract current leadership role if any."""
    if not leadership:
        return None

    # Find leadership position for current congress
    for position in leadership:
        if position.get('congress') == current_congress:
            return position.get('type')

    # Fall back to most recent leadership position
    sorted_leadership = sorted(leadership, key=lambda l: l.get('congress', 0), reverse=True)
    if sorted_leadership:
        return sorted_leadership[0].get('type')

    return None


def extract_congress_gov_data(member: Dict) -> Dict[str, Any]:
    """Extract all member data from Congress.gov member detail."""
    address_info = member.get('addressInformation', {})
    terms = member.get('terms', [])  # terms is a list, not a dict
    leadership = member.get('leadership', [])
    depiction = member.get('depiction', {})

    # Calculate service history
    first_elected, total_years, total_terms = calculate_service_years(terms)

    # Get leadership role
    leadership_role = extract_leadership_role(leadership)

    # Get legislative counts
    sponsored_count = member.get('sponsoredLegislation', {}).get('count', 0)
    cosponsored_count = member.get('cosponsoredLegislation', {}).get('count', 0)

    # Get nickname
    nickname = member.get('nickName')

    return {
        # Contact info
        'office_address': address_info.get('officeAddress'),
        'phone': address_info.get('phoneNumber'),
        'website_url': member.get('officialWebsiteUrl'),
        'contact_form_url': None,  # Not available in Congress.gov API
        'twitter_handle': None,  # Not available in Congress.gov API
        'facebook_url': None,  # Not available in Congress.gov API
        'youtube_url': None,  # Not available in Congress.gov API

        # Service history
        'first_elected_year': first_elected,
        'total_years_served': total_years,
        'total_terms_served': total_terms,

        # Leadership
        'current_leadership_role': leadership_role,

        # Legislative activity
        'sponsored_bills_count': sponsored_count,
        'cosponsored_bills_count': cosponsored_count,

        # Portrait
        'depiction_url': depiction.get('imageUrl'),
        'depiction_attribution': depiction.get('attribution'),

        # Nickname
        'nickname': nickname,
    }


def find_member_in_db(conn: sqlite3.Connection, bioguide_id: str = None, first_name: str = None, last_name: str = None, state: str = None) -> Optional[str]:
    """Find a member in the database by bioguide ID (preferred) or name and state."""
    # Try bioguide ID first (most reliable)
    if bioguide_id:
        result = conn.execute("""
            SELECT id FROM congress_members
            WHERE id = ?
        """, (bioguide_id,)).fetchone()

        if result:
            return result[0]

    # Fall back to name matching if no bioguide or not found
    if last_name and first_name and state:
        # Try exact match first
        result = conn.execute("""
            SELECT id FROM congress_members
            WHERE last_name = ? AND first_name LIKE ? AND state_code = ?
        """, (last_name, f"{first_name}%", state)).fetchone()

        if result:
            return result[0]

        # Try by last name and state only (for middle name mismatches)
        result = conn.execute("""
            SELECT id FROM congress_members
            WHERE last_name = ? AND state_code = ?
        """, (last_name, state)).fetchone()

        if result:
            return result[0]

    return None


def update_member_contact(conn: sqlite3.Connection, member_id: str, contact_info: Dict[str, Any], source: str) -> bool:
    """Update member contact information in the database."""
    try:
        # Only update fields that have non-empty values
        updates = []
        params = []

        for field, value in contact_info.items():
            if value:  # Only update if value is not None or empty string
                updates.append(f"{field} = ?")
                params.append(value)

        if not updates:
            return False

        # Add updated_at timestamp
        updates.append("updated_at = CURRENT_TIMESTAMP")

        sql = f"UPDATE congress_members SET {', '.join(updates)} WHERE id = ?"
        params.append(member_id)

        conn.execute(sql, params)
        return True
    except Exception as e:
        print(f"  ✗ Error updating member {member_id}: {e}")
        return False


def import_from_congress_gov(conn: sqlite3.Connection, fill_missing_only: bool = True) -> Dict[str, int]:
    """Import contact info from Congress.gov API."""
    print("\n" + "="*60)
    print("IMPORTING FROM CONGRESS.GOV")
    print("="*60)

    stats = {
        'updated': 0,
        'failed': 0,
        'missing': 0,
        'skipped': 0
    }

    # Get all members from database to fetch their details
    db_members = conn.execute("""
        SELECT id, full_name, chamber, state_code FROM congress_members
    """).fetchall()

    print(f"Found {len(db_members)} members in database")

    for db_member_id, full_name, chamber, state in db_members:
        print(f"\n{full_name} ({chamber}-{state}, {db_member_id})...")

        # If only filling missing, check if member already has contact info
        if fill_missing_only:
            result = conn.execute("""
                SELECT office_address, phone, website_url
                FROM congress_members WHERE id = ?
            """, (db_member_id,)).fetchone()

            if result and any(result):  # Has some contact info already
                print(f"  ⊘ Already has contact info, skipping")
                stats['skipped'] += 1
                continue

        # Fetch detailed member info from Congress.gov
        member_detail = fetch_congress_gov_member_detail(db_member_id)

        if not member_detail:
            print(f"  ⚠️  Could not fetch details from Congress.gov")
            stats['failed'] += 1
            continue

        # Extract all member data
        member_data = extract_congress_gov_data(member_detail)

        # Update database
        if update_member_contact(conn, db_member_id, member_data, 'congress.gov'):
            print(f"  ✓ Updated from Congress.gov")
            stats['updated'] += 1
        else:
            print(f"  ⚠️  No contact info to update")
            stats['failed'] += 1

    conn.commit()
    return stats


def verify_import(conn: sqlite3.Connection) -> None:
    """Verify the contact info import was successful."""
    print("\n" + "="*60)
    print("IMPORT VERIFICATION")
    print("="*60)

    # Count total members
    result = conn.execute("SELECT COUNT(*) FROM congress_members").fetchone()
    total_members = result[0]
    print(f"Total members: {total_members}")

    # Count members with contact info
    result = conn.execute("""
        SELECT COUNT(*) FROM congress_members
        WHERE office_address IS NOT NULL AND office_address != ''
    """).fetchone()
    with_address = result[0]

    result = conn.execute("""
        SELECT COUNT(*) FROM congress_members
        WHERE phone IS NOT NULL AND phone != ''
    """).fetchone()
    with_phone = result[0]

    result = conn.execute("""
        SELECT COUNT(*) FROM congress_members
        WHERE website_url IS NOT NULL AND website_url != ''
    """).fetchone()
    with_website = result[0]

    result = conn.execute("""
        SELECT COUNT(*) FROM congress_members
        WHERE contact_form_url IS NOT NULL AND contact_form_url != ''
    """).fetchone()
    with_contact_form = result[0]

    result = conn.execute("""
        SELECT COUNT(*) FROM congress_members
        WHERE twitter_handle IS NOT NULL AND twitter_handle != ''
    """).fetchone()
    with_twitter = result[0]

    print(f"\nContact info coverage:")
    print(f"  Office address: {with_address}/{total_members} ({with_address/total_members*100:.1f}%)")
    print(f"  Phone: {with_phone}/{total_members} ({with_phone/total_members*100:.1f}%)")
    print(f"  Website: {with_website}/{total_members} ({with_website/total_members*100:.1f}%)")
    print(f"  Contact form: {with_contact_form}/{total_members} ({with_contact_form/total_members*100:.1f}%)")
    print(f"  Twitter: {with_twitter}/{total_members} ({with_twitter/total_members*100:.1f}%)")

    # Sample members with contact info
    print("\nSample members with contact info:")
    samples = conn.execute("""
        SELECT full_name, chamber, state_code, phone, website_url
        FROM congress_members
        WHERE phone IS NOT NULL AND phone != ''
        ORDER BY RANDOM()
        LIMIT 5
    """).fetchall()

    for name, chamber, state, phone, website in samples:
        print(f"  {name} ({chamber}-{state})")
        print(f"    Phone: {phone}")
        print(f"    Website: {website}")

    print("\n✓ Import verification complete")


def main():
    """Main execution function."""
    print("="*60)
    print("MEMBER CONTACT INFO IMPORT")
    print("="*60)

    # Check for API key
    if not CONGRESS_API_KEY:
        print("\n✗ ERROR: No Congress.gov API key found!")
        print("Set CONGRESS_API_KEY environment variable")
        print("Sign up: https://api.congress.gov/sign-up/")
        return 1

    print(f"Congress.gov API Key: {CONGRESS_API_KEY[:10]}...")
    print("\nNote: ProPublica Congress API has been discontinued.")
    print("Using Congress.gov API for contact information.")

    try:
        # Connect to database
        conn = create_connection()

        # Import from Congress.gov
        congress_stats = import_from_congress_gov(conn, fill_missing_only=False)

        # Verify import
        verify_import(conn)

        conn.close()

        print("\n" + "="*60)
        print("✓ IMPORT COMPLETE")
        print("="*60)
        print(f"Updated: {congress_stats['updated']}")
        print(f"Failed: {congress_stats['failed']}")
        print(f"Skipped: {congress_stats.get('skipped', 0)}")

        return 0

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
