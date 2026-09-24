#!/usr/bin/env python3
"""
CivicCache consolidated data pipeline.

Stage definitions, snapshots, and the health check live here; the generic
runner (tier resolution, dependency waves, subprocess execution) comes from
cachecore.runner (~/GIT/cachecore), shared with ExecutiveCache/JudicialCache.

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

Every non-dry run also writes its payload to data/last_run.json for the
digest (scripts/digest.py) and the CacheStatus menu-bar app.
"""

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional

# config must be imported first: it loads .env and puts cachecore on sys.path
from config import CONGRESS_NUMBER, DB_PATH, PROJECT_ROOT, RETIREMENTS_CSV, SCRIPTS_DIR

from cachecore.runner import (
    Stage,
    list_stages as core_list_stages,
    run_pipeline as core_run_pipeline,
)

# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------

TIERS = ["daily", "weekly", "monthly", "full"]

# Several scripts accept either CONGRESS_GOV_API_KEY or CONGRESS_API_KEY.
ENV_ALIASES = {"CONGRESS_API_KEY": ["CONGRESS_GOV_API_KEY", "CONGRESS_API_KEY"]}

LAST_RUN_PATH = PROJECT_ROOT / "data" / "last_run.json"

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
    description="Import House roll-call votes from Congress.gov API",
)
_s(
    "senate-votes",
    script="import_senate_votes.py",
    args=["--congress", str(CONGRESS_NUMBER)],
    tier="daily",
    required_env=[],
    description="Import Senate roll-call votes from Senate.gov XML",
)

