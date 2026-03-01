# n8n Incremental Workflows for All Three Cache Projects

**Date Created**: 2025-12-27
**Philosophy**: Delta updates only - databases must already exist

---

## Core Principle: Incremental-First

All three Cache projects follow the same pattern:

1. **Primary Workflow** (automated): Incremental deltas only (5-15 minutes)
2. **Full Rebuild** (manual only): Complete database generation (2-6 hours)

The automated n8n workflows should **NEVER** rebuild the database from scratch. They only pull deltas.

---

## JudicialCache Incremental Workflow

**Schedule**: Weekly (Sunday 2:00 AM)
**Runtime**: 5-15 minutes
**Prerequisite**: Database must exist (`judicial_cache.db`)

### Pipeline (8 stages)

```
1. BACKUP      → Backup existing database (2-5 sec)
2. OPINIONS    → fetch_scotus_sqlite.py --incremental (1-2 min, ~0-20 new opinions/week)
3. JUSTICES    → fetch_scotus_justices.py --incremental (30 sec, ~0-1 new justices/year)
4. PHOTOS      → import_justice_images.py --new-only (1-2 min, fetch photos for new justices)
5. PRESIDENTS  → update_appointing_presidents.py --new-only (10 sec, update for new justices)
6. SEGMENTS    → parse_opinion_segments.py --incremental (1-2 min, parse new opinions only)
7. VALIDATE    → Check database integrity (2-5 sec, min 100K opinions, 100 justices)
8. PACKAGE     → package_for_judicialscope.py (5-10 sec, VACUUM/ANALYZE/copy)
```

### Execute Command Nodes

```bash
# Stage 2: OPINIONS (Incremental)
cd /git/JudicialCache && python3 scripts/fetch_scotus_sqlite.py --incremental

# Stage 3: JUSTICES (Incremental - adds new justices if appointed)
cd /git/JudicialCache && python3 scripts/fetch_scotus_justices.py --incremental

# Stage 4: PHOTOS (New justices only)
cd /git/JudicialCache && python3 scripts/import_justice_images.py --new-only

# Stage 5: PRESIDENTS (New justices only)
cd /git/JudicialCache && python3 scripts/update_appointing_presidents.py --new-only

# Stage 6: SEGMENTS (Incremental)
cd /git/JudicialCache && python3 scripts/parse_opinion_segments.py --incremental

# Stage 7: VALIDATE
#!/bin/bash
DB=/data/judicial/judicial_cache.db
if [ ! -f "$DB" ]; then
  echo '{"valid": false, "error": "Database not found - run full rebuild manually"}'
  exit 1
fi
OPINIONS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM opinions;" || echo "0")
JUSTICES=$(sqlite3 "$DB" "SELECT COUNT(*) FROM justices;" || echo "0")
if [ "$OPINIONS" -lt 100000 ] || [ "$JUSTICES" -lt 100 ]; then
  echo '{"valid": false, "error": "Too few records"}'
  exit 1
fi
echo '{"valid": true, "opinions": '$OPINIONS', "justices": '$JUSTICES'}'

# Stage 8: PACKAGE
cd /git/JudicialCache && python3 scripts/package_for_judicialscope.py
```

### Full Rebuild (Manual Only - NOT in n8n workflow)

```bash
# Run these MANUALLY for initial setup or database corruption:
cd /Users/abrooks/GIT/JudicialCache
python3 scripts/init_database.py
python3 scripts/fetch_scotus_sqlite.py --all          # 2-3 hours
python3 scripts/fetch_scotus_justices.py --all
python3 scripts/merge_fjc_data.py
python3 scripts/import_justice_images.py
python3 scripts/import_wikipedia_images.py
python3 scripts/update_appointing_presidents.py
python3 scripts/fetch_landmark_opinions.py
python3 scripts/fetch_harvard_cap_opinions.py
python3 scripts/parse_opinion_segments.py --all       # 30-45 minutes
```

---

## ExecutiveCache Incremental Workflow

**Schedule**: Weekly (Sunday 3:00 AM)
**Runtime**: 10-20 minutes
**Prerequisite**: Database must exist (`executive_cache.db`)

### Pipeline (8 stages)

