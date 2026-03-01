# Plan: Adding Congressional Staff Table to CivicScope Database

## Executive Summary

This plan outlines the approach for extracting congressional staff data from the Congressional Directory (CDIR) available on GovInfo.gov and integrating it into the CivicScope database. The data covers staff for members of Congress, committees, and institutional offices.

---

## 1. Data Source Analysis

### Primary Source
**GovInfo.gov Congressional Directory (CDIR)**
- URL: https://www.govinfo.gov/app/collection/CDIR
- Coverage: 41st Congress (1869) to present
- Electronic formats (PDF, HTML, TXT): 105th Congress (1997) forward
- Current directory: 118th Congress (2023-2024)

### Available Data Formats

| Format | Pros | Cons | Recommended Use |
|--------|------|------|-----------------|
| **HTML** | Structured markup, easier parsing | May have inconsistent formatting | Primary extraction source |
| **TXT** | Plain text, simple processing | No structure, harder to parse | Fallback/validation |
| **PDF** | Official format, complete | Requires OCR/PDF parsing | Historical records (pre-105th) |

### Data Access Methods

1. **GovInfo API** (Recommended for automation)
   - Endpoint: `https://api.govinfo.gov/`
   - Requires free API key from api.data.gov
   - Supports package/granule retrieval
   - Can fetch HTML, PDF, MODS metadata

2. **Direct URL Construction**
   - Pattern: `https://www.govinfo.gov/content/pkg/{PACKAGE_ID}/html/{GRANULE_ID}.htm`
   - Example: `CDIR-2024-04-25` for April 2024 edition

3. **Bulk Download** (for initial load)
   - Download entire directory as ZIP
   - Process offline for faster iteration

---

## 2. Data Categories & Schema Design

### Staff Categories Found in CDIR

#### A. Personal Office Staff (Member Offices)
Located in member biography sections (by state/district)

| Staff Position | Frequency | Notes |
|----------------|-----------|-------|
| Chief of Staff | Common | Primary staff lead |
| Deputy Chief of Staff | Common | |
| Legislative Director | Very Common | |
| Communications Director | Common | |
| Press Secretary | Common | |
| Scheduler | Common | |
| District Director | Very Common | State/district offices |
| Office Manager | Occasional | |
| Counsel | Occasional | |
| Staff Assistant | Common | Multiple per office |

#### B. Committee Staff
Located in Senate/House committee sections

| Staff Position | Frequency | Notes |
|----------------|-----------|-------|
| Staff Director | All committees | Majority side |
| Minority Staff Director | All committees | Minority side |
| Chief Counsel | Most committees | |
| Deputy Staff Director | Common | |
| Chief Clerk | Common | |
| Professional Staff | Very Common | Multiple per committee |
| Policy Advisor | Common | |
| Communications Director | Common | |

#### C. Institutional Staff
Located in Officers/Officials sections

| Category | Examples |
|----------|----------|
| Leadership Offices | Majority/Minority Leader staff, Whip staff |
| Secretary of Senate | Executive staff, clerks |
| Sergeant at Arms | Capitol police, administrative |
| Chaplain | Small staff |
| Parliamentarian | Procedural staff |

### Proposed Database Schema

