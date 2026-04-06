#!/usr/bin/env python3
"""
Import member positions from Wikidata and Congress.gov API.

This script:
1. Creates a member_positions table in both databases
2. Fetches all positions held (P39) from Wikidata for members with Bioguide IDs
3. Also imports Congressional terms from Congress.gov API
4. Updates both CivicCache and CivicScope databases
"""

import json
import sqlite3
import urllib.request
import urllib.error
import urllib.parse
import time
import os
from pathlib import Path
from datetime import datetime

try:
    from config import DB_PATH as _CFG_DB, CONGRESS_API_KEY as _CFG_KEY
except ImportError:
    _CFG_DB = Path(__file__).parent.parent / "data/civic_cache.db"
    _CFG_KEY = ""
    # Fallback .env loading
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key, value)

# Database locations
CIVICCACHE_DB = _CFG_DB
CIVICSCOPE_DB = Path.home() / "GIT/CivicScope/CivicScope/Resources/civic_cache.db"

# API configuration
WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"
CONGRESS_API_BASE = "https://api.congress.gov/v3"
CONGRESS_API_KEY = os.environ.get("CONGRESS_GOV_API_KEY") or os.environ.get("CONGRESS_API_KEY") or _CFG_KEY


def create_positions_table(conn: sqlite3.Connection):
    """Create the member_positions table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS member_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id TEXT NOT NULL,
            position_title TEXT NOT NULL,
            position_type TEXT,  -- 'federal', 'state', 'local', 'military', 'other'
            organization TEXT,   -- e.g., 'U.S. House of Representatives', 'Texas State Senate'
            district TEXT,       -- e.g., 'Texas 3rd', 'Montana At-Large'
            state TEXT,          -- State code if applicable
            start_year INTEGER,
            end_year INTEGER,
            is_current INTEGER DEFAULT 0,
            source TEXT,         -- 'wikidata', 'congress_gov'
            wikidata_position_id TEXT,  -- Wikidata Q-ID for the position
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (member_id) REFERENCES congress_members(id)
        )
    """)

    # Create indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_member ON member_positions(member_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_type ON member_positions(position_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_current ON member_positions(is_current)")

    conn.commit()
    print("  Created member_positions table")


def wikidata_query(query: str) -> list:
    """Execute a SPARQL query against Wikidata."""
    url = WIKIDATA_ENDPOINT
    params = {
        'format': 'json',
        'query': query
    }

    headers = {
        'User-Agent': 'CivicCache/1.0 (https://github.com/cczaphod/CivicCache)',
        'Accept': 'application/sparql-results+json'
    }

    try:
        full_url = url + '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(full_url, headers=headers)
        with urllib.request.urlopen(req, timeout=120) as response:
            data = json.loads(response.read().decode('utf-8'))
            return data.get('results', {}).get('bindings', [])
    except Exception as e:
        print(f"  Wikidata query error: {e}")
        return []