```
1. BACKUP          → Backup existing database (2-5 sec)
2. EXEC_ORDERS     → fetch_executive_orders.py --incremental --all (3-5 min, ~0-10 new orders/week)
3. PROCLAMATIONS   → fetch_proclamations.py --incremental --all (3-5 min, ~0-5 new/week)
4. MEMORANDA       → fetch_govinfo.py --incremental --all (2-3 min, ~0-2 new/week)
5. OFFICIALS       → fetch_executive_officials.py --incremental (1-2 min, new cabinet members/officials)
6. MERGE           → merge_historical_data.py --incremental (30 sec, update unified cache)
7. VALIDATE        → Check database integrity (2-5 sec, min 5900 total documents)
8. PACKAGE         → Create tar.gz archives (10-15 sec)
```

### Execute Command Nodes

```bash
# Stage 2: EXEC_ORDERS (Incremental)
cd /git/ExecutiveCache && python3 scripts/fetch_executive_orders.py --incremental --all

# Stage 3: PROCLAMATIONS (Incremental)
cd /git/ExecutiveCache && python3 scripts/fetch_proclamations.py --incremental --all

# Stage 4: MEMORANDA (Incremental)
cd /git/ExecutiveCache && python3 scripts/fetch_govinfo.py --incremental --all

# Stage 5: OFFICIALS (Incremental - new cabinet members, officials with photos/bios)
cd /git/ExecutiveCache && python3 scripts/fetch_executive_officials.py --incremental

# Stage 6: MERGE (Incremental - update unified cache with new data)
cd /git/ExecutiveCache && python3 scripts/merge_historical_data.py --incremental

# Stage 7: VALIDATE
#!/bin/bash
DB=/data/executive/executive_cache.db
if [ ! -f "$DB" ]; then
  echo '{"valid": false, "error": "Database not found - run full rebuild manually"}'
  exit 1
fi
TOTAL=$(sqlite3 "$DB" "SELECT COUNT(*) FROM documents;" || echo "0")
if [ "$TOTAL" -lt 5900 ]; then
  echo '{"valid": false, "error": "Too few documents: '$TOTAL' (expected >= 5900)"}'
  exit 1
fi
echo '{"valid": true, "total": '$TOTAL'}'

# Stage 8: PACKAGE
cd /git/ExecutiveCache/data && \
tar -czf executive_orders_seed.tar.gz executive_orders/batches/ && \
tar -czf proclamations_seed.tar.gz proclamations/batches/
```

### Full Rebuild (Manual Only - NOT in n8n workflow)

```bash
# Run these MANUALLY for initial setup:
cd /Users/abrooks/GIT/ExecutiveCache
python3 scripts/build_sqlite_database.py
python3 scripts/fetch_executive_orders.py --all          # 2-3 hours
python3 scripts/fetch_proclamations.py --all             # 1-2 hours
python3 scripts/fetch_govinfo.py --all                   # 30-45 min
python3 scripts/merge_historical_data.py --all
python3 scripts/generate_checksums.py
```

---

## CivicCache Incremental Workflow

**Schedule**: Daily (4:00 AM)
**Runtime**: 10-25 minutes
**Prerequisite**: Database must exist (`civic_cache.db`)

### Pipeline (9 stages)

```
1. BACKUP           → Backup existing database (2-5 sec)
2. LEGISLATORS      → import_congress_legislators.py (30 sec, check for new members)
3. CONTACT_INFO     → import_member_contact_info.py --new-only (30 sec, new members only)
4. MEMBER_IMAGES    → import_member_images.py --new-only (1-2 min, fetch portraits for new members)
5. MEMBER_METADATA  → import_member_positions.py --incremental (1-2 min, legislative activity updates)
6. LEGISLATION      → import_legislation.py --congress 119 --incremental (5-10 min, new bills only)
7. FEC_COMMITTEES   → import_fec_committees.py --incremental (2-3 min, new committees only)
8. VALIDATE         → Check database integrity (2-5 sec, min 535 members, 10K bills)
9. PACKAGE          → VACUUM and ANALYZE (5-10 sec)
```

### Execute Command Nodes

