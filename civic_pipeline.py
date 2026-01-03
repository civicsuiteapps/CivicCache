#!/usr/bin/env python3
"""
CivicCache Pipeline - Simplified
Runs update stages sequentially using existing scripts
"""

import subprocess
import sys
import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
SCRIPTS_DIR = BASE_DIR / "scripts"
DB_PATH = DATA_DIR / "civic_cache.db"

def run_command(cmd, description, cwd=None):
    """Run a shell command and handle errors"""
    print(f"\n{'='*60}")
    print(f"STAGE: {description}")
    print(f"CMD: {cmd}")
    print(f"{'='*60}")

    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        cwd=cwd or BASE_DIR
    )

    if result.stdout:
        print(f"STDOUT:\n{result.stdout}")
    if result.stderr:
        print(f"STDERR:\n{result.stderr}")

    if result.returncode != 0:
        print(f"\n❌ FAILED: {description}")
        sys.exit(1)

    print(f"✅ SUCCESS: {description}")
    return result.stdout

def main():
    print(f"\n🚀 CivicCache Pipeline - {datetime.now().isoformat()}")

    # Stage 0: Backup
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DATA_DIR / f"civic_cache_backup_{timestamp}.db"

    if DB_PATH.exists():
        run_command(
            f'cp "{DB_PATH}" "{backup_path}"',
            "Backup database"
        )

    # Stage 1-6: Smart incremental update
    # Uses incremental_update.py which orchestrates all data fetches
    if (SCRIPTS_DIR / "incremental_update.py").exists():
        run_command(
            f'python3 "{SCRIPTS_DIR}/incremental_update.py"',
            "Run smart incremental updates (all stages)"
        )
    else:
        # Fallback to individual scripts if incremental wrapper doesn't exist
        if (SCRIPTS_DIR / "import_congress_legislators.py").exists():
            run_command(
                f'python3 "{SCRIPTS_DIR}/import_congress_legislators.py"',
                "Import Congress legislators"
            )

        if (SCRIPTS_DIR / "import_legislation.py").exists():
            run_command(
                f'python3 "{SCRIPTS_DIR}/import_legislation.py" --congress 119',
                "Import legislation (119th Congress)"
            )

    # Stage 7: Validate
    if DB_PATH.exists():
        members_count = run_command(
            f'sqlite3 "{DB_PATH}" "SELECT COUNT(*) FROM congress_members;" 2>/dev/null || echo "0"',
            "Count Congress members"
        ).strip()

        legislation_count = run_command(
            f'sqlite3 "{DB_PATH}" "SELECT COUNT(*) FROM legislation;" 2>/dev/null || echo "0"',
            "Count legislation"
        ).strip()

        print(f"✅ Validation: {members_count} members, {legislation_count} legislation")
    else:
        print("⚠️ Database not found - may need initial setup")

    # Stage 8: Package (VACUUM and ANALYZE)
    if DB_PATH.exists():
        release_path = DATA_DIR / "civic_cache_release.db"
        run_command(
            f'cp "{DB_PATH}" "{release_path}"',
            "Copy to release database"
        )

        run_command(
            f'sqlite3 "{release_path}" "VACUUM; ANALYZE;"',
            "Optimize release database (VACUUM/ANALYZE)"
        )

    # Success summary
    print(f"\n{'='*60}")
    print(f"🎉 CivicCache Pipeline COMPLETED")
    print(f"{'='*60}")
    print(json.dumps({
        "success": True,
        "timestamp": datetime.now().isoformat()
    }, indent=2))

if __name__ == "__main__":
    main()