def categorize_position(position_label: str, position_id: str) -> tuple:
    """Categorize a position into type and organization."""
    position_lower = position_label.lower()

    # Federal positions
    if 'united states senator' in position_lower or 'u.s. senator' in position_lower:
        return ('federal', 'U.S. Senate')
    if 'united states representative' in position_lower or 'member of the united states house' in position_lower:
        return ('federal', 'U.S. House of Representatives')
    if 'united states secretary' in position_lower:
        return ('federal', 'U.S. Cabinet')
    if 'governor' in position_lower and 'lieutenant' not in position_lower:
        return ('state', 'Governor')
    if 'lieutenant governor' in position_lower:
        return ('state', 'Lieutenant Governor')
    if 'attorney general' in position_lower:
        if 'united states' in position_lower:
            return ('federal', 'U.S. Cabinet')
        return ('state', 'State Attorney General')

    # State legislature
    if 'state senator' in position_lower or 'state senate' in position_lower:
        return ('state', 'State Senate')
    if 'state representative' in position_lower or 'state house' in position_lower or 'state assembly' in position_lower:
        return ('state', 'State House')
    if 'member of the' in position_lower and 'legislature' in position_lower:
        return ('state', 'State Legislature')
    if 'member of the' in position_lower and ('house' in position_lower or 'assembly' in position_lower):
        return ('state', 'State House')
    if 'member of the' in position_lower and 'senate' in position_lower:
        return ('state', 'State Senate')

    # Local positions
    if 'mayor' in position_lower:
        return ('local', 'Mayor')
    if 'city council' in position_lower or 'councilmember' in position_lower:
        return ('local', 'City Council')
    if 'county' in position_lower and ('commissioner' in position_lower or 'supervisor' in position_lower):
        return ('local', 'County Government')
    if 'school board' in position_lower:
        return ('local', 'School Board')

    # Other positions
    if 'district attorney' in position_lower or 'prosecutor' in position_lower:
        return ('other', 'District Attorney')
    if 'judge' in position_lower or 'justice' in position_lower:
        return ('other', 'Judiciary')
    if 'military' in position_lower or 'army' in position_lower or 'navy' in position_lower or 'marine' in position_lower or 'air force' in position_lower:
        return ('military', 'Military')

    return ('other', position_label)


def extract_year(date_str: str) -> int | None:
    """Extract year from a Wikidata date string."""
    if not date_str:
        return None
    try:
        # Wikidata dates are like "1995-01-01T00:00:00Z"
        return int(date_str[:4])
    except (ValueError, IndexError):
        return None


def extract_state_from_district(district_label: str) -> str | None:
    """Try to extract state from district label."""
    if not district_label:
        return None

    # Common patterns: "Texas's 3rd congressional district", "Montana at-large"
    state_names = {
        'Alabama': 'AL', 'Alaska': 'AK', 'Arizona': 'AZ', 'Arkansas': 'AR',
        'California': 'CA', 'Colorado': 'CO', 'Connecticut': 'CT', 'Delaware': 'DE',
        'Florida': 'FL', 'Georgia': 'GA', 'Hawaii': 'HI', 'Idaho': 'ID',
        'Illinois': 'IL', 'Indiana': 'IN', 'Iowa': 'IA', 'Kansas': 'KS',
        'Kentucky': 'KY', 'Louisiana': 'LA', 'Maine': 'ME', 'Maryland': 'MD',
        'Massachusetts': 'MA', 'Michigan': 'MI', 'Minnesota': 'MN', 'Mississippi': 'MS',
        'Missouri': 'MO', 'Montana': 'MT', 'Nebraska': 'NE', 'Nevada': 'NV',
        'New Hampshire': 'NH', 'New Jersey': 'NJ', 'New Mexico': 'NM', 'New York': 'NY',
        'North Carolina': 'NC', 'North Dakota': 'ND', 'Ohio': 'OH', 'Oklahoma': 'OK',
        'Oregon': 'OR', 'Pennsylvania': 'PA', 'Rhode Island': 'RI', 'South Carolina': 'SC',
        'South Dakota': 'SD', 'Tennessee': 'TN', 'Texas': 'TX', 'Utah': 'UT',
        'Vermont': 'VT', 'Virginia': 'VA', 'Washington': 'WA', 'West Virginia': 'WV',
        'Wisconsin': 'WI', 'Wyoming': 'WY'
    }

    for state_name, code in state_names.items():
        if state_name.lower() in district_label.lower():
            return code
    return None


