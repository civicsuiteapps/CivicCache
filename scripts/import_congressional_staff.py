#!/usr/bin/env python3
"""
Congressional Staff Data Importer

Fetches staff information and imports it into the CivicCache database.
Supports two data sources:

1. GovInfo Congressional Directory (CDIR) - Official publication
2. Member website scraping - Direct from congressional websites

Usage:
    # GovInfo CDIR import (official directory, when available)
    python3 scripts/import_congressional_staff.py
    python3 scripts/import_congressional_staff.py --congress 118  # Specific congress
    python3 scripts/import_congressional_staff.py --check-only    # Check for updates
    python3 scripts/import_congressional_staff.py --package-id CDIR-2024-04-25

    # Web scraping (for current 119th Congress)
    python3 scripts/import_congressional_staff.py --scrape-web
    python3 scripts/import_congressional_staff.py --scrape-web --chamber senate
    python3 scripts/import_congressional_staff.py --scrape-web --chamber house --limit 50
    python3 scripts/import_congressional_staff.py --scrape-web --verbose

    # Include press release parsing (searches for staff announcements)
    python3 scripts/import_congressional_staff.py --scrape-web --include-press-releases

    # House Statement of Disbursements (100% House coverage)
    python3 scripts/import_congressional_staff.py --import-house-sod

    # Senate Secretary Report (100% Senate coverage)
    python3 scripts/import_congressional_staff.py --import-senate-pdf

    # Import both chambers
    python3 scripts/import_congressional_staff.py --import-house-sod && python3 scripts/import_congressional_staff.py --import-senate-pdf

Note: The 119th Congress Directory won't be published until September 2026.

Data Sources (in order of coverage):
1. --import-house-sod: House Statement of Disbursements (100% House coverage, CSV)
2. --import-senate-pdf: Senate Secretary Report (100% Senate coverage, PDF)
3. --scrape-web: Member website staff directories (~30% coverage)
4. --include-press-releases: Press release announcements (low yield)
5. Default: GovInfo CDIR (when published)
"""

import argparse
import json
import os
import random
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

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