```sql
-- Core staff table
CREATE TABLE congressional_staff (
    id SERIAL PRIMARY KEY,
    
    -- Personal identification
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    full_name VARCHAR(255) NOT NULL,
    suffix VARCHAR(20),  -- Jr., III, etc.
    
    -- Position details
    title VARCHAR(255) NOT NULL,
    title_normalized VARCHAR(100),  -- Standardized title for querying
    
    -- Organizational affiliation
    office_type VARCHAR(50) NOT NULL,  -- 'member', 'committee', 'institutional'
    chamber VARCHAR(20),  -- 'senate', 'house', 'joint', NULL
    
    -- Member office fields (when office_type = 'member')
    member_bioguide_id VARCHAR(10),  -- Foreign key to members table
    member_name VARCHAR(255),
    office_location VARCHAR(50),  -- 'dc', 'district', 'state'
    
    -- Committee fields (when office_type = 'committee')
    committee_code VARCHAR(20),  -- Foreign key to committees table
    committee_name VARCHAR(255),
    subcommittee_name VARCHAR(255),
    party_affiliation VARCHAR(20),  -- 'majority', 'minority', NULL
    
    -- Institutional fields (when office_type = 'institutional')
    institutional_office VARCHAR(255),
    
    -- Source tracking
    congress_number INTEGER NOT NULL,
    source_document VARCHAR(100),  -- CDIR package ID
    source_section VARCHAR(100),   -- Granule ID
    effective_date DATE,
    
    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_current BOOLEAN DEFAULT TRUE,
    
    -- Indexing
    CONSTRAINT idx_staff_member UNIQUE (member_bioguide_id, title, congress_number) 
        WHERE office_type = 'member',
    CONSTRAINT idx_staff_committee UNIQUE (committee_code, title, full_name, congress_number)
        WHERE office_type = 'committee'
);

-- Indexes for common queries
CREATE INDEX idx_staff_name ON congressional_staff(last_name, first_name);
CREATE INDEX idx_staff_member_id ON congressional_staff(member_bioguide_id);
CREATE INDEX idx_staff_committee ON congressional_staff(committee_code);
CREATE INDEX idx_staff_congress ON congressional_staff(congress_number);
CREATE INDEX idx_staff_title ON congressional_staff(title_normalized);
CREATE INDEX idx_staff_current ON congressional_staff(is_current) WHERE is_current = TRUE;

-- Title normalization lookup table
CREATE TABLE staff_title_mappings (
    raw_title VARCHAR(255) PRIMARY KEY,
    normalized_title VARCHAR(100) NOT NULL,
    category VARCHAR(50)  -- 'leadership', 'legislative', 'communications', 'admin', etc.
);

-- Historical tracking (for staff changes over time)
CREATE TABLE congressional_staff_history (
    id SERIAL PRIMARY KEY,
    staff_id INTEGER REFERENCES congressional_staff(id),
    change_type VARCHAR(20),  -- 'added', 'removed', 'title_change', 'office_change'
    previous_values JSONB,
    new_values JSONB,
    congress_number INTEGER,
    detected_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 3. Data Extraction Pipeline

### Phase 1: Document Retrieval

```python
# Pseudocode for document retrieval

# Step 1: Get list of available CDIR packages
def get_cdir_packages(api_key, start_congress=105):
    """Retrieve all CDIR package IDs from specified Congress forward."""
    packages = []
    url = f"https://api.govinfo.gov/collections/CDIR?api_key={api_key}"
    # Paginate through results
    # Filter by congress number
    return packages

# Step 2: Get granules for each package
def get_package_granules(package_id, api_key):
    """Get all granules (individual documents) in a package."""
    url = f"https://api.govinfo.gov/packages/{package_id}/granules?api_key={api_key}"
    # Return list of granule IDs with their sections
    return granules

# Step 3: Download relevant granules
def download_granule(package_id, granule_id, format='html'):
    """Download specific granule in desired format."""
    url = f"https://www.govinfo.gov/content/pkg/{package_id}/{format}/{granule_id}.{format}"
    return content
```

### Phase 2: HTML Parsing Strategy

The HTML files follow semi-structured patterns. Key parsing approaches:

```python
# Parsing patterns for different sections

# Member office staff pattern (in biography sections)
MEMBER_STAFF_PATTERNS = [
    r"Chief of Staff[.—:\s]+([A-Za-z\s,.']+)",
    r"Legislative Director[.—:\s]+([A-Za-z\s,.']+)",
    r"Communications Director[.—:\s]+([A-Za-z\s,.']+)",
    r"Press Secretary[.—:\s]+([A-Za-z\s,.']+)",
    r"Scheduler[.—:\s]+([A-Za-z\s,.']+)",
    r"District Director[.—:\s]+([A-Za-z\s,.']+)",
    # Handle multiple staff with same title
    r"Staff Assistants?[.—:\s]+([A-Za-z\s,.']+(?:,\s*[A-Za-z\s,.']+)*)",
]

# Committee staff pattern
COMMITTEE_STAFF_PATTERNS = [
    r"Staff Director[.—:\s]+([A-Za-z\s,.']+)",
    r"Minority Staff Director[.—:\s]+([A-Za-z\s,.']+)",
    r"Chief Counsel[.—:\s]+([A-Za-z\s,.']+)",
    r"Professional Staff[.—:\s]+([A-Za-z\s,.';]+)",
]

def parse_member_section(html_content, member_info):
    """Extract staff from a member biography section."""
    staff_records = []
    
    for pattern_name, pattern in MEMBER_STAFF_PATTERNS.items():
        matches = re.findall(pattern, html_content)
        for match in matches:
            # Handle multiple names (comma-separated)
            names = split_names(match)
            for name in names:
                staff_records.append({
                    'full_name': clean_name(name),
                    'title': pattern_name,
                    'member_bioguide_id': member_info['bioguide_id'],
                    'office_type': 'member',
                    'chamber': member_info['chamber']
                })
    
    return staff_records