```bash
# Stage 2: LEGISLATORS (Adds new members if elected/appointed)
cd /git/CivicCache && python3 scripts/import_congress_legislators.py

# Stage 3: CONTACT_INFO (New members only - office, phone, URLs)
cd /git/CivicCache && python3 scripts/import_member_contact_info.py --new-only

# Stage 4: MEMBER_IMAGES (New members only - official portraits from Congress.gov)
cd /git/CivicCache && python3 scripts/import_member_images.py --new-only

# Stage 5: MEMBER_METADATA (Incremental - updates legislative activity for all members)
cd /git/CivicCache && python3 scripts/import_member_positions.py --incremental

# Stage 6: LEGISLATION (Incremental - new bills only)
cd /git/CivicCache && python3 scripts/import_legislation.py --congress 119 --incremental

# Stage 7: FEC_COMMITTEES (Incremental - new committees only)
cd /git/CivicCache && python3 scripts/import_fec_committees.py --incremental

# Stage 8: VALIDATE
#!/bin/bash
DB=/data/civic/civic_cache.db
if [ ! -f "$DB" ]; then
  echo '{"valid": false, "error": "Database not found - run full rebuild manually"}'
  exit 1
fi
MEMBERS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM congress_members;" || echo "0")
LEGISLATION=$(sqlite3 "$DB" "SELECT COUNT(*) FROM legislation;" || echo "0")
if [ "$MEMBERS" -lt 535 ] || [ "$LEGISLATION" -lt 10000 ]; then
  echo '{"valid": false, "error": "Too few records"}'
  exit 1
fi
echo '{"valid": true, "members": '$MEMBERS', "legislation": '$LEGISLATION'}'

# Stage 9: PACKAGE
cd /data/civic && \
cp civic_cache.db civic_cache_release.db && \
sqlite3 civic_cache_release.db 'VACUUM; ANALYZE;'
```

### Full Rebuild (Manual Only - NOT in n8n workflow)

```bash
# Run these MANUALLY for initial setup:
cd /Users/abrooks/GIT/CivicCache
python3 scripts/build_database.py
python3 scripts/import_congress_legislators.py
python3 scripts/import_member_positions.py --all          # 3-5 min
python3 scripts/import_member_contact_info.py
python3 scripts/import_member_images.py
python3 scripts/import_legislation.py --congress 119 --all  # 2-3 hours
python3 scripts/import_fec_committees.py --all            # 10-15 min
python3 scripts/import_congressional_staff.py
python3 scripts/import_district_boundaries.py
python3 scripts/import_district_demographics.py           # 15-20 min
python3 scripts/import_zip_districts.py
```

---

## Common n8n Workflow Structure

All three workflows share this structure:

### Nodes (18 total for each workflow)

1. **Schedule Trigger** - Cron schedule (daily/weekly)
2. **Check Last Run** (Function) - Read state file, check if enough time passed
3. **Should Run?** (IF) - Compare current time vs last run
4. **Test API** (HTTP Request) - Health check primary API
5. **API Available?** (IF) - Check HTTP 200 response
6. **Backup Database** (Execute Command) - Create timestamped backup
7-N. **Pipeline Stages** (Execute Command) - Incremental data fetches
N+1. **Validate Database** (Execute Command) - Check integrity
N+2. **Validation Passed?** (IF) - Check validation results
N+3. **Package** (Execute Command) - Optimize for iOS distribution
N+4. **Update State** (Function) - Write success timestamp to state file
N+5. **Success Alert** (NoOp/Discord/Email) - Notify success
N+6. **Rollback to Backup** (Execute Command) - Restore on failure
N+7. **Failure Alert** (NoOp/Discord/Email) - Notify failure
N+8. **API Down Alert** (NoOp/Discord/Email) - Notify API unavailable
N+9. **Skip Execution** (NoOp) - End when too recent

### State Management

All three use the same state file pattern:

```javascript
// Check Last Run (Function node)
const fs = require('fs');
const stateFile = '/data/{project}/pipeline_state.json';

let state = { last_successful_run: null };
if (fs.existsSync(stateFile)) {
  state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
}

const now = new Date();
const threshold = {HOURS} * 60 * 60 * 1000;  // 168h for weekly, 24h for daily
const shouldRun = !state.last_successful_run ||
  (now - new Date(state.last_successful_run)) > threshold;

return [{
  json: {
    should_run: shouldRun,
    last_run: state.last_successful_run,
    hours_since_last_run: state.last_successful_run
      ? Math.floor((now - new Date(state.last_successful_run)) / (1000 * 60 * 60))
      : null
  }
}];
```

```javascript
// Update State (Function node)
const fs = require('fs');
const stateFile = '/data/{project}/pipeline_state.json';

const state = {
  last_successful_run: new Date().toISOString(),
  version: '1.0',
  workflow_id: $workflow.id,
  execution_id: $execution.id
};

fs.writeFileSync(stateFile, JSON.stringify(state, null, 2));

return [{ json: { state_updated: true, timestamp: state.last_successful_run } }];
```

---

## Key Differences from Original Workflows

### ❌ **OLD** Approach (Full Rebuild Every Run)
- Runs `init_database.py` (drops and recreates schema)
- Fetches ALL data from scratch every time
- Takes 2-6 hours per execution
- Wastes API calls on unchanged data
- Not sustainable for daily/weekly automation

### ✅ **NEW** Approach (Incremental Delta Only)
- Assumes database already exists
- Fetches only new/changed data since last run
- Takes 5-20 minutes per execution
- Minimal API usage (only fetch what's new)
- Sustainable for automated schedules
- Full rebuild is **manual only** for initial setup or corruption recovery

---

## Implementation Checklist

For each Cache project:

1. ✅ **Update n8n workflow documentation** to emphasize incremental-first
2. ✅ **Create simplified workflow JSON** (18 nodes vs 24+ in old version)
3. ⚠️ **Ensure Python scripts support `--incremental` flag**
4. ⚠️ **Test incremental mode** on existing database
5. ⚠️ **Document full rebuild process** as manual-only procedure
6. ⚠️ **Add database existence check** in validation stage
7. ⚠️ **Configure state file paths** in Docker volumes

---

## Environment Variables

Each project needs these in Docker Compose `.env`:

```bash
# JudicialCache
COURTLISTENER_API_TOKEN=your_token_here
JUDICIAL_DB=/Users/abrooks/GIT/JudicialCache/data

# ExecutiveCache
GOVINFO_API_KEY=your_key_here
EXECUTIVE_DB=/Users/abrooks/GIT/ExecutiveCache/data

# CivicCache
CONGRESS_GOV_API_KEY=your_key_here
OPENFEC_API_KEY=your_key_here
CENSUS_API_KEY=your_key_here
CIVIC_DB=/Users/abrooks/GIT/CivicCache/data
```

---

## Monitoring and Alerts

### Success Notification Template (All Projects)

```
✅ {ProjectName} Incremental Update Success

- New Records: {{ $json.new_count || 0 }}
- Total Records: {{ $json.total_count }}
- Execution Time: {{ $workflow.duration }} seconds
- Mode: Incremental (delta only)
- Next Run: {schedule}
```

### Failure Notification Template (All Projects)

```
🚨 {ProjectName} Incremental Update Failed

- Error: {{ $json.error }}
- Stage: {{ $node["Validate Database"].json.stage }}
- Rollback: Database restored from backup
- Action: Check logs and investigate
- Note: If "Database not found" error, run full rebuild manually
```

---

## Performance Comparison

| Project | Full Rebuild (Manual) | Incremental (Automated) | Time Savings |
|---------|----------------------|------------------------|--------------|
| JudicialCache | 3-4 hours | 5-15 minutes | 95%+ |
| ExecutiveCache | 3-6 hours | 10-20 minutes | 95%+ |
| CivicCache | 3-5 hours | 10-25 minutes | 95%+ |

**Note**: Incremental times include automatic fetching of photos and metadata for new members/officials when they are added.

---

## Related Documentation

- Individual workflow setup guides in each project's `docs/` directory
- `/Users/abrooks/GIT/CivicCache/docs/N8N_AUTOMATION_PLAN.md` - Overall strategy
- `/Users/abrooks/GIT/CivicCache/docs/N8N_INSTALLATION_GUIDE.md` - n8n installation
