# Plan: Scraping Congressional Staff from Member Websites

## Executive Summary

Since the official Congressional Directory for the 119th Congress won't be published until September 2026, this plan outlines an approach to scrape staff information directly from individual member websites to populate the `congressional_staff` table.

---

## 1. Data Source Analysis

### Source Websites

| Chamber | URL Pattern | Example |
|---------|-------------|---------|
| House | `https://{lastname}.house.gov` | `https://pelosi.house.gov` |
| Senate | `https://www.{lastname}.senate.gov` | `https://www.coons.senate.gov` |

### Staff Page Locations (Varies by Member)

Common URL patterns observed:
- `/contact/staff-directory/` (Senate - most common)
- `/contact/staff.htm` (House)
- `/about/staff` or `/about/team`
- Press releases announcing staff (119th Congress)
- No dedicated staff page (many members)

### Data Quality Assessment

| Source Type | Coverage | Data Quality | Freshness |
|-------------|----------|--------------|-----------|
| Staff directory pages | ~30% of members | High (full listings) | Current |
| Press releases | ~20% of members | Medium (key staff only) | Current |
| About pages | ~10% of members | Low (partial) | Variable |
| No staff info | ~40% of members | N/A | N/A |

---

## 2. Data Fields to Extract

### Required Fields
- `full_name` - Staff member's full name
- `title` - Job title as displayed
- `member_bioguide_id` - Link to congress member
- `office_type` - 'member' (all scraped data)
- `congress_number` - 119

### Optional Fields
- `first_name`, `last_name` - Parsed from full_name
- `office_location` - 'dc' or 'district'
- `chamber` - 'senate' or 'house'

### Title Normalization Map
```python
TITLE_NORMALIZATIONS = {
    'chief of staff': 'chief_of_staff',
    'deputy chief of staff': 'deputy_chief_of_staff',
    'legislative director': 'legislative_director',
    'communications director': 'communications_director',
    'press secretary': 'press_secretary',
    'scheduler': 'scheduler',
    'district director': 'district_director',
    'state director': 'state_director',
    'caseworker': 'caseworker',
    'staff assistant': 'staff_assistant',
    'legislative assistant': 'legislative_assistant',
    'legislative correspondent': 'legislative_correspondent',
    'counsel': 'counsel',
    'policy advisor': 'policy_advisor',
}
```

---

## 3. Technical Architecture

### Component Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    CivicCache System                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │   Member     │    │    Staff     │    │   Staff      │  │
│  │   Website    │───▶│   Page       │───▶│   Parser     │  │
│  │   Discovery  │    │   Finder     │    │              │  │
│  └──────────────┘    └──────────────┘    └──────────────┘  │
│         │                   │                   │          │
│         ▼                   ▼                   ▼          │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              Database Writer                          │  │
│  │         (congressional_staff table)                   │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### New Files to Create

```
scripts/
├── scrape_member_staff.py      # Main scraper orchestration
├── staff_scraper/
│   ├── __init__.py
│   ├── member_discovery.py     # Get member URLs from clerk data
│   ├── page_finder.py          # Find staff pages on member sites
│   ├── parsers/
│   │   ├── __init__.py
│   │   ├── base_parser.py      # Abstract parser class
│   │   ├── senate_parser.py    # Senate-specific patterns
│   │   ├── house_parser.py     # House-specific patterns
│   │   └── press_release_parser.py  # Extract from announcements
│   ├── normalizer.py           # Title and name normalization
│   └── db_writer.py            # Database insertion logic
└── tests/
    └── test_staff_scraper.py   # Unit tests
```

---

## 4. Implementation Phases

### Phase 1: Member Discovery (Week 1)

**Goal**: Build list of all 535+ member websites

**Data Sources**:
1. House Clerk XML: `https://clerk.house.gov/xml/lists/MemberData.xml`
2. Senate XML: `https://www.senate.gov/general/contact_information/senators_cfm.xml`
3. Congress.gov API (fallback)

**Output**: JSON file with member info:
```json
{
  "members": [
    {
      "bioguide_id": "C001088",
      "name": "Christopher Coons",
      "chamber": "senate",
      "state": "DE",
      "website": "https://www.coons.senate.gov"
    }
  ]
}
```

### Phase 2: Staff Page Discovery (Week 2)

**Goal**: Find staff page URLs for each member

**Strategy**:
1. Try common URL patterns:
   - `/contact/staff-directory/`
   - `/contact/staff`
   - `/about/staff`
   - `/about/team`
   - `/staff`
2. Parse sitemap.xml if available
3. Search main page for "staff" links
4. Fall back to press release search

**Output**: Updated member JSON with staff page URLs:
```json
{
  "bioguide_id": "C001088",
  "staff_page_url": "https://www.coons.senate.gov/contact/staff-directory/",
  "staff_page_type": "directory",  // or "press_release", "about", "none"
  "last_checked": "2026-01-31"
}
```

### Phase 3: Parser Development (Week 2-3)

**Goal**: Build parsers for each page type

**Parser Types**:

1. **Senate Directory Parser**
   - Target: Pages like Coons' staff directory
   - Pattern: Heading hierarchy (h2/h3/h4) + text content
   - Extract: Name, title, department (DC/State)

2. **House Staff Page Parser**
   - Target: `/contact/staff.htm` pages
   - Pattern: Often list-based or table format
   - Extract: Name, title, location

3. **Press Release Parser**
   - Target: "Announces staff" press releases
   - Pattern: Bullet lists, bold names
   - Extract: Key staff only (CoS, LD, CD, DD)

**Parser Interface**:
```python
class BaseStaffParser:
    def can_parse(self, url: str, html: str) -> bool:
        """Check if this parser can handle the page."""
        pass

    def parse(self, html: str, member_info: dict) -> List[StaffRecord]:
        """Extract staff records from HTML."""
        pass
```

### Phase 4: Data Extraction (Week 3-4)

**Goal**: Scrape all available staff data

**Process**:
1. Load member list with staff page URLs
2. For each member:
   - Fetch staff page
   - Select appropriate parser
   - Extract staff records
   - Validate and normalize
   - Write to database
3. Track extraction statistics

**Rate Limiting**:
- 1 request per second per domain
- Respect robots.txt
- Randomize request timing

### Phase 5: Integration & Automation (Week 4)

**Goal**: Integrate into CivicCache pipeline

**Tasks**:
1. Add to `civic_pipeline.py`
2. Create incremental update logic
3. Add to webhook server triggers
4. Set up monitoring/alerting

---

## 5. Database Schema

Uses existing `congressional_staff` table with additional tracking:

```sql
-- Add scrape tracking fields
ALTER TABLE congressional_staff ADD COLUMN scrape_source TEXT;
ALTER TABLE congressional_staff ADD COLUMN scrape_url TEXT;
ALTER TABLE congressional_staff ADD COLUMN scrape_date TEXT;
ALTER TABLE congressional_staff ADD COLUMN confidence_score REAL;

-- Create scrape tracking table
CREATE TABLE staff_scrape_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_bioguide_id TEXT NOT NULL,
    member_name TEXT,
    website_url TEXT,
    staff_page_url TEXT,
    staff_page_type TEXT,  -- 'directory', 'press_release', 'about', 'none'
    staff_count INTEGER,
    scrape_status TEXT,  -- 'success', 'no_page', 'parse_error', 'http_error'
    error_message TEXT,
    scrape_date TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_scrape_log_member ON staff_scrape_log(member_bioguide_id);
CREATE INDEX idx_scrape_log_status ON staff_scrape_log(scrape_status);
```

---

## 6. Example Implementation

### Member Discovery
```python
# scripts/staff_scraper/member_discovery.py

import requests
import xml.etree.ElementTree as ET

def get_house_members():
    """Fetch House members from Clerk XML."""
    url = 'https://clerk.house.gov/xml/lists/MemberData.xml'
    r = requests.get(url, timeout=30)
    root = ET.fromstring(r.text)

    members = []
    for member in root.findall('.//member'):
        info = member.find('member-info')
        if info is not None:
            bioguide = info.findtext('bioguideID', '')
            name = info.findtext('official-name', '')
            # Construct website URL from bioguide/name
            lastname = name.split()[-1].lower()
            website = f'https://{lastname}.house.gov'

            members.append({
                'bioguide_id': bioguide,
                'name': name,
                'chamber': 'house',
                'website': website
            })

    return members

def get_senate_members():
    """Fetch Senate members from Senate XML."""
    url = 'https://www.senate.gov/general/contact_information/senators_cfm.xml'
    r = requests.get(url, timeout=30)
    root = ET.fromstring(r.text)

    members = []
    for member in root.findall('.//member'):
        lastname = member.findtext('last_name', '').lower()
        firstname = member.findtext('first_name', '')
        website = member.findtext('website', '')

        # Get bioguide from Congress.gov if needed
        members.append({
            'name': f'{firstname} {lastname}'.title(),
            'chamber': 'senate',
            'website': website or f'https://www.{lastname}.senate.gov'
        })

    return members
```

### Staff Page Parser (Senate)
```python
# scripts/staff_scraper/parsers/senate_parser.py

from bs4 import BeautifulSoup
from typing import List, Dict
import re

class SenateStaffParser:
    """Parser for Senate member staff directory pages."""

    TITLE_PATTERNS = [
        r'chief of staff',
        r'legislative director',
        r'communications director',
        r'press secretary',
        r'scheduler',
        r'state director',
        r'counsel',
        r'policy advisor',
        r'staff assistant',
        r'caseworker',
    ]

    def can_parse(self, url: str, html: str) -> bool:
        """Check if this is a Senate staff directory page."""
        return 'senate.gov' in url and 'staff' in url.lower()

    def parse(self, html: str, member_info: Dict) -> List[Dict]:
        """Extract staff records from Senate staff directory."""
        soup = BeautifulSoup(html, 'html.parser')
        staff_records = []

        # Find main content
        main = soup.find('main') or soup.find('article') or soup

        current_location = 'dc'
        current_department = None

        # Process headings and following content
        for element in main.find_all(['h2', 'h3', 'h4', 'p', 'li']):
            text = element.get_text(strip=True)

            # Track location (DC vs State)
            if element.name in ['h2', 'h3']:
                if 'state' in text.lower() or 'district' in text.lower():
                    current_location = 'district'
                elif 'washington' in text.lower() or 'd.c.' in text.lower():
                    current_location = 'dc'
                current_department = text
                continue

            # Look for staff entries (name - title pattern)
            for pattern in self.TITLE_PATTERNS:
                if re.search(pattern, text, re.IGNORECASE):
                    # Try to extract name and title
                    record = self._parse_staff_entry(text, member_info, current_location)
                    if record:
                        staff_records.append(record)
                    break

        return staff_records

    def _parse_staff_entry(self, text: str, member_info: Dict, location: str) -> Dict:
        """Parse a single staff entry."""
        # Common patterns: "Name - Title" or "Title: Name"
        # Implementation details...
        pass
```

