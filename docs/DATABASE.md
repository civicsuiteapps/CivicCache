# CivicCache Database Schema

## Overview

The CivicCache SQLite database provides optimized access to Congressional and FEC campaign finance data for the CivicScope mobile applications (iOS and Android). The database is synced to both platforms as a pre-built asset.

**Current size**: ~268 MB (vacuumed)
**Tables**: 28 (plus 5 FTS virtual tables)
**Congress**: 119th (2025-2026)

## Design Principles

1. **Normalized structure** -- minimize duplication while maintaining query performance
2. **Full-text search** -- FTS5 tables for members, candidates, committees, and legislation
3. **Comprehensive indexing** -- strategic indexes on frequently queried columns
4. **Foreign key constraints** -- data integrity across relationships
5. **Mobile optimization** -- efficient for offline-first mobile apps
6. **Composite keys** -- FEC tables use `(candidate_id, election_cycle)` for multi-cycle data

## Table Summary

| Table | Rows | Description |
|-------|-----:|-------------|
| `states` | 56 | US states and territories |
| `congress_members` | 537 | 119th Congress members with full metadata |
| `legislation` | 13,574 | Bills and resolutions |
| `legislation_summaries` | 3,450 | Bill summaries |
| `legislation_sponsors` | 142,552 | Sponsors and cosponsors |
| `legislation_subjects` | 20,802 | Subject tags |
| `legislation_actions` | 0 | Legislative actions (on-demand) |
| `legislation_related` | 0 | Related legislation links |
| `congressional_committees` | 233 | Committees and subcommittees |
| `member_committee_assignments` | 3,894 | Member-committee assignments |
| `member_positions` | 6,712 | Member career positions |
| `member_fec_summary` | 1,013 | Financial summaries per member per cycle |
| `member_top_contributors` | 10,104 | Top 10 contributors per member per cycle |
| `candidate_top_contributors` | 7,432 | Top 10 PAC contributors per candidate per cycle |
| `committees` | 85,397 | FEC political committees and PACs |
| `candidates` | 129,954 | FEC candidates (all cycles) |
| `candidate_committee_linkages` | 47,011 | Candidate-committee relationships |
| `election_races` | 12,470 | Derived race matchups |
| `race_candidates` | 136,842 | Denormalized race-candidate join |
| `roll_call_votes` | 438 | House roll-call votes |
| `member_votes` | 188,516 | Individual member vote positions |
| `congressional_staff` | 17,444 | Staff from House SOD directory |
| `congressional_districts` | 444 | District boundaries (GeoJSON) |
| `district_demographics` | 436 | Census demographics per district |
| `zip_districts` | 42,033 | ZIP code to district mappings |
| `metadata` | 215 | Import tracking and versioning |

## Core Reference Tables

### states

```sql
CREATE TABLE states (
    code TEXT PRIMARY KEY,              -- Two-letter code (e.g., "TX")
    name TEXT NOT NULL UNIQUE,
    fips_code TEXT,
    is_territory INTEGER DEFAULT 0,
    region TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

### metadata

```sql
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

## Congress Members

### congress_members

Current members of the 119th Congress (537 members).

```sql
CREATE TABLE congress_members (
    id TEXT PRIMARY KEY,                -- Bioguide ID (e.g., "P000197")
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    full_name TEXT NOT NULL,
    nickname TEXT,
    middle_name TEXT,
    suffix TEXT,

    chamber TEXT NOT NULL,              -- 'House' or 'Senate'
    state_code TEXT,
    state_name TEXT NOT NULL,
    district TEXT,                      -- NULL for Senators

    current_party TEXT NOT NULL,        -- 'Democratic', 'Republican', 'Independent'

    office_address TEXT,
    phone TEXT,
    website_url TEXT,
    contact_form_url TEXT,

    twitter_handle TEXT,
    facebook_url TEXT,
    youtube_url TEXT,

    birth_year INTEGER,
    gender TEXT,
    birthday TEXT,

    term_start_date TEXT,
    term_end_date TEXT,

    image_url TEXT,
    image_blob BLOB,
    first_elected_year INTEGER,
    total_years_served INTEGER,
    total_terms_served INTEGER,
    current_leadership_role TEXT,
    sponsored_bills_count INTEGER,
    cosponsored_bills_count INTEGER,
    depiction_url TEXT,
    depiction_attribution TEXT,

    -- Cross-reference IDs
    wikipedia_id TEXT,
    govtrack_id TEXT,
    opensecrets_id TEXT,
    votesmart_id TEXT,
    fec_id TEXT,
    ballotpedia_id TEXT,
    cspan_id TEXT,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (state_code) REFERENCES states(code)
);
```

