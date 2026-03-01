# Data Pipeline Automation Playbook

A reusable guide for refactoring Python data-import scripts into an automation-ready pipeline. Originally developed for CivicCache but applicable to any project with multiple ETL scripts targeting a SQLite database.

---

## Problem Pattern

You have a directory of 20-40 Python scripts that each:
- Fetch data from an API or bulk file
- Transform and insert into a shared SQLite database
- Were written independently, leading to duplicated utility code
- Have implicit ordering dependencies (script B needs table A populated first)
- Work locally but fail in CI because the schema builder doesn't create all tables

## Solution Architecture

```
scripts/
  config.py          # Centralized paths, API keys, congress/cycle numbers
  utils.py           # Shared utilities (api_request, create_connection, etc.)
  update.py          # Orchestrator with dependency graph, parallel execution
  build_database.py  # Complete schema creation (ALL tables)
  import_*.py        # Individual data import scripts
  run_pipeline.sh    # Wrapper for launchd/cron with notifications
  install_launchd.sh # macOS launchd setup helper
  archive/           # Obsolete/one-time scripts preserved for reference
launchd/
  com.project.daily.plist
  com.project.weekly.plist
  com.project.monthly.plist
```

---

## Phase 1: Archive Obsolete Scripts

Before any code changes, reduce noise by moving dead scripts to `scripts/archive/`.

**Candidates for archival:**
- Legacy orchestrators replaced by `update.py`
- One-time migration scripts (schema changes already applied)
- Verification scripts for completed migrations
- Extraction scripts replaced by download scripts
- Prototype/sample scripts superseded by production versions

**How to identify:** Grep for the filename across all workflows and the orchestrator. If zero references exist outside the script itself, it's safe to archive.

```bash
mkdir -p scripts/archive
git mv scripts/old_script.py scripts/archive/
```

---

## Phase 2: Fix Schema Builder

The most common CI failure: `build_database.py` creates a subset of tables, but import scripts assume all tables exist. Foreign key references to missing tables cause silent failures or crashes.

**Audit process:**
```bash
# List all tables in the live (working) database
sqlite3 data/your_db.db ".tables"

# List tables created by build_database.py
grep "CREATE TABLE" scripts/build_database.py

# The difference = missing tables to add
```