class WebStaffScraper:
    """Scraper for extracting staff data from member websites."""

    # Common staff page URL patterns to try
    STAFF_PAGE_PATTERNS = [
        '/contact/staff-directory/',
        '/contact/staff-directory',
        '/contact/staff/',
        '/contact/staff',
        '/contact/staff.htm',
        '/about/staff/',
        '/about/staff',
        '/about/team/',
        '/about/team',
        '/staff/',
        '/staff',
        '/team/',
        '/team',
        '/about-me/staff/',
    ]

    # Common staff title patterns for web pages
    TITLE_NORMALIZATIONS = {
        'chief of staff': 'chief_of_staff',
        'deputy chief of staff': 'deputy_chief_of_staff',
        'legislative director': 'legislative_director',
        'communications director': 'communications_director',
        'director of communications': 'communications_director',
        'deputy communications director': 'deputy_communications_director',
        'press secretary': 'press_secretary',
        'deputy press secretary': 'deputy_press_secretary',
        'scheduler': 'scheduler',
        'scheduling director': 'scheduler',
        'director of scheduling': 'scheduler',
        'district director': 'district_director',
        'state director': 'state_director',
        'regional director': 'regional_director',
        'caseworker': 'caseworker',
        'case worker': 'caseworker',
        'staff assistant': 'staff_assistant',
        'legislative assistant': 'legislative_assistant',
        'legislative correspondent': 'legislative_correspondent',
        'legislative aide': 'legislative_assistant',
        'legislative fellow': 'legislative_fellow',
        'counsel': 'counsel',
        'general counsel': 'counsel',
        'chief counsel': 'chief_counsel',
        'associate counsel': 'associate_counsel',
        'policy advisor': 'policy_advisor',
        'policy adviser': 'policy_advisor',
        'policy director': 'policy_director',
        'senior advisor': 'senior_advisor',
        'senior adviser': 'senior_advisor',
        'national security adviser': 'national_security_advisor',
        'national security advisor': 'national_security_advisor',
        'foreign policy': 'foreign_policy_advisor',
        'executive assistant': 'executive_assistant',
        'office manager': 'office_manager',
        'systems administrator': 'systems_administrator',
        'digital director': 'digital_director',
        'digital manager': 'digital_manager',
        'outreach coordinator': 'outreach_coordinator',
        'outreach director': 'outreach_director',
        'constituent services': 'constituent_services',
        'constituent services director': 'constituent_services_director',
        'field representative': 'field_representative',
        'military liaison': 'military_liaison',
        'veterans liaison': 'veterans_liaison',
        'appropriations director': 'appropriations_director',
        'appropriations assistant': 'appropriations_assistant',
        'special assistant': 'special_assistant',
        'projects director': 'projects_director',
        'projects coordinator': 'projects_coordinator',
        'director of operations': 'director_of_operations',
        'operations director': 'director_of_operations',
    }

    def __init__(self, rate_limit_delay: float = 1.0, verbose: bool = False):
        """
        Initialize the web scraper.

        Args:
            rate_limit_delay: Seconds to wait between requests
            verbose: Enable verbose output
        """
        self.rate_limit_delay = rate_limit_delay
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'CivicCache-StaffScraper/1.0 (civic data research)',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        })

    def _rate_limit(self):
        """Apply rate limiting with slight randomization."""
        delay = self.rate_limit_delay + random.uniform(0, 0.5)
        time.sleep(delay)

    def find_staff_page(self, base_url: str) -> Optional[str]:
        """
        Find the staff directory page on a member's website.

        Args:
            base_url: Member's website base URL (e.g., https://coons.senate.gov)

        Returns:
            Full URL to staff page or None if not found
        """
        # Normalize base URL
        if not base_url.startswith('http'):
            base_url = f'https://{base_url}'
        base_url = base_url.rstrip('/')

        # Try common patterns
        for pattern in self.STAFF_PAGE_PATTERNS:
            url = f"{base_url}{pattern}"
            try:
                self._rate_limit()
                response = self.session.get(url, timeout=15, allow_redirects=True)

                if response.status_code == 200:
                    # Check if page actually has staff content
                    if self._looks_like_staff_page(response.text):
                        if self.verbose:
                            print(f"  Found staff page: {url}")
                        return url

            except requests.RequestException as e:
                if self.verbose:
                    print(f"  Error checking {url}: {e}")
                continue

        # Try searching the main page for staff links
        staff_link = self._find_staff_link_on_homepage(base_url)
        if staff_link:
            return staff_link

        return None

    def _looks_like_staff_page(self, html: str) -> bool:
        """Check if HTML content appears to be a staff directory page."""
        soup = BeautifulSoup(html, 'html.parser')
        text = soup.get_text().lower()

        # Look for staff-related keywords
        staff_keywords = ['chief of staff', 'legislative director', 'press secretary',
                         'communications director', 'scheduler', 'staff directory']

        matches = sum(1 for kw in staff_keywords if kw in text)
        return matches >= 2

    def _find_staff_link_on_homepage(self, base_url: str) -> Optional[str]:
        """Search homepage for links to staff pages."""
        try:
            self._rate_limit()
            response = self.session.get(base_url, timeout=15)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            # Look for links containing staff-related text
            for link in soup.find_all('a', href=True):
                text = link.get_text().lower()
                href = link['href'].lower()

                if any(word in text or word in href
                       for word in ['staff', 'team', 'office']):
                    # Resolve relative URLs
                    full_url = urljoin(base_url, link['href'])

                    # Verify it's a valid page
                    try:
                        self._rate_limit()
                        check = self.session.get(full_url, timeout=10)
                        if check.status_code == 200 and self._looks_like_staff_page(check.text):
                            return full_url
                    except:
                        continue

        except requests.RequestException:
            pass

        return None

    def parse_staff_page(self, url: str, member_info: Dict) -> List[Dict]:
        """
        Parse a staff directory page and extract staff records.

        Args:
            url: URL of the staff page
            member_info: Dict with member details (bioguide_id, name, chamber, etc.)

        Returns:
            List of staff record dicts
        """
        try:
            self._rate_limit()
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"  Error fetching {url}: {e}")
            return []

        html = response.text
        soup = BeautifulSoup(html, 'html.parser')

        # Determine parser based on URL
        if 'senate.gov' in url:
            return self._parse_senate_staff_page(soup, member_info, url)
        else:
            return self._parse_house_staff_page(soup, member_info, url)

    def _parse_senate_staff_page(self, soup: BeautifulSoup, member_info: Dict, url: str) -> List[Dict]:
        """Parse Senate-style staff directory page."""
        staff_records = []
        seen_names = set()

        # Remove meta, script, style tags to get clean content
        for tag in soup.find_all(['meta', 'script', 'style', 'noscript']):
            tag.decompose()

        # Find main content area
        main = soup.find('main') or soup.find('article') or soup.find('div', class_='content') or soup.find('body')
        if not main:
            main = soup

        current_location = 'dc'

        # Pattern 1: Look for <u> tags with titles followed by names
        # Common format: <p><u>Title<br/></u>Name</p>
        for p in main.find_all('p'):
            u_tag = p.find('u')
            if u_tag:
                title = u_tag.get_text(strip=True)
                # Get the text after the u tag (the name)
                # Remove the u tag content from full text
                full_text = p.get_text(strip=True)
                # The name is typically what follows the title
                if title and len(title) > 3:
                    # Extract name - it's the text after title but before any em/i tags
                    name = full_text.replace(title, '', 1).strip()
                    # Remove any topic/area descriptions (usually in em/i tags or all caps)
                    em_tag = p.find(['em', 'i'])
                    if em_tag:
                        topic = em_tag.get_text(strip=True)
                        name = name.replace(topic, '').strip()

                    # Clean up the name
                    name = re.sub(r'\s+', ' ', name).strip()

                    if name and len(name) > 2 and name not in seen_names:
                        record = self._create_staff_record(name, title, member_info, current_location)
                        if record:
                            record['scrape_url'] = url
                            staff_records.append(record)
                            seen_names.add(name)

        # Pattern 2: Track location from headings and look for other formats
        for element in main.find_all(['h2', 'h3', 'h4']):
            text = element.get_text(strip=True).lower()
            if any(word in text for word in ['state office', 'district office', 'wilmington', 'dover', 'newark', 'georgetown']):
                current_location = 'district'
            elif any(word in text for word in ['washington', 'd.c.', 'dc office']):
                current_location = 'dc'

        # Pattern 3: Look for li items with name-title patterns
        for item in main.find_all('li'):
            text = item.get_text(strip=True)
            if not text or len(text) < 5:
                continue

            record = self._extract_staff_from_text(text, member_info, current_location)
            if record and record['full_name'] not in seen_names:
                record['scrape_url'] = url
                staff_records.append(record)
                seen_names.add(record['full_name'])

        # Pattern 4: Look for strong/b tags as titles
        for strong in main.find_all(['strong', 'b']):
            title_text = strong.get_text(strip=True)
            if self._normalize_title(title_text):
                # Get sibling or parent text for name
                next_sibling = strong.next_sibling
                if next_sibling:
                    name_text = str(next_sibling).strip() if hasattr(next_sibling, 'strip') else next_sibling.get_text(strip=True) if hasattr(next_sibling, 'get_text') else ''
                    name_text = re.sub(r'^[:\s-]+', '', str(name_text)).strip()
                    if name_text and len(name_text) > 2 and name_text not in seen_names:
                        # Validate it looks like a name
                        if re.match(r'^[A-Z][a-z]+\s+', name_text):
                            record = self._create_staff_record(name_text, title_text, member_info, current_location)
                            if record:
                                record['scrape_url'] = url
                                staff_records.append(record)
                                seen_names.add(name_text)

        return staff_records

    def _parse_house_staff_page(self, soup: BeautifulSoup, member_info: Dict, url: str) -> List[Dict]:
        """Parse House-style staff page."""
        staff_records = []

        main = soup.find('main') or soup.find('article') or soup.find('div', class_='content') or soup

        current_location = 'dc'

        # Look for tables (common in House pages)
        for table in main.find_all('table'):
            for row in table.find_all('tr'):
                cells = row.find_all(['td', 'th'])
                if len(cells) >= 2:
                    # Try name in first column, title in second
                    name = cells[0].get_text(strip=True)
                    title = cells[1].get_text(strip=True)

                    if name and title and len(name) > 2:
                        record = self._create_staff_record(name, title, member_info, current_location)
                        if record:
                            record['scrape_url'] = url
                            staff_records.append(record)

        # Also check for list-based formats
        for element in main.find_all(['p', 'li', 'div']):
            text = element.get_text(strip=True)

            # Track location
            text_lower = text.lower()
            if 'district office' in text_lower:
                current_location = 'district'
            elif 'washington' in text_lower or 'd.c.' in text_lower:
                current_location = 'dc'

            record = self._extract_staff_from_text(text, member_info, current_location)
            if record and record not in staff_records:
                record['scrape_url'] = url
                staff_records.append(record)

        return staff_records

    def _extract_staff_from_text(self, text: str, member_info: Dict, location: str) -> Optional[Dict]:
        """
        Extract staff name and title from a text string.

        Common patterns:
        - "Name - Title"
        - "Name, Title"
        - "Title: Name"
        - "Title—Name"
        """
        if not text or len(text) < 5:
            return None

        # Skip navigation/footer elements
        skip_words = ['email', 'phone', 'fax', 'contact', 'home', 'menu', 'privacy', 'copyright']
        if any(word in text.lower() for word in skip_words):
            return None

        name = None
        title = None

        # Pattern 1: "Name - Title" or "Name — Title"
        match = re.match(r'^([A-Z][A-Za-z\s\.\'-]+?)\s*[-–—]\s*(.+)$', text)
        if match:
            name, title = match.groups()

        # Pattern 2: "Name, Title" (if name has capital letters and title matches known titles)
        if not name:
            match = re.match(r'^([A-Z][A-Za-z\s\.\'-]+?),\s*(.+)$', text)
            if match:
                potential_name, potential_title = match.groups()
                if self._normalize_title(potential_title):
                    name, title = potential_name, potential_title

        # Pattern 3: "Title: Name"
        if not name:
            match = re.match(r'^(.+?):\s*([A-Z][A-Za-z\s\.\'-]+)$', text)
            if match:
                potential_title, potential_name = match.groups()
                if self._normalize_title(potential_title):
                    title, name = potential_title, potential_name

        if name and title:
            return self._create_staff_record(name.strip(), title.strip(), member_info, location)

        return None

    def _create_staff_record(self, name: str, title: str, member_info: Dict, location: str) -> Optional[Dict]:
        """Create a standardized staff record dict."""
        # Clean and validate name
        name = self._clean_name(name)

        # Validate name
        if not self._is_valid_name(name):
            return None

        # Normalize title
        title_normalized = self._normalize_title(title)

        # Parse name components
        first_name, last_name, suffix = self._parse_name(name)

        return {
            'full_name': name,
            'first_name': first_name,
            'last_name': last_name,
            'suffix': suffix,
            'title': title.strip()[:100],  # Keep original, limit length
            'title_normalized': title_normalized,
            'office_type': 'member',
            'chamber': member_info.get('chamber', '').lower(),
            'member_bioguide_id': member_info.get('bioguide_id'),
            'member_name': member_info.get('full_name'),
            'office_location': location,
            'scrape_source': 'website',
            'scrape_date': datetime.now().date().isoformat(),
        }

    def _clean_name(self, name: str) -> str:
        """Clean and normalize a name."""
        # Remove HTML artifacts
        name = re.sub(r'<[^>]+>', '', name)
        # Remove email addresses
        name = re.sub(r'\S+@\S+', '', name)
        # Remove phone numbers
        name = re.sub(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', '', name)
        # Normalize whitespace
        name = re.sub(r'\s+', ' ', name)
        # Remove leading/trailing punctuation
        name = name.strip().strip('.,;:-–—')

        return name.strip()

    def _is_valid_name(self, name: str) -> bool:
        """Check if extracted text looks like a valid person name."""
        if not name or len(name) < 3:
            return False

        # Reject common non-name patterns
        invalid_patterns = [
            r'^\d',                  # Starts with number
            r'sign\s*up',           # Newsletter signup
            r'newsletter',
            r'subscribe',
            r'click\s*here',
            r'read\s*more',
            r'learn\s*more',
            r'contact\s*us',
            r'office\s*(hours|location)',
            r'^(home|about|contact|staff|directory|menu|privacy|terms)$',
            r'@',                    # Email addresses
            r'^\(',                  # Starts with parenthesis
            r'https?://',           # URLs
            r'^\d{3}',              # Phone numbers
            r'building|suite|room|floor|street|avenue|road|blvd',  # Addresses
        ]

        name_lower = name.lower()
        for pattern in invalid_patterns:
            if re.search(pattern, name_lower):
                return False

        # Must have at least one space (first and last name)
        if ' ' not in name:
            return False

        # Must start with a capital letter
        if not re.match(r'^[A-Z]', name):
            return False

        # Must look like a name (letters, spaces, hyphens, apostrophes, periods)
        if not re.match(r'^[A-Za-z][A-Za-z\s\.\'\-"]+$', name):
            return False

        return True

    def _normalize_title(self, title: str) -> Optional[str]:
        """Normalize a title to standard form."""
        if not title:
            return None

        title_lower = title.lower().strip()

        # Check exact and partial matches
        for pattern, normalized in self.TITLE_NORMALIZATIONS.items():
            if pattern in title_lower:
                return normalized

        return None

    def _parse_name(self, full_name: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Parse full name into components."""
        # Check for suffix
        suffix_match = re.search(r',?\s*(Jr\.|Sr\.|III|IV|II|V)\s*$', full_name, re.IGNORECASE)
        suffix = suffix_match.group(1) if suffix_match else None

        if suffix:
            full_name = full_name[:suffix_match.start()].strip()

        parts = full_name.split()

        if len(parts) == 0:
            return None, None, suffix
        elif len(parts) == 1:
            return None, parts[0], suffix
        else:
            return parts[0], parts[-1], suffix

    def scrape_member_staff(self, member: Dict) -> Tuple[Optional[str], List[Dict]]:
        """
        Scrape staff for a single member.

        Args:
            member: Dict with id, full_name, chamber, website_url

        Returns:
            Tuple of (staff_page_url or None, list of staff records)
        """
        website = member.get('website_url')
        if not website:
            return None, []

        print(f"  Checking {member['full_name']}...")

        # Find staff page
        staff_page = self.find_staff_page(website)

        if not staff_page:
            if self.verbose:
                print(f"    No staff page found")
            return None, []

        # Parse staff page
        member_info = {
            'bioguide_id': member.get('id'),
            'full_name': member.get('full_name'),
            'chamber': member.get('chamber'),
        }

        staff_records = self.parse_staff_page(staff_page, member_info)

        if self.verbose:
            print(f"    Found {len(staff_records)} staff members")

        return staff_page, staff_records


class PressReleaseParser:
    """Parser for extracting staff announcements from press releases."""

    # Press release page URL patterns
    PRESS_PAGE_PATTERNS = [
        '/media/press-releases',
        '/press-releases',
        '/newsroom',
        '/news/press-releases',
        '/news',
        '/media',
    ]

    # Keywords that indicate a staff announcement
    ANNOUNCEMENT_KEYWORDS = [
        'announces staff',
        'names staff',
        'hires',
        'announces.*chief of staff',
        'names.*director',
        'appoints',
        'joins.*team',
        'new team',
        'staff announcement',
        'announces.*team',
    ]

    # Patterns for extracting staff from announcement text
    STAFF_EXTRACTION_PATTERNS = [
        # "Name as Title" pattern
        r'([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)\s+as\s+([A-Za-z\s]+(?:Director|Chief|Secretary|Counsel|Advisor|Assistant|Manager))',
        # "Name, Title" in lists
        r'([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+),\s+(Chief of Staff|Legislative Director|Communications Director|Press Secretary|District Director|State Director|Scheduler)',
        # "Title: Name" pattern
        r'(Chief of Staff|Legislative Director|Communications Director|Press Secretary|District Director|State Director):\s*([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)',
        # Bullet point style "Name - Title"
        r'[•\-\*]\s*([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)\s*[-–—]\s*([A-Za-z\s]+(?:Director|Chief|Secretary|Counsel|Advisor|Assistant))',
    ]

    def __init__(self, rate_limit_delay: float = 1.0, verbose: bool = False):
        self.rate_limit_delay = rate_limit_delay
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'CivicCache-PressParser/1.0 (civic data research)',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        })

    def _rate_limit(self):
        """Apply rate limiting."""
        time.sleep(self.rate_limit_delay + random.uniform(0, 0.3))

    def find_press_page(self, base_url: str) -> Optional[str]:
        """Find the press releases page on a member's website."""
        if not base_url.startswith('http'):
            base_url = f'https://{base_url}'
        base_url = base_url.rstrip('/')

        for pattern in self.PRESS_PAGE_PATTERNS:
            url = f"{base_url}{pattern}"
            try:
                self._rate_limit()
                response = self.session.get(url, timeout=15, allow_redirects=True)
                if response.status_code == 200:
                    if self.verbose:
                        print(f"    Found press page: {url}")
                    return url
            except requests.RequestException:
                continue

        return None

    def find_staff_announcements(self, press_page_url: str, base_url: str) -> List[str]:
        """
        Find press releases that announce staff.

        Returns list of URLs to staff announcement press releases.
        """
        announcements = []

        try:
            self._rate_limit()
            response = self.session.get(press_page_url, timeout=15)
            response.raise_for_status()
        except requests.RequestException:
            return announcements

        soup = BeautifulSoup(response.text, 'html.parser')

        # Find all links
        for link in soup.find_all('a', href=True):
            text = link.get_text().lower()
            href = link['href']

            # Check if headline mentions staff
            for keyword in self.ANNOUNCEMENT_KEYWORDS:
                if re.search(keyword, text, re.IGNORECASE):
                    full_url = href if href.startswith('http') else urljoin(base_url, href)
                    if full_url not in announcements:
                        announcements.append(full_url)
                        if self.verbose:
                            print(f"    Found announcement: {text[:60]}...")
                    break

        return announcements

    def parse_announcement(self, announcement_url: str, member_info: Dict) -> List[Dict]:
        """
        Parse a staff announcement press release and extract staff records.
        """
        staff_records = []

        try:
            self._rate_limit()
            response = self.session.get(announcement_url, timeout=15)
            response.raise_for_status()
        except requests.RequestException:
            return staff_records

        soup = BeautifulSoup(response.text, 'html.parser')

        # Remove scripts and styles
        for tag in soup.find_all(['script', 'style', 'meta']):
            tag.decompose()

        text = soup.get_text()

        # Try each extraction pattern
        for pattern in self.STAFF_EXTRACTION_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                # Patterns return (name, title) or (title, name)
                if len(match) == 2:
                    # Determine which is name and which is title
                    if self._looks_like_title(match[0]):
                        title, name = match
                    else:
                        name, title = match

                    name = name.strip()
                    title = title.strip()

                    # Validate
                    if self._is_valid_staff_entry(name, title):
                        record = self._create_staff_record(name, title, member_info, announcement_url)
                        if record and record['full_name'] not in [r['full_name'] for r in staff_records]:
                            staff_records.append(record)

        return staff_records

    def _looks_like_title(self, text: str) -> bool:
        """Check if text looks like a job title."""
        title_words = ['chief', 'director', 'secretary', 'counsel', 'advisor',
                      'adviser', 'assistant', 'manager', 'coordinator']
        return any(word in text.lower() for word in title_words)

    def _is_valid_staff_entry(self, name: str, title: str) -> bool:
        """Validate extracted staff entry."""
        if not name or not title:
            return False
        if len(name) < 5 or len(title) < 5:
            return False
        # Name should have first and last
        if ' ' not in name:
            return False
        # Name should start with capital
        if not re.match(r'^[A-Z]', name):
            return False
        return True

    def _create_staff_record(self, name: str, title: str, member_info: Dict, source_url: str) -> Optional[Dict]:
        """Create a staff record dict."""
        # Parse name
        parts = name.split()
        first_name = parts[0] if parts else None
        last_name = parts[-1] if len(parts) > 1 else None

        # Normalize title
        title_normalized = self._normalize_title(title)

        return {
            'full_name': name,
            'first_name': first_name,
            'last_name': last_name,
            'suffix': None,
            'title': title[:100],
            'title_normalized': title_normalized,
            'office_type': 'member',
            'chamber': member_info.get('chamber', '').lower(),
            'member_bioguide_id': member_info.get('bioguide_id'),
            'member_name': member_info.get('full_name'),
            'office_location': 'dc',
            'scrape_source': 'press_release',
            'scrape_url': source_url,
            'scrape_date': datetime.now().date().isoformat(),
        }

    def _normalize_title(self, title: str) -> Optional[str]:
        """Normalize title to standard form."""
        title_lower = title.lower().strip()

        normalizations = {
            'chief of staff': 'chief_of_staff',
            'deputy chief of staff': 'deputy_chief_of_staff',
            'legislative director': 'legislative_director',
            'communications director': 'communications_director',
            'press secretary': 'press_secretary',
            'scheduler': 'scheduler',
            'district director': 'district_director',
            'state director': 'state_director',
            'policy director': 'policy_director',
            'policy advisor': 'policy_advisor',
        }

        for pattern, normalized in normalizations.items():
            if pattern in title_lower:
                return normalized

        return None

    def scrape_member_press_releases(self, member: Dict) -> Tuple[Optional[str], List[Dict]]:
        """
        Scrape staff from a member's press releases.

        Returns (press_page_url, list of staff records).
        """
        website = member.get('website_url')
        if not website:
            return None, []

        if self.verbose:
            print(f"  Searching press releases for {member['full_name']}...")

        # Find press page
        press_page = self.find_press_page(website)
        if not press_page:
            return None, []

        # Find staff announcements
        announcements = self.find_staff_announcements(press_page, website)

        if not announcements:
            if self.verbose:
                print(f"    No staff announcements found")
            return press_page, []

        # Parse each announcement
        all_staff = []
        member_info = {
            'bioguide_id': member.get('id'),
            'full_name': member.get('full_name'),
            'chamber': member.get('chamber'),
        }

        for url in announcements[:3]:  # Limit to 3 most recent
            staff = self.parse_announcement(url, member_info)
            all_staff.extend(staff)

        if self.verbose and all_staff:
            print(f"    Found {len(all_staff)} staff from press releases")

        return press_page, all_staff


class HouseSODImporter:
    """
    Importer for House Statement of Disbursements (SOD) data.

    The SOD is the most comprehensive source for House staff data,
    containing payroll records for every House employee.

    Data source: https://www.house.gov/the-house-explained/open-government/statement-of-disbursements
    Format: CSV with quarterly payroll data
    Coverage: 100% of House staff
    """

    SOD_BASE_URL = "https://www.house.gov/the-house-explained/open-government/statement-of-disbursements"
    DOWNLOAD_DIR = Path(__file__).parent.parent / "data" / "sod_downloads"

    # Title normalization map
    TITLE_NORMALIZATIONS = {
        'chief of staff': 'chief_of_staff',
        'deputy chief of staff': 'deputy_chief_of_staff',
        'legislative director': 'legislative_director',
        'communications director': 'communications_director',
        'director of communications': 'communications_director',
        'press secretary': 'press_secretary',
        'scheduler': 'scheduler',
        'director of scheduling': 'scheduler',
        'district director': 'district_director',
        'district liaison': 'district_liaison',
        'caseworker': 'caseworker',
        'staff assistant': 'staff_assistant',
        'legislative assistant': 'legislative_assistant',
        'legislative correspondent': 'legislative_correspondent',
        'legislative aide': 'legislative_aide',
        'counsel': 'counsel',
        'general counsel': 'counsel',
        'policy advisor': 'policy_advisor',
        'policy director': 'policy_director',
        'senior advisor': 'senior_advisor',
        'senior policy advisor': 'senior_policy_advisor',
        'executive assistant': 'executive_assistant',
        'office manager': 'office_manager',
        'systems administrator': 'systems_administrator',
        'digital director': 'digital_director',
        'outreach coordinator': 'outreach_coordinator',
        'constituent services': 'constituent_services',
        'field representative': 'field_representative',
        'director of operations': 'director_of_operations',
        'director of community outreach': 'community_outreach_director',
        'director of member services': 'member_services_director',
    }

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'CivicCache-SODImporter/1.0'})
        self.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    def find_latest_sod_csv(self) -> Optional[str]:
        """Find the URL of the latest SOD detail CSV."""
        try:
            response = self.session.get(self.SOD_BASE_URL, timeout=30)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            # Look for detail grid CSV links
            for link in soup.find_all('a', href=True):
                href = link['href']
                if 'DETAIL' in href.upper() and href.endswith('.csv'):
                    full_url = href if href.startswith('http') else f"https://www.house.gov{href}"
                    if self.verbose:
                        print(f"Found SOD CSV: {full_url}")
                    return full_url

            return None

        except requests.RequestException as e:
            print(f"Error finding SOD CSV: {e}")
            return None

    def download_sod_csv(self, url: str) -> Optional[Path]:
        """Download SOD CSV file."""
        filename = url.split('/')[-1]
        filepath = self.DOWNLOAD_DIR / filename

        # Check cache
        if filepath.exists():
            if self.verbose:
                print(f"Using cached SOD: {filepath}")
            return filepath

        try:
            print(f"Downloading SOD CSV ({filename})...")
            response = self.session.get(url, timeout=120, stream=True)
            response.raise_for_status()

            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            print(f"Downloaded: {filepath} ({filepath.stat().st_size / 1024 / 1024:.1f} MB)")
            return filepath

        except requests.RequestException as e:
            print(f"Error downloading SOD: {e}")
            return None

    def parse_sod_csv(self, filepath: Path, member_lookup: Dict[str, str]) -> List[Dict]:
        """
        Parse SOD CSV and extract member staff records.

        Args:
            filepath: Path to SOD CSV file
            member_lookup: Dict mapping member names to bioguide IDs

        Returns:
            List of staff record dicts
        """
        import csv

        staff_records = []
        seen = set()  # Track duplicates

        print(f"Parsing SOD CSV...")

        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)

            for row in reader:
                # Filter to member office personnel records
                org = row.get('ORGANIZATION', '')
                program = row.get('PROGRAM', '')
                sort_desc = row.get('SORT SUBTOTAL DESCRIPTION', '')
                sort_seq = row.get('SORT SEQUENCE', '')

                # Skip non-member offices
                if 'HON.' not in org:
                    continue

                # Only include personnel compensation records (code 11)
                if 'PERSONNEL COMPENSATION' not in sort_desc.upper():
                    continue

                # Skip subtotals and totals (only want DETAIL rows)
                if sort_seq != 'DETAIL':
                    continue

                # Skip interns
                if 'INTERN' in program.upper():
                    continue

                # Extract data
                vendor_name = row.get('VENDOR NAME', '').strip()
                title = row.get('DESCRIPTION', '').strip()

                if not vendor_name or not title:
                    continue

                # Parse member name from organization
                # Format: "2025 HON. ALMA S. ADAMS" or "2025 HON. 1ST DISTRICT OF FLORIDA"
                member_name = self._extract_member_name(org)
                if not member_name:
                    continue

                # Convert name from "LAST FIRST M." to "First M. Last"
                staff_name = self._convert_name_format(vendor_name)
                if not staff_name:
                    continue

                # Create unique key to avoid duplicates
                key = f"{member_name}|{staff_name}|{title}"
                if key in seen:
                    continue
                seen.add(key)

                # Look up bioguide ID
                bioguide_id = self._lookup_bioguide(member_name, member_lookup)

                # Normalize title
                title_normalized = self._normalize_title(title)

                # Parse name components
                name_parts = staff_name.split()
                first_name = name_parts[0] if name_parts else None
                last_name = name_parts[-1] if len(name_parts) > 1 else None

                record = {
                    'full_name': staff_name,
                    'first_name': first_name,
                    'last_name': last_name,
                    'suffix': None,
                    'title': title[:100],
                    'title_normalized': title_normalized,
                    'office_type': 'member',
                    'chamber': 'house',
                    'member_bioguide_id': bioguide_id,
                    'member_name': member_name,
                    'office_location': 'dc' if 'DISTRICT' not in title.upper() else 'district',
                    'scrape_source': 'house_sod',
                    'scrape_url': str(filepath),
                    'scrape_date': datetime.now().date().isoformat(),
                }

                staff_records.append(record)

        print(f"Extracted {len(staff_records)} staff records from SOD")
        return staff_records

    def _extract_member_name(self, org: str) -> Optional[str]:
        """Extract member name from organization field."""
        # Pattern: "2025 HON. ALMA S. ADAMS" -> "Alma S. Adams"
        # Pattern: "2025 HON. 1ST DISTRICT OF FLORIDA" -> skip (not a named member)

        match = re.search(r'HON\.\s+(.+)$', org)
        if not match:
            return None

        name = match.group(1).strip()

        # Skip district-based entries (no actual name)
        if re.match(r'\d+(ST|ND|RD|TH)\s+DISTRICT', name, re.IGNORECASE):
            return None

        # Convert to title case
        name = name.title()

        return name

    def _convert_name_format(self, vendor_name: str) -> Optional[str]:
        """Convert 'LAST FIRST M.' format to 'First M. Last'."""
        # Example: "ADAMS JOHN Q." -> "John Q. Adams"

        parts = vendor_name.strip().split()
        if len(parts) < 2:
            return None

        # First part is last name, rest is first/middle
        last_name = parts[0].title()
        first_middle = ' '.join(p.title() for p in parts[1:])

        return f"{first_middle} {last_name}"

    def _lookup_bioguide(self, member_name: str, lookup: Dict[str, str]) -> Optional[str]:
        """Look up bioguide ID from member name."""
        import unicodedata

        def normalize_name(name: str) -> str:
            """Remove accents and normalize for matching."""
            normalized = unicodedata.normalize('NFD', name)
            ascii_name = ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')
            return ascii_name.upper()

        def strip_suffix(name: str) -> str:
            """Remove common name suffixes."""
            suffixes = [' Jr.', ' Jr', ' Sr.', ' Sr', ' III', ' Iii', ' II', ' Ii',
                       ' IV', ' Iv', ' V', ' Md', ' M.D.', ' Ph.D.', ' Phd']
            result = name
            for suffix in suffixes:
                if result.endswith(suffix):
                    result = result[:-len(suffix)].strip()
            return result

        # Clean name - strip extra whitespace and suffixes
        clean_name = ' '.join(member_name.split())  # Normalize whitespace
        name_no_suffix = strip_suffix(clean_name)

        # Try various matching strategies
        for name_variant in [clean_name, name_no_suffix]:
            # Try exact match
            if name_variant in lookup:
                return lookup[name_variant]

            # Try uppercase match
            if name_variant.upper() in lookup:
                return lookup[name_variant.upper()]

            # Try normalized (no accents) match
            normalized = normalize_name(name_variant)
            if normalized in lookup:
                return lookup[normalized]

        # Try last name match (without suffix)
        parts = name_no_suffix.split()
        if parts:
            last_name = parts[-1]
            if last_name.upper() in lookup:
                return lookup[last_name.upper()]
            if normalize_name(last_name) in lookup:
                return lookup[normalize_name(last_name)]

        return None

    def _normalize_title(self, title: str) -> Optional[str]:
        """Normalize job title to standard form."""
        title_lower = title.lower().strip()

        for pattern, normalized in self.TITLE_NORMALIZATIONS.items():
            if pattern in title_lower:
                return normalized

        return None

    def import_staff(self, congress_number: int = 119) -> int:
        """
        Import House staff from SOD.

        Returns number of records inserted.
        """
        print("=" * 70)
        print("House Statement of Disbursements Importer")
        print("=" * 70)

        # Find latest CSV
        csv_url = self.find_latest_sod_csv()
        if not csv_url:
            print("Could not find SOD CSV")
            return 0

        # Download CSV
        csv_path = self.download_sod_csv(csv_url)
        if not csv_path:
            return 0

        # Load member lookup from database
        member_lookup = self._load_member_lookup()

        # Parse CSV
        staff_records = self.parse_sod_csv(csv_path, member_lookup)

        if not staff_records:
            print("No staff records extracted")
            return 0

        # Insert into database
        db = StaffDatabase()

        # Extract quarter info from filename for package_id
        filename = csv_path.name
        package_id = f"house-sod-{filename.replace('.csv', '')}"

        inserted = db.insert_staff_records(staff_records, congress_number, package_id)

        print(f"\nImport complete: {inserted} staff records added")
        return inserted

    def _load_member_lookup(self) -> Dict[str, str]:
        """Load member name to bioguide ID mapping."""
        import unicodedata

        def normalize_name(name: str) -> str:
            """Remove accents and normalize for matching."""
            # Normalize unicode and remove accents
            normalized = unicodedata.normalize('NFD', name)
            ascii_name = ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')
            return ascii_name.upper()

        lookup = {}

        if not MEMBER_DB_PATH.exists():
            return lookup

        conn = sqlite3.connect(MEMBER_DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, full_name, last_name
            FROM congress_members
            WHERE chamber = 'House'
        """)

        for row in cursor.fetchall():
            bioguide_id, full_name, last_name = row
            # Add multiple lookup keys
            lookup[full_name] = bioguide_id
            lookup[full_name.upper()] = bioguide_id
            lookup[normalize_name(full_name)] = bioguide_id
            lookup[last_name.upper()] = bioguide_id
            lookup[normalize_name(last_name)] = bioguide_id

        conn.close()

        if self.verbose:
            print(f"Loaded {len(lookup)} member name mappings")

        return lookup


class SenatePDFImporter:
    """
    Importer for Senate Report of the Secretary PDF data.

    Parses semi-annual PDF reports containing Senate staff salary information.

    Data source: https://www.senate.gov/legislative/common/generic/report_secsen.htm
    Format: PDF with salary tables for each Senator's office
    Coverage: 100% of Senate staff
    """

    SENATE_REPORTS_URL = "https://www.senate.gov/legislative/common/generic/report_secsen.htm"
    DOWNLOAD_DIR = Path(__file__).parent.parent / "data" / "senate_downloads"

    # Title normalization map
    TITLE_NORMALIZATIONS = {
        'chief of staff': 'chief_of_staff',
        'deputy chief of staff': 'deputy_chief_of_staff',
        'legislative director': 'legislative_director',
        'communications director': 'communications_director',
        'press secretary': 'press_secretary',
        'scheduler': 'scheduler',
        'state director': 'state_director',
        'state office director': 'state_director',
        'casework director': 'casework_director',
        'caseworker': 'caseworker',
        'staff assistant': 'staff_assistant',
        'legislative assistant': 'legislative_assistant',
        'legislative correspondent': 'legislative_correspondent',
        'legislative aide': 'legislative_aide',
        'counsel': 'counsel',
        'general counsel': 'counsel',
        'policy advisor': 'policy_advisor',
        'policy director': 'policy_director',
        'senior advisor': 'senior_advisor',
        'senior policy advisor': 'senior_policy_advisor',
        'executive assistant': 'executive_assistant',
        'administrative director': 'administrative_director',
        'office manager': 'office_manager',
        'systems administrator': 'systems_administrator',
        'outreach director': 'outreach_director',
        'constituent services': 'constituent_services',
        'field representative': 'field_representative',
        'regional director': 'regional_director',
        'appropriations director': 'appropriations_director',
    }

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'CivicCache-SenateImporter/1.0'})
        self.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    def find_latest_senate_pdf(self) -> Optional[str]:
        """Find the URL of the latest Senate Secretary report PDF."""
        try:
            response = self.session.get(self.SENATE_REPORTS_URL, timeout=30)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            # Look for 119th Congress PDF links (most recent first)
            for link in soup.find_all('a', href=True):
                href = link['href']
                if '119sdoc' in href and href.endswith('.pdf'):
                    # Prefer the full report (not Part I or Part II)
                    if '-1.pdf' not in href and '-2.pdf' not in href:
                        if self.verbose:
                            print(f"Found Senate PDF: {href}")
                        return href

            return None

        except requests.RequestException as e:
            print(f"Error finding Senate PDF: {e}")
            return None

    def download_senate_pdf(self, url: str) -> Optional[Path]:
        """Download Senate Secretary report PDF."""
        filename = url.split('/')[-1]
        filepath = self.DOWNLOAD_DIR / filename

        # Check cache
        if filepath.exists():
            if self.verbose:
                print(f"Using cached PDF: {filepath}")
            return filepath

        try:
            print(f"Downloading Senate PDF ({filename})...")
            response = self.session.get(url, timeout=120, stream=True)
            response.raise_for_status()

            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            print(f"Downloaded: {filepath} ({filepath.stat().st_size / 1024 / 1024:.1f} MB)")
            return filepath

        except requests.RequestException as e:
            print(f"Error downloading Senate PDF: {e}")
            return None

    def parse_senate_pdf(self, filepath: Path, member_lookup: Dict[str, str]) -> List[Dict]:
        """
        Parse Senate PDF and extract staff records.

        Args:
            filepath: Path to Senate PDF file
            member_lookup: Dict mapping senator names to bioguide IDs

        Returns:
            List of staff record dicts
        """
        try:
            import pdfplumber
        except ImportError:
            print("pdfplumber required: pip install pdfplumber")
            return []

        staff_records = []
        seen = set()
        current_senator = None
        current_bioguide = None

        print(f"Parsing Senate PDF (this may take a few minutes)...")

        with pdfplumber.open(filepath) as pdf:
            total_pages = len(pdf.pages)
            print(f"Processing {total_pages} pages...")

            for i, page in enumerate(pdf.pages):
                # Progress update every 500 pages
                if (i + 1) % 500 == 0:
                    print(f"  Processed {i + 1}/{total_pages} pages...")

                text = page.extract_text()
                if not text:
                    continue

                # Check if this is a Senator's office page
                # Pattern: "SENATOR [NAME]" - handles various formats:
                # - All caps: "SENATOR BEN RAY LUJAN"
                # - Multi-word: "SENATOR SHELLEY MOORE CAPITO"
                # - Hyphens: "SENATOR CINDY HYDE-SMITH"
                # - Initials: "SENATOR A. MITCHELL MCCONNELL, JR."
                # Note: Use [ ]+ (space only) instead of \s+ to avoid matching newlines
                senator_match = re.search(
                    r'SENATOR[ ]+([A-Z][A-Za-z\.\-\']+(?:[ ]+[A-Z][A-Za-z\.\-\']+)+(?:,[ ]*(?:JR\.?|SR\.?))?)\s*\n',
                    text
                )

                if senator_match:
                    potential_name = senator_match.group(1).strip()
                    # Skip known false positives
                    skip_words = ['per diem', 'transportation', 'incidentals', 'description',
                                 'net funds', 'iba card', 'fy 20', 'fy 21', 'fy 22', 'fy 23',
                                 'fy 24', 'fy 25', 'document no', 'payee name']
                    if not any(skip in potential_name.lower() for skip in skip_words):
                        if ' ' in potential_name:  # Must have first and last name
                            current_senator = potential_name.title()
                            current_bioguide = self._lookup_bioguide(current_senator, member_lookup)

                            if self.verbose:
                                print(f"    Found Senator: {current_senator}")

                # Skip pages without a current senator context
                if not current_senator:
                    continue

                # Skip non-staff pages (budget summaries, etc.)
                if 'PAYEE NAME' not in text and 'OBLIGATION/SERVICE' not in text:
                    continue

                # Extract staff from the page
                # Pattern: NAME (LAST, FIRST M.) followed by TITLE followed by $AMOUNT
                # Example: "WALSER, CAROLYN D EXECUTIVE ASSISTANT $92,663.92"
                lines = text.split('\n')
                for line in lines:
                    # Must have both comma (in name) and dollar sign (salary)
                    if ',' not in line or '$' not in line:
                        continue

                    # Skip summary/total lines
                    if any(skip in line.upper() for skip in ['TOTAL', 'BALANCE', 'ORGANIZATION']):
                        continue

                    # Match pattern: LASTNAME, FIRSTNAME [M.] TITLE $AMOUNT
                    match = re.match(
                        r'^([A-Z][A-Z\'\-]+),\s*([A-Z][A-Za-z\'\-\.]+(?:\s+[A-Z]\.?)?)\s+(.+?)\s+\$([\d,\.]+)',
                        line
                    )
                    if match:
                        last_name = match.group(1).strip()
                        first_middle = match.group(2).strip()
                        title = match.group(3).strip()

                        # Combine to full name (LAST, FIRST M. format -> First M. Last)
                        vendor_name = f"{last_name}, {first_middle}"
                        staff_name = self._convert_name_format(vendor_name)
                        if not staff_name:
                            continue

                        # Clean up title (remove date suffixes like "TO JUL. 18")
                        title = re.sub(r'\s+(?:TO|FROM)\s+[A-Z]{3}\.\s*\d+.*$', '', title)

                        # Skip duplicates
                        key = f"{current_senator}|{staff_name}|{title}"
                        if key in seen:
                            continue
                        seen.add(key)

                        # Create record
                        record = self._create_staff_record(
                            staff_name, title, current_senator,
                            current_bioguide, str(filepath)
                        )
                        if record:
                            staff_records.append(record)

        print(f"Extracted {len(staff_records)} staff records from Senate PDF")
        return staff_records

    def _convert_name_format(self, vendor_name: str) -> Optional[str]:
        """Convert 'LAST, FIRST M.' format to 'First M. Last'."""
        parts = vendor_name.split(',')
        if len(parts) != 2:
            return None

        last_name = parts[0].strip().title()
        first_middle = parts[1].strip().title()

        return f"{first_middle} {last_name}"

    def _lookup_bioguide(self, senator_name: str, lookup: Dict[str, str]) -> Optional[str]:
        """Look up bioguide ID from senator name with enhanced matching."""
        import unicodedata

        def normalize_name(name: str) -> str:
            """Normalize name by removing accents and converting to uppercase."""
            normalized = unicodedata.normalize('NFD', name)
            ascii_name = ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')
            return ascii_name.upper()

        def strip_suffix(name: str) -> str:
            """Remove common name suffixes."""
            suffixes = [', Jr.', ', Jr', ', Sr.', ', Sr', ', III', ', Iii', ', II', ', Ii',
                       ', IV', ', Iv', ', V', ' Jr.', ' Jr', ' Sr.', ' Sr', ' III', ' Iii',
                       ' II', ' Ii', ' IV', ' Iv', ' V', ' Md', ' M.D.', ' Ph.D.', ' Phd']
            result = name
            for suffix in suffixes:
                if result.endswith(suffix):
                    result = result[:-len(suffix)].strip()
            return result

        # Clean and normalize the input name
        clean_name = strip_suffix(senator_name).strip()
        normalized_input = normalize_name(clean_name)

        # Build normalized lookup table
        normalized_lookup = {}
        for name, bioguide in lookup.items():
            norm = normalize_name(strip_suffix(name))
            normalized_lookup[norm] = bioguide

        # Try exact normalized match
        if normalized_input in normalized_lookup:
            return normalized_lookup[normalized_input]

        # Try last name only match
        input_parts = clean_name.split()
        if input_parts:
            # Handle hyphenated last names
            last_name = input_parts[-1]
            if '-' in last_name:
                # Try full hyphenated name
                last_normalized = normalize_name(last_name)
            else:
                last_normalized = normalize_name(last_name)

            for norm_name, bioguide in normalized_lookup.items():
                name_parts = norm_name.split()
                if name_parts and normalize_name(name_parts[-1]) == last_normalized:
                    return bioguide

            # For multi-word last names like "VAN HOLLEN", "CORTEZ MASTO", "BLUNT ROCHESTER"
            if len(input_parts) >= 3:
                # Try last two words as last name
                last_two = ' '.join(input_parts[-2:])
                last_two_normalized = normalize_name(last_two)
                for norm_name, bioguide in normalized_lookup.items():
                    norm_parts = norm_name.split()
                    if len(norm_parts) >= 2:
                        lookup_last_two = ' '.join(norm_parts[-2:])
                        if last_two_normalized == normalize_name(lookup_last_two):
                            return bioguide

        return None

    def _normalize_title(self, title: str) -> Optional[str]:
        """Normalize job title to standard form."""
        title_lower = title.lower().strip()

        for pattern, normalized in self.TITLE_NORMALIZATIONS.items():
            if pattern in title_lower:
                return normalized

        return None

    def _create_staff_record(self, staff_name: str, title: str,
                            senator_name: str, bioguide_id: Optional[str],
                            source_path: str) -> Optional[Dict]:
        """Create a staff record dict."""
        # Validate name
        if not staff_name or len(staff_name) < 3:
            return None
        if ' ' not in staff_name:
            return None

        # Parse name components
        name_parts = staff_name.split()
        first_name = name_parts[0] if name_parts else None
        last_name = name_parts[-1] if len(name_parts) > 1 else None

        # Normalize title
        title_normalized = self._normalize_title(title)

        # Determine office location
        title_upper = title.upper()
        if any(loc in title_upper for loc in ['STATE', 'DISTRICT', 'REGIONAL', 'FIELD']):
            office_location = 'state'
        else:
            office_location = 'dc'

        return {
            'full_name': staff_name,
            'first_name': first_name,
            'last_name': last_name,
            'suffix': None,
            'title': title[:100],
            'title_normalized': title_normalized,
            'office_type': 'member',
            'chamber': 'senate',
            'member_bioguide_id': bioguide_id,
            'member_name': senator_name,
            'office_location': office_location,
            'scrape_source': 'senate_pdf',
            'scrape_url': source_path,
            'scrape_date': datetime.now().date().isoformat(),
        }

    def import_staff(self, congress_number: int = 119) -> int:
        """
        Import Senate staff from Secretary report PDF.

        Returns number of records inserted.
        """
        print("=" * 70)
        print("Senate Secretary Report PDF Importer")
        print("=" * 70)

        # Find latest PDF
        pdf_url = self.find_latest_senate_pdf()
        if not pdf_url:
            print("Could not find Senate PDF")
            return 0

        # Download PDF
        pdf_path = self.download_senate_pdf(pdf_url)
        if not pdf_path:
            return 0

        # Load senator lookup from database
        member_lookup = self._load_senator_lookup()

        # Parse PDF
        staff_records = self.parse_senate_pdf(pdf_path, member_lookup)

        if not staff_records:
            print("No staff records extracted")
            return 0

        # Insert into database
        db = StaffDatabase()

        # Extract report info for package_id
        filename = pdf_path.stem
        package_id = f"senate-pdf-{filename}"

        inserted = db.insert_staff_records(staff_records, congress_number, package_id)

        print(f"\nImport complete: {inserted} staff records added")
        return inserted

    def _load_senator_lookup(self) -> Dict[str, str]:
        """Load senator name to bioguide ID mapping."""
        import unicodedata

        def normalize_name(name: str) -> str:
            """Remove accents and normalize for matching."""
            normalized = unicodedata.normalize('NFD', name)
            ascii_name = ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')
            return ascii_name.upper()

        lookup = {}

        if not MEMBER_DB_PATH.exists():
            return lookup

        conn = sqlite3.connect(MEMBER_DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, full_name, last_name
            FROM congress_members
            WHERE chamber = 'Senate'
        """)

        for row in cursor.fetchall():
            bioguide_id, full_name, last_name = row
            # Add multiple lookup keys
            lookup[full_name] = bioguide_id
            lookup[full_name.upper()] = bioguide_id
            lookup[normalize_name(full_name)] = bioguide_id
            lookup[last_name.upper()] = bioguide_id
            lookup[normalize_name(last_name)] = bioguide_id

            # Also try first-last format
            parts = full_name.split()
            if len(parts) >= 2:
                simple_name = f"{parts[0]} {parts[-1]}"
                lookup[simple_name] = bioguide_id
                lookup[simple_name.upper()] = bioguide_id
                lookup[normalize_name(simple_name)] = bioguide_id

        conn.close()

        if self.verbose:
            print(f"Loaded {len(lookup)} senator name mappings")

        return lookup


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


