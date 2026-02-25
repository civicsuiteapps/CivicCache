#!/usr/bin/env python3
"""
CivicCache consolidated data pipeline.

Replaces civic_pipeline.py, incremental_update.py, and webhook_server.py
with a single, dependency-aware, parallel-capable entry point.

Usage:
    python3 scripts/update.py daily              # Legislation + votes only
    python3 scripts/update.py weekly             # All frequently-changing data
    python3 scripts/update.py monthly            # Weekly + demographics + staff
    python3 scripts/update.py full               # Everything including FEC bulk
    python3 scripts/update.py --stage NAME       # Single named stage
    python3 scripts/update.py --list-stages      # Show all stages and tiers
    python3 scripts/update.py daily --dry-run    # Show what would run
    python3 scripts/update.py daily --json       # JSON output for CI parsing
    python3 scripts/update.py --check            # Health check (human-readable)
    python3 scripts/update.py --check --json     # Health check (JSON output)
"""

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

from config import CONGRESS_NUMBER, DB_PATH, PROJECT_ROOT, SCRIPTS_DIR

# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------

TIERS = ["daily", "weekly", "monthly", "full"]
TIER_HIERARCHY = {t: i for i, t in enumerate(TIERS)}


@dataclass
class Stage:
    name: str
    script: str
    args: List[str] = field(default_factory=list)
    tier: str = "weekly"
    depends_on: List[str] = field(default_factory=list)
    required_env: List[str] = field(default_factory=list)
    can_parallel: bool = True
    description: str = ""


STAGES: Dict[str, Stage] = {}


def _s(name: str, **kwargs) -> None:
    STAGES[name] = Stage(name=name, **kwargs)


# --- daily ---
_s(
    "legislation",
    script="import_legislation.py",
    args=["--congress", str(CONGRESS_NUMBER), "--incremental"],
    tier="daily",
    required_env=["CONGRESS_API_KEY"],
    description="Import/update legislation (incremental)",
)
_s(
    "votes",
    script="import_votes.py",
    args=["--congress", str(CONGRESS_NUMBER)],
    tier="daily",
    required_env=["CONGRESS_API_KEY"],
    description="Import roll-call votes",
)

# --- weekly ---
_s(
    "legislators",
    script="import_congress_legislators.py",
    tier="weekly",
    description="Import/update legislators from GitHub YAML",
)
_s(
    "committees",
    script="import_committees.py",
    tier="weekly",
    depends_on=["legislators"],
    description="Import committee assignments",
)
_s(
    "contact_info",
    script="import_member_contact_info.py",
    tier="weekly",
    depends_on=["legislators"],
    required_env=["CONGRESS_API_KEY"],
    description="Import member contact info",
)
_s(
    "images",
    script="import_member_images.py",
    tier="weekly",
    depends_on=["legislators"],
    can_parallel=True,
    description="Import member portrait images",
)
_s(
    "positions",
    script="import_member_positions.py",
    tier="weekly",
    depends_on=["legislators"],
    description="Import member positions/activity",
)
_s(
    "birth_years",
    script="import_birth_years.py",
    tier="weekly",
    depends_on=["legislators"],
    required_env=["CONGRESS_API_KEY"],
    description="Import member birth years",
)
_s(
    "contact_forms",
    script="fill_contact_forms.py",
    tier="weekly",
    depends_on=["contact_info"],
    description="Discover missing contact form URLs",
)

# --- monthly ---
_s(
    "staff",
    script="import_congressional_staff.py",
    args=["--import-house-sod"],
    tier="monthly",
    depends_on=["legislators"],
    description="Import congressional staff (House SOD)",
)
_s(
    "demographics",
    script="import_district_demographics.py",
    tier="monthly",
    required_env=["CENSUS_API_KEY"],
    description="Import district demographics from Census",
)

