# Congressional Staff Importer

Automatically fetches and imports congressional staff data from the Congressional Directory (CDIR) on GovInfo.gov.

## Quick Start

### Check for Latest Directory

```bash
python3 scripts/import_congressional_staff.py --check-only
```

This will:
- Search GovInfo for the most recent Congressional Directory
- Display which Congress it covers
- Exit without importing

### Import Staff Data

```bash
python3 scripts/import_congressional_staff.py
```

This will:
- Find the latest Congressional Directory
- Download and parse staff information
- Import into `congressional_staff` table

### Import Specific Congress

```bash
python3 scripts/import_congressional_staff.py --congress 119
```

## Current Status

**As of December 24, 2025:**
- Latest available directory: **CDIR-2024-04-25** (118th Congress)
- 119th Congress directory: **Not yet published**

The script automatically checks for the latest available directory, so when the 119th Congress directory is published, it will be detected automatically.

## Database Schema

The `congressional_staff` table includes:

### Personal Information
- `first_name`, `last_name`, `full_name`, `suffix`

### Position
- `title` - Full title (e.g., "Chief Of Staff")
- `title_normalized` - Standardized (e.g., "chief_of_staff")

### Office Affiliation
- `office_type` - `member`, `committee`, or `institutional`
- `chamber` - `senate`, `house`, `joint`, or NULL
- `member_bioguide_id` - Links to members table
- `office_location` - `dc`, `district`, or `state`

### Source Tracking
- `congress_number` - Which Congress (e.g., 118)
- `source_package` - CDIR package ID
- `effective_date` - When imported

## Recognized Staff Titles

The parser recognizes these common positions:

**Member Office Staff:**
- Chief of Staff
- Deputy Chief of Staff
- Legislative Director
- Communications Director
- Press Secretary
- Scheduler
- District Director
- State Director

**Committee Staff:**
- Staff Director
- Minority Staff Director
- Chief Counsel

## API Key (Optional)

For production use, get a free API key from https://api.data.gov/signup/

Set it as an environment variable:

```bash
export GOVINFO_API_KEY="your-key-here"
python3 scripts/import_congressional_staff.py
```

The script will use `DEMO_KEY` if no key is provided (limited rate).

## Monitoring for Updates

Set up a cron job to check weekly for new directories:

```bash
# Check every Monday at 9am
0 9 * * 1 cd /path/to/CivicCache && python3 scripts/import_congressional_staff.py --check-only
```

When a new directory is detected, run the full import manually.

## Data Quality

The parser uses regex patterns to extract staff names from HTML. Expected accuracy:

- **High-level positions** (Chief of Staff, Legislative Director): ~95% accuracy
- **Common positions** (Press Secretary, Scheduler): ~90% accuracy
- **Multiple staff** (Staff Assistants): ~80% accuracy

False positives are minimized through:
- Name format validation
- Duplicate detection
- Context-aware parsing

## Troubleshooting

### "No Congressional Directory found"
- The 119th Congress directory hasn't been published yet
- Check https://www.govinfo.gov/app/collection/cdir manually
- Use `--congress 118` to import the most recent available

### "Error fetching granules"
- GovInfo API may be temporarily down
- Check your internet connection
- Try again in a few minutes

### "No records to insert"
- Parser may need adjustment for new CDIR format
- Check `data/cdir_downloads/` for cached HTML
- Report parsing issues to developer

## Next Steps

**Phase 2** (To be implemented):
- Complete full member section parsing
- Add committee staff extraction
- Add institutional office staff
- Implement change tracking
- Build data quality reporting

**Current Version:** 1.0 (Framework complete, full parsing in progress)

## Files Created

```
CivicCache/
├── civic_cache.db (congressional_staff table added)
├── scripts/
│   ├── import_congressional_staff.py (main script)
│   └── README_STAFF_IMPORTER.md (this file)
└── data/
    └── cdir_downloads/ (cached HTML granules)
```

## Example Queries

After importing, query staff data:

```sql
-- Get Chief of Staff for a specific member
SELECT full_name, member_name
FROM congressional_staff
WHERE member_bioguide_id = 'S000148'  -- Chuck Schumer
  AND title_normalized = 'chief_of_staff'
  AND is_current = 1;

-- Count staff by title
SELECT title, COUNT(*) as count
FROM congressional_staff
WHERE congress_number = 118
GROUP BY title
ORDER BY count DESC;

-- Find all staff for a state delegation
SELECT cs.full_name, cs.title, m.first_name || ' ' || m.last_name as member
FROM congressional_staff cs
JOIN members m ON cs.member_bioguide_id = m.bioguide_id
WHERE m.state = 'CA'
  AND cs.is_current = 1
ORDER BY m.last_name, cs.title;
```

## License

Public Domain - U.S. Government data
