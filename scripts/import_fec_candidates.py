#!/usr/bin/env python3
"""
Import FEC candidate data with correct field mapping and build election races.

Replaces the broken import_fec_candidates() in build_database.py which had
shifted column mappings (office contained I/C/O status codes, etc.).

Five phases:
  1. Parse Candidate-Master files (biographical data, 15 fields)
  2. Overlay All-Candidates files (financial data, 30 fields)
  2.5. Deduplicate candidates (merge same-name filings under multiple FEC IDs)
  3. Build election_races from grouped candidates + congress_members linkage
  4. Populate race_candidates denormalized join table

Usage:
    python3 scripts/import_fec_candidates.py
    python3 scripts/import_fec_candidates.py --cycles 2024,2026
    python3 scripts/import_fec_candidates.py --current-only   # 2026 cycle only
"""

import argparse
import math
import re
import sqlite3
import sys
from pathlib import Path

from config import DB_PATH, FEC_DIR

# CivicScope only uses current election data — skip historical cycles
MIN_ELECTION_CYCLE = 2024

# ---------------------------------------------------------------------------
# Cycle derivation
# ---------------------------------------------------------------------------


def derive_cycle_from_filename(filename: str) -> int:
    """Derive the election cycle from a bulk-data filename.

    Take the higher year from the filename, round up to even.
    Examples:
        All-Candidates_2024-2025.txt -> 2025 -> 2026
        Candidate-Master_2025-2026.txt -> 2026 -> 2026
        Candidate-Master_2023-2024.txt -> 2024 -> 2024
    """
    m = re.search(r"(\d{4})-(\d{4})", filename)
    if not m:
        raise ValueError(f"Cannot derive cycle from filename: {filename}")
    higher_year = int(m.group(2))
    return higher_year if higher_year % 2 == 0 else higher_year + 1


# ---------------------------------------------------------------------------
# Phase 1: Candidate-Master (biographical)
# ---------------------------------------------------------------------------

# Candidate-Master fields (pipe-delimited, 15 fields):
#  0: CAND_ID
#  1: CAND_NAME
#  2: CAND_PTY_AFFILIATION
#  3: CAND_ELECTION_YR
#  4: CAND_OFFICE_ST
#  5: CAND_OFFICE (H/S/P)
#  6: CAND_OFFICE_DISTRICT
#  7: CAND_ICI (I/C/O)
#  8: CAND_STATUS (C/F/N/P)
#  9: CAND_PCC (principal committee ID)
# 10: CAND_ST1 (mailing address 1)
# 11: CAND_ST2 (mailing address 2)
# 12: CAND_CITY
# 13: CAND_ST (mailing state)
# 14: CAND_ZIP