# --- monthly ---  (FEC bulk pipeline)
_s(
    "fec_bulk_download",
    script="download_fec_bulk.py",
    args=["--cycles", "2026", "--force"],
    tier="monthly",
    description="Download current-cycle FEC bulk data files",
)
_s(
    "fec_committees",
    script="import_fec_committees.py",
    tier="monthly",
    depends_on=["fec_bulk_download"],
    description="Import FEC committee master files",
)
_s(
    "fec_candidates",
    script="import_fec_candidates.py",
    args=["--current-only"],
    tier="monthly",
    depends_on=["fec_committees", "legislators"],
    description="Import FEC candidates with correct field mapping + races",
)
_s(
    "fec_summary",
    script="import_fec_summary.py",
    tier="monthly",
    depends_on=["fec_committees"],
    description="Import FEC financial summaries",
)
_s(
    "race_ratings",
    script="import_race_ratings.py",
    tier="monthly",
    depends_on=["fec_candidates"],
    description="Import race ratings (FiveThirtyEight / CSV)",
)
_s(
    "candidate_photos",
    script="import_candidate_photos.py",
    args=["--cycle", "2026"],
    tier="monthly",
    depends_on=["fec_candidates"],
    description="Import candidate photos from Wikidata",
)
_s(
    "openfec_candidates",
    script="import_openfec_candidates.py",
    tier="weekly",
    required_env=["OPENFEC_API_KEY"],
    description="Weekly OpenFEC API candidate updates",
)
_s(
    "district_boundaries",
    script="import_district_boundaries.py",
    tier="full",
    description="Import district boundary polygons",
)
_s(
    "zip_districts",
    script="import_zip_districts.py",
    tier="full",
    description="Import ZIP-to-district mappings",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _env_available(keys: List[str]) -> List[str]:
    """Return list of env vars that are missing or empty."""
    import os

    return [k for k in keys if not os.environ.get(k)]


def _check_env_for_stage(stage: Stage) -> bool:
    """Return True if the required env vars are present.

    The check maps logical key names to what is actually set in the
    environment.  Several scripts accept either ``CONGRESS_GOV_API_KEY``
    or ``CONGRESS_API_KEY`` -- we accept either.
    """
    import os

    for key in stage.required_env:
        if key == "CONGRESS_API_KEY":
            if os.environ.get("CONGRESS_GOV_API_KEY") or os.environ.get(
                "CONGRESS_API_KEY"
            ):
                continue
            return False
        elif not os.environ.get(key):
            return False
    return True


def _db_stat(label: str, sql: str) -> Optional[int]:
    """Run a simple SELECT COUNT / MAX query and return the value."""
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(DB_PATH))
        val = conn.execute(sql).fetchone()[0]
        conn.close()
        return val
    except Exception:
        return None


def _snapshot_stats() -> Dict[str, Optional[int]]:
    """Capture before/after database counts."""
    return {
        "members": _db_stat("members", "SELECT COUNT(*) FROM congress_members"),
        "legislation": _db_stat(
            "legislation", "SELECT COUNT(*) FROM legislation"
        ),
        "roll_call_votes": _db_stat(
            "roll_call_votes", "SELECT COUNT(*) FROM roll_call_votes"
        ),
        "member_votes": _db_stat(
            "member_votes", "SELECT COUNT(*) FROM member_votes"
        ),
        "staff": _db_stat("staff", "SELECT COUNT(*) FROM congressional_staff"),
        "fec_summaries": _db_stat(
            "fec_summaries", "SELECT COUNT(*) FROM member_fec_summary"
        ),
        "candidates": _db_stat(
            "candidates", "SELECT COUNT(*) FROM candidates"
        ),
        "election_races": _db_stat(
            "election_races", "SELECT COUNT(*) FROM election_races"
        ),
    }


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@dataclass
class StageResult:
    name: str
    status: str  # "success", "failed", "skipped"
    duration_s: float = 0.0
    message: str = ""


def _run_stage(stage: Stage) -> StageResult:
    """Execute a single stage script as a subprocess."""
    script_path = SCRIPTS_DIR / stage.script
    if not script_path.exists():
        return StageResult(
            stage.name, "skipped", message=f"Script not found: {stage.script}"
        )

    if not _check_env_for_stage(stage):
        missing = _env_available(stage.required_env)
        return StageResult(
            stage.name,
            "skipped",
            message=f"Missing env: {', '.join(missing)}",
        )

    cmd = [sys.executable, str(script_path)] + stage.args
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=7200,  # 2h hard timeout per stage
        )
        elapsed = time.monotonic() - t0
        if result.returncode != 0:
            stderr_tail = (result.stderr or "")[-500:]
            return StageResult(
                stage.name,
                "failed",
                duration_s=elapsed,
                message=f"exit {result.returncode}: {stderr_tail}",
            )
        return StageResult(stage.name, "success", duration_s=elapsed)
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        return StageResult(
            stage.name, "failed", duration_s=elapsed, message="Timed out (2h)"
        )
    except Exception as exc:
        elapsed = time.monotonic() - t0
        return StageResult(
            stage.name, "failed", duration_s=elapsed, message=str(exc)
        )


