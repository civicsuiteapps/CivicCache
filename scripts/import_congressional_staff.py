#!/usr/bin/env python3
"""
Congressional Staff Data Importer

Fetches staff information from the Congressional Directory (CDIR) on GovInfo.gov
and imports it into the CivicCache database.

Usage:
    python3 scripts/import_congressional_staff.py
    python3 scripts/import_congressional_staff.py --congress 119  # Specific congress
    python3 scripts/import_congressional_staff.py --check-only  # Check for updates
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# Configuration
GOVINFO_API_KEY = os.getenv('GOVINFO_API_KEY', 'DEMO_KEY')
BASE_URL = "https://api.govinfo.gov"
CDIR_COLLECTION_URL = f"{BASE_URL}/collections/CDIR"
DB_PATH = Path(__file__).parent.parent / "civic_cache.db"  # Staff records DB
MEMBER_DB_PATH = Path(__file__).parent.parent / "data" / "civic_cache.db"  # Member lookup DB
DOWNLOAD_DIR = Path(__file__).parent.parent / "data" / "cdir_downloads"

# Ensure download directory exists
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


class CDIRClient:
    """Client for interacting with GovInfo Congressional Directory API."""

    def __init__(self, api_key: str = GOVINFO_API_KEY):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'CivicCache-StaffImporter/1.0'})

    def get_latest_directory(self, congress: Optional[int] = None) -> Optional[Dict]:
        """
        Get the latest Congressional Directory package.

        Args:
            congress: Optional specific congress number to fetch

        Returns:
            Package metadata dict or None if not found
        """
        # Search from current year backwards
        current_year = datetime.now().year

        for year in range(current_year, current_year - 3, -1):
            start_date = f"{year}-01-01T00:00:00Z"
            end_date = f"{year}-12-31T23:59:59Z"

            url = f"{CDIR_COLLECTION_URL}/{start_date}/{end_date}"
            params = {
                'offset': 0,
                'pageSize': 50,
                'api_key': self.api_key
            }

            try:
                print(f"🔍 Searching for CDIR in {year}...")
                response = self.session.get(url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()

                packages = data.get('packages', [])

                if congress:
                    # Filter for specific congress
                    matching = [p for p in packages if p.get('congress') == str(congress)]
                    if matching:
                        latest = matching[0]
                        print(f"✅ Found {latest['packageId']} (Congress {latest['congress']})")
                        return latest
                else:
                    # Get the most recent
                    if packages:
                        latest = packages[0]
                        print(f"✅ Found latest: {latest['packageId']} (Congress {latest['congress']})")
                        return latest

            except requests.RequestException as e:
                print(f"⚠️  Error fetching CDIR for {year}: {e}")
                continue

        print(f"❌ No Congressional Directory found")
        return None

    def get_package_granules(self, package_id: str) -> List[Dict]:
        """
        Get all granules (individual sections) for a package.

        Args:
            package_id: Package ID like "CDIR-2024-04-25"

        Returns:
            List of granule metadata dicts
        """
        url = f"{BASE_URL}/packages/{package_id}/granules"
        params = {'offset': 0, 'pageSize': 1000, 'api_key': self.api_key}

        all_granules = []

        while True:
            try:
                response = self.session.get(url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()

                granules = data.get('granules', [])
                all_granules.extend(granules)

                # Check for next page
                next_page = data.get('nextPage')
                if not next_page:
                    break

                url = next_page
                params = {}  # Next page URL has params embedded

            except requests.RequestException as e:
                print(f"⚠️  Error fetching granules: {e}")
                break

        print(f"📄 Found {len(all_granules)} granules in {package_id}")
        return all_granules

    def download_granule_html(self, package_id: str, granule_id: str) -> Optional[str]:
        """
        Download HTML content of a specific granule.

        Args:
            package_id: Package ID like "CDIR-2024-04-25"
            granule_id: Granule ID

        Returns:
            HTML content as string or None if failed
        """
        # Check cache first
        cache_file = DOWNLOAD_DIR / f"{package_id}_{granule_id}.html"
        if cache_file.exists():
            print(f"📦 Using cached {granule_id}")
            return cache_file.read_text(encoding='utf-8')

        # Download from GovInfo
        url = f"https://www.govinfo.gov/content/pkg/{package_id}/html/{granule_id}.htm"

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            html_content = response.text

            # Cache it
            cache_file.write_text(html_content, encoding='utf-8')
            print(f"💾 Downloaded and cached {granule_id}")

            return html_content

        except requests.RequestException as e:
            print(f"⚠️  Error downloading {granule_id}: {e}")
            return None

    def download_full_text(self, package_id: str) -> Optional[str]:
        """
        Download full text content of Congressional Directory.

        Args:
            package_id: Package ID like "CDIR-2024-04-25"

        Returns:
            Full text content as string or None if failed
        """
        # Check cache first
        cache_file = DOWNLOAD_DIR / f"{package_id}.txt"
        if cache_file.exists():
            print(f"📦 Using cached {package_id}.txt ({cache_file.stat().st_size / 1024 / 1024:.1f}MB)")
            return cache_file.read_text(encoding='utf-8')

        # Download from GovInfo
        url = f"https://api.govinfo.gov/packages/{package_id}/txt"
        params = {'api_key': self.api_key}

        try:
            print(f"⬇️  Downloading {package_id}.txt (may take a minute)...")
            response = self.session.get(url, params=params, timeout=120)
            response.raise_for_status()
            text_content = response.text

            # Cache it
            cache_file.write_text(text_content, encoding='utf-8')
            print(f"💾 Downloaded and cached {package_id}.txt ({len(text_content) / 1024 / 1024:.1f}MB)")

            return text_content

        except requests.RequestException as e:
            print(f"⚠️  Error downloading {package_id}: {e}")
            return None


class StaffParser:
    """Parser for extracting staff data from CDIR text."""

    # Common staff title patterns
    # Pattern: Title\.—Name. (stops at period followed by capital letter indicating next title/sentence)
    # Matches names including middle initials (e.g. "Stephen E. Boyd")
    STAFF_PATTERNS = {
        'chief_of_staff': [
            r'Chief of Staff\.—(.+?)\.(?:\s+[A-Z][a-z]+\s+[A-Z]|$|\n)',
        ],
        'deputy_chief_of_staff': [
            r'Deputy Chief of Staff\.—(.+?)\.(?:\s+[A-Z][a-z]+\s+[A-Z]|$|\n)',
        ],
        'legislative_director': [
            r'Legislative Director\.—(.+?)\.(?:\s+[A-Z][a-z]+\s+[A-Z]|$|\n)',
        ],
        'communications_director': [
            r'Communications Director\.—(.+?)\.(?:\s+[A-Z][a-z]+\s+[A-Z]|$|\n)',
        ],
        'press_secretary': [
            r'Press Secretary\.—(.+?)\.(?:\s+[A-Z][a-z]+\s+[A-Z]|$|\n)',
        ],
        'scheduler': [
            r'Scheduler\.—(.+?)\.(?:\s+[A-Z][a-z]+\s+[A-Z]|$|\n)',
        ],
        'district_director': [
            r'District Director\.—(.+?)\.(?:\s+[A-Z][a-z]+\s+[A-Z]|$|\n)',
        ],
        'state_director': [
            r'State Director\.—(.+?)\.(?:\s+[A-Z][a-z]+\s+[A-Z]|$|\n)',
        ],
        'staff_director': [
            r'Staff Director\.—(.+?)\.(?:\s+[A-Z][a-z]+\s+[A-Z]|$|\n)',
        ],
        'minority_staff_director': [
            r'Minority Staff Director\.—(.+?)\.(?:\s+[A-Z][a-z]+\s+[A-Z]|$|\n)',
        ],
        'chief_counsel': [
            r'Chief Counsel\.—(.+?)\.(?:\s+[A-Z][a-z]+\s+[A-Z]|$|\n)',
        ],
        'director_of_operations': [
            r'Director of Operations\.—(.+?)\.(?:\s+[A-Z][a-z]+\s+[A-Z]|$|\n)',
        ],
        'office_manager': [
            r'Office Manager\.—(.+?)\.(?:\s+[A-Z][a-z]+\s+[A-Z]|$|\n)',
        ],
    }

    def parse_full_directory(self, text_content: str, member_lookup: Dict[str, Dict]) -> List[Dict]:
        """
        Parse full Congressional Directory text and extract all staff.

        Args:
            text_content: Full text of Congressional Directory
            member_lookup: Dict mapping last names to member info

        Returns:
            List of all staff records
        """
        all_staff = []

        # Split into member sections using pattern of member name headings
        # Pattern: "TOMMY TUBERVILLE" or "JERRY L. CARL" (all caps names)
        sections = re.split(r'\n([A-Z][A-Z\s\'.]+(?:Jr\.|Sr\.|III|IV|II)?),\s+(Republican|Democrat)', text_content)

        print(f"📖 Found {len(sections) // 3} potential member sections")

        # Process every 3 items (name, party, content)
        for i in range(1, len(sections), 3):
            if i+2 >= len(sections):
                break

            member_name = sections[i].strip()
            party = sections[i+1].strip()
            section_text = sections[i+2] if i+2 < len(sections) else ""

            # Extract bioguide ID and other info from section
            staff_records = self._parse_member_section_text(
                section_text, member_name, party, member_lookup
            )

            all_staff.extend(staff_records)

        return all_staff

    def _parse_member_section_text(self, text: str, member_name: str,
                                   party: str, member_lookup: Dict) -> List[Dict]:
        """
        Extract staff from a single member's section text.

        Args:
            text: Text content of member section
            member_name: Full name of the member
            party: Political party
            member_lookup: Dict mapping names to bioguide IDs

        Returns:
            List of staff record dicts for this member
        """
        staff_records = []

        # Try to find bioguide ID from member_lookup
        # Extract last name from full name
        name_parts = member_name.split()
        last_name = name_parts[-1].rstrip('.,')
        bioguide_id = None
        chamber = None

        # Look up in member database
        member_key = last_name.upper()
        if member_key in member_lookup:
            bioguide_id = member_lookup[member_key]['id']
            chamber = member_lookup[member_key]['chamber'].lower()

        # If no match, try to infer chamber from section location
        if not chamber:
            if 'Senate' in text[:500] or 'Senator' in text[:500]:
                chamber = 'senate'
            else:
                chamber = 'house'

        # Extract staff using patterns
        for title_normalized, patterns in self.STAFF_PATTERNS.items():
            for pattern in patterns:
                matches = re.findall(pattern, text)

                for match in matches:
                    # Clean up the name
                    name = self._clean_name(match)

                    if name and len(name) > 2:  # Avoid false matches
                        first, last, suffix = self._parse_name(name)

                        staff_records.append({
                            'full_name': name,
                            'first_name': first,
                            'last_name': last,
                            'suffix': suffix,
                            'title': title_normalized.replace('_', ' ').title(),
                            'title_normalized': title_normalized,
                            'office_type': 'member',
                            'chamber': chamber,
                            'member_bioguide_id': bioguide_id,
                            'member_name': member_name,
                            'office_location': 'dc'  # Default
                        })

        return staff_records

    def _clean_name(self, name: str) -> str:
        """Clean and normalize a staff member's name."""
        # Remove common artifacts
        name = re.sub(r'<[^>]+>', '', name)  # Remove HTML tags
        name = re.sub(r'\s+', ' ', name)  # Normalize whitespace
        name = name.strip().strip('.').strip(',')

        # Remove titles
        name = re.sub(r'^(Mr\.|Ms\.|Mrs\.|Dr\.)\s+', '', name, flags=re.IGNORECASE)

        return name

    def _parse_name(self, full_name: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Parse full name into components.

        Returns:
            Tuple of (first_name, last_name, suffix)
        """
        # Check for suffix
        suffix_match = re.search(r',?\s*(Jr\.|Sr\.|III|IV|II|V)\s*$', full_name, re.IGNORECASE)
        suffix = suffix_match.group(1) if suffix_match else None

        if suffix:
            full_name = full_name[:suffix_match.start()].strip()

        # Split into parts
        parts = full_name.split()

        if len(parts) == 0:
            return None, None, suffix
        elif len(parts) == 1:
            return None, parts[0], suffix
        else:
            first_name = parts[0]
            last_name = parts[-1]
            return first_name, last_name, suffix


class StaffDatabase:
    """Database interface for congressional staff data."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._ensure_schema()

    def _ensure_schema(self):
        """Create staff table if it doesn't exist."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS congressional_staff (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                -- Personal identification
                first_name TEXT,
                last_name TEXT,
                full_name TEXT NOT NULL,
                suffix TEXT,

                -- Position details
                title TEXT NOT NULL,
                title_normalized TEXT,

                -- Organizational affiliation
                office_type TEXT NOT NULL,  -- 'member', 'committee', 'institutional'
                chamber TEXT,  -- 'senate', 'house', 'joint', NULL

                -- Member office fields
                member_bioguide_id TEXT,
                member_name TEXT,
                office_location TEXT,  -- 'dc', 'district', 'state'

                -- Committee fields
                committee_code TEXT,
                committee_name TEXT,
                subcommittee_name TEXT,
                party_affiliation TEXT,  -- 'majority', 'minority', NULL

                -- Institutional fields
                institutional_office TEXT,

                -- Source tracking
                congress_number INTEGER NOT NULL,
                source_package TEXT,
                source_granule TEXT,
                effective_date TEXT,

                -- Metadata
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                is_current INTEGER DEFAULT 1,

                -- Unique constraint for member staff
                UNIQUE(member_bioguide_id, title_normalized, full_name, congress_number)
                    ON CONFLICT REPLACE
            )
        """)

        # Create indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_staff_name
            ON congressional_staff(last_name, first_name)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_staff_member
            ON congressional_staff(member_bioguide_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_staff_congress
            ON congressional_staff(congress_number)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_staff_current
            ON congressional_staff(is_current) WHERE is_current = 1
        """)

        conn.commit()
        conn.close()

        print(f"✅ Database schema ready at {self.db_path}")

    def insert_staff_records(self, records: List[Dict], congress: int, package_id: str):
        """Insert staff records into database."""
        if not records:
            print("⚠️  No records to insert")
            return 0

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        inserted = 0

        for record in records:
            try:
                cursor.execute("""
                    INSERT INTO congressional_staff (
                        first_name, last_name, full_name, suffix,
                        title, title_normalized,
                        office_type, chamber,
                        member_bioguide_id, member_name, office_location,
                        congress_number, source_package, effective_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record.get('first_name'),
                    record.get('last_name'),
                    record['full_name'],
                    record.get('suffix'),
                    record['title'],
                    record['title_normalized'],
                    record['office_type'],
                    record.get('chamber'),
                    record.get('member_bioguide_id'),
                    record.get('member_name'),
                    record.get('office_location'),
                    congress,
                    package_id,
                    datetime.now().date().isoformat()
                ))
                inserted += 1

            except sqlite3.IntegrityError:
                # Duplicate, skip
                pass

        conn.commit()
        conn.close()

        print(f"✅ Inserted {inserted} staff records")
        return inserted


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Import Congressional staff data')
    parser.add_argument('--congress', type=int, help='Specific congress number to import')
    parser.add_argument('--check-only', action='store_true', help='Only check for latest directory')
    parser.add_argument('--package-id', type=str, help='Use specific package ID (e.g., CDIR-2024-04-25)')
    args = parser.parse_args()

    print("=" * 70)
    print("Congressional Staff Data Importer")
    print("=" * 70)

    # Initialize clients
    client = CDIRClient()
    staff_parser = StaffParser()
    db = StaffDatabase()

    # Determine package to use
    if args.package_id:
        # Use specified package directly (skip API check)
        package_id = args.package_id
        # Extract congress number from package_id (e.g., CDIR-2024-04-25 -> 118)
        # For now, default to 118 if using cached file
        congress_num = args.congress if args.congress else 118
        date_issued = package_id.split('-', 1)[1] if '-' in package_id else 'unknown'

        print(f"\n📦 Using specified package: {package_id}")
        print(f"   Congress: {congress_num}th Congress")
        print(f"   Date: {date_issued}")
    else:
        # Get latest directory via API
        print(f"\n📋 Checking for latest Congressional Directory...")
        package = client.get_latest_directory(congress=args.congress)

        if not package:
            print("\n❌ No Congressional Directory found")
            return 1

        package_id = package['packageId']
        congress_num = int(package['congress'])
        date_issued = package['dateIssued']

        print(f"\n📦 Package: {package_id}")
        print(f"   Congress: {congress_num}th Congress")
        print(f"   Date: {date_issued}")

    if args.check_only:
        print("\n✅ Check complete (use without --check-only to import)")
        return 0

    # Download full text
    print(f"\n📥 Downloading full Congressional Directory text...")
    full_text = client.download_full_text(package_id)

    if not full_text:
        print("\n❌ Failed to download directory text")
        return 1

    # Load member database for bioguide ID lookup
    print(f"\n🔍 Loading member database...")

    if not MEMBER_DB_PATH.exists():
        print(f"\n⚠️  Member database not found at {MEMBER_DB_PATH}")
        print("   Continuing without bioguide ID mapping...")
        member_lookup = {}
    else:
        conn = sqlite3.connect(MEMBER_DB_PATH)
        cursor = conn.cursor()

        # Create lookup dict: last_name -> {id, chamber, full_name}
        cursor.execute("""
            SELECT id, first_name, last_name, full_name, chamber
            FROM congress_members
            WHERE id IS NOT NULL
        """)

        member_lookup = {}
        for row in cursor.fetchall():
            bioguide_id, first, last, full, chamber = row
            # Key by last name for matching
            key = last.upper()
            if key not in member_lookup:  # First match wins
                member_lookup[key] = {
                    'id': bioguide_id,
                    'chamber': chamber,
                    'full_name': full
                }

        conn.close()
        print(f"✅ Loaded {len(member_lookup)} members from database")

    # Parse all staff records
    print(f"\n📖 Parsing Congressional Directory...")
    all_staff = staff_parser.parse_full_directory(full_text, member_lookup)

    print(f"\n✅ Extracted {len(all_staff)} staff records")

    # Show sample
    if all_staff:
        print(f"\nSample records:")
        for record in all_staff[:5]:
            print(f"  • {record['full_name']} - {record['title']} ({record.get('member_name', 'Unknown')})")

    # Insert into database
    if all_staff:
        print(f"\n💾 Inserting records into database...")
        inserted_count = db.insert_staff_records(all_staff, congress_num, package_id)
        print(f"✅ Successfully inserted {inserted_count} staff records")
    else:
        print(f"\n⚠️  No staff records found to insert")

    print("\n" + "=" * 70)
    print("✅ Import process complete")
    print("=" * 70)

    return 0


if __name__ == '__main__':
    sys.exit(main())