### congress_members_fts

```sql
CREATE VIRTUAL TABLE congress_members_fts USING fts5(
    member_id UNINDEXED, full_name, first_name, last_name, state_name
);
```

## Legislation

### legislation

```sql
CREATE TABLE legislation (
    id TEXT PRIMARY KEY,                -- e.g., "119-hr-1234"
    congress INTEGER NOT NULL,
    bill_type TEXT NOT NULL,
    bill_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    origin_chamber TEXT,
    origin_chamber_code TEXT,
    introduced_date TEXT,
    latest_action_date TEXT,
    latest_action_text TEXT,
    policy_area TEXT,
    is_law INTEGER DEFAULT 0,
    law_number TEXT,
    law_type TEXT,
    status TEXT,
    actions_count INTEGER DEFAULT 0,
    amendments_count INTEGER DEFAULT 0,
    cosponsors_count INTEGER DEFAULT 0,
    subjects_count INTEGER DEFAULT 0,
    summaries_count INTEGER DEFAULT 0,
    legislation_url TEXT,
    session INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(congress, bill_type, bill_number)
);
```

### legislation_sponsors

Stores both primary sponsors (`is_primary_sponsor = 1`) and cosponsors.

```sql
CREATE TABLE legislation_sponsors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    legislation_id TEXT NOT NULL,
    bioguide_id TEXT,
    full_name TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    middle_name TEXT,
    party TEXT,
    state TEXT,
    district INTEGER,
    is_original_cosponsor INTEGER DEFAULT 0,
    sponsorship_date TEXT,
    is_primary_sponsor INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (legislation_id) REFERENCES legislation(id),
    FOREIGN KEY (bioguide_id) REFERENCES congress_members(id),
    UNIQUE(legislation_id, bioguide_id)
);
```

### legislation_summaries, legislation_subjects, legislation_actions, legislation_related

Child tables of `legislation` for summaries, subject tags, actions, and related bills.

## Roll-Call Votes

### roll_call_votes

```sql
CREATE TABLE roll_call_votes (
    id TEXT PRIMARY KEY,
    congress INTEGER NOT NULL,
    chamber TEXT NOT NULL,
    session INTEGER NOT NULL,
    roll_number INTEGER NOT NULL,
    vote_date TEXT NOT NULL,
    vote_time TEXT,
    vote_question TEXT,
    vote_question_text TEXT,
    vote_type TEXT,
    vote_result TEXT,
    vote_result_text TEXT,
    yea_count INTEGER DEFAULT 0,
    nay_count INTEGER DEFAULT 0,
    present_count INTEGER DEFAULT 0,
    not_voting_count INTEGER DEFAULT 0,
    required_majority TEXT,
    legislation_id TEXT,
    amendment_number TEXT,
    source_url TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(congress, chamber, session, roll_number),
    FOREIGN KEY (legislation_id) REFERENCES legislation(id)
);
```

### member_votes

```sql
CREATE TABLE member_votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    roll_call_id TEXT NOT NULL,
    bioguide_id TEXT NOT NULL,
    vote_position TEXT NOT NULL,
    member_name TEXT,
    party TEXT,
    state TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(roll_call_id, bioguide_id),
    FOREIGN KEY (roll_call_id) REFERENCES roll_call_votes(id),
    FOREIGN KEY (bioguide_id) REFERENCES congress_members(id)
);
```

## Congressional Committees & Staff

### congressional_committees

