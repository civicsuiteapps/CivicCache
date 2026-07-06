# CivicCache Project Status

**Last Updated**: July 6, 2026

## 📅 Recent Changes

### July 6, 2026
- Refactored pipeline runner to use shared cachecore framework (~/GIT/cachecore)
- Extracted pipeline execution logic from update.py into generic cachecore.runner module
  - Moved stage resolution, dependency resolution, parallel execution to cachecore
  - CivicCache now maintains only stage definitions and snapshot functions
- Enhanced run_pipeline.sh with single-run locking to prevent concurrent writes to SQLite
  - Uses mkdir atomic lock with pid tracking to detect stale locks
  - Serializes pipeline execution (--workers 1) to avoid single-writer contention
  - Removed macOS notification on failure (now handled by digest.py)
- Created new digest.py script for material-change reporting
  - Writes status.json for CacheStatus menu-bar app
  - Sends ntfy/Notification Center alerts for pipeline failures
  - Captures run metadata from last_run.json
- Created reconcile_room_schema.py utility for room schema verification

### June 2, 2026
- Enhanced build_database.py: Improved incremental database build process with better error handling
- Significantly expanded update_redistricting.py: Added comprehensive redistricting source management with support for multiple TIGER/Line and EOGP sources
  - Implemented detailed redistricting metadata tracking (source_type, display_name, cycle)
  - Added geographic boundary extraction and simplification for efficient storage
  - Extended race_candidates schema with redistricting reference fields
  - Created log_primary_loss.py utility for analyzing primary election results and race status
  - Refined data import pipeline with improved validation and conflict resolution

### April 3, 2026 (Evening)
- Enhanced import_fec_committees.py: Added filtering for terminated (T) and administratively terminated (A) committees to prevent bloating the table with inactive entities
- Created new import_committee_financials.py script: Populates committees.total_receipts, committees.disbursements, and committees.cash_on_hand fields using OpenFEC API
- Updated update.py pipeline: Added committee_financials stage (monthly tier, depends on fec_committees and fec_summary)

### April 3, 2026
- Enhanced import_campaign_sites.py: Added rival candidate detection to prevent misidentified campaign websites (e.g. wrong Chen when probing for candidate with same surname)
- Improved photo validation: Added last-name matching in photo URLs to prevent Ballotpedia redirects from saving wrong candidate photos
- Enhanced import_congressional_staff.py: Added filtering for "(OTHER COMPENSATION)" pay lines to avoid duplicate staffer records
- Improved duplicate detection: Added pre-check for existing records before insert and explicit duplicate skip tracking
- Fixed import_fec_committees.py: Added proper foreign key constraint handling by deleting linkages before committees
- Fixed import_member_positions.py: Updated duplicate detection to check position_type instead of position_title for broader cross-source deduplication
- Enhanced import_openfec_candidates.py: Added exponential backoff retry logic with rate-limit (429) handling for OpenFEC API resilience
- Optimized database connection: Added PRAGMA busy_timeout = 30000 for better concurrent access

### March 8, 2026
- Extended congress members schema with lis_id field for Legislative Information System identifiers
- Updated import_congress_legislators.py to extract and store LIS identifiers from member data
- Enhanced import_votes.py to populate additional vote metadata fields: questionText, resultText, requiredMajority, amendmentNumber
- Improved vote imports by mapping Congress API response fields to database columns
- Added import_senate_votes.py script for importing Senate roll-call votes from Senate.gov XML (daily tier)
- Updated update.py pipeline with senate-votes stage for daily Senate vote imports

### March 4, 2026 (Evening)
- Extended race_candidates schema with Google Civic API candidate contact information fields (campaign_url, campaign_phone, campaign_email, photo_url, social_facebook, social_twitter, social_youtube)
- Added GOOGLE_CIVIC_API_KEY to config.py environment variable configuration
- Created new import_google_civic.py script for Google Civic API integration (2026 cycle, monthly tier)
- Added google_civic to update pipeline with proper dependencies on fec_candidates
- Documented candidate data source enhancement plan (CANDIDATE_DATA_SOURCES.md)

### March 4, 2026
- Added polygon simplification (Douglas-Peucker algorithm) to import_district_boundaries.py for optimized GeoJSON storage
- Implemented coordinate rounding (5 decimals = ~1.1m precision) reducing boundary point count by significant margin
- Added pyshp and pyproj to requirements.txt for shapefile and coordinate transformation support
- Updated download_fec_bulk.py: Added MIN_ELECTION_CYCLE = 2024 to skip historical data and reduce database size
- Updated import_fec_candidates.py: Added MIN_ELECTION_CYCLE filtering for cleaner current-election-only data
- Created new import_redistricting.py script for future redistricting data support
- Improved memory efficiency: Set recursion limit (50000) for processing districts with detailed boundaries
- Enhanced database performance: Disabled foreign key checks during district import for faster bulk loading