```

### Phase 3: Name Parsing & Normalization

```python
def parse_full_name(full_name):
    """Parse name into components, handling edge cases."""
    # Remove titles/prefixes
    full_name = re.sub(r'^(Dr\.|Mr\.|Ms\.|Mrs\.)\s+', '', full_name)
    
    # Handle suffixes
    suffix_match = re.search(r',?\s*(Jr\.|Sr\.|III|IV|II)\s*$', full_name)
    suffix = suffix_match.group(1) if suffix_match else None
    if suffix:
        full_name = full_name[:suffix_match.start()]
    
    # Split remaining name
    parts = full_name.strip().split()
    
    if len(parts) >= 2:
        first_name = parts[0]
        last_name = parts[-1]
        middle = ' '.join(parts[1:-1]) if len(parts) > 2 else None
    else:
        first_name = None
        last_name = parts[0] if parts else full_name
    
    return {
        'first_name': first_name,
        'last_name': last_name,
        'middle_name': middle,
        'suffix': suffix,
        'full_name': full_name
    }

def normalize_title(raw_title):
    """Map raw titles to standardized categories."""
    title_map = {
        'Chief of Staff': 'chief_of_staff',
        'CoS': 'chief_of_staff',
        'Deputy Chief of Staff': 'deputy_chief_of_staff',
        'Legislative Director': 'legislative_director',
        'LD': 'legislative_director',
        'Communications Director': 'communications_director',
        'Press Secretary': 'press_secretary',
        'Scheduler': 'scheduler',
        'District Director': 'district_director',
        'State Director': 'state_director',
        # ... extend as needed
    }
    return title_map.get(raw_title.strip(), raw_title.lower().replace(' ', '_'))
```

---

## 4. Implementation Phases

### Phase 1: Proof of Concept (Week 1-2)
**Goal**: Validate extraction approach with single Congress

1. Obtain GovInfo API key from api.data.gov
2. Download 118th Congress directory (current)
3. Build parser for 3 test sections:
   - One Senate member biography
   - One House member biography
   - One committee
4. Validate extracted data manually
5. Refine parsing patterns

**Deliverables**:
- Working extraction script for test cases
- Initial data quality assessment
- Refined regex patterns

### Phase 2: Full Current Congress (Week 3-4)
**Goal**: Extract all staff from current directory

1. Extend parser to all sections:
   - All Senate members (100)
   - All House members (435)
   - All committees (~40)
   - Institutional offices
2. Build data validation layer
3. Implement database loading
4. Generate data quality report

**Deliverables**:
- Complete 118th Congress staff data
- Data quality metrics
- Loading scripts

### Phase 3: Historical Data (Week 5-8)
**Goal**: Backfill historical Congresses

1. Prioritize recent Congresses (117th, 116th, etc.)
2. Handle format variations across years
3. Implement incremental loading
4. Build deduplication logic

**Deliverables**:
- Historical staff data (configurable depth)
- Change tracking implementation
- Historical trend analysis capability

### Phase 4: Automation & Maintenance (Week 9-10)
**Goal**: Establish ongoing data pipeline

1. Build scheduled extraction job (monitor for new editions)
2. Implement change detection (who's new, who left)
3. Create data refresh workflow
4. Build monitoring/alerting

**Deliverables**:
- Automated extraction pipeline
- Change notification system
- Documentation

---

## 5. Technical Implementation Details

### Recommended Tech Stack

| Component | Technology | Rationale |
|-----------|------------|-----------|
| Extraction | Python 3.11+ | Rich ecosystem for web scraping |
| HTML Parsing | BeautifulSoup4 + lxml | Fast, flexible HTML parsing |
| PDF Parsing | PyMuPDF (fitz) | For historical pre-105th Congress |
| HTTP Client | httpx or requests | API calls |
| Database | PostgreSQL | Existing CivicScope DB |
| ORM | SQLAlchemy | If using Python backend |
| Scheduling | Apache Airflow or Prefect | Pipeline orchestration |
| Monitoring | Sentry + custom logging | Error tracking |

### Project Structure

```
civicscope-staff-ingestion/
├── src/
│   ├── __init__.py
│   ├── config.py              # API keys, DB connection
│   ├── api/
│   │   ├── govinfo_client.py  # GovInfo API wrapper
│   │   └── congress_gov.py    # Congress.gov API (supplemental)
│   ├── parsers/
│   │   ├── base_parser.py     # Abstract parser class
│   │   ├── member_parser.py   # Member biography parser
│   │   ├── committee_parser.py
│   │   └── institutional_parser.py
│   ├── models/
│   │   ├── staff.py           # SQLAlchemy models
│   │   └── title_mapping.py
│   ├── loaders/
│   │   ├── database_loader.py # DB insert/update logic
│   │   └── validation.py      # Data quality checks
│   └── utils/
│       ├── name_parser.py     # Name normalization
│       └── text_cleaning.py   # HTML/text cleaning
├── scripts/
│   ├── initial_load.py        # One-time historical load
│   ├── incremental_update.py  # Scheduled updates
│   └── generate_report.py     # Data quality reports
├── tests/
│   ├── test_parsers.py
│   ├── test_name_parser.py
│   └── fixtures/              # Sample HTML files
├── data/
│   └── title_mappings.csv     # Title normalization data
├── requirements.txt
└── README.md
```

### Key Dependencies

```txt
# requirements.txt
httpx>=0.24.0
beautifulsoup4>=4.12.0
lxml>=4.9.0
sqlalchemy>=2.0.0
psycopg2-binary>=2.9.0
python-dateutil>=2.8.0
pydantic>=2.0.0
pymupdf>=1.23.0  # For PDF parsing
tenacity>=8.2.0  # Retry logic
structlog>=23.0.0  # Structured logging
```

---

## 6. Data Quality & Validation

### Expected Challenges

| Challenge | Mitigation Strategy |
|-----------|---------------------|
| Inconsistent formatting | Multiple regex patterns, fuzzy matching |
| Name variations | Normalize to canonical form, track aliases |
| Missing data | Flag for manual review, don't fail pipeline |
| OCR errors (historical) | Confidence scoring, manual verification |
| Staff with same name | Use office context for disambiguation |
| Title abbreviations | Comprehensive title mapping table |

### Validation Rules

```python
VALIDATION_RULES = {
    'full_name': {
        'required': True,
        'min_length': 3,
        'max_length': 255,
        'pattern': r'^[A-Za-z\s\-\'.]+$'
    },
    'title': {
        'required': True,
        'min_length': 2,
        'known_values': KNOWN_TITLES  # Warn on unknown
    },
    'member_bioguide_id': {
        'required_when': lambda r: r['office_type'] == 'member',
        'pattern': r'^[A-Z]\d{6}$'
    },
    'congress_number': {
        'required': True,
        'min_value': 41,
        'max_value': 120  # Future-proof
    }
}
```

### Quality Metrics to Track

- **Extraction rate**: % of expected staff positions captured
- **Parse success rate**: % of sections parsed without errors
- **Name quality**: % of names with first/last successfully split
- **Title coverage**: % of titles matching known mappings
- **Duplicate detection**: Number of potential duplicates flagged

---

## 7. Integration with CivicScope

### API Endpoints (Suggested)

```
GET /api/v1/staff
    ?member_id={bioguide_id}
    ?committee={committee_code}
    ?title={title_normalized}
    ?congress={number}
    ?chamber={senate|house}
    