def _resolve_stages(
    tier: Optional[str], stage_name: Optional[str]
) -> List[str]:
    """Return an ordered list of stage names to run."""
    if stage_name:
        if stage_name not in STAGES:
            print(f"Unknown stage: {stage_name}")
            print(f"Available: {', '.join(STAGES)}")
            sys.exit(1)
        return [stage_name]

    if not tier or tier not in TIER_HIERARCHY:
        print(f"Unknown tier: {tier}")
        print(f"Available: {', '.join(TIERS)}")
        sys.exit(1)

    max_level = TIER_HIERARCHY[tier]
    selected = [
        name
        for name, s in STAGES.items()
        if TIER_HIERARCHY.get(s.tier, 99) <= max_level
    ]
    return selected


def _toposort_stages(names: List[str]) -> List[List[str]]:
    """Group stages into parallel waves respecting dependencies.

    Returns a list of waves; stages within a wave can run concurrently.
    """
    name_set: Set[str] = set(names)
    completed: Set[str] = set()
    waves: List[List[str]] = []
    remaining = set(names)

    while remaining:
        # Stages whose deps are all completed (or outside the run set)
        ready = [
            n
            for n in remaining
            if all(
                d in completed or d not in name_set
                for d in STAGES[n].depends_on
            )
        ]
        if not ready:
            # Shouldn't happen with valid deps; break cycle
            ready = sorted(remaining)
        waves.append(sorted(ready))
        completed.update(ready)
        remaining -= set(ready)

    return waves


def run_pipeline(
    tier: Optional[str],
    stage_name: Optional[str],
    dry_run: bool = False,
    json_output: bool = False,
    max_workers: int = 4,
) -> int:
    """Run the pipeline. Returns 0 on success, 1 if any stage failed."""

    selected = _resolve_stages(tier, stage_name)
    waves = _toposort_stages(selected)

    # --- Dry-run ---
    if dry_run:
        label = stage_name or tier
        print(f"\nDry-run for: {label}")
        print(f"Stages ({len(selected)}):\n")
        for i, wave in enumerate(waves):
            parallel_tag = " (parallel)" if len(wave) > 1 else ""
            print(f"  Wave {i + 1}{parallel_tag}:")
            for n in wave:
                s = STAGES[n]
                deps = f" [after {', '.join(s.depends_on)}]" if s.depends_on else ""
                env = f" (needs {', '.join(s.required_env)})" if s.required_env else ""
                print(f"    - {n:22s} {s.description}{deps}{env}")
        return 0

    # --- Before stats ---
    before = _snapshot_stats()

    started = datetime.now()
    if not json_output:
        print("=" * 70)
        print(f"  CivicCache Pipeline  |  {tier or stage_name}  |  {started:%Y-%m-%d %H:%M:%S}")
        print(f"  Congress: {CONGRESS_NUMBER}  |  DB: {DB_PATH}")
        print("=" * 70)

    results: List[StageResult] = []
    failed_stages: Set[str] = set()

    for wave in waves:
        # Filter out stages whose dependencies failed
        runnable = []
        for n in wave:
            failed_deps = [d for d in STAGES[n].depends_on if d in failed_stages]
            if failed_deps:
                r = StageResult(
                    n,
                    "skipped",
                    message=f"Dependency failed: {', '.join(failed_deps)}",
                )
                results.append(r)
                if not json_output:
                    print(f"\n  SKIP  {n} -- dependency failed: {', '.join(failed_deps)}")
            else:
                runnable.append(n)

        if not runnable:
            continue

        # Run wave (parallel when >1 and stages support it)
        if len(runnable) == 1:
            n = runnable[0]
            if not json_output:
                print(f"\n  RUN   {n} ...")
            r = _run_stage(STAGES[n])
            results.append(r)
            if r.status == "failed":
                failed_stages.add(n)
            if not json_output:
                _print_result(r)
        else:
            if not json_output:
                print(f"\n  PARALLEL  {', '.join(runnable)}")
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(_run_stage, STAGES[n]): n for n in runnable}
                for future in as_completed(futures):
                    n = futures[future]
                    r = future.result()
                    results.append(r)
                    if r.status == "failed":
                        failed_stages.add(n)
                    if not json_output:
                        _print_result(r)

    # --- After stats ---
    after = _snapshot_stats()
    elapsed = (datetime.now() - started).total_seconds()

    if json_output:
        _print_json(results, before, after, elapsed)
    else:
        _print_summary(results, before, after, elapsed)

    return 1 if failed_stages else 0


