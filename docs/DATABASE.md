# CivicCache Database Schema

## Overview

The CivicCache SQLite database provides optimized access to Congressional and FEC campaign finance data for the CivicScope mobile application. The schema is designed for:
- Fast queries on mobile devices
- Full-text search capabilities
- Efficient data relationships
- iOS/Android compatibility

## Database Design Principles

1. **Normalized Structure**: Minimize data duplication while maintaining query performance
2. **Full-Text Search**: FTS5 tables for member names, candidate names, and committees
3. **Comprehensive Indexing**: Strategic indexes on frequently queried columns
4. **Foreign Key Constraints**: Maintain data integrity across relationships
5. **Mobile Optimization**: Efficient for offline-first mobile apps

## Core Tables

### 1. congress_members

Current members of the 119th United States Congress (537 members).

```sql
CREATE TABLE congress_members (
    id TEXT PRIMARY KEY,              -- Bioguide ID (e.g., "B000944")
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    full_name TEXT NOT NULL,
    nickname TEXT,
    middle_name TEXT,
    suffix TEXT,

    -- Current position
    chamber TEXT NOT NULL,            -- 'House' or 'Senate'
    state_code TEXT NOT NULL,         -- Two-letter state code
    state_name TEXT NOT NULL,
    district TEXT,                    -- For House members (NULL for Senators)

    -- Political affiliation
    current_party TEXT NOT NULL,      -- 'Democratic', 'Republican', 'Independent'

    -- Contact information
    office_address TEXT,
    phone TEXT,
    website_url TEXT,
    contact_form_url TEXT,

    -- Social media
    twitter_handle TEXT,
    facebook_url TEXT,
    youtube_url TEXT,

    -- Additional info
    birth_year INTEGER,
    gender TEXT,

    -- Term information
    term_start_date TEXT,             -- ISO 8601 date
    term_end_date TEXT,               -- ISO 8601 date

    -- Service history (from Congress.gov API)
    first_elected_year INTEGER,       -- Year first elected to Congress
    total_years_served INTEGER,       -- Total years in Congress
    total_terms_served INTEGER,       -- Total number of terms served
    current_leadership_role TEXT,     -- Current leadership position (e.g., "Speaker of the House")

    -- Legislative activity (from Congress.gov API)
    sponsored_bills_count INTEGER,    -- Total bills/resolutions sponsored
    cosponsored_bills_count INTEGER,  -- Total bills/resolutions cosponsored

    -- Official portrait (from Congress.gov API)
    depiction_url TEXT,               -- URL to official portrait image
    depiction_attribution TEXT,       -- Image attribution/credit

    -- Metadata
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (state_code) REFERENCES states(code)
);

CREATE INDEX idx_members_chamber ON congress_members(chamber);
CREATE INDEX idx_members_state ON congress_members(state_code);
CREATE INDEX idx_members_party ON congress_members(current_party);
CREATE INDEX idx_members_district ON congress_members(state_code, district);
```

### 2. congress_members_fts

Full-text search index for Congress members.

```sql
CREATE VIRTUAL TABLE congress_members_fts USING fts5(
    member_id,
    full_name,
    first_name,
    last_name,
    state_name,
    content=congress_members,
    content_rowid=rowid
);
```

### 3. states

US states and territories reference data.

```sql
CREATE TABLE states (
    code TEXT PRIMARY KEY,            -- Two-letter state code
    name TEXT NOT NULL UNIQUE,
    fips_code TEXT,                   -- Federal Information Processing Standards code
    is_territory INTEGER DEFAULT 0,   -- 0 for states, 1 for territories

    -- Geographic info
    region TEXT,                      -- Northeast, South, Midwest, West, Territory
    population INTEGER,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_states_region ON states(region);
```

### 4. congressional_districts

Geographic boundary data for all 444 congressional districts.

**Note**: Due to the large size of GeoJSON data (276 MB), we have two options:

**Option A**: Store simplified district metadata only (recommended for mobile):
```sql
CREATE TABLE congressional_districts (
    district_id TEXT PRIMARY KEY,     -- Format: "STATE_CODE-DISTRICT_NUM" (e.g., "CA-01")
    state_code TEXT NOT NULL,
    district_number TEXT NOT NULL,
    fips_code TEXT,

    -- Current representative
    current_member_id TEXT,

    -- Simplified geography (centroid for mapping)
    centroid_latitude REAL,
    centroid_longitude REAL,

    -- Bounding box for quick map filtering
    bbox_min_lat REAL,
    bbox_min_lon REAL,
    bbox_max_lat REAL,
    bbox_max_lon REAL,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (state_code) REFERENCES states(code),
    FOREIGN KEY (current_member_id) REFERENCES congress_members(id)
);
```

