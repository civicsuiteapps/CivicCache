#!/usr/bin/env python3
"""
Fill missing contact form URLs by discovering them from official House/Senate websites.

This script supplements the unitedstates/congress-legislators data by:
1. Finding members missing contact_form_url
2. Constructing candidate URLs based on known patterns
3. Validating URLs are reachable
4. Updating the database with discovered contact forms

URL Patterns:
- House: https://{lastname}.house.gov/contact
         https://{firstname}{lastname}.house.gov/contact
- Senate: https://www.{lastname}.senate.gov/contact
          https://www.{lastname}.senate.gov/contact/email

Usage:
    python3 fill_contact_forms.py [--dry-run] [--limit N]
"""

import argparse
import os
import re
import sqlite3
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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

# Request timeout in seconds
REQUEST_TIMEOUT = 10

# Delay between requests to be respectful
REQUEST_DELAY = 0.5

# User agent for requests
USER_AGENT = "CivicCache/1.0 (Contact Form Discovery)"


def normalize_name(name: str) -> str:
    """Normalize a name for URL construction."""
    # Convert to lowercase
    name = name.lower()
    # Remove suffixes like Jr., III, etc.
    name = re.sub(r'\s+(jr\.?|sr\.?|i{1,3}|iv|v)$', '', name, flags=re.IGNORECASE)
    # Remove periods and apostrophes
    name = name.replace('.', '').replace("'", '')
    # Replace spaces and hyphens with nothing (for combined names)
    name = re.sub(r'[\s\-]+', '', name)
    # Remove any remaining non-alphanumeric characters
    name = re.sub(r'[^a-z]', '', name)
    return name


def get_house_url_candidates(first_name: str, last_name: str) -> List[str]:
    """Generate candidate contact URLs for a House member."""
    last = normalize_name(last_name)
    first = normalize_name(first_name)

    candidates = [
        f"https://{last}.house.gov/contact",
        f"https://{last}.house.gov/contact/email",
        f"https://{last}.house.gov/contact-me",
        f"https://{first}{last}.house.gov/contact",
        f"https://{first}{last}.house.gov/contact/email",
    ]

    # Handle hyphenated last names - try first part only
    if '-' in last_name:
        first_part = normalize_name(last_name.split('-')[0])
        candidates.append(f"https://{first_part}.house.gov/contact")

    return candidates


def get_senate_url_candidates(first_name: str, last_name: str) -> List[str]:
    """Generate candidate contact URLs for a Senate member."""
    last = normalize_name(last_name)
    first = normalize_name(first_name)

    candidates = [
        f"https://www.{last}.senate.gov/contact",
        f"https://www.{last}.senate.gov/contact/email",
        f"https://www.{last}.senate.gov/contact/share-your-opinion",
        f"https://www.{last}.senate.gov/contact/email-me",
        f"https://{last}.senate.gov/contact",
    ]

    # Handle hyphenated last names
    if '-' in last_name:
        first_part = normalize_name(last_name.split('-')[0])
        candidates.append(f"https://www.{first_part}.senate.gov/contact")

    return candidates


def check_url(url: str) -> Tuple[bool, Optional[str]]:
    """
    Check if a URL is reachable and returns a valid page.
    Returns (success, final_url) where final_url may differ due to redirects.
    """
    try:
        request = urllib.request.Request(
            url,
            method='HEAD',
            headers={'User-Agent': USER_AGENT}
        )
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            # Check for successful response
            if response.status == 200:
                return True, response.url
            return False, None
    except urllib.error.HTTPError as e:
        # Try GET if HEAD is not allowed (405)
        if e.code == 405:
            try:
                request = urllib.request.Request(
                    url,
                    headers={'User-Agent': USER_AGENT}
                )
                with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                    if response.status == 200:
                        return True, response.url
            except:
                pass
        return False, None
    except (urllib.error.URLError, TimeoutError, Exception):
        return False, None