def import_candidate_master(
    conn: sqlite3.Connection, cycles: set[int] | None
) -> int:
    """Phase 1: Import Candidate-Master files."""
    cm_dir = FEC_DIR / "Candidate-Master"
    if not cm_dir.exists():
        print(f"  Candidate-Master directory not found: {cm_dir}")
        return 0

    total = 0
    for fpath in sorted(cm_dir.glob("*.txt")):
        cycle = derive_cycle_from_filename(fpath.name)
        if cycles and cycle not in cycles:
            continue
        if cycle < MIN_ELECTION_CYCLE:
            continue

        count = 0
        print(f"  Phase 1: {fpath.name} (cycle {cycle})")

        with open(fpath, "r", encoding="latin-1") as f:
            for line in f:
                fields = line.strip().split("|")
                if len(fields) < 10:
                    continue

                cand_id = fields[0].strip()
                if not cand_id:
                    continue

                try:
                    election_year = int(fields[3]) if fields[3].strip() else None
                except ValueError:
                    election_year = None

                conn.execute(
                    """
                    INSERT OR REPLACE INTO candidates (
                        candidate_id, election_cycle, candidate_name,
                        office, office_state, office_district,
                        party_code, incumbent_challenger_status,
                        candidate_status, election_year,
                        principal_committee_id,
                        mailing_city, mailing_state, mailing_zip
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cand_id,
                        cycle,
                        fields[1].strip() if len(fields) > 1 else None,
                        fields[5].strip() if len(fields) > 5 else None,  # H/S/P
                        fields[4].strip() if len(fields) > 4 else None,  # state
                        fields[6].strip() if len(fields) > 6 else None,  # district
                        fields[2].strip() if len(fields) > 2 else None,  # party
                        fields[7].strip() if len(fields) > 7 else None,  # I/C/O
                        fields[8].strip() if len(fields) > 8 else None,  # status
                        election_year,
                        fields[9].strip() if len(fields) > 9 else None,
                        fields[12].strip() if len(fields) > 12 else None,
                        fields[13].strip() if len(fields) > 13 else None,
                        fields[14].strip() if len(fields) > 14 else None,
                    ),
                )
                count += 1

        conn.commit()
        print(f"    Imported {count:,} candidates from {fpath.name}")
        total += count

    return total


# ---------------------------------------------------------------------------
# Phase 2: All-Candidates (financial overlay)
# ---------------------------------------------------------------------------

# All-Candidates fields (pipe-delimited, 30 fields):
#  0: CAND_ID
#  1: CAND_NAME
#  2: CAND_ICI
#  3: PTY_CD (single-char party code)
#  4: CAND_PTY_AFFILIATION
#  5: TTL_RECEIPTS
#  6: TRANS_FROM_AUTH
#  7: TTL_DISB
#  8: TRANS_TO_AUTH
#  9: COH_BOP
# 10: COH_COP (cash on hand)
# 11: CAND_CONTRIB
# 12: CAND_LOANS
# 13: OTHER_LOANS
# 14: CAND_LOAN_REPAY
# 15: OTHER_LOAN_REPAY
# 16: DEBTS_OWED_BY
# 17: TTL_INDIV_CONTRIB
# 18: CAND_OFFICE_ST
# 19: CAND_OFFICE_DISTRICT
# 20: SPEC_ELECTION
# 21: PRIM_ELECTION
# 22: RUN_ELECTION
# 23: GEN_ELECTION
# 24: GEN_ELECTION_PERCENT
# 25: OTHER_POL_CMTE_CONTRIB
# 26: POL_PTY_CONTRIB
# 27: CVG_END_DT
# 28: INDIV_REFUNDS
# 29: CMTE_REFUNDS


def _safe_float(val: str) -> float | None:
    val = val.strip()
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        return None


def import_all_candidates(
    conn: sqlite3.Connection, cycles: set[int] | None
) -> int:
    """Phase 2: Overlay financial data from All-Candidates files."""
    ac_dir = FEC_DIR / "All-Candidates"
    if not ac_dir.exists():
        print(f"  All-Candidates directory not found: {ac_dir}")
        return 0

    total = 0
    for fpath in sorted(ac_dir.glob("*.txt")):
        cycle = derive_cycle_from_filename(fpath.name)
        if cycles and cycle not in cycles:
            continue
        if cycle < MIN_ELECTION_CYCLE:
            continue

        count = 0
        inserted = 0
        print(f"  Phase 2: {fpath.name} (cycle {cycle})")

        with open(fpath, "r", encoding="latin-1") as f:
            for line in f:
                fields = line.strip().split("|")
                if len(fields) < 18:
                    continue

                cand_id = fields[0].strip()
                if not cand_id:
                    continue

                # Check if candidate already exists from Phase 1
                row = conn.execute(
                    "SELECT 1 FROM candidates WHERE candidate_id = ? AND election_cycle = ?",
                    (cand_id, cycle),
                ).fetchone()

                if row:
                    # Update with financial data
                    conn.execute(
                        """
                        UPDATE candidates SET
                            total_receipts = ?,
                            total_disbursements = ?,
                            cash_on_hand = ?,
                            candidate_contributions = ?,
                            candidate_loans = ?,
                            debts_owed = ?,
                            total_individual_contributions = ?,
                            pac_contributions = ?,
                            party_contributions = ?,
                            coverage_end_date = ?
                        WHERE candidate_id = ? AND election_cycle = ?
                        """,
                        (
                            _safe_float(fields[5]),   # TTL_RECEIPTS
                            _safe_float(fields[7]),   # TTL_DISB
                            _safe_float(fields[10]),  # COH_COP
                            _safe_float(fields[11]),  # CAND_CONTRIB
                            _safe_float(fields[12]),  # CAND_LOANS
                            _safe_float(fields[16]),  # DEBTS_OWED_BY
                            _safe_float(fields[17]),  # TTL_INDIV_CONTRIB
                            _safe_float(fields[25]) if len(fields) > 25 else None,
                            _safe_float(fields[26]) if len(fields) > 26 else None,
                            fields[27].strip() if len(fields) > 27 else None,
                            cand_id,
                            cycle,
                        ),
                    )
                    count += 1
                else:
                    # Insert candidate from All-Candidates only (no Candidate-Master)
                    # Use backup fields for office info
                    ici = fields[2].strip() if len(fields) > 2 else None
                    party = fields[4].strip() if len(fields) > 4 else None
                    office_state = fields[18].strip() if len(fields) > 18 else None
                    office_district = fields[19].strip() if len(fields) > 19 else None

                    # Derive office from candidate ID prefix: H=House, S=Senate, P=President
                    office = None
                    if cand_id and len(cand_id) > 0:
                        prefix = cand_id[0].upper()
                        if prefix in ("H", "S", "P"):
                            office = prefix

                    conn.execute(
                        """
                        INSERT OR IGNORE INTO candidates (
                            candidate_id, election_cycle, candidate_name,
                            office, office_state, office_district,
                            party_code, incumbent_challenger_status,
                            total_receipts, total_disbursements, cash_on_hand,
                            candidate_contributions, candidate_loans,
                            debts_owed, total_individual_contributions,
                            pac_contributions, party_contributions,
                            coverage_end_date
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            cand_id,
                            cycle,
                            fields[1].strip() if len(fields) > 1 else None,
                            office,
                            office_state,
                            office_district,
                            party,
                            ici,
                            _safe_float(fields[5]),
                            _safe_float(fields[7]),
                            _safe_float(fields[10]),
                            _safe_float(fields[11]),
                            _safe_float(fields[12]),
                            _safe_float(fields[16]),
                            _safe_float(fields[17]),
                            _safe_float(fields[25]) if len(fields) > 25 else None,
                            _safe_float(fields[26]) if len(fields) > 26 else None,
                            fields[27].strip() if len(fields) > 27 else None,
                        ),
                    )
                    inserted += 1

        conn.commit()
        print(f"    Updated {count:,}, inserted {inserted:,} from {fpath.name}")
        total += count + inserted

    return total


