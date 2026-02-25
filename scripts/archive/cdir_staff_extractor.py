#!/usr/bin/env python3
"""
Congressional Staff Extraction - Proof of Concept Script
=========================================================

This script demonstrates extracting congressional staff data from 
GovInfo.gov Congressional Directory (CDIR) files.

Prerequisites:
    pip install httpx beautifulsoup4 lxml

Usage:
    1. Get a free API key from https://api.data.gov/signup/
    2. Set environment variable: export GOVINFO_API_KEY=your_key_here
    3. Run: python cdir_staff_extractor.py

Author: CivicScope Team
"""

import os
import re
import json
import logging
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

GOVINFO_API_BASE = "https://api.govinfo.gov"
GOVINFO_CONTENT_BASE = "https://www.govinfo.gov/content/pkg"

# Get API key from environment
API_KEY = os.environ.get("GOVINFO_API_KEY", "DEMO_KEY")

# Current Congress package ID (update as new editions are released)
CURRENT_CDIR_PACKAGE = "CDIR-2024-04-25"


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class StaffMember:
    """Represents a congressional staff member."""
    full_name: str
    title: str
    title_normalized: str
    office_type: str  # 'member', 'committee', 'institutional'
    chamber: Optional[str] = None  # 'senate', 'house', 'joint'
    
    # Member office fields
    member_bioguide_id: Optional[str] = None
    member_name: Optional[str] = None
    state: Optional[str] = None
    district: Optional[str] = None
    office_location: Optional[str] = None  # 'dc', 'district'
    
    # Committee fields
    committee_code: Optional[str] = None
    committee_name: Optional[str] = None
    party_affiliation: Optional[str] = None  # 'majority', 'minority'
    
    # Institutional fields
    institutional_office: Optional[str] = None
    
    # Source tracking
    congress_number: Optional[int] = None
    source_package: Optional[str] = None
    source_granule: Optional[str] = None
    
    # Parsed name components
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================================
# Name Parsing
# ============================================================================

def parse_name(full_name: str) -> Dict[str, Optional[str]]:
    """
    Parse a full name into components.
    
    Handles:
    - Prefixes: Dr., Mr., Ms., Mrs.
    - Suffixes: Jr., Sr., II, III, IV
    - Middle names
    """
    if not full_name:
        return {'first_name': None, 'last_name': None, 'suffix': None}
    
    name = full_name.strip()
    
    # Remove prefixes
    name = re.sub(r'^(Dr\.|Mr\.|Ms\.|Mrs\.)\s+', '', name)
    
    # Extract suffix
    suffix_match = re.search(r',?\s*(Jr\.|Sr\.|II|III|IV)\s*$', name, re.I)
    suffix = None
    if suffix_match:
        suffix = suffix_match.group(1)
        name = name[:suffix_match.start()].strip()
    
    # Split into parts
    parts = name.split()
    
    if len(parts) == 0:
        return {'first_name': None, 'last_name': full_name, 'suffix': suffix}
    elif len(parts) == 1:
        return {'first_name': None, 'last_name': parts[0], 'suffix': suffix}
    else:
        return {
            'first_name': parts[0],
            'last_name': parts[-1],
            'suffix': suffix
        }


def normalize_title(raw_title: str) -> str:
    """Map raw title strings to normalized versions."""
    title_map = {
        'chief of staff': 'chief_of_staff',
        'deputy chief of staff': 'deputy_chief_of_staff',
        'legislative director': 'legislative_director',
        'communications director': 'communications_director',
        'press secretary': 'press_secretary',
        'scheduler': 'scheduler',
        'executive assistant': 'executive_assistant',
        'district director': 'district_director',
        'state director': 'state_director',
        'staff director': 'staff_director',
        'chief counsel': 'chief_counsel',
        'general counsel': 'general_counsel',
        'deputy staff director': 'deputy_staff_director',
        'minority staff director': 'minority_staff_director',
        'professional staff': 'professional_staff',
        'policy advisor': 'policy_advisor',
        'senior advisor': 'senior_advisor',
        'office manager': 'office_manager',
        'staff assistant': 'staff_assistant',
        'legislative assistant': 'legislative_assistant',
        'legislative correspondent': 'legislative_correspondent',
    }
    
    normalized = raw_title.lower().strip()
    # Remove trailing punctuation
    normalized = re.sub(r'[.—:\s]+$', '', normalized)
    
    return title_map.get(normalized, normalized.replace(' ', '_'))


# ============================================================================
# GovInfo API Client
# ============================================================================

