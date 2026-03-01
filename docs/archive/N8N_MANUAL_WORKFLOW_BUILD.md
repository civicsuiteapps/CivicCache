# Building the Cache Automation Workflow Manually in n8n

**Date Created**: 2025-12-27
**Problem**: Execute Command node is deprecated in modern n8n, causing import errors
**Solution**: Build workflow manually using the n8n UI with available nodes

---

## Why Manual Build Instead of JSON Import?

The `n8n-nodes-base.executeCommand` node type has been **deprecated** in recent n8n versions. Instead, modern n8n uses:
- **SSH node** (requires credential setup - complex)
- **Execute Workflow Trigger** (requires sub-workflows - complex)
- **Manual building** (SIMPLEST - recommended)

---

## Alternative Approach: Use n8n Community Nodes

Before building manually, you can try installing the Execute Command community node:

### Option 1: Install Community Execute Command Node

1. Open n8n settings (gear icon)
2. Go to "Community Nodes"
3. Search for and install: `n8n-nodes-execute-command`
4. Restart n8n container:
   ```bash
   cd /Users/abrooks/Documents/n8n-docker
   docker-compose restart
   ```
5. Try importing `cache_automation_workflow.json` again

If this works, you're done! If not, proceed with manual build below.

---

## Option 2: Manual Workflow Build Guide

### Prerequisites