# ---------------------------------------------------------------------------
# Phase 2.5: Deduplicate candidates
# ---------------------------------------------------------------------------


def deduplicate_candidates(
    conn: sqlite3.Connection, cycles: set[int] | None
) -> tuple[int, int]:
    """Phase 2.5: Merge duplicate candidates sharing the same name/race/cycle.

    The same person can file under multiple FEC candidate IDs for the same
    race and cycle.  For each group sharing (candidate_name, office,
    office_state, office_district, party_code, election_cycle):

      1. Keep the candidate_id with the highest total_receipts (ties broken
         by most recent coverage_end_date, then alphabetical candidate_id).
      2. Merge financial columns into the canonical row (sums for flows,
         max for cash_on_hand, latest coverage_end_date).
      3. Delete the non-canonical rows.

    Returns (groups_merged, rows_removed).
    """
    cycle_filter = ""
    params: list = []
    if cycles:
        placeholders = ",".join("?" * len(cycles))
        cycle_filter = f"AND election_cycle IN ({placeholders})"
        params = list(cycles)

    # 1. Find duplicate groups
    dup_groups = conn.execute(
        f"""
        SELECT candidate_name, office, office_state, office_district,
               party_code, election_cycle
        FROM candidates
        WHERE candidate_name IS NOT NULL
              {cycle_filter}
        GROUP BY candidate_name, office, office_state, office_district,
                 party_code, election_cycle
        HAVING COUNT(*) > 1
        """,
        params,
    ).fetchall()

    if not dup_groups:
        print("  No duplicate candidates found.")
        return (0, 0)

    groups_merged = 0
    rows_removed = 0

    for group in dup_groups:
        name, office, state, district, party, cycle = group

        # 2. Fetch all candidates in this group, ordered for canonical pick
        #    Handle NULLs in district comparison
        if district is None or district == "":
            members = conn.execute(
                """
                SELECT candidate_id, total_receipts, total_disbursements,
                       cash_on_hand, total_individual_contributions,
                       pac_contributions, party_contributions,
                       candidate_contributions, candidate_loans,
                       debts_owed, coverage_end_date
                FROM candidates
                WHERE candidate_name = ? AND office IS ? AND office_state IS ?
                      AND (office_district IS NULL OR office_district = '')
                      AND party_code IS ? AND election_cycle = ?
                ORDER BY total_receipts DESC NULLS LAST,
                         coverage_end_date DESC NULLS LAST,
                         candidate_id ASC
                """,
                (name, office, state, party, cycle),
            ).fetchall()
        else:
            members = conn.execute(
                """
                SELECT candidate_id, total_receipts, total_disbursements,
                       cash_on_hand, total_individual_contributions,
                       pac_contributions, party_contributions,
                       candidate_contributions, candidate_loans,
                       debts_owed, coverage_end_date
                FROM candidates
                WHERE candidate_name = ? AND office IS ? AND office_state IS ?
                      AND office_district = ?
                      AND party_code IS ? AND election_cycle = ?
                ORDER BY total_receipts DESC NULLS LAST,
                         coverage_end_date DESC NULLS LAST,
                         candidate_id ASC
                """,
                (name, office, state, district, party, cycle),
            ).fetchall()

        if len(members) < 2:
            continue  # shouldn't happen, but be safe

        canonical_id = members[0][0]
        duplicates = members[1:]

        # 3. Merge financials: sum flow columns, max cash_on_hand, latest coverage_end_date
        sum_cols = {
            "total_receipts": 0.0,
            "total_disbursements": 0.0,
            "total_individual_contributions": 0.0,
            "pac_contributions": 0.0,
            "party_contributions": 0.0,
            "candidate_contributions": 0.0,
            "candidate_loans": 0.0,
            "debts_owed": 0.0,
        }
        max_coh = None
        latest_coverage = None

        for member in members:
            # Indices: 1=receipts, 2=disbursements, 3=coh, 4=indiv, 5=pac,
            #          6=party, 7=cand_contrib, 8=cand_loans, 9=debts, 10=coverage
            vals = {
                "total_receipts": member[1],
                "total_disbursements": member[2],
                "total_individual_contributions": member[4],
                "pac_contributions": member[5],
                "party_contributions": member[6],
                "candidate_contributions": member[7],
                "candidate_loans": member[8],
                "debts_owed": member[9],
            }
            for col, v in vals.items():
                if v is not None:
                    sum_cols[col] += v

            coh = member[3]
            if coh is not None and (max_coh is None or coh > max_coh):
                max_coh = coh

            cov = member[10]
            if cov is not None and (latest_coverage is None or cov > latest_coverage):
                latest_coverage = cov

        # Convert zero sums back to None if all source values were None
        for col in sum_cols:
            idx_map = {
                "total_receipts": 1,
                "total_disbursements": 2,
                "total_individual_contributions": 4,
                "pac_contributions": 5,
                "party_contributions": 6,
                "candidate_contributions": 7,
                "candidate_loans": 8,
                "debts_owed": 9,
            }
            if all(m[idx_map[col]] is None for m in members):
                sum_cols[col] = None

        # 4. Update canonical row with merged financials
        conn.execute(
            """
            UPDATE candidates SET
                total_receipts = ?,
                total_disbursements = ?,
                cash_on_hand = ?,
                total_individual_contributions = ?,
                pac_contributions = ?,
                party_contributions = ?,
                candidate_contributions = ?,
                candidate_loans = ?,
                debts_owed = ?,
                coverage_end_date = ?
            WHERE candidate_id = ? AND election_cycle = ?
            """,
            (
                sum_cols["total_receipts"],
                sum_cols["total_disbursements"],
                max_coh,
                sum_cols["total_individual_contributions"],
                sum_cols["pac_contributions"],
                sum_cols["party_contributions"],
                sum_cols["candidate_contributions"],
                sum_cols["candidate_loans"],
                sum_cols["debts_owed"],
                latest_coverage,
                canonical_id,
                cycle,
            ),
        )

        # 5. Delete duplicate rows
        dup_ids = [d[0] for d in duplicates]
        for dup_id in dup_ids:
            conn.execute(
                "DELETE FROM candidates WHERE candidate_id = ? AND election_cycle = ?",
                (dup_id, cycle),
            )

        groups_merged += 1
        rows_removed += len(dup_ids)

    conn.commit()
    return (groups_merged, rows_removed)