class GovInfoClient:
    """Client for interacting with the GovInfo API."""
    
    def __init__(self, api_key: str = API_KEY):
        self.api_key = api_key
        self.client = httpx.Client(timeout=30.0)
    
    def get_collection_packages(
        self, 
        collection: str = "CDIR",
        page_size: int = 100
    ) -> List[Dict]:
        """Get list of packages in a collection."""
        packages = []
        offset = 0
        
        while True:
            url = f"{GOVINFO_API_BASE}/collections/{collection}"
            params = {
                'api_key': self.api_key,
                'pageSize': page_size,
                'offset': offset
            }
            
            response = self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            batch = data.get('packages', [])
            if not batch:
                break
                
            packages.extend(batch)
            offset += page_size
            
            logger.info(f"Retrieved {len(packages)} packages so far...")
            
            # Check if we have more
            if len(batch) < page_size:
                break
        
        return packages
    
    def get_package_granules(self, package_id: str) -> List[Dict]:
        """Get list of granules (documents) in a package."""
        granules = []
        offset = 0
        page_size = 100
        
        while True:
            url = f"{GOVINFO_API_BASE}/packages/{package_id}/granules"
            params = {
                'api_key': self.api_key,
                'pageSize': page_size,
                'offset': offset
            }
            
            response = self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            batch = data.get('granules', [])
            if not batch:
                break
                
            granules.extend(batch)
            offset += page_size
            
            if len(batch) < page_size:
                break
        
        return granules
    
    def get_granule_content(
        self, 
        package_id: str, 
        granule_id: str,
        format: str = 'htm'
    ) -> str:
        """Download granule content in specified format."""
        url = f"{GOVINFO_CONTENT_BASE}/{package_id}/{format}/{granule_id}.{format}"
        
        response = self.client.get(url)
        response.raise_for_status()
        return response.text
    
    def close(self):
        self.client.close()


# ============================================================================
# Staff Parsers
# ============================================================================

# Patterns for extracting staff from member biography sections
MEMBER_STAFF_PATTERNS = [
    (r"Chief of Staff[.—:\s]+([A-Za-z\s,.\-']+?)(?=\.|Legislative|Press|Scheduler|Communications|Deputy|$)", 
     "Chief of Staff"),
    (r"Deputy Chief of Staff[.—:\s]+([A-Za-z\s,.\-']+?)(?=\.|Legislative|Press|Scheduler|Communications|Chief|$)",
     "Deputy Chief of Staff"),
    (r"Legislative Director[.—:\s]+([A-Za-z\s,.\-']+?)(?=\.|Press|Scheduler|Communications|Chief|Deputy|$)",
     "Legislative Director"),
    (r"Communications Director[.—:\s]+([A-Za-z\s,.\-']+?)(?=\.|Legislative|Press|Scheduler|Chief|Deputy|$)",
     "Communications Director"),
    (r"Press Secretary[.—:\s]+([A-Za-z\s,.\-']+?)(?=\.|Legislative|Scheduler|Communications|Chief|Deputy|$)",
     "Press Secretary"),
    (r"Scheduler[.—:\s]+([A-Za-z\s,.\-']+?)(?=\.|Legislative|Press|Communications|Chief|Deputy|$)",
     "Scheduler"),
    (r"District Director[.—:\s]+([A-Za-z\s,.\-']+?)(?=\.|Legislative|Press|Scheduler|Communications|Chief|$)",
     "District Director"),
    (r"State Director[.—:\s]+([A-Za-z\s,.\-']+?)(?=\.|Legislative|Press|Scheduler|Communications|Chief|$)",
     "State Director"),
]


def split_multiple_names(names_str: str) -> List[str]:
    """Split a string containing multiple names (comma, semicolon, or 'and' separated)."""
    if not names_str:
        return []
    
    # Clean up the string
    names_str = names_str.strip()
    names_str = re.sub(r'\s+', ' ', names_str)
    
    # Split by common delimiters
    names = re.split(r'[;,]|\sand\s', names_str)
    
    # Clean each name
    cleaned = []
    for name in names:
        name = name.strip()
        # Skip if too short or looks like a title fragment
        if len(name) > 2 and not re.match(r'^(and|or|the)$', name, re.I):
            cleaned.append(name)
    
    return cleaned


def parse_member_section(html_content: str, member_info: Dict) -> List[StaffMember]:
    """
    Parse staff from a member biography section.
    
    Args:
        html_content: HTML content of the member's section
        member_info: Dict with member details (name, state, bioguide_id, etc.)
    
    Returns:
        List of StaffMember objects
    """
    staff_records = []
    
    # Parse HTML
    soup = BeautifulSoup(html_content, 'lxml')
    text = soup.get_text(separator=' ')
    
    # Clean up text
    text = re.sub(r'\s+', ' ', text)
    
    for pattern, title in MEMBER_STAFF_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
        
        for match in matches:
            # Handle multiple names
            names = split_multiple_names(match)
            
            for name in names:
                # Parse the name
                name_parts = parse_name(name)
                
                staff = StaffMember(
                    full_name=name,
                    first_name=name_parts['first_name'],
                    last_name=name_parts['last_name'],
                    title=title,
                    title_normalized=normalize_title(title),
                    office_type='member',
                    chamber=member_info.get('chamber'),
                    member_bioguide_id=member_info.get('bioguide_id'),
                    member_name=member_info.get('name'),
                    state=member_info.get('state'),
                    district=member_info.get('district'),
                    office_location='dc',  # Default, can be refined
                    congress_number=member_info.get('congress_number'),
                    source_package=member_info.get('source_package'),
                    source_granule=member_info.get('source_granule')
                )
                
                staff_records.append(staff)
                logger.debug(f"Found: {title} - {name}")
    
    return staff_records