GET /api/v1/staff/{id}

GET /api/v1/staff/search?q={name}

GET /api/v1/staff/history
    ?staff_id={id}
    ?congress_range={start}-{end}
```

### Linking to Existing Data

Ensure foreign key relationships to existing CivicScope tables:

```sql
-- Link to members table (if exists)
ALTER TABLE congressional_staff
ADD CONSTRAINT fk_staff_member
FOREIGN KEY (member_bioguide_id) REFERENCES members(bioguide_id);

-- Link to committees table (if exists)
ALTER TABLE congressional_staff
ADD CONSTRAINT fk_staff_committee
FOREIGN KEY (committee_code) REFERENCES committees(code);
```

---

## 8. Resources & References

### GovInfo Resources
- API Documentation: https://api.govinfo.gov/docs
- GitHub: https://github.com/usgpo/api
- CDIR Help: https://www.govinfo.gov/help/cdir
- Bulk Data: https://www.govinfo.gov/bulkdata

### Supplementary Data Sources
- Congress.gov API: https://api.congress.gov (member data, committees)
- @unitedstates/congress: https://github.com/unitedstates/congress
- ProPublica Congress API: https://projects.propublica.org/api-docs/congress-api/

### Related Projects
- GovTrack.us: https://www.govtrack.us/about-our-data
- OpenSecrets: https://www.opensecrets.org (lobbying connections)

---

## 9. Estimated Timeline & Resources

| Phase | Duration | Effort |
|-------|----------|--------|
| Phase 1: POC | 2 weeks | 20-30 hours |
| Phase 2: Current Congress | 2 weeks | 30-40 hours |
| Phase 3: Historical | 4 weeks | 40-60 hours |
| Phase 4: Automation | 2 weeks | 20-30 hours |
| **Total** | **10 weeks** | **110-160 hours** |

### Ongoing Maintenance
- Monitor for new CDIR releases (~quarterly)
- Handle format changes (~1-2 hours per release)
- Data quality review (~2 hours monthly)

---

## 10. Next Steps

1. **Obtain GovInfo API key** from https://api.data.gov/signup/
2. **Review current CivicScope schema** for integration points
3. **Download sample CDIR files** for parser development
4. **Set up development environment** with dependencies
5. **Begin Phase 1 POC** with single-member parsing

---

*Document prepared for CivicScope congressional staff data integration project.*