def load_members_from_db(chamber: Optional[str] = None, limit: Optional[int] = None) -> List[Dict]:
    """
    Load congress members from the database.

    Args:
        chamber: Optional filter for 'senate' or 'house'
        limit: Optional limit on number of members

    Returns:
        List of member dicts with id, full_name, chamber, website_url
    """
    if not MEMBER_DB_PATH.exists():
        print(f"Member database not found at {MEMBER_DB_PATH}")
        return []

    conn = sqlite3.connect(MEMBER_DB_PATH)
    cursor = conn.cursor()

    query = """
        SELECT id, full_name, chamber, website_url
        FROM congress_members
        WHERE website_url IS NOT NULL AND website_url != ''
    """

    if chamber:
        query += f" AND LOWER(chamber) = '{chamber.lower()}'"

    query += " ORDER BY chamber, last_name"

    if limit:
        query += f" LIMIT {limit}"

    cursor.execute(query)

    members = []
    for row in cursor.fetchall():
        members.append({
            'id': row[0],
            'full_name': row[1],
            'chamber': row[2],
            'website_url': row[3]
        })

    conn.close()
    return members


def scrape_staff_from_websites(
    chamber: Optional[str] = None,
    limit: Optional[int] = None,
    verbose: bool = False,
    congress_number: int = 119,
    include_press_releases: bool = False
) -> int:
    """
    Scrape staff from member websites and insert into database.

    Args:
        chamber: Optional filter for 'senate' or 'house'
        limit: Optional limit on number of members to scrape
        verbose: Enable verbose output
        congress_number: Congress number (default 119)
        include_press_releases: Also check press releases for staff announcements

    Returns:
        Total number of staff records inserted
    """
    print("=" * 70)
    print("Congressional Staff Web Scraper")
    print("=" * 70)

    # Load members
    print(f"\nLoading members from database...")
    members = load_members_from_db(chamber=chamber, limit=limit)
    print(f"Found {len(members)} members with websites")

    if not members:
        return 0

    # Initialize scrapers and database
    scraper = WebStaffScraper(rate_limit_delay=1.0, verbose=verbose)
    press_parser = PressReleaseParser(rate_limit_delay=1.0, verbose=verbose) if include_press_releases else None
    db = StaffDatabase()

    total_inserted = 0
    members_with_staff = 0
    members_from_press = 0
    members_no_page = 0

    print(f"\nScraping staff pages...")
    if include_press_releases:
        print("(Also checking press releases as fallback)")

    for i, member in enumerate(members, 1):
        print(f"\n[{i}/{len(members)}] {member['full_name']} ({member['chamber']})")

        try:
            # Try staff directory page first
            staff_page, staff_records = scraper.scrape_member_staff(member)

            # If no staff page found and press releases enabled, try that
            if not staff_records and include_press_releases and press_parser:
                print(f"    No staff page, checking press releases...")
                press_page, press_staff = press_parser.scrape_member_press_releases(member)
                if press_staff:
                    staff_records = press_staff
                    staff_page = press_page
                    members_from_press += 1

            if not staff_page and not staff_records:
                members_no_page += 1
                continue

            if staff_records:
                members_with_staff += 1
                # Insert records
                inserted = db.insert_staff_records(
                    staff_records,
                    congress=congress_number,
                    package_id=f"web-scrape-{datetime.now().strftime('%Y-%m-%d')}"
                )
                total_inserted += inserted
                print(f"    Inserted {inserted} staff records from {staff_page}")

        except Exception as e:
            print(f"    Error: {e}")
            continue

    # Summary
    print("\n" + "=" * 70)
    print("Scraping Complete!")
    print("=" * 70)
    print(f"Members checked:     {len(members)}")
    print(f"Members with staff:  {members_with_staff}")
    if include_press_releases:
        print(f"  - From staff pages: {members_with_staff - members_from_press}")
        print(f"  - From press releases: {members_from_press}")
    print(f"No staff found:      {members_no_page}")
    print(f"Total staff added:   {total_inserted}")

    return total_inserted


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Import Congressional staff data')
    parser.add_argument('--congress', type=int, help='Specific congress number to import')
    parser.add_argument('--check-only', action='store_true', help='Only check for latest directory')
    parser.add_argument('--package-id', type=str, help='Use specific package ID (e.g., CDIR-2024-04-25)')

    # Web scraping arguments
    parser.add_argument('--scrape-web', action='store_true',
                       help='Scrape staff from member websites instead of GovInfo')
    parser.add_argument('--include-press-releases', action='store_true',
                       help='Also check press releases for staff announcements (slower)')
    parser.add_argument('--chamber', type=str, choices=['senate', 'house'],
                       help='Only scrape specific chamber (use with --scrape-web)')
    parser.add_argument('--limit', type=int,
                       help='Limit number of members to scrape (use with --scrape-web)')

    # House SOD import
    parser.add_argument('--import-house-sod', action='store_true',
                       help='Import House staff from Statement of Disbursements (100%% coverage)')

    # Senate PDF import
    parser.add_argument('--import-senate-pdf', action='store_true',
                       help='Import Senate staff from Secretary of Senate PDF (100%% coverage)')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose output')

    args = parser.parse_args()

    # Handle web scraping mode
    # Handle House SOD import
    if args.import_house_sod:
        congress_num = args.congress if args.congress else 119
        importer = HouseSODImporter(verbose=args.verbose)
        total = importer.import_staff(congress_number=congress_num)
        return 0 if total > 0 else 1

    # Handle Senate PDF import
    if args.import_senate_pdf:
        congress_num = args.congress if args.congress else 119
        importer = SenatePDFImporter(verbose=args.verbose)
        total = importer.import_staff(congress_number=congress_num)
        return 0 if total > 0 else 1

    # Handle web scraping mode
    if args.scrape_web:
        congress_num = args.congress if args.congress else 119
        total = scrape_staff_from_websites(
            chamber=args.chamber,
            limit=args.limit,
            verbose=args.verbose,
            congress_number=congress_num,
            include_press_releases=args.include_press_releases
        )
        return 0 if total > 0 else 1

    print("=" * 70)
    print("Congressional Staff Data Importer (GovInfo CDIR)")
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