```sql
CREATE TABLE congressional_committees (
    id TEXT PRIMARY KEY,                -- e.g., "HSJU" for House Judiciary
    name TEXT NOT NULL,
    chamber TEXT NOT NULL,              -- 'House', 'Senate', or 'Joint'
    committee_type TEXT,
    parent_committee_id TEXT,
    jurisdiction TEXT,
    url TEXT,
    is_subcommittee INTEGER DEFAULT 0,
    congress INTEGER DEFAULT 119,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

### member_committee_assignments

```sql
CREATE TABLE member_committee_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id TEXT NOT NULL,
    committee_id TEXT NOT NULL,
    party_side TEXT,
    rank INTEGER,
    title TEXT,
    is_subcommittee INTEGER DEFAULT 0,
    congress INTEGER DEFAULT 119,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (member_id) REFERENCES congress_members(id),
    UNIQUE(member_id, committee_id, congress)
);
```

### congressional_staff

Imported from the House Statement of Disbursements (SOD).

```sql
CREATE TABLE congressional_staff (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT,
    last_name TEXT,
    full_name TEXT NOT NULL,
    suffix TEXT,
    title TEXT NOT NULL,
    title_normalized TEXT,
    office_type TEXT NOT NULL,          -- 'member', 'committee', 'institutional'
    chamber TEXT,
    member_bioguide_id TEXT,
    member_name TEXT,
    office_location TEXT,
    committee_code TEXT,
    committee_name TEXT,
    subcommittee_name TEXT,
    party_affiliation TEXT,
    institutional_office TEXT,
    congress_number INTEGER NOT NULL,
    source_document TEXT,
    source_section TEXT,
    effective_date TEXT,
    created_at TEXT,
    updated_at TEXT,
    is_current INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (member_bioguide_id) REFERENCES congress_members(id)
);
```

### member_positions

Career history and leadership positions.

```sql
CREATE TABLE member_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id TEXT NOT NULL,
    position_title TEXT NOT NULL,
    position_type TEXT,
    organization TEXT,
    district TEXT,
    state TEXT,
    start_year INTEGER,
    end_year INTEGER,
    is_current INTEGER DEFAULT 0,
    source TEXT,
    wikidata_position_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (member_id) REFERENCES congress_members(id)
);
```

## FEC Campaign Finance

### committees (FEC)

All active and historical FEC political committees and PACs.

```sql
CREATE TABLE committees (
    committee_id TEXT PRIMARY KEY,
    committee_name TEXT NOT NULL,
    committee_type TEXT,
    committee_type_full TEXT,
    committee_designation TEXT,
    committee_designation_full TEXT,
    party_code TEXT,
    party_full_name TEXT,
    total_receipts REAL,
    total_disbursements REAL,
    cash_on_hand REAL,
    coverage_end_date TEXT,
    connected_org_name TEXT,
    treasurer_name TEXT,
    street_1 TEXT, street_2 TEXT, city TEXT, state TEXT, zip TEXT,
    filing_frequency TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

### candidates

FEC candidate records with composite primary key.

```sql
CREATE TABLE candidates (
    candidate_id TEXT NOT NULL,           -- e.g., "H0CA12345"
    election_cycle INTEGER NOT NULL,      -- Even-year cycle
    candidate_name TEXT NOT NULL,         -- FEC format: "LAST, FIRST MIDDLE"
    office TEXT,                          -- 'H', 'S', 'P'
    office_state TEXT,
    office_district TEXT,
    party_code TEXT,
    incumbent_challenger_status TEXT,     -- 'I', 'C', 'O'
    candidate_status TEXT,               -- 'C', 'F', 'N', 'P'
    election_year INTEGER,
    total_receipts REAL,
    total_disbursements REAL,
    cash_on_hand REAL,
    total_individual_contributions REAL,
    candidate_contributions REAL,
    candidate_loans REAL,
    pac_contributions REAL,
    party_contributions REAL,
    debts_owed REAL,
    coverage_end_date TEXT,
    principal_committee_id TEXT,
    mailing_city TEXT, mailing_state TEXT, mailing_zip TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (candidate_id, election_cycle)
);
```

### election_races

Derived race matchups grouping candidates by office/state/district per cycle.

```sql
CREATE TABLE election_races (
    race_id TEXT PRIMARY KEY,             -- e.g., "2026-H-CA-12"
    election_cycle INTEGER NOT NULL,
    office TEXT NOT NULL,
    state TEXT NOT NULL,
    district TEXT,
    incumbent_bioguide_id TEXT,
    incumbent_fec_id TEXT,
    incumbent_party TEXT,
    total_candidates INTEGER DEFAULT 0,
    challenger_count INTEGER DEFAULT 0,
    open_seat INTEGER DEFAULT 0,
    rating TEXT,                          -- solid_d, lean_r, tossup, etc.
    rating_source TEXT,
    rating_date TEXT,
    FOREIGN KEY (incumbent_bioguide_id) REFERENCES congress_members(id)
);
```

### race_candidates

Denormalized join for fast app display without joining back to `candidates`.

```sql
CREATE TABLE race_candidates (
    race_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    election_cycle INTEGER NOT NULL,
    candidate_name TEXT,
    party_code TEXT,
    incumbent_challenger_status TEXT,
    total_receipts REAL,
    cash_on_hand REAL,
    total_individual_contributions REAL,
    principal_committee_id TEXT,
    image_url TEXT,
    image_blob BLOB,
    PRIMARY KEY (race_id, candidate_id),
    FOREIGN KEY (race_id) REFERENCES election_races(race_id)
);
```

#### Incumbent attribution and the retirements override

`election_races.incumbent_*` and `open_seat` are derived structurally by
`import_fec_candidates.py`: a race has an incumbent only when a sitting member
(`congress_members`, `term_end_date` not yet passed) has an FEC filing in the
race **for their own seat**. FEC's `ICI` code is not trusted (it marks a House
member's Senate bid 'I'), and members who resigned or died are ignored so a
vacated seat reads as open unless a successor has filed.

FEC data still cannot see a member who is retiring or running for a *state*
office -- the federal committee stays active and keeps filing (Tuberville's
Senate committee while he runs for Governor of Alabama). Those cases live in
the curated file **`data/retirements_2026.csv`**:

| column | meaning |
|---|---|
| `bioguide_id` | the member not seeking this seat |
| `fec_candidate_id` | their FEC id as it appears in the race (or their current `congress_members.fec_id`) |
| `race_id` | the seat they are leaving, e.g. `2026-S-AL` |
| `reason` | `retiring`, `running for Governor of Alabama`, `running for U.S. Senate`, ... |
| `source_url` | announcement coverage / press release |
| `announced_date` | YYYY-MM-DD |

On import, every FEC id belonging to that member (the CSV id plus
`congress_members.fec_id`) is **dropped from the race entirely**: no
`race_candidates` row, `open_seat = 1`, `incumbent_bioguide_id` /
`incumbent_fec_id` / `incumbent_party` NULL, and
`challenger_count = total_candidates` (both exclude the retiree). Dropping
rather than keeping a row with a "not running" status was chosen because the
apps resolve a member's *own* race through their fec_id in `race_candidates`
-- a lingering row would render the nominees as the retiree's "opponents" and
list him as a candidate on the race screen. With no row, `isIncumbent` (`== 'I'`)
never sees him and the member page simply has no 2026 race, which is the
truth. The `candidates` table (raw FEC record) is untouched.

Gates: `update.py --check` (`retirements_applied`) and the digest rule
`retirements` fail when a listed member is still a race's incumbent or still
sits in that race's `race_candidates`.

### primary_winners

Curated party nominees, one row per `(race_id, party)`; maintained with
`scripts/log_primary_loss.py` (`--race <id> --winner-id <FEC id> --date
<primary date> [--decided-by primary|runoff|top-two|top-four|rcv|convention]`).
`party` is copied from the nominee's `race_candidates.party_code` (Minnesota
Democrats are therefore `DFL`). Kept in a separate table because the FEC
import drops and rebuilds `race_candidates`; the import then validates that
every winner still joins (`validate_primary_winners`, health check
`primary_winner_integrity`). Apps fall back to the full field for any race
with no rows, so a top-two race that sent two same-party candidates to
November (nine California districts in 2026) is intentionally left
uncurated rather than misrepresented as having one nominee.