**Option B**: Store full GeoJSON (for desktop/server use):
```sql
-- Add column to store full GeoJSON geometry
ALTER TABLE congressional_districts ADD COLUMN geojson TEXT;
```

For CivicScope mobile app, we recommend **Option A** with GeoJSON served from a separate API/CDN when needed for detailed mapping.

### 5. candidates

FEC candidate records (1979-2026).

```sql
CREATE TABLE candidates (
    candidate_id TEXT PRIMARY KEY,    -- FEC candidate ID
    candidate_name TEXT NOT NULL,

    -- Election info
    office TEXT,                      -- 'H' (House), 'S' (Senate), 'P' (President)
    office_state TEXT,
    office_district TEXT,

    -- Party and status
    party_code TEXT,                  -- 'DEM', 'REP', 'IND', etc.
    party_full_name TEXT,
    incumbent_challenger_status TEXT, -- 'I', 'C', 'O'
    candidate_status TEXT,            -- 'C' (candidate), 'F' (future), 'N' (not yet candidate), 'P' (prior candidate)

    -- Election cycle
    election_year INTEGER NOT NULL,
    election_cycle INTEGER NOT NULL,

    -- Financial summary (from most recent cycle)
    total_receipts REAL,
    total_disbursements REAL,
    cash_on_hand REAL,
    total_contributions REAL,
    candidate_contributions REAL,

    -- Principal campaign committee
    principal_committee_id TEXT,

    -- Address
    mailing_address_1 TEXT,
    mailing_address_2 TEXT,
    mailing_city TEXT,
    mailing_state TEXT,
    mailing_zip TEXT,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (principal_committee_id) REFERENCES committees(committee_id)
);

CREATE INDEX idx_candidates_office ON candidates(office);
CREATE INDEX idx_candidates_year ON candidates(election_year);
CREATE INDEX idx_candidates_state ON candidates(office_state);
CREATE INDEX idx_candidates_party ON candidates(party_code);
CREATE INDEX idx_candidates_cycle ON candidates(election_cycle);
```

### 6. candidates_fts

Full-text search index for candidates.

```sql
CREATE VIRTUAL TABLE candidates_fts USING fts5(
    candidate_id,
    candidate_name,
    office_state,
    party_full_name,
    content=candidates,
    content_rowid=rowid
);
```

### 7. committees

Political committees and PACs.

```sql
CREATE TABLE committees (
    committee_id TEXT PRIMARY KEY,    -- FEC committee ID
    committee_name TEXT NOT NULL,
    committee_type TEXT,              -- 'H', 'S', 'P', 'N', 'Q', 'O', etc.
    committee_type_full TEXT,

    -- Designation
    committee_designation TEXT,       -- 'P' (Principal), 'A' (Authorized), 'J' (Joint), etc.
    committee_designation_full TEXT,

    -- Party affiliation
    party_code TEXT,
    party_full_name TEXT,

    -- Financial summary
    total_receipts REAL,
    total_disbursements REAL,
    cash_on_hand REAL,
    coverage_end_date TEXT,

    -- Connected organization
    connected_org_name TEXT,

    -- Treasurer
    treasurer_name TEXT,

    -- Address
    street_1 TEXT,
    street_2 TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,

    -- Filing info
    filing_frequency TEXT,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_committees_type ON committees(committee_type);
CREATE INDEX idx_committees_party ON committees(party_code);
CREATE INDEX idx_committees_state ON committees(state);
```

### 8. committees_fts

Full-text search index for committees.

```sql
CREATE VIRTUAL TABLE committees_fts USING fts5(
    committee_id,
    committee_name,
    connected_org_name,
    party_full_name,
    content=committees,
    content_rowid=rowid
);
```

### 9. candidate_committee_linkages

Relationships between candidates and their associated committees.

```sql
CREATE TABLE candidate_committee_linkages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    committee_id TEXT NOT NULL,
    linkage_type TEXT,                -- 'P' (Principal), 'A' (Authorized), etc.
    election_year INTEGER,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id),
    FOREIGN KEY (committee_id) REFERENCES committees(committee_id),

    UNIQUE(candidate_id, committee_id, election_year)
);

CREATE INDEX idx_linkages_candidate ON candidate_committee_linkages(candidate_id);
CREATE INDEX idx_linkages_committee ON candidate_committee_linkages(committee_id);
```