def parse_committee_section(html_content: str, committee_info: Dict) -> List[StaffMember]:
    """
    Parse staff from a committee section.
    
    Args:
        html_content: HTML content of the committee section
        committee_info: Dict with committee details
    
    Returns:
        List of StaffMember objects
    """
    staff_records = []
    
    soup = BeautifulSoup(html_content, 'lxml')
    text = soup.get_text(separator=' ')
    text = re.sub(r'\s+', ' ', text)
    
    # Committee staff patterns
    committee_patterns = [
        (r"Staff Director[.—:\s]+([A-Za-z\s,.\-']+?)(?=\.|Deputy|Chief|Counsel|$)",
         "Staff Director", "majority"),
        (r"Minority Staff Director[.—:\s]+([A-Za-z\s,.\-']+?)(?=\.|Deputy|Chief|Counsel|$)",
         "Minority Staff Director", "minority"),
        (r"Chief Counsel[.—:\s]+([A-Za-z\s,.\-']+?)(?=\.|Staff|Deputy|Professional|$)",
         "Chief Counsel", None),
        (r"Chief Clerk[.—:\s]+([A-Za-z\s,.\-']+?)(?=\.|Staff|Deputy|Professional|$)",
         "Chief Clerk", None),
    ]
    
    for pattern, title, party in committee_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        
        for match in matches:
            names = split_multiple_names(match)
            
            for name in names:
                name_parts = parse_name(name)
                
                staff = StaffMember(
                    full_name=name,
                    first_name=name_parts['first_name'],
                    last_name=name_parts['last_name'],
                    title=title,
                    title_normalized=normalize_title(title),
                    office_type='committee',
                    chamber=committee_info.get('chamber'),
                    committee_code=committee_info.get('code'),
                    committee_name=committee_info.get('name'),
                    party_affiliation=party,
                    congress_number=committee_info.get('congress_number'),
                    source_package=committee_info.get('source_package'),
                    source_granule=committee_info.get('source_granule')
                )
                
                staff_records.append(staff)
    
    return staff_records


# ============================================================================
# Main Extraction Logic
# ============================================================================

def extract_congress_staff(package_id: str, sample_only: bool = True) -> List[StaffMember]:
    """
    Extract all staff from a Congressional Directory package.
    
    Args:
        package_id: CDIR package ID (e.g., 'CDIR-2024-04-25')
        sample_only: If True, only process a few sections for testing
    
    Returns:
        List of all extracted StaffMember objects
    """
    all_staff = []
    client = GovInfoClient()
    
    try:
        # Get list of granules
        logger.info(f"Fetching granules for {package_id}...")
        granules = client.get_package_granules(package_id)
        logger.info(f"Found {len(granules)} granules")
        
        # Filter to relevant sections
        member_granules = [g for g in granules if '-S-' in g['granuleId'] or '-H-' in g['granuleId']]
        committee_granules = [g for g in granules if 'COMMITTEES' in g['granuleId'].upper()]
        
        logger.info(f"Member sections: {len(member_granules)}")
        logger.info(f"Committee sections: {len(committee_granules)}")
        
        # Process member sections
        if sample_only:
            member_granules = member_granules[:5]  # Just first 5 for testing
            committee_granules = committee_granules[:2]
        
        for granule in member_granules:
            granule_id = granule['granuleId']
            logger.info(f"Processing member section: {granule_id}")
            
            try:
                # Parse granule ID for member info
                # Format: CDIR-YYYY-MM-DD-{STATE}-{S|H}-{SEAT}
                parts = granule_id.split('-')
                state = parts[4] if len(parts) > 4 else None
                chamber = 'senate' if '-S-' in granule_id else 'house'
                district = parts[6] if chamber == 'house' and len(parts) > 6 else None
                
                # Extract congress number from package
                # Approximate from date - would need lookup table for accuracy
                congress_number = 118  # Current, should be derived
                
                member_info = {
                    'chamber': chamber,
                    'state': state,
                    'district': district,
                    'bioguide_id': None,  # Would need cross-reference
                    'name': granule.get('title', ''),
                    'congress_number': congress_number,
                    'source_package': package_id,
                    'source_granule': granule_id
                }
                
                # Fetch and parse content
                content = client.get_granule_content(package_id, granule_id)
                staff = parse_member_section(content, member_info)
                all_staff.extend(staff)
                
                logger.info(f"  Found {len(staff)} staff members")
                
            except Exception as e:
                logger.error(f"Error processing {granule_id}: {e}")
                continue
        
        # Process committee sections
        for granule in committee_granules:
            granule_id = granule['granuleId']
            logger.info(f"Processing committee section: {granule_id}")
            
            try:
                chamber = 'senate' if 'SENATE' in granule_id.upper() else 'house'
                
                committee_info = {
                    'chamber': chamber,
                    'name': granule.get('title', ''),
                    'code': None,  # Would need mapping
                    'congress_number': 118,
                    'source_package': package_id,
                    'source_granule': granule_id
                }
                
                content = client.get_granule_content(package_id, granule_id)
                staff = parse_committee_section(content, committee_info)
                all_staff.extend(staff)
                
                logger.info(f"  Found {len(staff)} staff members")
                
            except Exception as e:
                logger.error(f"Error processing {granule_id}: {e}")
                continue
        
    finally:
        client.close()
    
    return all_staff