---

## 7. Validation & Quality Control

### Data Validation Rules
```python
VALIDATION_RULES = {
    'full_name': {
        'min_length': 3,
        'max_length': 100,
        'pattern': r'^[A-Za-z\s\-\'.]+$'
    },
    'title': {
        'min_length': 2,
        'max_length': 100,
    }
}
```

### Confidence Scoring
```python
def calculate_confidence(record: Dict, source_type: str) -> float:
    """Calculate confidence score for extracted record."""
    base_scores = {
        'directory': 0.9,
        'press_release': 0.8,
        'about': 0.6,
    }

    score = base_scores.get(source_type, 0.5)

    # Adjust based on data quality
    if record.get('title_normalized'):
        score += 0.05
    if record.get('first_name') and record.get('last_name'):
        score += 0.05

    return min(score, 1.0)
```

### Quality Metrics to Track
- Extraction rate: % of members with staff data
- Parse success rate: % of pages parsed without errors
- Title coverage: % of titles matching known patterns
- Duplicate detection: Number flagged as potential duplicates

---

## 8. Incremental Updates

### Update Strategy
1. **Weekly full scan**: Check all member websites for changes
2. **Daily press release scan**: Monitor for staff announcements
3. **On-demand refresh**: Triggered via API/webhook

### Change Detection
```python
def detect_changes(member_id: str, new_staff: List[Dict]) -> Dict:
    """Compare new staff data against existing records."""
    existing = get_existing_staff(member_id)

    changes = {
        'added': [],
        'removed': [],
        'changed': []
    }

    existing_names = {s['full_name'] for s in existing}
    new_names = {s['full_name'] for s in new_staff}

    changes['added'] = [s for s in new_staff if s['full_name'] not in existing_names]
    changes['removed'] = [s for s in existing if s['full_name'] not in new_names]

    return changes
```

---

## 9. Error Handling & Monitoring

### Error Categories
1. **HTTP Errors**: 404, 403, 500, timeout
2. **Parse Errors**: Unexpected HTML structure
3. **Validation Errors**: Invalid data extracted
4. **Rate Limit Errors**: Too many requests

### Logging Strategy
```python
import logging

logger = logging.getLogger('staff_scraper')

# Log levels:
# DEBUG: Individual parse operations
# INFO: Member processed, staff count
# WARNING: Parse issues, missing data
# ERROR: HTTP failures, critical parse errors
```

### Monitoring Dashboard Metrics
- Members processed / total
- Staff records extracted
- Error rate by type
- Pages with no staff data
- New staff detected (changes)

---

## 10. Timeline & Resources

| Phase | Duration | Effort |
|-------|----------|--------|
| Phase 1: Member Discovery | 1 week | 10 hours |
| Phase 2: Staff Page Discovery | 1 week | 15 hours |
| Phase 3: Parser Development | 2 weeks | 30 hours |
| Phase 4: Data Extraction | 2 weeks | 20 hours |
| Phase 5: Integration | 1 week | 10 hours |
| **Total** | **7 weeks** | **85 hours** |

### Dependencies
- BeautifulSoup4 (HTML parsing)
- requests (HTTP client)
- lxml (fast XML/HTML parser)
- sqlite3 (database)

---

## 11. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Website structure changes | High | Multiple parser strategies, fallback logic |
| Rate limiting/blocking | Medium | Respectful crawling, delays, rotating user agents |
| Low staff page coverage | Medium | Press release parsing, manual data entry |
| Data quality issues | Medium | Validation rules, confidence scoring |
| Member website downtime | Low | Retry logic, cached results |

---

## 12. Success Criteria

- [ ] Staff data for 60%+ of members
- [ ] 90%+ parse success rate on pages with staff info
- [ ] All extracted records pass validation
- [ ] Integration with CivicCache pipeline complete
- [ ] Incremental update process working
- [ ] Documentation and tests complete

---

## 13. Next Steps

1. **Approve this plan**
2. Create `scripts/staff_scraper/` directory structure
3. Implement Phase 1 (member discovery)
4. Build prototype parser for Senate staff directories
5. Test on 10 member websites
6. Iterate and expand

---

*Document prepared for CivicCache congressional staff scraping implementation.*