```sql
CREATE TABLE primary_winners (
    race_id TEXT NOT NULL,
    party TEXT NOT NULL,
    candidate_id TEXT NOT NULL,           -- must exist in race_candidates for race_id
    election_cycle INTEGER NOT NULL,
    won_date TEXT,                        -- primary or runoff date
    decided_by TEXT,                      -- primary | runoff | top-two | top-four | rcv | convention
    note TEXT,                            -- source URL
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (race_id, party)
);
```

### primary_calendar

Every state primary and runoff for the cycle (pipeline-only; drives the digest
rules `primaries` and `uncurated_primaries`). `election_type` is `primary`,
`runoff` or `special-primary`. A `note` beginning `office=H` or `office=S`
scopes the date to one chamber -- Louisiana's 2026 Senate primary was May 16 /
June 27 while its House races use a jungle primary on November 3 (runoff
December 12); Alabama re-ran CDs 1, 2, 6 and 7 on August 11 after redistricting.

```sql
CREATE TABLE primary_calendar (
    state_code    TEXT NOT NULL,
    election_date TEXT NOT NULL,              -- YYYY-MM-DD
    election_type TEXT NOT NULL DEFAULT 'primary',
    note          TEXT,
    PRIMARY KEY (state_code, election_date)
);
```

### candidate_committee_linkages