def _print_result(r: StageResult) -> None:
    icons = {"success": "OK", "failed": "FAIL", "skipped": "SKIP"}
    icon = icons.get(r.status, "??")
    dur = f" ({r.duration_s:.1f}s)" if r.duration_s else ""
    msg = f" -- {r.message}" if r.message else ""
    print(f"  {icon:4s}  {r.name:22s}{dur}{msg}")


def _print_summary(
    results: List[StageResult],
    before: Dict,
    after: Dict,
    elapsed: float,
) -> None:
    print("\n" + "=" * 70)
    print("  PIPELINE SUMMARY")
    print("=" * 70)

    ok = sum(1 for r in results if r.status == "success")
    fail = sum(1 for r in results if r.status == "failed")
    skip = sum(1 for r in results if r.status == "skipped")
    print(f"  Stages: {ok} succeeded, {fail} failed, {skip} skipped")
    print(f"  Total time: {elapsed:.0f}s ({elapsed / 60:.1f}min)")

    print("\n  Before / After:")
    for key in before:
        b = before.get(key)
        a = after.get(key)
        if b is not None and a is not None:
            diff = a - b
            sign = "+" if diff >= 0 else ""
            print(f"    {key:20s}  {b:>10,}  ->  {a:>10,}  ({sign}{diff:,})")

    if any(r.status == "failed" for r in results):
        print("\n  FAILED stages:")
        for r in results:
            if r.status == "failed":
                print(f"    - {r.name}: {r.message}")
    print()


def _print_json(
    results: List[StageResult],
    before: Dict,
    after: Dict,
    elapsed: float,
) -> None:
    out = {
        "success": all(r.status != "failed" for r in results),
        "elapsed_s": round(elapsed, 1),
        "congress": CONGRESS_NUMBER,
        "stages": [
            {
                "name": r.name,
                "status": r.status,
                "duration_s": round(r.duration_s, 1),
                "message": r.message or None,
            }
            for r in results
        ],
        "before": before,
        "after": after,
    }
    print(json.dumps(out, indent=2, default=str))


# ---------------------------------------------------------------------------
# --list-stages
# ---------------------------------------------------------------------------


def list_stages() -> None:
    print(f"\nCivicCache pipeline stages (Congress {CONGRESS_NUMBER})\n")
    for tier in TIERS:
        stages_in_tier = [s for s in STAGES.values() if s.tier == tier]
        print(f"  [{tier}]")
        for s in stages_in_tier:
            deps = f" -> after {', '.join(s.depends_on)}" if s.depends_on else ""
            env = f" (needs {', '.join(s.required_env)})" if s.required_env else ""
            print(f"    {s.name:22s}  {s.description}{deps}{env}")
        print()


# ---------------------------------------------------------------------------
# --check  (health check)
# ---------------------------------------------------------------------------

EXPECTED_TABLES = [
    "states",
    "congress_members",
    "legislation",
    "legislation_summaries",
    "legislation_sponsors",
    "legislation_subjects",
    "legislation_actions",
    "legislation_related",
    "congressional_committees",
    "member_committee_assignments",
    "member_positions",
    "member_fec_summary",
    "member_top_contributors",
    "committees",
    "candidates",
    "election_races",
    "race_candidates",
    "candidate_committee_linkages",
    "roll_call_votes",
    "member_votes",
    "congressional_staff",
    "congressional_districts",
    "district_demographics",
    "zip_districts",
    "metadata",
    "congress_members_fts",
    "legislation_fts",
]