### March 2, 2026
- Added candidate deduplication phase (Phase 2.5) to import_fec_candidates.py
- Implements merging of duplicate candidates with same name/race/cycle under multiple FEC IDs
- Canonical candidate selection based on highest total_receipts with tie-breaking by coverage_end_date and candidate_id
- Financial data consolidation: Sums for flow columns, max for cash_on_hand, latest coverage_end_date
- Integrated deduplicate_candidates() function into main FEC import pipeline

### March 1, 2026
- Reorganized documentation: Moved legacy N8N workflow files, social media implementation, and other deprecated plans to docs/archive/
- Renamed candidate photo enrichment plan: Standardized naming convention to CANDIDATE_PHOTO_ENRICHMENT.md
- Updated README.md with comprehensive overview: Added data pipeline diagram, coverage statistics, and current implementation status
- Consolidated archive structure: Centralized all deprecated documentation and workflow templates in docs/archive/

### February 25, 2026
- Added launchd plist files for macOS scheduled execution (daily, weekly, monthly)
- Implemented candidate_top_contributors table for tracking top PAC/committee donors to candidates
- Enhanced FEC bulk download pipeline: Added automatic zip extraction with smart filename mapping
- Updated import_candidate_contributors.py: New script to populate candidate donor information
- Improved district boundaries import: Added bounding box calculation and storage (bbox JSON)
- Refined member image import paths: Updated search locations for congress members JSON file
- Enhanced database connection: Added 30-second busy timeout for better concurrency handling
- Updated pipeline configuration: Added candidate_contributors to monthly tier with proper dependencies

### February 24, 2026
- Archived legacy and incremental update scripts into scripts/archive/
- Streamlined CI/CD workflows: Removed daily/monthly/weekly update workflows, focusing on full-rebuild workflow
- Enhanced build_database.py: Added --fresh and --schema-only flags for flexible schema/data management
- Updated full-rebuild.yml: Added health check step and optimized schema building for better validation
- Created automation infrastructure: Added install_launchd.sh and run_pipeline.sh for local scheduled pipeline execution
- Added utility functions: Created scripts/utils.py for shared pipeline functionality
- Added documentation: Created candidate-photo-enrichment-plan.md and pipeline-automation-playbook.md

### February 21, 2026 (Evening Update)
- Optimized FEC bulk download pipeline: Added --force flag to refresh stale data and delete existing files before downloading
- Restructured update tiers: Moved FEC-related imports from "full" to "monthly" tier for better scheduling
- Added new fec_bulk_download step: Downloads current-cycle FEC data files (2026) as dependency for committee/candidate imports
- Enhanced candidate imports: Limited FEC candidates to current election cycle only (--current-only flag)
- Updated CI/CD workflows: Added OPENFEC_API_KEY environment variable to GitHub Actions monthly and weekly update workflows
- Optimized weekly updates: Moved openfec_candidates import to weekly tier for more frequent OpenFEC API sync

### February 21, 2026 (Morning)
- Added candidate photos support: New import_candidate_photos.py script to fetch candidate images from Wikidata
- Extended race_candidates schema: Added principal_committee_id, image_url, and image_blob columns
- Updated FEC candidate import: Now extracts and stores principal_committee_id from candidates table
- Integrated candidate photos into update pipeline

### February 18, 2026
- Fixed FEC candidate import script: Changed PRAGMA foreign_keys from ON to OFF to allow flexible data import during initial population

## ✅ Completed

### Infrastructure
- [x] Git repository initialized and connected to GitHub
- [x] Project structure following ExecutiveCache patterns
- [x] Python scripts for data extraction
- [x] SQLite database schema designed
- [x] Comprehensive documentation

### Data Extraction
- [x] Congressional member data extracted (537 members)
- [x] FEC bulk data extracted (1.97 GB, 208 files)
- [x] State reference data extracted (56 states/territories)
- [x] District metadata extracted

### Database
- [x] SQLite database created (4.6 MB)
- [x] 56 US states and territories imported
- [x] 537 members of 119th Congress imported
- [x] 14,532 FEC candidates imported (sampled)
- [x] Full-text search (FTS5) enabled
- [x] 14 indexes created for performance
- [x] Database validation completed

### Documentation
- [x] README.md - Project overview
- [x] DATABASE.md - Complete schema documentation
- [x] ADDITIONAL_DATA_SOURCES.md - Future enhancements guide
- [x] API_KEY_SETUP.md - API key registration guide
- [x] Fully documented .env configuration file

### API Integration
- [x] API key configuration system
- [x] API validation test script
- [x] Congress.gov API - ✅ Working
- [x] OpenFEC API - ✅ Working

## 🚧 In Progress

### API Keys
- [ ] Census Bureau API - Needs email activation
- [ ] Google Civic API - Needs Cloud Console configuration
- [ ] ProPublica API - Not yet registered
- [ ] OpenSecrets API - Requires donation

## 📋 Planned

### Phase 1: Enhanced Data Import
- [ ] Import Congressional voting records (Congress.gov API)
- [ ] Import current campaign finance data (OpenFEC API)
- [ ] Import district demographics (Census API)
- [ ] Create incremental update system