def fetch_wikidata_positions(bioguide_ids: list) -> dict:
    """Fetch all positions for members from Wikidata."""
    print("\nFetching positions from Wikidata...")

    # Wikidata SPARQL query to get all positions for members with Bioguide IDs
    # We'll query in batches to avoid timeout
    all_positions = {}

    # Query all members with Bioguide IDs at once
    query = """
    SELECT ?person ?bioguideId ?position ?positionLabel ?positionId ?startDate ?endDate ?district ?districtLabel
    WHERE {
      ?person wdt:P1157 ?bioguideId .
      ?person p:P39 ?statement .
      ?statement ps:P39 ?position .
      BIND(STRAFTER(STR(?position), "entity/") AS ?positionId)
      OPTIONAL { ?statement pq:P580 ?startDate . }
      OPTIONAL { ?statement pq:P582 ?endDate . }
      OPTIONAL { ?statement pq:P768 ?district . }

      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }
    ORDER BY ?bioguideId ?startDate
    """

    print("  Executing Wikidata SPARQL query (this may take a minute)...")
    results = wikidata_query(query)

    print(f"  Retrieved {len(results)} position records from Wikidata")

    for result in results:
        bioguide_id = result.get('bioguideId', {}).get('value', '')
        if not bioguide_id or bioguide_id not in bioguide_ids:
            continue

        position_label = result.get('positionLabel', {}).get('value', '')
        position_id = result.get('positionId', {}).get('value', '')
        start_date = result.get('startDate', {}).get('value', '')
        end_date = result.get('endDate', {}).get('value', '')
        district_label = result.get('districtLabel', {}).get('value', '')

        if not position_label:
            continue

        position_type, organization = categorize_position(position_label, position_id)
        start_year = extract_year(start_date)
        end_year = extract_year(end_date)
        state = extract_state_from_district(district_label)

        # Determine if current (no end date or end year is current year or later)
        current_year = datetime.now().year
        is_current = 1 if (not end_year or end_year >= current_year) else 0

        position_data = {
            'position_title': position_label,
            'position_type': position_type,
            'organization': organization,
            'district': district_label if district_label else None,
            'state': state,
            'start_year': start_year,
            'end_year': end_year,
            'is_current': is_current,
            'source': 'wikidata',
            'wikidata_position_id': position_id
        }

        if bioguide_id not in all_positions:
            all_positions[bioguide_id] = []
        all_positions[bioguide_id].append(position_data)

    return all_positions


def fetch_congress_terms(bioguide_id: str) -> list:
    """Fetch Congressional terms from Congress.gov API."""
    url = f"{CONGRESS_API_BASE}/member/{bioguide_id}?api_key={CONGRESS_API_KEY}&format=json"

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'CivicCache/1.0'})
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode('utf-8'))
            member = data.get('member', {})
            terms = member.get('terms', [])

            positions = []
            for term in terms:
                chamber = term.get('chamber', '')
                congress = term.get('congress')
                start_year = term.get('startYear')
                end_year = term.get('endYear')
                state_code = term.get('stateCode', '')
                district = term.get('district')

                if chamber == 'Senate':
                    org = 'U.S. Senate'
                    position_title = f"United States Senator from {term.get('stateName', state_code)}"
                    district_str = None
                else:
                    org = 'U.S. House of Representatives'
                    if district:
                        position_title = f"Member of the U.S. House from {term.get('stateName', state_code)}'s {district} district"
                        district_str = f"{state_code}-{district}"
                    else:
                        position_title = f"Member of the U.S. House from {term.get('stateName', state_code)}"
                        district_str = f"{state_code}-AL"

                current_year = datetime.now().year
                is_current = 1 if (not end_year or end_year >= current_year) else 0

                positions.append({
                    'position_title': position_title,
                    'position_type': 'federal',
                    'organization': org,
                    'district': district_str,
                    'state': state_code,
                    'start_year': start_year,
                    'end_year': end_year,
                    'is_current': is_current,
                    'source': 'congress_gov',
                    'wikidata_position_id': None
                })

            return positions
    except Exception as e:
        return []