def discover_contact_url(member: Dict) -> Optional[str]:
    """Try to discover the contact form URL for a member."""
    chamber = member['chamber']
    first_name = member['first_name']
    last_name = member['last_name']
    nickname = member.get('nickname')

    # Get candidate URLs based on chamber
    if chamber == 'House':
        candidates = get_house_url_candidates(first_name, last_name)
        # Also try nickname if available
        if nickname:
            candidates.extend(get_house_url_candidates(nickname, last_name))
    else:  # Senate
        candidates = get_senate_url_candidates(first_name, last_name)
        if nickname:
            candidates.extend(get_senate_url_candidates(nickname, last_name))

    # Remove duplicates while preserving order
    seen = set()
    unique_candidates = []
    for url in candidates:
        if url not in seen:
            seen.add(url)
            unique_candidates.append(url)

    # Try each candidate URL
    for url in unique_candidates:
        success, final_url = check_url(url)
        if success:
            return final_url or url
        time.sleep(REQUEST_DELAY)

    return None


def get_members_missing_contact(conn: sqlite3.Connection) -> List[Dict]:
    """Get all members missing contact_form_url."""
    cursor = conn.execute("""
        SELECT id, first_name, last_name, nickname, chamber, full_name
        FROM congress_members
        WHERE contact_form_url IS NULL OR contact_form_url = ''
        ORDER BY chamber, last_name
    """)

    members = []
    for row in cursor.fetchall():
        members.append({
            'id': row[0],
            'first_name': row[1],
            'last_name': row[2],
            'nickname': row[3],
            'chamber': row[4],
            'full_name': row[5]
        })
    return members


def update_contact_url(conn: sqlite3.Connection, member_id: str, url: str, dry_run: bool = False) -> None:
    """Update a member's contact_form_url."""
    if not dry_run:
        conn.execute("""
            UPDATE congress_members
            SET contact_form_url = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (url, member_id))


def main():
    parser = argparse.ArgumentParser(description='Fill missing contact form URLs')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without updating database')
    parser.add_argument('--limit', type=int, default=0, help='Limit number of members to process (0 = all)')
    parser.add_argument('--chamber', choices=['House', 'Senate'], help='Only process specific chamber')
    args = parser.parse_args()

    print("=" * 70)
    print("CONTACT FORM URL DISCOVERY")
    print("=" * 70)
    print(f"Database: {DATABASE_PATH}")
    print(f"Dry run: {args.dry_run}")
    if args.limit:
        print(f"Limit: {args.limit} members")
    if args.chamber:
        print(f"Chamber: {args.chamber} only")
    print()

    if not DATABASE_PATH.exists():
        print(f"ERROR: Database not found at {DATABASE_PATH}")
        return 1

    # Connect to database
    conn = sqlite3.connect(DATABASE_PATH)

    # Get members missing contact URLs
    members = get_members_missing_contact(conn)

    # Filter by chamber if specified
    if args.chamber:
        members = [m for m in members if m['chamber'] == args.chamber]

    print(f"Found {len(members)} members missing contact_form_url")
    print()

    if args.limit:
        members = members[:args.limit]
        print(f"Processing first {len(members)} members...")
        print()

    # Statistics
    stats = {
        'processed': 0,
        'found': 0,
        'not_found': 0,
        'by_chamber': {'House': 0, 'Senate': 0}
    }

    print("Discovering contact URLs...")
    print("-" * 70)

    for member in members:
        stats['processed'] += 1
        name = member['full_name']
        chamber = member['chamber']

        # Show progress
        print(f"  [{stats['processed']}/{len(members)}] {name} ({chamber})...", end=' ', flush=True)

        url = discover_contact_url(member)

        if url:
            stats['found'] += 1
            stats['by_chamber'][chamber] += 1
            update_contact_url(conn, member['id'], url, args.dry_run)
            print(f"FOUND: {url}")
        else:
            stats['not_found'] += 1
            print("not found")

    if not args.dry_run:
        conn.commit()

    conn.close()

    # Summary
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Members processed: {stats['processed']}")
    print(f"Contact URLs found: {stats['found']}")
    print(f"  House: {stats['by_chamber']['House']}")
    print(f"  Senate: {stats['by_chamber']['Senate']}")
    print(f"Not found: {stats['not_found']}")

    if stats['processed'] > 0:
        success_rate = stats['found'] / stats['processed'] * 100
        print(f"Success rate: {success_rate:.1f}%")

    if args.dry_run:
        print()
        print("DRY RUN - No changes were made. Run without --dry-run to apply changes.")

    return 0


if __name__ == "__main__":
    exit(main())