# ============================================================================
# Output & Reporting
# ============================================================================

def print_summary(staff: List[StaffMember]):
    """Print a summary of extracted staff."""
    print("\n" + "="*60)
    print("EXTRACTION SUMMARY")
    print("="*60)
    
    print(f"\nTotal staff members extracted: {len(staff)}")
    
    # By office type
    by_type = {}
    for s in staff:
        by_type[s.office_type] = by_type.get(s.office_type, 0) + 1
    print("\nBy office type:")
    for t, count in sorted(by_type.items()):
        print(f"  {t}: {count}")
    
    # By title
    by_title = {}
    for s in staff:
        by_title[s.title] = by_title.get(s.title, 0) + 1
    print("\nBy title (top 10):")
    for title, count in sorted(by_title.items(), key=lambda x: -x[1])[:10]:
        print(f"  {title}: {count}")
    
    # By chamber
    by_chamber = {}
    for s in staff:
        chamber = s.chamber or 'unknown'
        by_chamber[chamber] = by_chamber.get(chamber, 0) + 1
    print("\nBy chamber:")
    for c, count in sorted(by_chamber.items()):
        print(f"  {c}: {count}")
    
    print("\n" + "="*60)


def export_to_json(staff: List[StaffMember], filename: str):
    """Export staff data to JSON file."""
    data = [s.to_dict() for s in staff]
    with open(filename, 'w') as f:
        json.dump(data, f, indent=2, default=str)
    logger.info(f"Exported {len(staff)} records to {filename}")


def export_to_csv(staff: List[StaffMember], filename: str):
    """Export staff data to CSV file."""
    import csv
    
    if not staff:
        return
    
    fields = list(staff[0].to_dict().keys())
    
    with open(filename, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for s in staff:
            writer.writerow(s.to_dict())
    
    logger.info(f"Exported {len(staff)} records to {filename}")


# ============================================================================
# Entry Point
# ============================================================================

def main():
    """Main entry point for the extraction script."""
    print("\n" + "="*60)
    print("Congressional Staff Extraction - Proof of Concept")
    print("="*60)
    
    # Check API key
    if API_KEY == "DEMO_KEY":
        print("\n⚠️  Using DEMO_KEY - rate limits apply.")
        print("   Get a free key at: https://api.data.gov/signup/")
        print("   Set: export GOVINFO_API_KEY=your_key_here\n")
    
    # Run extraction
    package_id = CURRENT_CDIR_PACKAGE
    print(f"Extracting from: {package_id}")
    print("Running in SAMPLE mode (first 5 members, 2 committees)\n")
    
    try:
        staff = extract_congress_staff(package_id, sample_only=True)
        
        # Print summary
        print_summary(staff)
        
        # Export results
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_file = f"staff_extract_{timestamp}.json"
        csv_file = f"staff_extract_{timestamp}.csv"
        
        export_to_json(staff, json_file)
        export_to_csv(staff, csv_file)
        
        print(f"\nResults exported to:")
        print(f"  - {json_file}")
        print(f"  - {csv_file}")
        
        # Sample output
        if staff:
            print("\n" + "-"*60)
            print("SAMPLE RECORDS:")
            print("-"*60)
            for s in staff[:5]:
                print(f"  {s.title}: {s.full_name}")
                print(f"    Office: {s.member_name or s.committee_name}")
                print(f"    Chamber: {s.chamber}")
                print()
        
    except Exception as e:
        logger.exception(f"Extraction failed: {e}")
        raise


if __name__ == "__main__":
    main()