### Phase 2: Additional Tables
- [ ] bills - Legislation tracking
- [ ] votes - Roll call votes
- [ ] committee_assignments - Member committees
- [ ] contributions_by_industry - Industry influence
- [ ] lobbying_activity - Lobbying expenditures

### Phase 3: API Integration Scripts
- [ ] congress_sync.py - Fetch bills and votes
- [ ] openfec_sync.py - Update campaign finance
- [ ] census_sync.py - Update demographics
- [ ] Implement rate limiting and caching

### Phase 4: Mobile Optimization
- [ ] Create compressed database for iOS/Android
- [ ] Implement selective sync (user's district only)
- [ ] Generate API endpoint documentation for mobile apps
- [ ] Create sample SwiftUI/Kotlin integration code

## 📊 Current Statistics

### Database
- **Size**: 4.6 MB
- **Tables**: 7 (+ 3 FTS tables)
- **Indexes**: 14
- **States**: 56
- **Congress Members**: 537
- **Candidates**: 14,532

### Data Sources
- **CivicScope Raw Data**: 2.2 GB extracted
- **FEC Files**: 208 files, 1.97 GB
- **Congressional Data**: 277 MB (mostly GeoJSON)

### API Status
| API | Status | Rate Limit | Notes |
|-----|--------|------------|-------|
| Congress.gov | ✅ Working | 5,000/hour | 10,660 bills accessible |
| OpenFEC | ✅ Working | 1,000/hour | 52,649 candidates total |
| Census Bureau | ⚠️ Pending | Unlimited | Awaiting activation |
| Google Civic | ⚠️ Blocked | 25,000/day | Needs config |
| ProPublica | ⬜ Not setup | 5,000/day | Optional |
| OpenSecrets | ⬜ Not setup | Varies | Requires donation |

## 🎯 Next Steps

### Immediate (This Week)
1. **Activate Census API Key**
   - Check email for activation link
   - Click to activate
   - Rerun test: `python3 scripts/test_apis.py`

2. **Fix Google Civic API**
   - Visit: https://console.cloud.google.com/apis/credentials
   - Configure API restrictions
   - Remove iOS client restriction

3. **Register for ProPublica API**
   - Visit: https://www.propublica.org/datastore/api/propublica-congress-api
   - Request key for civic engagement project
   - Add to .env when received

### Short Term (This Month)
4. **Create API Integration Scripts**
   - Build on ExecutiveCache patterns
   - Implement incremental sync
   - Add rate limiting and caching

5. **Enhance Database Schema**
   - Add bills table for legislation
   - Add votes table for roll calls
   - Add committee_assignments table

6. **Test with Mobile App**
   - Integrate database with CivicScope iOS
   - Test query performance
   - Implement selective sync

### Long Term (Next Quarter)
7. **Full API Integration**
   - Complete data pipeline for all sources
   - Automated daily/weekly updates
   - Monitoring and error handling

8. **Advanced Features**
   - Real-time bill tracking
   - Voting record analysis
   - Contribution pattern detection
   - District comparison tools

9. **Production Deployment**
   - CI/CD pipeline
   - Automated testing
   - Database versioning
   - API rate limit monitoring

## 🐛 Known Issues

1. **Census API Key**: Requires email activation (in progress)
2. **Google Civic API**: Blocked due to API restrictions (needs config)
3. **GeoJSON Size**: 276 MB district boundaries too large for mobile
   - Solution: Use simplified boundaries or API endpoint
4. **FEC Data Volume**: 1.97 GB too large for complete mobile import
   - Solution: Use sampled data (currently: 14,532 of 52,649 candidates)

## 📝 Notes

### Design Decisions
- **Sampled FEC Data**: Importing 1,000 candidates per election cycle file instead of all data to keep mobile database size reasonable
- **No Full GeoJSON**: District boundaries stored as metadata only; full GeoJSON served via API when needed
- **FTS5 for Search**: Using SQLite's full-text search for sub-second queries on mobile
- **ExecutiveCache Pattern**: Following proven architecture from ExecutiveScope project

### Performance Targets
- Database size: < 100 MB for mobile
- Query response: < 100ms for typical queries
- Full-text search: < 50ms
- Initial load: < 2 seconds on mobile

### Security Notes
- All API keys stored in .env (git-ignored)
- No secrets committed to repository
- Consider secrets manager for production
- Rotate keys every 90 days

## 🔗 Quick Links

- **GitHub**: https://github.com/cczaphod/CivicCache
- **Related Project**: CivicScope iOS/Android
- **Pattern Source**: ExecutiveCache
- **Database**: `data/civic_cache.db`

## 📞 API Support Contacts

- Congress.gov: api@loc.gov
- OpenFEC: https://github.com/fecgov/openFEC/issues
- Census: https://www.census.gov/data/developers/about/contact-us.html
- ProPublica: https://www.propublica.org/contact

---

**Project Progress**: 70% complete (infrastructure and core data extraction done)
