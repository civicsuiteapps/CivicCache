#!/usr/bin/env python3
"""
Import member data from the unitedstates/congress-legislators repository.

This script downloads the legislators-current.yaml and legislators-social-media.yaml
files from GitHub and enriches our congress_members table with:
- nickname (e.g., "Bernie" for Bernard Sanders)
- birthday (full date: YYYY-MM-DD)
- gender (M/F)
- middle_name
- External IDs: wikipedia, govtrack, opensecrets, votesmart, fec, ballotpedia, cspan
- Social media: twitter_handle, facebook_url, youtube_url
- Contact form URL
- Term start/end dates (full YYYY-MM-DD format)

Data source: https://github.com/unitedstates/congress-legislators
This is an official, community-maintained dataset that pulls from bioguide
and other government sources.

External ID URL patterns (for use in app):
- wikipedia:    https://en.wikipedia.org/wiki/{wikipedia_id}
- govtrack:     https://www.govtrack.us/congress/members/{govtrack_id}
- opensecrets:  https://www.opensecrets.org/members-of-congress/summary?cid={opensecrets_id}
- votesmart:    https://justfacts.votesmart.org/candidate/{votesmart_id}
- fec:          https://www.fec.gov/data/candidate/{fec_id}/
- ballotpedia:  https://ballotpedia.org/{ballotpedia_id}
- cspan:        https://www.c-span.org/person/?{cspan_id}
- bioguide:     https://bioguide.congress.gov/search/bio/{bioguide_id}

Usage:
    python3 import_congress_legislators.py [--dry-run]
"""

import argparse
import os
import sqlite3
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip3 install pyyaml")
    exit(1)

from utils import download_yaml

# Configuration
LEGISLATORS_CURRENT_URL = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main/legislators-current.yaml"
LEGISLATORS_HISTORICAL_URL = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main/legislators-historical.yaml"
SOCIAL_MEDIA_URL = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main/legislators-social-media.yaml"

# External ID columns to add/update
EXTERNAL_ID_COLUMNS = [
    'wikipedia_id',
    'govtrack_id',
    'opensecrets_id',
    'votesmart_id',
    'fec_id',
    'ballotpedia_id',
    'cspan_id',
    'lis_id'
]

# Social media columns to update
SOCIAL_MEDIA_COLUMNS = [
    'twitter_handle',
    'facebook_url',
    'youtube_url'
]

# Term/contact columns to update
TERM_COLUMNS = [
    'contact_form_url',
    'term_start_date',
    'term_end_date'
]

try:
    from config import DB_PATH as _CFG_DB
except ImportError:
    _CFG_DB = None
    # Fallback .env loading
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key, value)

DATABASE_PATH = _CFG_DB or (Path(__file__).parent.parent / os.environ.get('DATABASE_PATH', 'data/civic_cache.db'))


def download_social_media() -> Dict[str, Dict]:
    """Download social media data and return dict keyed by bioguide_id."""
    data = download_yaml(SOCIAL_MEDIA_URL, "social media entries")
    result = {}
    for entry in data:
        bioguide_id = entry.get('id', {}).get('bioguide')
        if bioguide_id:
            social = entry.get('social', {})
            result[bioguide_id] = {
                'twitter_handle': social.get('twitter'),
                'facebook_url': f"https://facebook.com/{social['facebook']}" if social.get('facebook') else None,
                'youtube_url': f"https://youtube.com/{social['youtube']}" if social.get('youtube') else None,
            }
    return result


def ensure_columns(conn: sqlite3.Connection) -> List[str]:
    """Add missing columns to congress_members table."""
    cursor = conn.execute("PRAGMA table_info(congress_members)")
    existing_columns = [row[1] for row in cursor.fetchall()]

    columns_to_add = ['birthday'] + EXTERNAL_ID_COLUMNS + SOCIAL_MEDIA_COLUMNS + TERM_COLUMNS
    added = []

    for column in columns_to_add:
        if column not in existing_columns:
            print(f"Adding '{column}' column to congress_members table...")
            conn.execute(f"ALTER TABLE congress_members ADD COLUMN {column} TEXT")
            added.append(column)

    if added:
        conn.commit()

    return added