# --- weekly ---
_s(
    "legislators",
    script="import_congress_legislators.py",
    args=["--include-historical"],
    tier="weekly",
    description="Import/update legislators from GitHub YAML (incl. mid-term departures from current Congress)",
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
    "candidate_contributors",
    script="import_candidate_contributors.py",
    args=["--cycle", "2026"],
    tier="monthly",
    depends_on=["fec_candidates", "fec_committees"],
    description="Import top PAC/committee contributors for all candidates",
)
_s(
    "committee_financials",
    script="import_committee_financials.py",
    args=["--cycle", "2026", "--api-limit", "500"],
    tier="monthly",
    depends_on=["fec_committees", "fec_summary"],
    description="Populate committees.total_receipts/disbursements/cash_on_hand",
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
    "google_civic",
    script="import_google_civic.py",
    args=["--cycle", "2026"],
    tier="monthly",
    required_env=["GOOGLE_CIVIC_API_KEY"],
    depends_on=["fec_candidates"],
    description="Import candidate contact info from Google Civic API",
)
_s(
    "campaign_sites",
    script="import_campaign_sites.py",
    args=["--cycle", "2026"],
    tier="monthly",
    depends_on=["fec_candidates"],
    description="Import campaign websites and social links from Ballotpedia + URL probing",
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
# Snapshots
# ---------------------------------------------------------------------------


def _db_stat(sql: str) -> Optional[int]:
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
        "members": _db_stat("SELECT COUNT(*) FROM congress_members"),
        "legislation": _db_stat("SELECT COUNT(*) FROM legislation"),
        "roll_call_votes": _db_stat("SELECT COUNT(*) FROM roll_call_votes"),
        "member_votes": _db_stat("SELECT COUNT(*) FROM member_votes"),
        "staff": _db_stat("SELECT COUNT(*) FROM congressional_staff"),
        "fec_summaries": _db_stat("SELECT COUNT(*) FROM member_fec_summary"),
        "candidates": _db_stat("SELECT COUNT(*) FROM candidates"),
        "election_races": _db_stat("SELECT COUNT(*) FROM election_races"),
    }


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
    "candidate_top_contributors",
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

    # 6. Contributor display names resolved (no raw FEC committee IDs or
    #    empty strings shipping as reader-facing names)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM candidate_top_contributors "
            "WHERE committee_name IS NULL OR committee_name = '' "
            "   OR committee_name = committee_id "
            "   OR committee_name GLOB 'C[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'"
        ).fetchone()[0]
        _check("candidate_contributor_names", n == 0, f"{n} unresolved" if n else "all resolved")
    except Exception as e:
        _check("candidate_contributor_names", False, str(e))

    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM member_top_contributors "
            "WHERE contributor_name IS NULL OR contributor_name = '' "
            "   OR contributor_name GLOB 'C[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'"
        ).fetchone()[0]
        _check("member_contributor_names", n == 0, f"{n} unresolved" if n else "all resolved")
    except Exception as e:
        _check("member_contributor_names", False, str(e))

    # 7. No raw HTML markup in reader-facing attribution text
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM congress_members WHERE depiction_attribution LIKE '%<%'"
        ).fetchone()[0]
        _check("attribution_plain_text", n == 0, f"{n} rows with HTML" if n else "clean")
    except Exception as e:
        _check("attribution_plain_text", False, str(e))

    # 8. Curated primary winners join to race_candidates AND candidates
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM primary_winners pw "
            "LEFT JOIN race_candidates rc "
            "  ON rc.race_id = pw.race_id AND rc.candidate_id = pw.candidate_id "
            "LEFT JOIN candidates c "
            "  ON c.candidate_id = pw.candidate_id AND c.election_cycle = pw.election_cycle "
            "WHERE rc.candidate_id IS NULL OR c.candidate_id IS NULL"
        ).fetchone()[0]
        _check("primary_winner_integrity", n == 0, f"{n} dangling" if n else "all resolve")
    except Exception as e:
        _check("primary_winner_integrity", False, str(e))

    # 9. committees_fts in sync with committees
    try:
        fts_n = conn.execute("SELECT COUNT(*) FROM committees_fts").fetchone()[0]
        base_n = conn.execute("SELECT COUNT(*) FROM committees").fetchone()[0]
        _check("committees_fts_sync", fts_n == base_n, f"{fts_n:,} fts vs {base_n:,} base")
    except Exception as e:
        _check("committees_fts_sync", False, str(e))

    # 10. Every House race maps to a real seat (delegate 98 files as '00');
    #     no district-less or phantom-district races
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM election_races er WHERE er.office = 'H' AND ("
            "  er.district IS NULL OR NOT EXISTS ("
            "    SELECT 1 FROM congressional_districts cd "
            "    WHERE cd.state_code = er.state "
            "      AND printf('%02d', CASE WHEN cd.district_number = 98 THEN 0 "
            "                              ELSE cd.district_number END) = er.district))"
        ).fetchone()[0]
        _check("race_districts_valid", n == 0, f"{n} invalid" if n else "all valid")
    except Exception as e:
        _check("race_districts_valid", False, str(e))

    # 11. District boundaries are real GeoJSON ([lng,lat]; US => |lng| > |lat|)
    try:
        bad = 0
        for (gj,) in conn.execute("SELECT geojson FROM congressional_districts"):
            try:
                g = json.loads(gj)
                if not (isinstance(g, dict) and g.get("type") in ("Polygon", "MultiPolygon")):
                    bad += 1
                    continue
                c = g["coordinates"]
                first = c[0][0][0] if g["type"] == "MultiPolygon" else c[0][0]
                if abs(first[0]) <= abs(first[1]):
                    bad += 1
            except Exception:
                bad += 1
        _check("district_geojson_format", bad == 0, f"{bad} malformed" if bad else "valid GeoJSON")
    except Exception as e:
        _check("district_geojson_format", False, str(e))

    # 12. Curated retirements applied: a member listed in
    #     data/retirements_2026.csv must not still be the race's incumbent
    #     (open_seat = 0) nor sit in race_candidates for that race. FEC data
    #     alone can't see a run for state office, so this is the only guard
    #     against e.g. Tuberville shipping as the 2026-S-AL incumbent.
    try:
        bad_races = []
        n_rows = 0
        if RETIREMENTS_CSV.exists():
            with RETIREMENTS_CSV.open(newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    race_id = (row.get("race_id") or "").strip()
                    bioguide = (row.get("bioguide_id") or "").strip()
                    fec_id = (row.get("fec_candidate_id") or "").strip()
                    if not race_id or not bioguide:
                        continue
                    n_rows += 1
                    race = conn.execute(
                        "SELECT open_seat, incumbent_bioguide_id, incumbent_fec_id "
                        "FROM election_races WHERE race_id = ?",
                        (race_id,),
                    ).fetchone()
                    if race is None:
                        continue  # no such race built (nobody filed) -- nothing to gate
                    open_seat, inc_bid, inc_fec = race
                    still_listed = conn.execute(
                        "SELECT COUNT(*) FROM race_candidates rc "
                        "JOIN congress_members m ON m.id = ? "
                        "WHERE rc.race_id = ? AND (rc.candidate_id = ? OR rc.candidate_id = m.fec_id)",
                        (bioguide, race_id, fec_id or "-"),
                    ).fetchone()[0]
                    if (
                        inc_bid == bioguide
                        or (fec_id and inc_fec == fec_id)
                        or (open_seat == 0 and inc_bid is None)
                        or still_listed
                    ):
                        bad_races.append(race_id)
        _check(
            "retirements_applied",
            len(bad_races) == 0,
            f"{len(bad_races)} race(s) still show a retiree as incumbent/candidate: "
            f"{', '.join(bad_races)}" if bad_races
            else (f"{n_rows} curated retirement(s) honored" if n_rows else "no retirements file"),
        )
    except Exception as e:
        _check("retirements_applied", False, str(e))

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
        core_list_stages(
            "CivicCache", STAGES, TIERS, header_extra=f"Congress {CONGRESS_NUMBER}"
        )
        return 0

    if not args.tier and not args.stage:
        parser.print_help()
        return 1

    try:
        rc, payload = core_run_pipeline(
            project="CivicCache",
            stages=STAGES,
            tiers=TIERS,
            scripts_dir=SCRIPTS_DIR,
            project_root=PROJECT_ROOT,
            tier=args.tier,
            stage_name=args.stage,
            dry_run=args.dry_run,
            json_output=args.json_output,
            max_workers=args.workers,
            snapshot_fn=_snapshot_stats,
            env_aliases=ENV_ALIASES,
            header_extra=f"Congress: {CONGRESS_NUMBER}  |  DB: {DB_PATH}",
            extra_json={"congress": CONGRESS_NUMBER},
        )
    except ValueError as exc:
        print(exc)
        return 1

    # Record the run for digest.py and the CacheStatus app.
    if payload is not None:
        try:
            LAST_RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = LAST_RUN_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2, default=str))
            tmp.replace(LAST_RUN_PATH)
        except OSError:
            pass

    return rc


if __name__ == "__main__":
    sys.exit(main())
