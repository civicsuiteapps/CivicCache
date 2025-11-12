# CivicCache

A comprehensive data caching and processing system for Congressional and campaign finance data, designed to support the CivicScope mobile application.

## Overview

CivicCache extracts, processes, and maintains a SQLite database of:
- Congressional members (119th Congress) with full metadata
  - Contact information, portraits, service history
  - Leadership roles, legislative activity counts
  - Years in Congress, bills sponsored/cosponsored
- Legislation (119th Congress - 10,798 bills)
  - Full bill text summaries
  - Sponsorship and subject tags
- Congressional district boundaries (GeoJSON)
- Federal Election Commission (FEC) campaign finance data (1979-2026)
- State and territory reference data

This project serves as the data backend for CivicScope, similar to how ExecutiveCache serves ExecutiveScope.

## Data Sources

**Primary Source**: CivicScope iOS application data files
- Congressional member data (JSON)
- District boundary data (GeoJSON - 276 MB)
- FEC bulk data (CSV/pipe-delimited text)
  - All Candidates (1979-2026)
  - Candidate Master files
  - Committee Master files
  - PAC summaries
  - House/Senate campaign data
  - Committee contributions
  - Operating expenditures
  - Inter-committee transactions

**Active API Sources**:
- Congress.gov API (legislation, member metadata, contact information)
  - Bills and resolutions (119th Congress)
  - Member service history and portraits
  - Leadership roles and legislative activity counts

**Additional Potential Sources**:
- OpenFEC API (current campaign finance)
- GovTrack.us API

## Project Structure

```
CivicCache/
├── README.md
├── requirements.txt
├── .env.example
├── scripts/
│   ├── extract_congress_data.py
│   ├── extract_fec_data.py
│   ├── build_database.py
│   └── validate_database.py
├── data/
│   ├── raw/              # Extracted data from CivicScope
│   ├── processed/        # Cleaned and normalized data
│   └── civic_cache.db    # SQLite database
└── docs/
    └── DATABASE.md       # Database schema documentation
```

## Database Schema (Planned)

### Core Tables
- **congress_members** - Current Congress member directory (537 members)
- **congressional_districts** - District boundaries and geographic data
- **states** - US states and territories reference data
- **candidates** - FEC candidate records (1979-2026)
- **committees** - Political committees and PACs
- **contributions** - Campaign contributions
- **expenditures** - Campaign spending records
- **candidate_committee_links** - Relationships between candidates and committees

### Features
- Full-text search (FTS5) for members, candidates, and committees
- Comprehensive indexes for fast queries
- Foreign key constraints for data integrity
- Optimized for iOS/Android mobile integration

## Installation

```bash
# Clone the repository
git clone https://github.com/cczaphod/CivicCache.git
cd CivicCache

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Usage

```bash
# Extract data from CivicScope
python scripts/extract_congress_data.py
python scripts/extract_fec_data.py

# Build SQLite database
python scripts/build_database.py

# Validate database
python scripts/validate_database.py
```

## Data Volume

**Source Data**: ~2.2 GB from CivicScope
- Congressional data: ~277 MB (mostly district GeoJSON)
- FEC data: ~1.9 GB (1979-2026 campaign finance)

**Target Database Size**: TBD (estimated 100-500 MB after processing)

## License

TBD

## Related Projects

- **CivicScope**: iOS/Android mobile apps for civic engagement
- **ExecutiveCache**: Presidential documents data cache