def get_existing_members(conn: sqlite3.Connection) -> Dict[str, Dict]:
    """Get all existing members keyed by bioguide_id."""
    cursor = conn.execute("""
        SELECT id, first_name, last_name, nickname, middle_name, birth_year, gender
        FROM congress_members
    """)

    members = {}
    for row in cursor.fetchall():
        members[row[0]] = {
            'first_name': row[1],
            'last_name': row[2],
            'nickname': row[3],
            'middle_name': row[4],
            'birth_year': row[5],
            'gender': row[6]
        }
    return members


def extract_member_data(legislator: Dict) -> Optional[Dict]:
    """Extract relevant fields from a legislator entry."""
    ids = legislator.get('id', {})
    name = legislator.get('name', {})
    bio = legislator.get('bio', {})
    terms = legislator.get('terms', [])

    bioguide_id = ids.get('bioguide')
    if not bioguide_id:
        return None

    # Get FEC IDs - can be a list, take the most recent one
    fec_ids = ids.get('fec', [])
    fec_id = fec_ids[-1] if fec_ids else None

    # Get current term data (most recent term)
    current_term = terms[-1] if terms else {}
    contact_form = current_term.get('contact_form')
    term_start = current_term.get('start')  # Format: YYYY-MM-DD
    term_end = current_term.get('end')  # Format: YYYY-MM-DD

    return {
        'bioguide_id': bioguide_id,
        'first_name': name.get('first'),
        'last_name': name.get('last'),
        'middle_name': name.get('middle'),
        'nickname': name.get('nickname'),
        'birthday': bio.get('birthday'),  # Format: YYYY-MM-DD
        'gender': bio.get('gender'),  # M or F
        # External IDs
        'wikipedia_id': ids.get('wikipedia'),
        'govtrack_id': str(ids.get('govtrack')) if ids.get('govtrack') else None,
        'opensecrets_id': ids.get('opensecrets'),
        'votesmart_id': str(ids.get('votesmart')) if ids.get('votesmart') else None,
        'fec_id': fec_id,
        'ballotpedia_id': ids.get('ballotpedia'),
        'cspan_id': str(ids.get('cspan')) if ids.get('cspan') else None,
        'lis_id': ids.get('lis'),
        # Term and contact info
        'contact_form_url': contact_form,
        'term_start_date': term_start,
        'term_end_date': term_end,
    }


def update_member(conn: sqlite3.Connection, bioguide_id: str, data: Dict, existing: Dict, dry_run: bool = False) -> List[str]:
    """Update a member with new data. Returns list of fields updated."""
    updates = []
    params = []

    # Bio fields - only update if empty in DB
    if data.get('nickname') and not existing.get('nickname'):
        updates.append('nickname = ?')
        params.append(data['nickname'])

    if data.get('middle_name') and not existing.get('middle_name'):
        updates.append('middle_name = ?')
        params.append(data['middle_name'])

    if data.get('birthday'):
        updates.append('birthday = ?')
        params.append(data['birthday'])
        try:
            birth_year = int(data['birthday'].split('-')[0])
            updates.append('birth_year = ?')
            params.append(birth_year)
        except (ValueError, IndexError):
            pass

    if data.get('gender') and not existing.get('gender'):
        updates.append('gender = ?')
        params.append(data['gender'])

    # External IDs - always update if available (overwrite)
    for col in EXTERNAL_ID_COLUMNS:
        if data.get(col):
            updates.append(f'{col} = ?')
            params.append(data[col])

    # Social media - always update if available (overwrite)
    for col in SOCIAL_MEDIA_COLUMNS:
        if data.get(col):
            updates.append(f'{col} = ?')
            params.append(data[col])

    # Term and contact info - always update if available (overwrite)
    for col in TERM_COLUMNS:
        if data.get(col):
            updates.append(f'{col} = ?')
            params.append(data[col])

    if not updates:
        return []

    updates.append('updated_at = CURRENT_TIMESTAMP')
    params.append(bioguide_id)

    sql = f"UPDATE congress_members SET {', '.join(updates)} WHERE id = ?"

    if not dry_run:
        conn.execute(sql, params)

    # Return simplified field names
    return [u.split(' = ')[0] for u in updates if u != 'updated_at = CURRENT_TIMESTAMP']


