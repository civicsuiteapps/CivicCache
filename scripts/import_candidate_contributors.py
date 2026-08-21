#!/usr/bin/env python3
"""
Import top PAC/Committee contributors for all FEC candidates.

Reads Committee-Contributions (pas2) ZIP files, aggregates contributions by
committee per candidate, ranks top N, and writes to candidate_top_contributors.

Unlike import_fec_summary.py (which keys on bioguide_id for sitting members),
this script keys directly on candidate_id so challengers get contributor data too.

Usage:
    python3 scripts/import_candidate_contributors.py              # Default: 2026 cycle, top 10
    python3 scripts/import_candidate_contributors.py --cycle 2026
    python3 scripts/import_candidate_contributors.py --top-n 15
"""

import argparse
import sqlite3
import zipfile
from collections import defaultdict
from pathlib import Path

try:
    from config import DB_PATH as _CFG_DB, FEC_DIR as _CFG_FEC
except ImportError:
    _CFG_DB = Path("data/civic_cache.db")
    _CFG_FEC = Path("FEC-Bulk-Data")

from utils import (
    create_connection,
    fetch_openfec_committee,
    is_unresolved_committee_name,
    upsert_committee_from_openfec,
)

DB_PATH = _CFG_DB
FEC_DIR = _CFG_FEC
COMMITTEE_CONTRIB_DIR = FEC_DIR / "Contributions-from-Committees"


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create the candidate_top_contributors table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS candidate_top_contributors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT NOT NULL,
            election_cycle INTEGER NOT NULL,
            rank INTEGER NOT NULL,
            committee_id TEXT NOT NULL,
            committee_name TEXT NOT NULL,
            committee_type TEXT,
            total_amount REAL NOT NULL,
            transaction_count INTEGER DEFAULT 0,
            UNIQUE(candidate_id, election_cycle, rank),
            FOREIGN KEY (candidate_id, election_cycle) REFERENCES candidates(candidate_id, election_cycle)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cand_top_contrib "
        "ON candidate_top_contributors(candidate_id, election_cycle)"
    )
    conn.commit()


def find_zip(cycle: int) -> Path | None:
    """Locate the Committee-Contributions ZIP for the given cycle.

    Checks for the short FEC name (pas2XX.zip) first, then the long name.
    """
    # Short name: pas2 + last two digits of cycle
    short = COMMITTEE_CONTRIB_DIR / f"pas2{cycle % 100:02d}.zip"
    if short.exists():
        return short
    # Long name
    long = COMMITTEE_CONTRIB_DIR / f"Committee-Contributions_{cycle}.zip"
    if long.exists():
        return long
    return None