**Schema builder requirements:**
1. Use `CREATE TABLE IF NOT EXISTS` everywhere (idempotent)
2. Create tables in FK-dependency order (parent tables first)
3. Add `--schema-only` flag that skips seed data import
4. Add `--fresh` flag for destructive rebuild (don't delete DB by default)
5. Import DB path from centralized config, don't hardcode
6. Include ALL tables, indexes, and FTS virtual tables

**Extract schemas from live DB:**
```bash
sqlite3 data/your_db.db ".schema table_name"
```

---

## Phase 3: Extract Shared Utilities

**Common duplicated functions and their unified signatures:**

### `api_request(url, retries=3, delay=0.5, timeout=30)`
- urllib.request-based with User-Agent header
- Exponential backoff on HTTP 429 (rate limited)
- Returns None on 404 (not found)
- Configurable `delay` between requests (scripts use different rates)
- Returns `Optional[Dict]`

### `create_connection(db_path=None, foreign_keys=True)`
- Defaults to centralized DB_PATH from config
- Raises FileNotFoundError if DB missing
- Configurable foreign key enforcement (some FEC imports disable it)

### `download_image(url, timeout=15, min_size=100)`
- Rejects responses smaller than min_size bytes
- Returns `Optional[bytes]`

### `download_yaml(url, item_name="entries")`
- Downloads and parses YAML from GitHub raw URLs
- Handles both list and dict return types

**Refactoring pattern for each import script:**
```python
# Before (duplicated in each script):
def create_connection() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found at {DB_PATH}")
    conn = sqlite3.Connection(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def api_request(url: str, retries: int = 3) -> Optional[Dict]:
    # 30 lines of retry logic...

# After:
from utils import create_connection, api_request
```

---

## Phase 4: Add Health Check

Add `--check` command to the orchestrator that validates without running imports:

- Database exists and is readable
- All expected tables exist
- Key tables have minimum row counts
- Data freshness via metadata timestamps
- `PRAGMA foreign_key_check` for referential integrity
- Supports `--json` output for automation parsing

---

## Phase 5: Launchd Automation (macOS)

### Wrapper Script (`run_pipeline.sh`)

```bash
#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_ROOT/data/logs"
TIER="${1:-daily}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
LOG_FILE="$LOG_DIR/${TIER}-${TIMESTAMP}.log"

mkdir -p "$LOG_DIR"

# Load environment
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a; source "$PROJECT_ROOT/.env"; set +a
fi

PYTHON="${PYTHON:-python3}"

echo "=== Pipeline $TIER ===" | tee "$LOG_FILE"
echo "Started: $(date)" | tee -a "$LOG_FILE"

set +e
$PYTHON "$SCRIPT_DIR/update.py" "$TIER" --json 2>&1 | tee -a "$LOG_FILE"
EXIT_CODE=${PIPESTATUS[0]}
set -e

echo "Finished: $(date), exit: $EXIT_CODE" | tee -a "$LOG_FILE"

if [ $EXIT_CODE -ne 0 ]; then
    osascript -e "display notification \"$TIER pipeline failed (exit $EXIT_CODE)\" with title \"Pipeline\" sound name \"Basso\""
fi

# Prune logs older than 30 days
find "$LOG_DIR" -name "*.log" -mtime +30 -delete 2>/dev/null || true
exit $EXIT_CODE
```

### Launchd Plist Template

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.yourproject.daily</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/scripts/run_pipeline.sh</string>
        <string>daily</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>4</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/path/to/data/logs/launchd-daily-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/path/to/data/logs/launchd-daily-stderr.log</string>
    <key>WorkingDirectory</key>
    <string>/path/to/project</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
```

### Installation Helper

```bash
#!/bin/bash
PLIST_DIR="$(cd "$(dirname "$0")/../launchd" && pwd)"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
for plist in "$PLIST_DIR"/com.*.plist; do
    name=$(basename "$plist")
    ln -sf "$plist" "$LAUNCH_DIR/$name"
    launchctl unload "$LAUNCH_DIR/$name" 2>/dev/null || true
    launchctl load "$LAUNCH_DIR/$name"
    echo "Loaded: $name"
done
```

### Launchd Management Commands

```bash
# Load/start
launchctl load ~/Library/LaunchAgents/com.yourproject.daily.plist

# Unload/stop
launchctl unload ~/Library/LaunchAgents/com.yourproject.daily.plist

# Trigger manually (test)
launchctl start com.yourproject.daily

# Check status
launchctl list | grep yourproject

# View logs
tail -f /path/to/data/logs/launchd-daily-stdout.log
```

---

## Phase 6: Simplify CI

For a solo-developer project where local automation is primary:

**Remove** scheduled CI workflows (daily/weekly/monthly) -- replaced by launchd.

**Keep:**
- Manual-trigger full-rebuild workflow (for creating clean release artifacts)
- Test/lint workflow (for PR validation)

**Fix the full-rebuild workflow** to use `build_database.py --schema-only` so it works from scratch without seed data files.

---

## Automation Platform Decision Matrix

| Criteria | GitHub Actions | Local launchd | Cloudflare Workers | n8n |
|---|---|---|---|---|
| Setup effort | Low (YAML) | Low (plist) | Medium | Medium |
| Daily run speed | Slow (artifact I/O) | Fast (local DB) | N/A (compute limits) | Fast |
| Cost | Free (public repo) | Free | Free (trigger only) | Free (self-hosted) |
| Observability | Good (web UI) | Low (logs only) | N/A | Excellent (web UI) |
| DB sync to apps | Can't (no local FS) | Native | Can't | Native |
| Reliability | High (managed) | Needs machine on | High | Needs machine on |

**Recommendation for solo/small team:** Local launchd for daily/weekly, GitHub Actions for manual releases and CI.

---

## Checklist

- [ ] Archive obsolete scripts
- [ ] Audit live DB tables vs build_database.py
- [ ] Add all missing tables to build_database.py
- [ ] Add --schema-only and --fresh flags
- [ ] Create utils.py with shared functions
- [ ] Refactor import scripts to use utils.py
- [ ] Add --check health command to orchestrator
- [ ] Create run_pipeline.sh wrapper
- [ ] Create launchd plists
- [ ] Create install_launchd.sh
- [ ] Remove scheduled CI workflows
- [ ] Fix full-rebuild workflow
- [ ] Test: fresh schema build
- [ ] Test: health check on existing DB
- [ ] Test: single stage run
- [ ] Test: launchd trigger