def main():
    parser = argparse.ArgumentParser(description='Import congress-legislators data')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without updating database')
    parser.add_argument('--include-historical', action='store_true', help='Also process historical legislators')
    args = parser.parse_args()

    print("=" * 70)
    print("CONGRESS-LEGISLATORS DATA IMPORT")
    print("=" * 70)
    print(f"Database: {DATABASE_PATH}")
    print(f"Dry run: {args.dry_run}")
    print()

    if not DATABASE_PATH.exists():
        print(f"ERROR: Database not found at {DATABASE_PATH}")
        return 1

    # Connect to database
    conn = sqlite3.connect(DATABASE_PATH)

    # Ensure all columns exist
    added_columns = ensure_columns(conn)
    if added_columns:
        print(f"  Added columns: {', '.join(added_columns)}")
    print()

    # Get existing members
    existing_members = get_existing_members(conn)
    print(f"Found {len(existing_members)} members in database")
    print()

    # Download current legislators
    legislators = download_yaml(LEGISLATORS_CURRENT_URL, item_name="legislators")

    if args.include_historical:
        historical = download_yaml(LEGISLATORS_HISTORICAL_URL, item_name="legislators")
        legislators.extend(historical)

    # Download social media data
    print()
    social_media = download_social_media()

    # Process legislators
    stats = {
        'matched': 0,
        'not_found': 0,
        'updated': 0,
        'no_changes': 0,
        'fields_updated': {}
    }

    print()
    print("Processing legislators...")

    for legislator in legislators:
        data = extract_member_data(legislator)
        if not data:
            continue

        bioguide_id = data['bioguide_id']

        # Merge in social media data if available
        if bioguide_id in social_media:
            data.update(social_media[bioguide_id])

        if bioguide_id not in existing_members:
            stats['not_found'] += 1
            continue

        stats['matched'] += 1
        existing = existing_members[bioguide_id]

        fields = update_member(conn, bioguide_id, data, existing, args.dry_run)

        if fields:
            stats['updated'] += 1
            for field in fields:
                stats['fields_updated'][field] = stats['fields_updated'].get(field, 0) + 1

            # Print sample updates (first 10)
            if stats['updated'] <= 10:
                name = f"{existing['first_name']} {existing['last_name']}"
                # Summarize external IDs
                id_fields = [f for f in fields if f.endswith('_id')]
                other_fields = [f for f in fields if not f.endswith('_id')]
                summary = ', '.join(other_fields)
                if id_fields:
                    summary += f" + {len(id_fields)} external IDs" if summary else f"{len(id_fields)} external IDs"
                print(f"  {name}: {summary}")
            elif stats['updated'] == 11:
                print("  ...")
        else:
            stats['no_changes'] += 1

    if not args.dry_run:
        conn.commit()

    conn.close()

    # Summary
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Legislators processed: {len(legislators)}")
    print(f"Matched in database: {stats['matched']}")
    print(f"Not found in database: {stats['not_found']}")
    print(f"Members updated: {stats['updated']}")
    print(f"No changes needed: {stats['no_changes']}")
    print()
    print("Fields updated:")

    # Group by type
    bio_fields = ['nickname', 'middle_name', 'birthday', 'birth_year', 'gender']
    id_fields = EXTERNAL_ID_COLUMNS
    social_fields = SOCIAL_MEDIA_COLUMNS
    term_fields = TERM_COLUMNS

    print("  Bio fields:")
    for field in bio_fields:
        if field in stats['fields_updated']:
            print(f"    {field}: {stats['fields_updated'][field]}")

    print("  External IDs:")
    for field in id_fields:
        if field in stats['fields_updated']:
            print(f"    {field}: {stats['fields_updated'][field]}")

    print("  Social media:")
    for field in social_fields:
        if field in stats['fields_updated']:
            print(f"    {field}: {stats['fields_updated'][field]}")

    print("  Term/contact info:")
    for field in term_fields:
        if field in stats['fields_updated']:
            print(f"    {field}: {stats['fields_updated'][field]}")

    if args.dry_run:
        print()
        print("DRY RUN - No changes were made. Run without --dry-run to apply changes.")

    return 0


if __name__ == "__main__":
    exit(main())
