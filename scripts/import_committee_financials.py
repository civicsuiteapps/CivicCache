#!/usr/bin/env python3
"""
Populate financial data on the committees table.

Three-phase enrichment, fast → slow:
  Phase 1: Bulk PAC-Summary file (webk26.txt) — covers ~10k PACs/leadership
           PACs in seconds.
  Phase 2: Candidate JOIN — copies receipts/disbursements/COH from the
           candidates table into the candidate's principal committee
           (committee_designation = 'P' or 'A').
  Phase 3: OpenFEC API fallback for any priority committee still missing
           data, with exponential backoff for rate limits.

Priority order: leadership PACs (D), then principal/authorized candidate
committees (P/A), then other PAC types.

Skips terminated and administratively terminated committees entirely.

Usage:
    python3 scripts/import_committee_financials.py
    python3 scripts/import_committee_financials.py --skip-api
    python3 scripts/import_committee_financials.py --api-limit 500
    python3 scripts/import_committee_financials.py --cycle 2026
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

try:
    from config import DB_PATH as _CFG_DB, FEC_DIR as _CFG_FEC
except ImportError:
    _CFG_DB = Path("data/civic_cache.db")
    _CFG_FEC = Path("data/raw/fec")

DB_PATH = _CFG_DB
FEC_DIR = _CFG_FEC
PAC_SUMMARY_DIR = FEC_DIR / "PAC-Summary"

OPENFEC_BASE = "https://api.open.fec.gov/v1"
# FEC free tier: 1000 req/hour. 4s/call = 900/hour, well under the limit.
SLEEP_BETWEEN_CALLS = 4.0


# ---------------------------------------------------------------------------
# Phase 1: Bulk PAC-Summary file
# ---------------------------------------------------------------------------

# Field positions in the FEC webkYY.txt PAC-Summary file (0-indexed):
#  0  CMTE_ID
#  1  CMTE_NM
#  2  CMTE_TP
#  3  CMTE_DSGN
#  4  CMTE_FILING_FREQ
#  5  TTL_RECEIPTS
#  6  TRANS_FROM_AFF
#  ...
# 12  TTL_DISB
#  ...
# 19  COH_COP   (cash on hand close of period)
#  ...
# 26  CVG_END_DT
WEBK_TTL_RECEIPTS = 5
WEBK_TTL_DISB = 12
WEBK_COH_COP = 19
WEBK_CVG_END_DT = 26


def _parse_money(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_date(value: str) -> str | None:
    """FEC dates are MM/DD/YYYY -- normalize to ISO YYYY-MM-DD."""
    value = value.strip()
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def import_pac_summary(conn: sqlite3.Connection, cycle: int) -> dict:
    """Phase 1: Read webk{yy}.txt and update committee financials in bulk."""
    yy = str(cycle)[-2:]
    zip_path = PAC_SUMMARY_DIR / f"PAC-Summary_{cycle}.zip"
    inner_name = f"webk{yy}.txt"

    if not zip_path.exists():
        print(f"  ! PAC-Summary zip not found at {zip_path}")
        return {"updated": 0, "skipped": 0, "missing": 0}

    print(f"  Reading {zip_path.name} → {inner_name}")

    updated = 0
    skipped = 0
    missing = 0

    with zipfile.ZipFile(zip_path, "r") as zf:
        with zf.open(inner_name) as f:
            for raw in f:
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                fields = line.split("|")
                if len(fields) < 27:
                    skipped += 1
                    continue

                committee_id = fields[0].strip()
                if not committee_id:
                    skipped += 1
                    continue

                receipts = _parse_money(fields[WEBK_TTL_RECEIPTS])
                disbursements = _parse_money(fields[WEBK_TTL_DISB])
                coh = _parse_money(fields[WEBK_COH_COP])
                cvg_end = _parse_date(fields[WEBK_CVG_END_DT])

                # Update only — committee row must already exist (not terminated)
                cur = conn.execute(
                    """
                    UPDATE committees
                       SET total_receipts = ?,
                           total_disbursements = ?,
                           cash_on_hand = ?,
                           coverage_end_date = ?,
                           updated_at = CURRENT_TIMESTAMP
                     WHERE committee_id = ?
                    """,
                    (receipts, disbursements, coh, cvg_end, committee_id),
                )
                if cur.rowcount > 0:
                    updated += 1
                else:
                    missing += 1

                if updated % 2000 == 0 and updated:
                    conn.commit()

    conn.commit()
    return {"updated": updated, "skipped": skipped, "missing": missing}


# ---------------------------------------------------------------------------
# Phase 2: Candidate JOIN
# ---------------------------------------------------------------------------


def import_from_candidates(conn: sqlite3.Connection, cycle: int) -> int:
    """Phase 2: Copy candidate financial data into their principal committee.

    Only updates committees that don't already have receipts populated, so
    this won't clobber data set by Phase 1.
    """
    cur = conn.execute(
        """
        UPDATE committees
           SET total_receipts = sub.total_receipts,
               total_disbursements = sub.total_disbursements,
               cash_on_hand = sub.cash_on_hand,
               coverage_end_date = sub.coverage_end_date,
               updated_at = CURRENT_TIMESTAMP
          FROM (
              SELECT principal_committee_id,
                     total_receipts,
                     total_disbursements,
                     cash_on_hand,
                     coverage_end_date
                FROM candidates
               WHERE election_cycle = ?
                 AND principal_committee_id IS NOT NULL
                 AND total_receipts IS NOT NULL
          ) AS sub
         WHERE committees.committee_id = sub.principal_committee_id
           AND committees.total_receipts IS NULL
        """,
        (cycle,),
    )
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# Phase 3: OpenFEC API fallback
# ---------------------------------------------------------------------------


def api_get(
    endpoint: str, params: dict, api_key: str, retries: int = 4
) -> dict | None:
    """GET an OpenFEC endpoint with exponential backoff."""
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
                wait = 2 ** (attempt + 2)  # 4, 8, 16, 32
                print(f"      Rate limited (429), waiting {wait}s...")
                time.sleep(wait)
            elif e.code == 404:
                return None
            else:
                if attempt == retries - 1:
                    print(f"      HTTP {e.code}: {e.reason}")
                    return None
                time.sleep(2 ** attempt)
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError) as e:
            if attempt == retries - 1:
                print(f"      Final API error: {e}")
                return None
            wait = 2 ** (attempt + 1)
            time.sleep(wait)

    return None


def fetch_committee_totals(committee_id: str, cycle: int, api_key: str) -> dict | None:
    """Hit /committee/{cid}/totals/?cycle=YYYY for one committee."""
    data = api_get(
        f"/committee/{committee_id}/totals/",
        {"cycle": str(cycle), "per_page": "1", "sort": "-coverage_end_date"},
        api_key,
    )
    if not data or not data.get("results"):
        return None
    t = data["results"][0]
    return {
        "total_receipts": t.get("receipts"),
        "total_disbursements": t.get("disbursements"),
        "cash_on_hand": t.get("last_cash_on_hand_end_period")
            or t.get("cash_on_hand_end_period"),
        "coverage_end_date": (t.get("coverage_end_date") or "")[:10] or None,
    }


def enrich_via_api(
    conn: sqlite3.Connection,
    cycle: int,
    api_key: str,
    limit: int,
) -> dict:
    """Phase 3: Fetch financials for the highest-priority committees still NULL.

    Priority order:
      1. Leadership PACs (committee_designation = 'D')
      2. Authorized/principal candidate committees ('A', 'P')
      3. Other PAC types
    """
    rows = conn.execute(
        """
        SELECT committee_id, committee_name, committee_designation
          FROM committees
         WHERE total_receipts IS NULL
         ORDER BY CASE committee_designation
                    WHEN 'D' THEN 1
                    WHEN 'P' THEN 2
                    WHEN 'A' THEN 3
                    WHEN 'B' THEN 4
                    WHEN 'J' THEN 5
                    WHEN 'U' THEN 6
                    ELSE 7
                  END,
                  committee_name
         LIMIT ?
        """,
        (limit,),
    ).fetchall()

    print(f"  Fetching financials for {len(rows)} committees via OpenFEC API...")

    updated = 0
    not_found = 0
    failed = 0

    for i, (cid, name, dsgn) in enumerate(rows, 1):
        if i == 1 or i % 25 == 0 or i == len(rows):
            print(
                f"    [{i}/{len(rows)}] updated={updated} 404={not_found} fail={failed}"
            )

        totals = fetch_committee_totals(cid, cycle, api_key)
        time.sleep(SLEEP_BETWEEN_CALLS)

        if totals is None:
            not_found += 1
            continue

        if (
            totals["total_receipts"] is None
            and totals["total_disbursements"] is None
            and totals["cash_on_hand"] is None
        ):
            failed += 1
            continue

        conn.execute(
            """
            UPDATE committees
               SET total_receipts = ?,
                   total_disbursements = ?,
                   cash_on_hand = ?,
                   coverage_end_date = ?,
                   updated_at = CURRENT_TIMESTAMP
             WHERE committee_id = ?
            """,
            (
                totals["total_receipts"],
                totals["total_disbursements"],
                totals["cash_on_hand"],
                totals["coverage_end_date"],
                cid,
            ),
        )
        updated += 1

        if updated % 25 == 0:
            conn.commit()

    conn.commit()
    return {"updated": updated, "not_found": not_found, "failed": failed}


# ---------------------------------------------------------------------------
# Coverage report
# ---------------------------------------------------------------------------


def coverage_report(conn: sqlite3.Connection) -> None:
    print("\n  Coverage by committee_designation:")
    rows = conn.execute(
        """
        SELECT committee_designation,
               COUNT(*) AS total,
               SUM(CASE WHEN total_receipts IS NOT NULL THEN 1 ELSE 0 END) AS populated
          FROM committees
         GROUP BY committee_designation
         ORDER BY total DESC
        """
    ).fetchall()
    for dsgn, total, pop in rows:
        pct = (pop / total * 100) if total else 0
        print(f"    {dsgn or '(blank)':6s}  {pop:>6,} / {total:>6,}  ({pct:5.1f}%)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle", type=int, default=2026)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument(
        "--skip-bulk", action="store_true",
        help="Skip Phase 1 (PAC-Summary bulk file)",
    )
    parser.add_argument(
        "--skip-candidates", action="store_true",
        help="Skip Phase 2 (candidate JOIN)",
    )
    parser.add_argument(
        "--skip-api", action="store_true",
        help="Skip Phase 3 (OpenFEC API)",
    )
    parser.add_argument(
        "--api-limit", type=int, default=500,
        help="Max committees to fetch via API in Phase 3 (default: 500)",
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"ERROR: Database not found: {args.db}")
        return 1

    print(f"Committee financial enrichment | cycle={args.cycle} | db={args.db}")

    conn = sqlite3.connect(str(args.db))
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        # Phase 1
        if not args.skip_bulk:
            print("\n--- Phase 1: PAC-Summary bulk file ---")
            stats = import_pac_summary(conn, args.cycle)
            print(
                f"  Phase 1: updated={stats['updated']:,} "
                f"missing_in_db={stats['missing']:,} skipped={stats['skipped']:,}"
            )
        else:
            print("\n--- Phase 1: skipped via --skip-bulk ---")

        # Phase 2
        if not args.skip_candidates:
            print("\n--- Phase 2: Candidate principal committee JOIN ---")
            n = import_from_candidates(conn, args.cycle)
            print(f"  Phase 2: copied financials to {n:,} committees from candidates table")
        else:
            print("\n--- Phase 2: skipped via --skip-candidates ---")

        # Phase 3
        if not args.skip_api:
            api_key = os.environ.get("OPENFEC_API_KEY")
            if not api_key:
                print("\n--- Phase 3: SKIPPED (OPENFEC_API_KEY not set) ---")
            elif args.api_limit <= 0:
                print("\n--- Phase 3: skipped via --api-limit 0 ---")
            else:
                print(f"\n--- Phase 3: OpenFEC API (limit {args.api_limit}) ---")
                stats = enrich_via_api(conn, args.cycle, api_key, args.api_limit)
                print(
                    f"  Phase 3: updated={stats['updated']:,} "
                    f"not_found={stats['not_found']:,} failed={stats['failed']:,}"
                )
        else:
            print("\n--- Phase 3: skipped via --skip-api ---")

        coverage_report(conn)

        # Metadata
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value, updated_at) VALUES (?, ?, ?)",
            (
                "committee_financials_imported",
                datetime.now().isoformat(),
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