def load_committee_info(conn: sqlite3.Connection) -> dict[str, tuple[str, str | None]]:
    """Load committee names and types from the committees table.

    Returns {committee_id: (committee_name, committee_type)}.
    """
    rows = conn.execute(
        "SELECT committee_id, committee_name, committee_type FROM committees"
    ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def load_known_candidates(conn: sqlite3.Connection, cycle: int) -> set[str]:
    """Return the set of candidate_ids present in the candidates table for this cycle."""
    rows = conn.execute(
        "SELECT candidate_id FROM candidates WHERE election_cycle = ?",
        (cycle,),
    ).fetchall()
    return {r[0] for r in rows}


def aggregate_contributions(
    zip_path: Path, known_candidates: set[str]
) -> dict[str, dict[str, tuple[float, int]]]:
    """Read a Committee-Contributions ZIP and aggregate by candidate + committee.

    Returns {candidate_id: {committee_id: (total_amount, transaction_count)}}.
    """
    # {cand_id: {cmte_id: [sum, count]}}
    agg: dict[str, dict[str, list[float | int]]] = defaultdict(
        lambda: defaultdict(lambda: [0.0, 0])
    )

    print(f"  Reading {zip_path.name}...")
    line_count = 0
    matched = 0

    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if not name.endswith(".txt"):
                continue
            with zf.open(name) as f:
                for raw_line in f:
                    line_count += 1
                    if line_count % 500_000 == 0:
                        print(
                            f"    {line_count:,} lines, {matched:,} matched..."
                        )

                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if not line:
                        continue

                    fields = line.split("|")
                    if len(fields) < 17:
                        continue

                    cmte_id = fields[0].strip()
                    cand_id = fields[16].strip()

                    if cand_id not in known_candidates:
                        continue

                    try:
                        amount = float(fields[14].strip())
                    except (ValueError, IndexError):
                        continue

                    bucket = agg[cand_id][cmte_id]
                    bucket[0] += amount
                    bucket[1] += 1
                    matched += 1

    print(f"  {line_count:,} lines, {matched:,} matched to known candidates")

    # Convert to immutable tuples
    return {
        cand: {cmte: (vals[0], vals[1]) for cmte, vals in cmtes.items()}
        for cand, cmtes in agg.items()
    }


def resolve_missing_committees(
    conn: sqlite3.Connection,
    agg: dict[str, dict[str, tuple[float, int]]],
    cmte_info: dict[str, tuple[str, str | None]],
    top_n: int,
) -> int:
    """Resolve committees absent from the local master via the FEC API.

    Brand-new committees (common early in a cycle) lag the FEC bulk master
    file by days to weeks; without this, their rows would ship with the raw
    committee ID as the display name. Resolved committees are also upserted
    into the committees table so detail lookups work offline.

    Mutates cmte_info in place. Returns the number resolved.
    """
    missing: set[str] = set()
    for committees in agg.values():
        ranked = sorted(committees.items(), key=lambda x: x[1][0], reverse=True)[:top_n]
        for cmte_id, _ in ranked:
            existing = cmte_info.get(cmte_id)
            if existing is None or is_unresolved_committee_name(existing[0], cmte_id):
                missing.add(cmte_id)

    if not missing:
        return 0

    print(f"\n  {len(missing)} ranked committee(s) not in local master; resolving via FEC API...")
    resolved = 0
    for cmte_id in sorted(missing):
        info = fetch_openfec_committee(cmte_id)
        if info and info.get("name"):
            cmte_info[cmte_id] = (info["name"], info.get("committee_type"))
            upsert_committee_from_openfec(conn, cmte_id, info)
            resolved += 1
            print(f"    {cmte_id} -> {info['name']}")
        else:
            print(f"    {cmte_id} -> UNRESOLVED (not found via API)")
    conn.commit()
    print(f"  Resolved {resolved}/{len(missing)} via FEC API")
    return resolved


def validate_committee_names(conn: sqlite3.Connection, cycle: int) -> int:
    """Data gate: no row may ship with an empty or ID-shaped committee_name."""
    rows = conn.execute(
        """
        SELECT candidate_id, rank, committee_id, committee_name
        FROM candidate_top_contributors
        WHERE election_cycle = ?
          AND (committee_name IS NULL OR committee_name = ''
               OR committee_name = committee_id
               OR committee_name GLOB 'C[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]')
        """,
        (cycle,),
    ).fetchall()
    if rows:
        print(f"\n  VALIDATION FAILED: {len(rows)} row(s) with unresolved committee names:")
        for cand_id, rank, cmte_id, name in rows[:20]:
            print(f"    {cand_id} rank {rank}: {cmte_id} -> '{name}'")
        if len(rows) > 20:
            print(f"    ... and {len(rows) - 20} more")
    return len(rows)


def write_top_contributors(
    conn: sqlite3.Connection,
    agg: dict[str, dict[str, tuple[float, int]]],
    cmte_info: dict[str, tuple[str, str | None]],
    cycle: int,
    top_n: int,
) -> int:
    """Rank and write top N contributors per candidate. Returns rows written."""

    # Clear existing data for this cycle
    conn.execute(
        "DELETE FROM candidate_top_contributors WHERE election_cycle = ?",
        (cycle,),
    )

    total = 0
    for cand_id, committees in agg.items():
        # Sort by total_amount descending
        ranked = sorted(committees.items(), key=lambda x: x[1][0], reverse=True)[:top_n]

        for rank, (cmte_id, (amount, count)) in enumerate(ranked, 1):
            name, ctype = cmte_info.get(cmte_id, (cmte_id, None))
            conn.execute(
                """
                INSERT OR REPLACE INTO candidate_top_contributors
                (candidate_id, election_cycle, rank, committee_id,
                 committee_name, committee_type, total_amount, transaction_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (cand_id, cycle, rank, cmte_id, name, ctype, amount, count),
            )
            total += 1

        if total % 5000 == 0:
            conn.commit()

    conn.commit()
    return total


def update_metadata(conn: sqlite3.Connection, cycle: int, row_count: int) -> None:
    """Record import metadata."""
    conn.execute(
        """
        INSERT OR REPLACE INTO metadata (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        """,
        (f"candidate_contributors_{cycle}_count", str(row_count)),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO metadata (key, value, updated_at)
        VALUES ('candidate_contributors_import_date', datetime('now'), CURRENT_TIMESTAMP)
        """,
    )
    conn.commit()


def verify(conn: sqlite3.Connection, cycle: int) -> None:
    """Print verification stats."""
    print("\n" + "=" * 60)
    print("VERIFICATION")
    print("=" * 60)

    row = conn.execute(
        "SELECT COUNT(*) FROM candidate_top_contributors WHERE election_cycle = ?",
        (cycle,),
    ).fetchone()
    print(f"  Total rows (cycle {cycle}): {row[0]:,}")

    row = conn.execute(
        "SELECT COUNT(DISTINCT candidate_id) FROM candidate_top_contributors WHERE election_cycle = ?",
        (cycle,),
    ).fetchone()
    print(f"  Distinct candidates: {row[0]:,}")

    # Sample: show top 5 candidates by their #1 contributor amount
    print(f"\n  Top 5 candidates by largest single committee contribution:")
    rows = conn.execute(
        """
        SELECT ctc.candidate_id, c.candidate_name, ctc.committee_name, ctc.total_amount
        FROM candidate_top_contributors ctc
        JOIN candidates c ON ctc.candidate_id = c.candidate_id AND ctc.election_cycle = c.election_cycle
        WHERE ctc.election_cycle = ? AND ctc.rank = 1
        ORDER BY ctc.total_amount DESC
        LIMIT 5
        """,
        (cycle,),
    ).fetchall()
    for cand_id, cand_name, cmte_name, amount in rows:
        print(f"    {cand_name} ({cand_id}): ${amount:,.0f} from {cmte_name}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import top PAC/committee contributors for all candidates"
    )
    parser.add_argument(
        "--cycle", type=int, default=2026, help="Election cycle (default: 2026)"
    )
    parser.add_argument(
        "--top-n", type=int, default=10, help="Number of top contributors per candidate (default: 10)"
    )
    args = parser.parse_args()

    cycle = args.cycle
    top_n = args.top_n

    print("=" * 60)
    print(f"CANDIDATE TOP CONTRIBUTORS IMPORT  (cycle {cycle}, top {top_n})")
    print("=" * 60)

    zip_path = find_zip(cycle)
    if not zip_path:
        print(f"\nERROR: No Committee-Contributions ZIP found for cycle {cycle}")
        print(f"  Looked in: {COMMITTEE_CONTRIB_DIR}")
        return 1

    try:
        conn = create_connection(use_wal=True)
        ensure_table(conn)

        # Load reference data
        print("\nLoading reference data...")
        cmte_info = load_committee_info(conn)
        print(f"  {len(cmte_info):,} committees loaded")

        known_candidates = load_known_candidates(conn, cycle)
        print(f"  {len(known_candidates):,} candidates for cycle {cycle}")

        if not known_candidates:
            print("\nWARNING: No candidates found for this cycle. Run import_fec_candidates.py first.")
            conn.close()
            return 1

        # Aggregate contributions from ZIP
        print(f"\nAggregating contributions from {zip_path.name}...")
        agg = aggregate_contributions(zip_path, known_candidates)
        print(f"  {len(agg):,} candidates have committee contributions")

        # Resolve committees missing from the local master via FEC API
        resolve_missing_committees(conn, agg, cmte_info, top_n)

        # Write top contributors
        print(f"\nWriting top {top_n} contributors per candidate...")
        row_count = write_top_contributors(conn, agg, cmte_info, cycle, top_n)
        print(f"  Wrote {row_count:,} rows")

        # Metadata + verification
        update_metadata(conn, cycle, row_count)
        verify(conn, cycle)

        # Data gate: raw committee IDs must never ship as display names
        bad_rows = validate_committee_names(conn, cycle)

        conn.close()

        if bad_rows:
            print("\n" + "=" * 60)
            print(f"IMPORT FAILED VALIDATION: {bad_rows} unresolved committee name(s)")
            print("=" * 60)
            return 1

        print("\n" + "=" * 60)
        print("IMPORT COMPLETE")
        print("=" * 60)
        return 0

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