### 10. contributions

Campaign contributions from committees (sampled data for mobile).

**Note**: Full contributions data is massive. We store aggregated/sampled data for mobile.

```sql
CREATE TABLE contributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Contributor
    committee_id TEXT NOT NULL,       -- Contributor committee

    -- Recipient
    recipient_candidate_id TEXT,
    recipient_committee_id TEXT,

    -- Transaction details
    transaction_date TEXT,            -- ISO 8601 date
    transaction_amount REAL NOT NULL,
    transaction_type TEXT,

    -- Additional context
    entity_type TEXT,                 -- 'IND', 'ORG', 'PAC', 'CCM'
    city TEXT,
    state TEXT,
    employer TEXT,
    occupation TEXT,

    -- Memo
    memo_text TEXT,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (committee_id) REFERENCES committees(committee_id),
    FOREIGN KEY (recipient_candidate_id) REFERENCES candidates(candidate_id),
    FOREIGN KEY (recipient_committee_id) REFERENCES committees(committee_id)
);

CREATE INDEX idx_contributions_committee ON contributions(committee_id);
CREATE INDEX idx_contributions_recipient_candidate ON contributions(recipient_candidate_id);
CREATE INDEX idx_contributions_date ON contributions(transaction_date);
CREATE INDEX idx_contributions_amount ON contributions(transaction_amount);
```

### 11. metadata

Database versioning and statistics.

```sql
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Initial metadata
INSERT INTO metadata (key, value) VALUES
    ('schema_version', '1.0.0'),
    ('database_created', datetime('now')),
    ('data_source', 'CivicScope v1.0'),
    ('congress_session', '119'),
    ('fec_data_range', '1979-2026');
```

## Database Statistics (Current)

| Table | Rows | Purpose |
|-------|------|---------|
| congress_members | 537 | Current Congress with full metadata |
| legislation | 10,798 | Bills from 119th Congress |
| legislation_summaries | ~10,000 | Bill summaries and analysis |
| legislation_sponsors | 114,164 | Bill sponsorship records (10,789 primary + 103,375 cosponsors) |
| legislation_subjects | ~50,000 | Bill subject tags |
| states | 56 | US states + territories |
| congressional_districts | 444 | District metadata |
| candidates | ~50,000+ | All FEC candidates (1979-2026) |
| committees | ~20,000+ | All active/historical committees |
| candidate_committee_linkages | ~100,000+ | Candidate-committee relationships |
| contributions | ~1,000,000+ | Sampled contribution records |

**Current Database Size**: 164 MB (with legislation summaries, without full legislative actions)

**Note**: Legislative actions for each bill are available via Congress.gov API on-demand to reduce database size.

## Query Examples

### Find your representative by location

```sql
-- Find district by state and district number
SELECT m.*
FROM congress_members m
WHERE m.state_code = 'CA'
  AND m.district = '12'
  AND m.chamber = 'House';
```

### Search members by name

```sql
-- Full-text search for members
SELECT m.*
FROM congress_members m
JOIN congress_members_fts fts ON m.id = fts.member_id
WHERE congress_members_fts MATCH 'pelosi';
```

### Find candidate's campaign finances

```sql
-- Get candidate with their principal committee
SELECT
    c.candidate_name,
    c.election_year,
    c.total_receipts,
    c.total_disbursements,
    c.cash_on_hand,
    cm.committee_name
FROM candidates c
LEFT JOIN committees cm ON c.principal_committee_id = cm.committee_id
WHERE c.candidate_name LIKE '%Biden%'
  AND c.election_year = 2020;
```

### Top contributors to a candidate

```sql
-- Aggregate contributions by committee
SELECT
    cm.committee_name,
    cm.party_full_name,
    SUM(contrib.transaction_amount) as total_contributed,
    COUNT(*) as num_contributions
FROM contributions contrib
JOIN committees cm ON contrib.committee_id = cm.committee_id
JOIN candidates cand ON contrib.recipient_candidate_id = cand.candidate_id
WHERE cand.candidate_name LIKE '%Harris%'
  AND cand.election_year = 2024
GROUP BY cm.committee_id
ORDER BY total_contributed DESC
LIMIT 20;
```

### Members by party and chamber