```sql
CREATE TABLE candidate_committee_linkages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    committee_id TEXT NOT NULL,
    linkage_type TEXT,
    election_year INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (committee_id) REFERENCES committees(committee_id),
    UNIQUE(candidate_id, committee_id, election_year)
);
```

### member_fec_summary

Pre-computed financial summary per sitting member per cycle.

```sql
CREATE TABLE member_fec_summary (
    bioguide_id TEXT NOT NULL,
    election_cycle INTEGER NOT NULL,
    fec_candidate_id TEXT NOT NULL,
    total_receipts REAL DEFAULT 0,
    total_disbursements REAL DEFAULT 0,
    cash_on_hand REAL DEFAULT 0,
    debts_owed REAL DEFAULT 0,
    total_individual_contributions REAL DEFAULT 0,
    total_pac_contributions REAL DEFAULT 0,
    total_party_contributions REAL DEFAULT 0,
    candidate_self_funding REAL DEFAULT 0,
    pct_individual REAL DEFAULT 0,
    pct_pac REAL DEFAULT 0,
    pct_self_funded REAL DEFAULT 0,
    top_contributor_name TEXT,
    top_contributor_type TEXT,
    top_contributor_amount REAL DEFAULT 0,
    coverage_end_date TEXT,
    import_date TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (bioguide_id, election_cycle),
    FOREIGN KEY (bioguide_id) REFERENCES congress_members(id)
);
```

### member_top_contributors

Top 10 contributors per sitting member per cycle (PACs and employer-aggregated individuals).

```sql
CREATE TABLE member_top_contributors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bioguide_id TEXT NOT NULL,
    election_cycle INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    contributor_name TEXT NOT NULL,
    contributor_type TEXT NOT NULL,      -- 'employer', 'pac'
    total_amount REAL NOT NULL,
    transaction_count INTEGER DEFAULT 0,
    UNIQUE(bioguide_id, election_cycle, rank),
    FOREIGN KEY (bioguide_id) REFERENCES congress_members(id)
);
```

### candidate_top_contributors

Top 10 PAC/committee contributors per FEC candidate per cycle. Covers challengers and all candidates, not just sitting members.

```sql
CREATE TABLE candidate_top_contributors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    election_cycle INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    committee_id TEXT NOT NULL,
    committee_name TEXT NOT NULL,
    committee_type TEXT,
    total_amount REAL NOT NULL,
    transaction_count INTEGER DEFAULT 0,
    UNIQUE(candidate_id, election_cycle, rank),
    FOREIGN KEY (candidate_id, election_cycle)
        REFERENCES candidates(candidate_id, election_cycle)
);
```

## Geographic Tables

### congressional_districts

All 444 districts with full GeoJSON boundaries and bounding boxes.

```sql
CREATE TABLE congressional_districts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state_fips TEXT NOT NULL,
    state_code TEXT,
    district_code TEXT NOT NULL,
    district_number INTEGER,
    name TEXT NOT NULL,
    geoid TEXT UNIQUE NOT NULL,
    center_latitude REAL,
    center_longitude REAL,
    population INTEGER,
    geojson TEXT NOT NULL,              -- Standard GeoJSON Feature
    bbox TEXT NOT NULL,                 -- Bounding box for spatial queries
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (state_code) REFERENCES states(code)
);
```

