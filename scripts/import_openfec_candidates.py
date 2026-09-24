#!/usr/bin/env python3
"""
Weekly incremental candidate updates via the OpenFEC API.

Uses the OPENFEC_API_KEY to fetch near-real-time candidate data for races
where we have an incumbent (congress_members.fec_id match). The bulk FEC
files update infrequently; this API provides fresher financial data.

Rate limit: 1000 req/hr. Strategy:
  - Only query races with incumbents (~470 races)
  - Skip races updated <7 days ago (via metadata timestamps)
  - Sleep 4s between calls to stay well under the limit
  - Full sweep takes ~2 hours; designed for weekly runs

Usage:
    python3 scripts/import_openfec_candidates.py
    python3 scripts/import_openfec_candidates.py --cycle 2026
    python3 scripts/import_openfec_candidates.py --force  # ignore cooldown
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

from config import DB_PATH
from import_fec_candidates import _not_running, load_retirements

OPENFEC_BASE = "https://api.open.fec.gov/v1"
COOLDOWN_DAYS = 7
SLEEP_BETWEEN_CALLS = 4  # seconds


def get_api_key() -> str | None:
    return os.environ.get("OPENFEC_API_KEY")


def api_get(
    endpoint: str, params: dict, api_key: str, retries: int = 4
) -> dict | None:
    """Make a GET request to the OpenFEC API with exponential backoff."""
    params["api_key"] = api_key
    qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    url = f"{OPENFEC_BASE}{endpoint}?{qs}"

    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "CivicCache/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 2 ** (attempt + 1)
                print(f"    Rate limited (429), waiting {wait}s...")
                time.sleep(wait)
            elif e.code == 404:
                return None
            else:
                print(f"    HTTP {e.code}: {e.reason}")
                if attempt == retries - 1:
                    return None
                time.sleep(2 ** attempt)
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError) as e:
            wait = 2 ** (attempt + 1)
            print(f"    API error (attempt {attempt + 1}/{retries}): {e}")
            if attempt == retries - 1:
                return None
            print(f"    Retrying in {wait}s...")
            time.sleep(wait)

    return None


def get_last_update(conn: sqlite3.Connection, race_id: str) -> datetime | None:
    """Get last OpenFEC update time for a race."""
    row = conn.execute(
        "SELECT value FROM metadata WHERE key = ?",
        (f"openfec_update_{race_id}",),
    ).fetchone()
    if row and row[0]:
        try:
            return datetime.fromisoformat(row[0])
        except ValueError:
            return None
    return None


def set_last_update(conn: sqlite3.Connection, race_id: str) -> None:
    """Record OpenFEC update timestamp for a race."""
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value, updated_at) VALUES (?, ?, ?)",
        (f"openfec_update_{race_id}", now, now),
    )


def update_race_candidates(
    conn: sqlite3.Connection,
    race_id: str,
    cycle: int,
    office: str,
    state: str,
    district: str | None,
    api_key: str,
) -> int:
    """Fetch candidates for a race from OpenFEC and upsert."""
    params = {
        "office": office,
        "state": state,
        "election_year": str(cycle),
        "sort": "-receipts",
        "per_page": "20",
        "is_active_candidate": "true",
    }
    if district and office == "H":
        params["district"] = district

    data = api_get("/candidates/search/", params, api_key)
    if not data or "results" not in data:
        return 0

    # Curated retirees (data/retirements_2026.csv) stay out of their old
    # seat's race even though OpenFEC still lists them as active candidates.
    retirements = load_retirements(conn)

    updated = 0
    for cand in data["results"]:
        if _not_running(retirements, race_id, cand.get("candidate_id", "")):
            continue
        cand_id = cand.get("candidate_id")
        if not cand_id:
            continue

        # Fetch financial totals
        time.sleep(SLEEP_BETWEEN_CALLS)
        totals_data = api_get(
            f"/candidate/{cand_id}/totals/",
            {"election_year": str(cycle)},
            api_key,
        )

        receipts = None
        disbursements = None
        cash_on_hand = None
        individual_contributions = None
        candidate_contributions = None
        candidate_loans = None
        pac_contributions = None
        party_contributions = None
        debts_owed = None
        coverage_end_date = None

        if totals_data and totals_data.get("results"):
            t = totals_data["results"][0]
            receipts = t.get("receipts")
            disbursements = t.get("disbursements")
            cash_on_hand = t.get("last_cash_on_hand_end_period")
            individual_contributions = t.get("individual_contributions")
            candidate_contributions = t.get("candidate_contribution")
            candidate_loans = t.get("loans_made_by_candidate")
            pac_contributions = t.get("other_political_committee_contributions")
            party_contributions = t.get("political_party_committee_contributions")
            debts_owed = t.get("debts_owed_by_committee")
            raw_date = t.get("coverage_end_date")
            if raw_date:
                coverage_end_date = raw_date[:10]

        # Upsert into candidates table
        name = cand.get("name", "")
        party = cand.get("party", "")
        ici = cand.get("incumbent_challenge", "")

        conn.execute(
            """
            INSERT INTO candidates (
                candidate_id, election_cycle, candidate_name,
                office, office_state, office_district,
                party_code, incumbent_challenger_status,
                total_receipts, total_disbursements, cash_on_hand,
                total_individual_contributions, candidate_contributions,
                candidate_loans, pac_contributions, party_contributions,
                debts_owed, coverage_end_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id, election_cycle) DO UPDATE SET
                candidate_name = excluded.candidate_name,
                party_code = excluded.party_code,
                incumbent_challenger_status = excluded.incumbent_challenger_status,
                total_receipts = COALESCE(excluded.total_receipts, total_receipts),
                total_disbursements = COALESCE(excluded.total_disbursements, total_disbursements),
                cash_on_hand = COALESCE(excluded.cash_on_hand, cash_on_hand),
                total_individual_contributions = COALESCE(excluded.total_individual_contributions, total_individual_contributions),
                candidate_contributions = COALESCE(excluded.candidate_contributions, candidate_contributions),
                candidate_loans = COALESCE(excluded.candidate_loans, candidate_loans),
                pac_contributions = COALESCE(excluded.pac_contributions, pac_contributions),
                party_contributions = COALESCE(excluded.party_contributions, party_contributions),
                debts_owed = COALESCE(excluded.debts_owed, debts_owed),
                coverage_end_date = COALESCE(excluded.coverage_end_date, coverage_end_date)
            """,
            (
                cand_id, cycle, name, office, state,
                district if office == "H" else None,
                party, ici,
                receipts, disbursements, cash_on_hand,
                individual_contributions, candidate_contributions,
                candidate_loans, pac_contributions, party_contributions,
                debts_owed, coverage_end_date,
            ),
        )

        # Upsert into race_candidates
        conn.execute(
            """
            INSERT INTO race_candidates (
                race_id, candidate_id, election_cycle,
                candidate_name, party_code, incumbent_challenger_status,
                total_receipts, cash_on_hand, total_individual_contributions
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(race_id, candidate_id) DO UPDATE SET
                candidate_name = excluded.candidate_name,
                party_code = excluded.party_code,
                incumbent_challenger_status = excluded.incumbent_challenger_status,
                total_receipts = COALESCE(excluded.total_receipts, total_receipts),
                cash_on_hand = COALESCE(excluded.cash_on_hand, cash_on_hand),
                total_individual_contributions = COALESCE(excluded.total_individual_contributions, total_individual_contributions)
            """,
            (
                race_id, cand_id, cycle, name, party, ici,
                receipts, cash_on_hand, individual_contributions,
            ),
        )
        updated += 1

    # Update race candidate count
    total_in_race = conn.execute(
        "SELECT COUNT(*) FROM race_candidates WHERE race_id = ?", (race_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE election_races SET total_candidates = ? WHERE race_id = ?",
        (total_in_race, race_id),
    )

    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenFEC weekly candidate updates")
    parser.add_argument(
        "--cycle", type=int, default=2026, help="Election cycle (default: 2026)"
    )
    parser.add_argument(
        "--force", action="store_true", help="Ignore cooldown, update all races"
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Max races to update (0 = all)"
    )
    parser.add_argument(
        "--db", type=Path, default=DB_PATH, help=f"Database path (default: {DB_PATH})"
    )
    args = parser.parse_args()

    api_key = get_api_key()
    if not api_key:
        print("ERROR: OPENFEC_API_KEY environment variable not set")
        return 1

    print("=" * 60)
    print("CivicCache - OpenFEC Candidate Updates")
    print("=" * 60)
    print(f"  Database: {args.db}")
    print(f"  Cycle:    {args.cycle}")
    print(f"  Force:    {args.force}")

    if not args.db.exists():
        print(f"\nERROR: Database not found: {args.db}")
        return 1

    conn = sqlite3.connect(str(args.db))
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    try:
        # Get races with incumbents
        races = conn.execute(
            """
            SELECT race_id, election_cycle, office, state, district
            FROM election_races
            WHERE election_cycle = ? AND incumbent_bioguide_id IS NOT NULL
            ORDER BY race_id
            """,
            (args.cycle,),
        ).fetchall()

        print(f"\n  Found {len(races)} races with incumbents")

        updated_races = 0
        skipped = 0
        total_candidates = 0
        cutoff = datetime.now() - timedelta(days=COOLDOWN_DAYS)

        for race in races:
            race_id, cycle, office, state, district = race

            if args.limit and updated_races >= args.limit:
                print(f"\n  Reached limit of {args.limit} races")
                break

            # Check cooldown
            if not args.force:
                last = get_last_update(conn, race_id)
                if last and last > cutoff:
                    skipped += 1
                    continue

            print(f"\n  Updating {race_id}...")
            count = update_race_candidates(
                conn, race_id, cycle, office, state, district, api_key
            )
            total_candidates += count

            set_last_update(conn, race_id)
            conn.commit()
            updated_races += 1

            time.sleep(SLEEP_BETWEEN_CALLS)

        # Update metadata
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value, updated_at) VALUES (?, ?, ?)",
            ("openfec_last_run", datetime.now().isoformat(), datetime.now().isoformat()),
        )
        conn.commit()

        print("\n" + "=" * 60)
        print("OpenFEC Update Complete")
        print("=" * 60)
        print(f"  Races updated: {updated_races}")
        print(f"  Races skipped (cooldown): {skipped}")
        print(f"  Candidates updated: {total_candidates}")

        return 0

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