```sql
-- Count members by party and chamber
SELECT
    chamber,
    current_party,
    COUNT(*) as count
FROM congress_members
GROUP BY chamber, current_party
ORDER BY chamber, count DESC;
```

### Most senior members of Congress

```sql
-- Find longest-serving members
SELECT
    full_name,
    chamber,
    state_code,
    first_elected_year,
    total_years_served,
    total_terms_served,
    current_leadership_role
FROM congress_members
WHERE total_years_served IS NOT NULL
ORDER BY total_years_served DESC
LIMIT 10;
```

### Congressional leadership roster

```sql
-- Get all current congressional leaders
SELECT
    full_name,
    chamber,
    current_party,
    current_leadership_role
FROM congress_members
WHERE current_leadership_role IS NOT NULL
ORDER BY chamber, current_party;
```

### Most active legislators

```sql
-- Members with most sponsored legislation
SELECT
    full_name,
    chamber,
    state_code,
    sponsored_bills_count,
    cosponsored_bills_count,
    (sponsored_bills_count + cosponsored_bills_count) as total_legislative_activity
FROM congress_members
WHERE sponsored_bills_count IS NOT NULL
ORDER BY total_legislative_activity DESC
LIMIT 20;
```

### Bill sponsors and cosponsors

```sql
-- Get primary sponsor and all cosponsors for a bill
SELECT
    l.id,
    l.title,
    ls.full_name,
    ls.party,
    ls.state,
    CASE
        WHEN ls.is_primary_sponsor = 1 THEN 'Primary Sponsor'
        ELSE 'Cosponsor'
    END as sponsor_type,
    ls.sponsorship_date
FROM legislation l
JOIN legislation_sponsors ls ON l.id = ls.legislation_id
WHERE l.id = '119-hr-5815'
ORDER BY ls.is_primary_sponsor DESC, ls.sponsorship_date;

-- Get only the primary sponsor of a bill
SELECT
    l.id,
    l.title,
    ls.full_name as primary_sponsor,
    ls.party,
    ls.state
FROM legislation l
JOIN legislation_sponsors ls ON l.id = ls.legislation_id AND ls.is_primary_sponsor = 1
WHERE l.id = '119-hr-5815';

-- Find bills sponsored by a specific member
SELECT
    l.id,
    l.title,
    l.introduced_date,
    COUNT(cs.id) as cosponsor_count
FROM legislation l
JOIN legislation_sponsors ls ON l.id = ls.legislation_id AND ls.is_primary_sponsor = 1
LEFT JOIN legislation_sponsors cs ON l.id = cs.legislation_id AND cs.is_primary_sponsor = 0
WHERE ls.bioguide_id = 'B000944'  -- Example: Sherrod Brown
GROUP BY l.id
ORDER BY l.introduced_date DESC
LIMIT 10;
```

## Performance Considerations

1. **Indexes**: All foreign keys and frequently queried columns are indexed
2. **FTS5**: Full-text search provides sub-second query times
3. **Partial Data**: Mobile database contains sampled/aggregated data, not complete archive
4. **GeoJSON**: Store simplified geographic data; serve full GeoJSON from API when needed
5. **Contributions**: Consider time-based sampling (e.g., recent 5 years + major donations)

## iOS Integration

The database is designed to work seamlessly with SwiftData and CoreData:

```swift
// Example: Query members using raw SQL
let db = try Connection("civic_cache.db")
let members = try db.prepare("SELECT * FROM congress_members WHERE state_code = ?")

// Or use SwiftData models that mirror the schema
@Model
class CongressMember {
    @Attribute(.unique) var id: String
    var firstName: String
    var lastName: String
    // ... other properties
}
```

## Future Enhancements

1. **Voting Records**: Add tables for roll call votes and bill sponsorships
2. **Legislation**: Track bills and their sponsors
3. **Committee Assignments**: Congressional committee memberships
4. **Historical Data**: Archive previous Congress sessions
5. **Real-time Updates**: Incremental sync from Congress.gov and OpenFEC APIs

## Data Sources

- **Congressional Data**: Congress.gov API, Official Congressional Directory
- **FEC Data**: Federal Election Commission Bulk Data API
- **District Boundaries**: US Census Bureau TIGER/Line shapefiles (via GeoJSON)

## License and Attribution

Data sourced from official U.S. Government APIs:
- Federal Election Commission (fec.gov)
- Congress.gov
- US Census Bureau

Public domain under 17 U.S.C. § 105.