# ---------------------------------------------------------------------------
# Phase 3: Build election_races
# ---------------------------------------------------------------------------


def build_election_races(conn: sqlite3.Connection, cycles: set[int] | None) -> int:
    """Phase 3: Group candidates into election races and link incumbents."""
    print("\n  Phase 3: Building election races...")

    # Clear existing races for these cycles
    if cycles:
        for cycle in cycles:
            conn.execute("DELETE FROM race_candidates WHERE election_cycle = ?", (cycle,))
            conn.execute("DELETE FROM election_races WHERE election_cycle = ?", (cycle,))
    else:
        conn.execute("DELETE FROM race_candidates")
        conn.execute("DELETE FROM election_races")

    # Build a lookup of congress_members by fec_id for incumbent matching
    incumbent_lookup = {}
    try:
        rows = conn.execute(
            """
            SELECT id, fec_id, current_party, chamber, state_code, district
            FROM congress_members
            WHERE fec_id IS NOT NULL AND fec_id != ''
            """
        ).fetchall()
        for row in rows:
            incumbent_lookup[row[1]] = {
                "bioguide_id": row[0],
                "party": row[2],
                "chamber": row[3],
                "state_code": row[4],
                "district": row[5],
            }
    except sqlite3.OperationalError:
        print("    Warning: congress_members table not available for incumbent matching")

    # Get distinct race keys from candidates
    cycle_filter = ""
    if cycles:
        placeholders = ",".join("?" * len(cycles))
        cycle_filter = f"AND election_cycle IN ({placeholders})"

    query = f"""
        SELECT election_cycle, office, office_state, office_district,
               COUNT(*) as num_candidates
        FROM candidates
        WHERE office IS NOT NULL AND office != ''
              AND office_state IS NOT NULL AND office_state != ''
              {cycle_filter}
        GROUP BY election_cycle, office, office_state, office_district
    """

    params = list(cycles) if cycles else []
    race_rows = conn.execute(query, params).fetchall()

    race_count = 0
    for row in race_rows:
        cycle, office, state, district, num_candidates = row

        # Skip non-standard offices
        if office not in ("H", "S", "P"):
            continue

        # Build race_id
        if office == "H" and district:
            race_id = f"{cycle}-{office}-{state}-{district.zfill(2)}"
        elif office == "S":
            race_id = f"{cycle}-{office}-{state}"
        elif office == "P":
            race_id = f"{cycle}-{office}"
        else:
            race_id = f"{cycle}-{office}-{state}"

        # Find incumbent in this race
        incumbent_bioguide = None
        incumbent_fec_id = None
        incumbent_party = None
        challenger_count = 0
        open_seat = 0

        # Get candidates for this race
        cand_query = f"""
            SELECT candidate_id, candidate_name, party_code,
                   incumbent_challenger_status, total_receipts
            FROM candidates
            WHERE election_cycle = ? AND office = ? AND office_state = ?
                  AND (office_district = ? OR (? IS NULL AND office_district IS NULL)
                       OR office_district = '' OR office_district = '00')
        """
        # For Senate/President, match regardless of district
        if office in ("S", "P"):
            cand_query = f"""
                SELECT candidate_id, candidate_name, party_code,
                       incumbent_challenger_status, total_receipts
                FROM candidates
                WHERE election_cycle = ? AND office = ? AND office_state = ?
            """
            race_candidates = conn.execute(
                cand_query, (cycle, office, state)
            ).fetchall()
        else:
            race_candidates = conn.execute(
                cand_query, (cycle, office, state, district, district)
            ).fetchall()

        has_incumbent = False
        for cand in race_candidates:
            cand_id, cand_name, party, ici, receipts = cand

            # Check if this candidate is a known incumbent
            if cand_id in incumbent_lookup:
                incumbent_bioguide = incumbent_lookup[cand_id]["bioguide_id"]
                incumbent_fec_id = cand_id
                incumbent_party = incumbent_lookup[cand_id]["party"]
                has_incumbent = True
            elif ici == "I":
                incumbent_fec_id = cand_id
                incumbent_party = party
                has_incumbent = True
                challenger_count = num_candidates - 1
            elif ici == "C":
                challenger_count += 1

        if not has_incumbent:
            # Check if any candidate is marked as Open seat
            open_markers = [c for c in race_candidates if c[3] == "O"]
            if open_markers:
                open_seat = 1
            challenger_count = num_candidates

        if has_incumbent:
            challenger_count = num_candidates - 1

        conn.execute(
            """
            INSERT OR REPLACE INTO election_races (
                race_id, election_cycle, office, state, district,
                incumbent_bioguide_id, incumbent_fec_id, incumbent_party,
                total_candidates, challenger_count, open_seat
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                race_id,
                cycle,
                office,
                state,
                district if office == "H" else None,
                incumbent_bioguide,
                incumbent_fec_id,
                incumbent_party,
                num_candidates,
                challenger_count,
                open_seat,
            ),
        )
        race_count += 1

    conn.commit()
    print(f"    Created {race_count:,} election races")
    return race_count


# ---------------------------------------------------------------------------
# Phase 4: Populate race_candidates
# ---------------------------------------------------------------------------


def populate_race_candidates(conn: sqlite3.Connection, cycles: set[int] | None) -> int:
    """Phase 4: Denormalized join table for fast app queries."""
    print("\n  Phase 4: Populating race_candidates...")

    cycle_filter = ""
    params = []
    if cycles:
        placeholders = ",".join("?" * len(cycles))
        cycle_filter = f"WHERE election_cycle IN ({placeholders})"
        params = list(cycles)

    # For each race, insert its candidates
    races = conn.execute(
        f"SELECT race_id, election_cycle, office, state, district FROM election_races {cycle_filter}",
        params,
    ).fetchall()

    total = 0
    for race in races:
        race_id, cycle, office, state, district = race

        if office in ("S", "P"):
            candidates = conn.execute(
                """
                SELECT candidate_id, candidate_name, party_code,
                       incumbent_challenger_status, total_receipts,
                       cash_on_hand, total_individual_contributions,
                       principal_committee_id
                FROM candidates
                WHERE election_cycle = ? AND office = ? AND office_state = ?
                ORDER BY total_receipts DESC NULLS LAST
                """,
                (cycle, office, state),
            ).fetchall()
        else:
            candidates = conn.execute(
                """
                SELECT candidate_id, candidate_name, party_code,
                       incumbent_challenger_status, total_receipts,
                       cash_on_hand, total_individual_contributions,
                       principal_committee_id
                FROM candidates
                WHERE election_cycle = ? AND office = ? AND office_state = ?
                      AND (office_district = ? OR office_district = '' OR office_district = '00')
                ORDER BY total_receipts DESC NULLS LAST
                """,
                (cycle, office, state, district),
            ).fetchall()

        for cand in candidates:
            conn.execute(
                """
                INSERT OR REPLACE INTO race_candidates (
                    race_id, candidate_id, election_cycle,
                    candidate_name, party_code, incumbent_challenger_status,
                    total_receipts, cash_on_hand, total_individual_contributions,
                    principal_committee_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (race_id, cand[0], cycle, cand[1], cand[2], cand[3],
                 cand[4], cand[5], cand[6], cand[7]),
            )
            total += 1

    conn.commit()
    print(f"    Inserted {total:,} race_candidates rows")
    return total


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------


def ensure_tables(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist (idempotent)."""

    # Drop and recreate candidates (existing data is garbage)
    conn.execute("DROP TABLE IF EXISTS race_candidates")
    conn.execute("DROP TABLE IF EXISTS election_races")
    conn.execute("DROP TABLE IF EXISTS candidates_fts")
    conn.execute("DROP TABLE IF EXISTS candidates")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS candidates (
            candidate_id TEXT NOT NULL,
            election_cycle INTEGER NOT NULL,
            candidate_name TEXT NOT NULL,
            office TEXT,
            office_state TEXT,
            office_district TEXT,
            party_code TEXT,
            incumbent_challenger_status TEXT,
            candidate_status TEXT,
            election_year INTEGER,
            total_receipts REAL,
            total_disbursements REAL,
            cash_on_hand REAL,
            total_individual_contributions REAL,
            candidate_contributions REAL,
            candidate_loans REAL,
            pac_contributions REAL,
            party_contributions REAL,
            debts_owed REAL,
            coverage_end_date TEXT,
            principal_committee_id TEXT,
            mailing_city TEXT,
            mailing_state TEXT,
            mailing_zip TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (candidate_id, election_cycle)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS election_races (
            race_id TEXT PRIMARY KEY,
            election_cycle INTEGER NOT NULL,
            office TEXT NOT NULL,
            state TEXT NOT NULL,
            district TEXT,
            incumbent_bioguide_id TEXT,
            incumbent_fec_id TEXT,
            incumbent_party TEXT,
            total_candidates INTEGER DEFAULT 0,
            challenger_count INTEGER DEFAULT 0,
            open_seat INTEGER DEFAULT 0,
            rating TEXT,
            rating_source TEXT,
            rating_date TEXT,
            FOREIGN KEY (incumbent_bioguide_id) REFERENCES congress_members(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS race_candidates (
            race_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            election_cycle INTEGER NOT NULL,
            candidate_name TEXT,
            party_code TEXT,
            incumbent_challenger_status TEXT,
            total_receipts REAL,
            cash_on_hand REAL,
            total_individual_contributions REAL,
            principal_committee_id TEXT,
            image_url TEXT,
            image_blob BLOB,
            PRIMARY KEY (race_id, candidate_id),
            FOREIGN KEY (race_id) REFERENCES election_races(race_id)
        )
    """)

    # Recreate indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_office ON candidates(office)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_state ON candidates(office_state)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_party ON candidates(party_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_cycle ON candidates(election_cycle)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_cycle_office ON candidates(election_cycle, office, office_state)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_races_cycle ON election_races(election_cycle)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_races_incumbent ON election_races(incumbent_bioguide_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_race_cand_race ON race_candidates(race_id)")

    conn.commit()


def rebuild_candidates_fts(conn: sqlite3.Connection) -> None:
    """Rebuild the candidates FTS table."""
    conn.execute("DROP TABLE IF EXISTS candidates_fts")
    conn.execute("""
        CREATE VIRTUAL TABLE candidates_fts USING fts5(
            candidate_id UNINDEXED,
            election_cycle UNINDEXED,
            candidate_name,
            office_state,
            party_code
        )
    """)
    conn.execute("""
        INSERT INTO candidates_fts(candidate_id, election_cycle, candidate_name, office_state, party_code)
        SELECT candidate_id, election_cycle, candidate_name, office_state, party_code
        FROM candidates
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Import FEC candidate data")
    parser.add_argument(
        "--cycles",
        default=None,
        help=f"Comma-separated election cycles (e.g. 2024,2026). Default: all >= {MIN_ELECTION_CYCLE}.",
    )
    parser.add_argument(
        "--current-only",
        action="store_true",
        help="Only import the current (2026) cycle",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help=f"Database path (default: {DB_PATH})",
    )
    args = parser.parse_args()

    cycles = None
    if args.current_only:
        cycles = {2026}
    elif args.cycles:
        cycles = {int(c.strip()) for c in args.cycles.split(",")}

    print("=" * 60)
    print("CivicCache - FEC Candidate Import (Fixed)")
    print("=" * 60)
    print(f"  Database: {args.db}")
    print(f"  FEC data: {FEC_DIR}")
    print(f"  Cycles:   {sorted(cycles) if cycles else f'all >= {MIN_ELECTION_CYCLE}'}")

    if not args.db.exists():
        print(f"\nERROR: Database not found: {args.db}")
        return 1

    conn = sqlite3.connect(str(args.db))
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA journal_mode = WAL")

    try:
        # Ensure schema
        ensure_tables(conn)

        # Phase 1: Candidate-Master (biographical)
        print("\n--- Phase 1: Candidate-Master ---")
        cm_count = import_candidate_master(conn, cycles)

        # Phase 2: All-Candidates (financial overlay)
        print("\n--- Phase 2: All-Candidates (financial) ---")
        ac_count = import_all_candidates(conn, cycles)

        # Phase 2.5: Deduplicate candidates
        print("\n--- Phase 2.5: Deduplicate Candidates ---")
        groups, removed = deduplicate_candidates(conn, cycles)
        print(f"  Merged {groups} duplicate groups, removed {removed} rows")

        # Phase 3: Build election races
        print("\n--- Phase 3: Election Races ---")
        race_count = build_election_races(conn, cycles)

        # Phase 4: Populate race_candidates
        print("\n--- Phase 4: Race Candidates ---")
        rc_count = populate_race_candidates(conn, cycles)

        # Rebuild FTS
        print("\n  Rebuilding candidates FTS...")
        rebuild_candidates_fts(conn)

        # Update metadata
        from datetime import datetime

        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value, updated_at) VALUES (?, ?, ?)",
            ("fec_candidates_imported", datetime.now().isoformat(), datetime.now().isoformat()),
        )

        # Stats
        total_candidates = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value, updated_at) VALUES (?, ?, ?)",
            ("total_candidates", str(total_candidates), datetime.now().isoformat()),
        )
        conn.commit()

        print("\n" + "=" * 60)
        print("Import Complete")
        print("=" * 60)
        print(f"  Candidate-Master records: {cm_count:,}")
        print(f"  All-Candidates overlays:  {ac_count:,}")
        print(f"  Election races:           {race_count:,}")
        print(f"  Race-candidate entries:   {rc_count:,}")
        print(f"  Total candidates in DB:   {total_candidates:,}")

        # Quick verification
        if cycles is None or 2026 in cycles:
            sample = conn.execute(
                """
                SELECT candidate_id, candidate_name, office, office_state,
                       office_district, party_code, incumbent_challenger_status,
                       total_receipts
                FROM candidates
                WHERE election_cycle = 2026 AND office = 'H'
                ORDER BY total_receipts DESC NULLS LAST
                LIMIT 3
                """
            ).fetchall()
            if sample:
                print("\n  Sample (2026 House, top 3 by receipts):")
                for s in sample:
                    receipts = f"${s[7]:,.0f}" if s[7] else "N/A"
                    print(f"    {s[0]} | {s[1][:30]:30s} | {s[2]}-{s[3]}-{s[4] or '':>2s} | {s[5]:3s} | {s[6] or '?'} | {receipts}")

            race_stats = conn.execute(
                """
                SELECT COUNT(*),
                       SUM(CASE WHEN incumbent_bioguide_id IS NOT NULL THEN 1 ELSE 0 END),
                       SUM(open_seat)
                FROM election_races WHERE election_cycle = 2026
                """
            ).fetchone()
            if race_stats and race_stats[0]:
                print(f"\n  2026 Races: {race_stats[0]} total, {race_stats[1]} with incumbent, {race_stats[2]} open seats")

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