def insert_positions(conn: sqlite3.Connection, member_id: str, positions: list):
    """Insert positions for a member."""
    for pos in positions:
        # Check if this position already exists (avoid duplicates across sources)
        existing = conn.execute("""
            SELECT id FROM member_positions
            WHERE member_id = ? AND position_type = ? AND start_year IS ?
            LIMIT 1
        """, (member_id, pos['position_type'], pos['start_year'])).fetchone()

        if existing:
            continue

        conn.execute("""
            INSERT INTO member_positions
            (member_id, position_title, position_type, organization, district, state,
             start_year, end_year, is_current, source, wikidata_position_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            member_id,
            pos['position_title'],
            pos['position_type'],
            pos['organization'],
            pos['district'],
            pos['state'],
            pos['start_year'],
            pos['end_year'],
            pos['is_current'],
            pos['source'],
            pos['wikidata_position_id']
        ))


def main():
    print("=" * 60)
    print("IMPORTING MEMBER POSITIONS FROM WIKIDATA & CONGRESS.GOV")
    print("=" * 60)

    if not CONGRESS_API_KEY:
        print("\nWARNING: No Congress.gov API key found. Congressional terms will be limited.")

    print(f"CivicCache DB: {CIVICCACHE_DB}")
    print(f"CivicScope DB: {CIVICSCOPE_DB}")

    # Connect to both databases and create tables
    dbs = []
    for db_path in [CIVICCACHE_DB, CIVICSCOPE_DB]:
        if db_path.exists():
            conn = sqlite3.connect(db_path)
            create_positions_table(conn)
            dbs.append((db_path.name, conn))

    if not dbs:
        print("ERROR: No databases found!")
        return 1

    # Get all member IDs from the first database
    primary_conn = dbs[0][1]
    members = primary_conn.execute("""
        SELECT id, full_name FROM congress_members
        ORDER BY last_name, first_name
    """).fetchall()

    bioguide_ids = [m[0] for m in members]
    print(f"\nFound {len(members)} members in database")

    # Fetch all Wikidata positions at once
    wikidata_positions = fetch_wikidata_positions(bioguide_ids)
    members_with_wikidata = len([b for b in bioguide_ids if b in wikidata_positions])
    print(f"  Found Wikidata positions for {members_with_wikidata} members")

    # Now process each member
    stats = {'wikidata': 0, 'congress': 0, 'members_processed': 0}

    print("\nImporting positions for each member...")
    for i, (member_id, full_name) in enumerate(members, 1):
        if i % 50 == 0:
            print(f"  Processed {i}/{len(members)} members...")

        positions = []

        # Add Wikidata positions
        if member_id in wikidata_positions:
            positions.extend(wikidata_positions[member_id])
            stats['wikidata'] += len(wikidata_positions[member_id])

        # Fetch and add Congress.gov terms
        if CONGRESS_API_KEY:
            congress_positions = fetch_congress_terms(member_id)
            positions.extend(congress_positions)
            stats['congress'] += len(congress_positions)
            time.sleep(0.3)  # Rate limiting for Congress.gov

        # Insert into all databases
        for db_name, conn in dbs:
            insert_positions(conn, member_id, positions)

        stats['members_processed'] += 1

    # Commit all changes
    for db_name, conn in dbs:
        conn.commit()

        # Get stats
        result = conn.execute("SELECT COUNT(*) FROM member_positions").fetchone()
        print(f"\n{db_name}: {result[0]} total position records")

        result = conn.execute("""
            SELECT position_type, COUNT(*)
            FROM member_positions
            GROUP BY position_type
            ORDER BY COUNT(*) DESC
        """).fetchall()
        print("  By type:")
        for ptype, count in result:
            print(f"    {ptype}: {count}")

        conn.close()

    print("\n" + "=" * 60)
    print("IMPORT COMPLETE")
    print("=" * 60)
    print(f"Members processed: {stats['members_processed']}")
    print(f"Wikidata positions: {stats['wikidata']}")
    print(f"Congress.gov terms: {stats['congress']}")

    return 0


if __name__ == "__main__":
    exit(main())