MIN_ROW_COUNTS = {
    "states": 50,
    "congress_members": 530,
    "metadata": 1,
}


def run_health_check(json_output: bool = False) -> int:
    """Validate database health without running imports."""
    checks = []

    def _check(name: str, ok: bool, detail: str = ""):
        checks.append({"name": name, "ok": ok, "detail": detail})

    # 1. DB exists and is readable
    _check("db_exists", DB_PATH.exists(), str(DB_PATH))
    if not DB_PATH.exists():
        if json_output:
            print(json.dumps({"healthy": False, "checks": checks}, indent=2))
        else:
            print(f"FAIL  Database not found: {DB_PATH}")
        return 1

    try:
        conn = sqlite3.connect(str(DB_PATH))
    except Exception as e:
        _check("db_readable", False, str(e))
        if json_output:
            print(json.dumps({"healthy": False, "checks": checks}, indent=2))
        else:
            print(f"FAIL  Cannot open database: {e}")
        return 1
    _check("db_readable", True)

    # 2. All expected tables exist
    existing_tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    }
    missing = [t for t in EXPECTED_TABLES if t not in existing_tables]
    _check("tables_present", len(missing) == 0, f"missing: {', '.join(missing)}" if missing else f"{len(EXPECTED_TABLES)} tables")

    # 3. Minimum row counts
    for table, min_count in MIN_ROW_COUNTS.items():
        if table not in existing_tables:
            _check(f"rows_{table}", False, "table missing")
            continue
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
            _check(f"rows_{table}", count >= min_count, f"{count:,} rows (min {min_count})")
        except Exception as e:
            _check(f"rows_{table}", False, str(e))

    # 4. Data freshness via metadata timestamps
    try:
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = 'database_created'"
        ).fetchone()
        if row:
            _check("freshness_created", True, row[0])
        else:
            _check("freshness_created", False, "no database_created metadata")
    except Exception:
        _check("freshness_created", False, "metadata table error")

    try:
        row = conn.execute(
            "SELECT key, value, updated_at FROM metadata ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if row:
            _check("freshness_latest", True, f"{row[0]}={row[1]} at {row[2]}")
        else:
            _check("freshness_latest", False, "no metadata rows")
    except Exception:
        _check("freshness_latest", False, "metadata query error")

    # 5. Foreign key integrity check
    try:
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        _check("fk_integrity", len(fk_errors) == 0, f"{len(fk_errors)} violations" if fk_errors else "clean")
    except Exception as e:
        _check("fk_integrity", False, str(e))

    conn.close()

    # Output
    healthy = all(c["ok"] for c in checks)

    if json_output:
        print(json.dumps({"healthy": healthy, "checks": checks}, indent=2))
    else:
        print(f"\nCivicCache Health Check  |  DB: {DB_PATH}\n")
        for c in checks:
            icon = "OK  " if c["ok"] else "FAIL"
            detail = f"  ({c['detail']})" if c["detail"] else ""
            print(f"  {icon}  {c['name']}{detail}")
        print(f"\n  {'HEALTHY' if healthy else 'UNHEALTHY'}")

    return 0 if healthy else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CivicCache data pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "tier",
        nargs="?",
        choices=TIERS,
        help="Run all stages up to this tier",
    )
    parser.add_argument(
        "--stage", help="Run a single named stage"
    )
    parser.add_argument(
        "--check", action="store_true", help="Run health check on the database"
    )
    parser.add_argument(
        "--list-stages", action="store_true", help="Show all stages and tiers"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would run without executing"
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output", help="JSON output for CI"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Max parallel workers (default: 4)",
    )

    args = parser.parse_args()

    if args.check:
        return run_health_check(json_output=args.json_output)

    if args.list_stages:
        list_stages()
        return 0

    if not args.tier and not args.stage:
        parser.print_help()
        return 1

    return run_pipeline(
        tier=args.tier,
        stage_name=args.stage,
        dry_run=args.dry_run,
        json_output=args.json_output,
        max_workers=args.workers,
    )


if __name__ == "__main__":
    sys.exit(main())