Before you start, ensure:
- ✅ n8n is running (http://localhost:5678)
- ✅ All three databases already exist:
  - `/Users/abrooks/GIT/JudicialCache/data/judicial_cache.db`
  - `/Users/abrooks/GIT/ExecutiveCache/data/executive_cache.db`
  - `/Users/abrooks/GIT/CivicCache/data/civic_cache.db`

---

## Step-by-Step Build Instructions

### Phase 1: Create New Workflow

1. Open n8n: http://localhost:5678
2. Click "+ Add workflow"
3. Name: "Cache Automation - Unified"
4. Click "Save"

---

### Phase 2: Add Triggers (3 nodes)

#### Node 1: Judicial Trigger
1. Click "+" to add node
2. Search: "Schedule Trigger"
3. Click to add
4. Configure:
   - **Cron Expression**: `0 2 * * 0` (Sunday 2:00 AM)
   - **Timezone**: Your local timezone
5. Rename node: "Judicial Trigger (Sun 2AM)"
6. Position: Top of canvas

#### Node 2: Executive Trigger
1. Add another "Schedule Trigger" node
2. Configure:
   - **Cron Expression**: `0 3 * * 0` (Sunday 3:00 AM)
3. Rename: "Executive Trigger (Sun 3AM)"
4. Position: Middle of canvas

#### Node 3: Civic Trigger
1. Add another "Schedule Trigger" node
2. Configure:
   - **Cron Expression**: `0 4 * * *` (Daily 4:00 AM)
3. Rename: "Civic Trigger (Daily 4AM)"
4. Position: Bottom of canvas

---

### Phase 3: Add Project Tags (3 nodes)

#### Node 4: Tag Judicial
1. Add "Set" node
2. Connect from "Judicial Trigger (Sun 2AM)"
3. Configure:
   - Click "+ Add Value"
   - **Name**: `project`
   - **Value**: `judicial`
4. Rename: "Tag: Judicial"

#### Node 5: Tag Executive
1. Add "Set" node
2. Connect from "Executive Trigger (Sun 3AM)"
3. Configure:
   - **Name**: `project`
   - **Value**: `executive`
4. Rename: "Tag: Executive"

#### Node 6: Tag Civic
1. Add "Set" node
2. Connect from "Civic Trigger (Daily 4AM)"
3. Configure:
   - **Name**: `project`
   - **Value**: `civic`
4. Rename: "Tag: Civic"

---

### Phase 4: State Management (2 nodes)

#### Node 7: Check Last Run
1. Add "Code" node
2. Connect from all 3 "Tag" nodes (merge point)
3. Paste this JavaScript:

```javascript
const fs = require('fs');
const project = $json.project;
const stateFile = `/Users/abrooks/GIT/${project.charAt(0).toUpperCase() + project.slice(1)}Cache/data/pipeline_state.json`;

let state = { last_successful_run: null };
if (fs.existsSync(stateFile)) {
  const stateData = fs.readFileSync(stateFile, 'utf8');
  state = JSON.parse(stateData);
}

const now = new Date();
const thresholds = {
  judicial: 7 * 24 * 60 * 60 * 1000,   // 7 days
  executive: 7 * 24 * 60 * 60 * 1000,  // 7 days
  civic: 24 * 60 * 60 * 1000            // 1 day
};

const shouldRun = !state.last_successful_run ||
  (now - new Date(state.last_successful_run)) > thresholds[project];

return [{
  json: {
    project: project,
    should_run: shouldRun,
    last_run: state.last_successful_run,
    hours_since_last_run: state.last_successful_run
      ? Math.floor((now - new Date(state.last_successful_run)) / (1000 * 60 * 60))
      : null
  }
}];
```

4. Rename: "Check Last Run"

#### Node 8: Should Run?
1. Add "IF" node
2. Connect from "Check Last Run"
3. Configure condition:
   - **Value 1**: `{{ $json.should_run }}`
   - **Operation**: `Equal to`
   - **Value 2**: `true`
4. Rename: "Should Run?"

---

### Phase 5: Routing (3 nodes)

#### Node 9: Skip - Too Recent
1. Add "NoOp" node
2. Connect from "Should Run?" **FALSE** output
3. Rename: "Skip - Too Recent"

#### Node 10: Route: Judicial?
1. Add "IF" node
2. Connect from "Should Run?" **TRUE** output
3. Configure:
   - **Value 1**: `{{ $json.project }}`
   - **Operation**: `Equal to`
   - **Value 2**: `judicial`
4. Rename: "Route: Judicial?"

#### Node 11: Route: Executive?
1. Add "IF" node
2. Connect from "Route: Judicial?" **FALSE** output
3. Configure:
   - **Value 1**: `{{ $json.project }}`
   - **Operation**: `Equal to`
   - **Value 2**: `executive`
4. Rename: "Route: Executive?"

---

### Phase 6: JudicialCache Pipeline (8 nodes)

**IMPORTANT**: Since Execute Command nodes don't exist, you have 3 options:

#### Option A: Use SSH Node (Localhost)
1. Set up SSH credentials for localhost
2. Use SSH node type instead of Execute Command

#### Option B: Use HTTP Request to Webhook
1. Create a simple webhook server that accepts POST requests
2. Pass commands as JSON body
3. Webhook executes commands locally

#### Option C: **RECOMMENDED - Use Code Node with child_process**

For each pipeline stage, use a "Code" node with this pattern:

**Example - J0: Backup**
```javascript
const { execSync } = require('child_process');

const command = 'cp /Users/abrooks/GIT/JudicialCache/data/judicial_cache.db /Users/abrooks/GIT/JudicialCache/data/judicial_cache_backup_$(date +%Y%m%d_%H%M%S).db';

try {
  const output = execSync(command, { encoding: 'utf8' });
  return [{
    json: {
      success: true,
      output: output,
      command: command
    }
  }];
} catch (error) {
  return [{
    json: {
      success: false,
      error: error.message,
      command: command
    }
  }];
}
```

Create 8 Code nodes for JudicialCache pipeline:

1. **J0: Backup**
   - Command: `cp /Users/abrooks/GIT/JudicialCache/data/judicial_cache.db ...`

2. **J1: Opinions**
   - Command: `cd /Users/abrooks/GIT/JudicialCache && python3 scripts/fetch_scotus_sqlite.py --incremental`

3. **J2: Justices**
   - Command: `cd /Users/abrooks/GIT/JudicialCache && python3 scripts/fetch_scotus_justices.py --incremental`

4. **J3: Photos**
   - Command: `cd /Users/abrooks/GIT/JudicialCache && python3 scripts/import_justice_images.py --new-only`

5. **J4: Presidents**
   - Command: `cd /Users/abrooks/GIT/JudicialCache && python3 scripts/update_appointing_presidents.py --new-only`

6. **J5: Segments**
   - Command: `cd /Users/abrooks/GIT/JudicialCache && python3 scripts/parse_opinion_segments.py --incremental`

7. **J6: Validate**
   - Command: Bash validation script (see below)

8. **J7: Package**
   - Command: `cd /Users/abrooks/GIT/JudicialCache && python3 scripts/package_for_judicialscope.py`

**Connect**: Route: Judicial? TRUE → J0 → J1 → J2 → J3 → J4 → J5 → J6 → J7

---

### Phase 7: ExecutiveCache Pipeline (8 nodes)

Create 8 Code nodes (same pattern as above):

1. **E0: Backup**
2. **E1: Exec Orders**
3. **E2: Proclamations**
4. **E3: Memoranda**
5. **E4: Officials**
6. **E5: Merge**
7. **E6: Validate**
8. **E7: Package**

**Connect**: Route: Executive? TRUE → E0 → E1 → E2 → E3 → E4 → E5 → E6 → E7

---

### Phase 8: CivicCache Pipeline (9 nodes)

Create 9 Code nodes:

1. **C0: Backup**
2. **C1: Legislators**
3. **C2: Contact Info**
4. **C3: Member Images**
5. **C4: Member Metadata**
6. **C5: Legislation**
7. **C6: FEC Committees**
8. **C7: Validate**
9. **C8: Package**

**Connect**: Route: Executive? FALSE → C0 → C1 → C2 → C3 → C4 → C5 → C6 → C7 → C8

---

### Phase 9: Completion (2 nodes)

#### Update State
1. Add "Code" node
2. Connect from J7, E7, and C8 (merge point)
3. Paste this code:

```javascript
const fs = require('fs');
const project = $json.project;
const stateFile = `/Users/abrooks/GIT/${project.charAt(0).toUpperCase() + project.slice(1)}Cache/data/pipeline_state.json`;

const state = {
  last_successful_run: new Date().toISOString(),
  version: '1.0',
  workflow_id: $workflow.id,
  execution_id: $execution.id
};

fs.writeFileSync(stateFile, JSON.stringify(state, null, 2));

return [{
  json: {
    project: project,
    state_updated: true,
    timestamp: state.last_successful_run
  }
}];
```

4. Rename: "Update State"

#### Success Alert
1. Add "NoOp" node (placeholder for future notifications)
2. Connect from "Update State"
3. Rename: "Success Alert"

---

## Validation Script Template

For J6, E6, and C7 validation nodes, use this pattern:

### JudicialCache Validation (J6)
```javascript
const { execSync } = require('child_process');

const dbPath = '/Users/abrooks/GIT/JudicialCache/data/judicial_cache.db';
const fs = require('fs');

if (!fs.existsSync(dbPath)) {
  throw new Error('Database not found - run full rebuild manually');
}

const opinions = execSync(`sqlite3 "${dbPath}" "SELECT COUNT(*) FROM opinions;"`, { encoding: 'utf8' }).trim();
const justices = execSync(`sqlite3 "${dbPath}" "SELECT COUNT(*) FROM justices;"`, { encoding: 'utf8' }).trim();

if (parseInt(opinions) < 100000 || parseInt(justices) < 100) {
  throw new Error(`Too few records: opinions=${opinions}, justices=${justices}`);
}

return [{
  json: {
    valid: true,
    opinions: parseInt(opinions),
    justices: parseInt(justices)
  }
}];
```

---

## Testing Checklist

After building the workflow:

1. ✅ **Visual Check**: All nodes connected properly
2. ✅ **Save Workflow**: Click "Save" button
3. ✅ **Test Trigger**: Click "Execute Workflow" on one trigger
4. ✅ **Check Logs**: Watch execution in real-time
5. ✅ **Verify State File**: Check that `pipeline_state.json` was created
6. ✅ **Test Schedule**: Wait for actual cron trigger (or adjust to sooner time for testing)

---

## Troubleshooting

### Issue: "child_process is not defined"
**Fix**: Make sure you're using `const { execSync } = require('child_process');` at the top of each Code node

### Issue: "ENOENT: no such file or directory"
**Fix**: Use absolute paths in all commands (already included in examples above)

### Issue: "sqlite3: command not found"
**Fix**: The n8n Docker container may not have sqlite3 installed. You'll need to:
1. Exec into the container: `docker exec -it n8n /bin/sh`
2. Install sqlite3: `apk add sqlite` (if using Alpine base image)
3. Or use Python to query database instead

---

## Simplified Alternative: Single Python Script

Instead of 25+ Code nodes, you could create **3 master Python scripts** (one per project) that handle the entire pipeline, then call them from just 3 Code nodes:

**Example - judicial_pipeline.py**:
```python
#!/usr/bin/env python3
import subprocess
import sys

def run_command(cmd, description):
    print(f"Running: {description}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: {result.stderr}")
        sys.exit(1)
    print(f"SUCCESS: {result.stdout}")

# Backup
run_command('cp /path/to/db /path/to/backup_$(date +%Y%m%d_%H%M%S).db', 'Backup database')

# Opinions
run_command('cd /path && python3 scripts/fetch_scotus_sqlite.py --incremental', 'Fetch opinions')

# ... continue for all stages
```

Then in n8n, just 3 Code nodes:
```javascript
const { execSync } = require('child_process');
const output = execSync('python3 /Users/abrooks/GIT/JudicialCache/judicial_pipeline.py', { encoding: 'utf8' });
return [{ json: { output: output } }];
```

This reduces complexity significantly!

---

## Final Workflow Count

- **Manual build**: 38 nodes (as documented)
- **Simplified with Python scripts**: 11 nodes (3 triggers, 3 tags, 1 state check, 1 should run, 3 python runners, 1 update state, 1 success)

Choose the approach that works best for your setup!