### district_demographics

Census ACS 5-Year Estimates per district.

```sql
CREATE TABLE district_demographics (
    geoid TEXT PRIMARY KEY,
    population INTEGER,
    median_household_income REAL,
    poverty_rate REAL,
    unemployment_rate REAL,
    median_age REAL,
    college_educated_pct REAL,
    high_school_grad_pct REAL,
    white_pct REAL,
    black_pct REAL,
    hispanic_pct REAL,
    asian_pct REAL,
    median_home_value REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (geoid) REFERENCES congressional_districts(geoid)
);
```

### zip_districts

ZIP code to congressional district mappings.

```sql
CREATE TABLE zip_districts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zip_code TEXT NOT NULL,
    state_fips TEXT NOT NULL,
    state_abbr TEXT NOT NULL,
    district INTEGER NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(zip_code, state_abbr, district)
);
```

## Full-Text Search Tables

```sql
CREATE VIRTUAL TABLE congress_members_fts USING fts5(
    member_id UNINDEXED, full_name, first_name, last_name, state_name
);

CREATE VIRTUAL TABLE candidates_fts USING fts5(
    candidate_id UNINDEXED, election_cycle UNINDEXED,
    candidate_name, office_state, party_code
);

CREATE VIRTUAL TABLE committees_fts USING fts5(
    committee_id UNINDEXED, committee_name, connected_org_name, party_full_name
);

CREATE VIRTUAL TABLE legislation_fts USING fts5(
    id, title, content='legislation', content_rowid='rowid'
);

CREATE VIRTUAL TABLE legislation_summaries_fts USING fts5(
    legislation_id, summary_text, content='legislation_summaries', content_rowid='id'
);
```

## Query Examples

### Find a representative by district

```sql
SELECT * FROM congress_members
WHERE state_code = 'TX' AND district = '32' AND chamber = 'House';
```

### Full-text search for members

```sql
SELECT m.* FROM congress_members m
JOIN congress_members_fts fts ON m.id = fts.member_id
WHERE congress_members_fts MATCH 'pelosi';
```

### Top PAC contributors for a candidate

```sql
SELECT rank, committee_name, total_amount, transaction_count
FROM candidate_top_contributors
WHERE candidate_id = 'S2TX00312' AND election_cycle = 2026
ORDER BY rank;
```

### Race matchup with candidates

```sql
SELECT rc.candidate_name, rc.party_code, rc.incumbent_challenger_status,
       rc.total_receipts, rc.cash_on_hand
FROM race_candidates rc
WHERE rc.race_id = '2026-S-TX'
ORDER BY rc.total_receipts DESC;
```

### Member voting record

```sql
SELECT rcv.vote_date, rcv.vote_question, mv.vote_position
FROM member_votes mv
JOIN roll_call_votes rcv ON mv.roll_call_id = rcv.id
WHERE mv.bioguide_id = 'P000197'
ORDER BY rcv.vote_date DESC LIMIT 20;
```

### District demographics

```sql
SELECT cd.name, cd.state_code, cd.district_number,
       dd.population, dd.median_household_income, dd.poverty_rate
FROM congressional_districts cd
JOIN district_demographics dd ON cd.geoid = dd.geoid
WHERE cd.state_code = 'CA'
ORDER BY cd.district_number;
```

## Data Sources

| Source | Tables | Update Frequency |
|--------|--------|-----------------|
| Congress.gov API | legislation, votes, members, contact info | Daily / Weekly |
| GitHub `unitedstates/congress-legislators` | congress_members, committees | Weekly |
| FEC Bulk Data | candidates, committees, linkages, races | Monthly |
| FEC Committee Contributions | member_top_contributors, candidate_top_contributors | Monthly |
| OpenFEC API | candidates (supplemental) | Weekly |
| Census Bureau API | district_demographics | Annually |
| Census TIGER/Line | congressional_districts, zip_districts | Per redistricting |
| House SOD | congressional_staff | Monthly |
| Wikidata SPARQL | race_candidates (photos) | Monthly |

## License

Data sourced from official U.S. Government APIs. Public domain under 17 U.S.C. 105.
