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


def load_seat_map(conn: sqlite3.Connection) -> dict[str, set[int]]:
    """Valid House seats per state from congressional_districts.

    Territories/DC store the Census delegate code 98; at-large states store 0.
    Both are single-seat and normalize to FEC's '00'.
    """
    seat_map: dict[str, set[int]] = {}
    try:
        rows = conn.execute(
            "SELECT state_code, district_number FROM congressional_districts "
            "WHERE state_code IS NOT NULL"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    for state, dnum in rows:
        seat_map.setdefault(state, set()).add(dnum)
    return seat_map


def normalize_house_district(
    state: str, district: str | None, seat_map: dict[str, set[int]]
) -> str | None:
    """Map an FEC office_district to a canonical seat, or None if invalid.

    FEC registrations carry stale district numbers (e.g. FL '59', NY '27'
    from pre-2022 maps), blanks, and '00'/'01' variants for single-seat
    states. Single-seat states/territories accept any value as '00';
    multi-seat states require a district that actually exists. None means
    the candidate belongs to no buildable race.
    """
    seats = seat_map.get(state)
    if not seats:
        return None
    if len(seats) == 1:
        return "00"
    d = (district or "").strip()
    if not d.isdigit():
        return None
    n = int(d)
    return f"{n:02d}" if n in seats else None


def _candidate_election_year_ok(election_year: int | None, cycle: int) -> bool:
    """Keep only candidates actually running this cycle.

    FEC cycle files include rolled-forward registrations for future races
    (e.g. a 2030-class senator fundraising during 2026) and garbage years.
    NULL is tolerated; otherwise the candidate's election year must be the
    cycle or the odd year before it (special-election filers).
    """
    return election_year is None or election_year in (cycle, cycle - 1)


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

    seat_map = load_seat_map(conn)
    if not seat_map:
        print("    WARNING: congressional_districts is empty — cannot validate "
              "House districts; races with stale/invalid districts may ship")

    cycle_filter = ""
    params: list = []
    if cycles:
        placeholders = ",".join("?" * len(cycles))
        cycle_filter = f"AND election_cycle IN ({placeholders})"
        params = list(cycles)

    rows = conn.execute(
        f"""
        SELECT election_cycle, office, office_state, office_district,
               election_year, candidate_id, candidate_name, party_code,
               incumbent_challenger_status
        FROM candidates
        WHERE office IN ('H', 'S', 'P')
              AND office_state IS NOT NULL AND office_state != ''
              {cycle_filter}
        """,
        params,
    ).fetchall()

    # Group candidates by canonical race key
    races: dict[tuple, list[tuple]] = {}
    skipped_district = 0
    skipped_year = 0
    for cycle, office, state, district, eyear, cand_id, name, party, ici in rows:
        if not _candidate_election_year_ok(eyear, cycle):
            skipped_year += 1
            continue
        if office == "H":
            norm = normalize_house_district(state, district, seat_map)
            if seat_map and norm is None:
                skipped_district += 1
                continue
            if norm is None:  # empty seat map fallback: keep raw digits only
                d = (district or "").strip()
                if not d.isdigit():
                    skipped_district += 1
                    continue
                norm = d.zfill(2)
            race_id = f"{cycle}-H-{state}-{norm}"
            key = (race_id, cycle, "H", state, norm)
        elif office == "S":
            race_id = f"{cycle}-S-{state}"
            key = (race_id, cycle, "S", state, None)
        else:
            # Presidential: one national race (state column is NOT NULL)
            race_id = f"{cycle}-P"
            key = (race_id, cycle, "P", "US", None)
        races.setdefault(key, []).append((cand_id, name, party, ici))

    if skipped_year:
        print(f"    Excluded {skipped_year:,} candidates not running this cycle "
              f"(election_year outside cycle)")
    if skipped_district:
        print(f"    Excluded {skipped_district:,} candidates with invalid/stale "
              f"House districts")

    def _seat_matches(member: dict, office: str, state: str, district: str | None) -> bool:
        """A member is the race's incumbent only if it is THEIR seat.

        fec_id alone is not enough: a House member running for Senate (their
        fec_id points at the Senate candidacy) is a candidate there, not the
        incumbent. FEC's ICI code has the same flaw — it marks any sitting
        officeholder 'I' regardless of which seat they are running for
        (e.g. a House member's Senate bid) — so seat identity is the only
        reliable signal.
        """
        if office == "S":
            return member["chamber"] == "Senate" and member["state_code"] == state
        if office == "H":
            if member["chamber"] != "House" or member["state_code"] != state:
                return False
            try:
                member_d = int(member["district"] or 0)
            except (TypeError, ValueError):
                member_d = 0
            if member_d == 98:  # delegate code
                member_d = 0
            return member_d == int(district or 0)
        return False

    race_count = 0
    for (race_id, cycle, office, state, district), members in races.items():
        num_candidates = len(members)
        incumbent_bioguide = None
        incumbent_fec_id = None
        incumbent_party = None

        has_incumbent = False
        for cand_id, name, party, ici in members:
            member = incumbent_lookup.get(cand_id)
            if member and _seat_matches(member, office, state, district):
                incumbent_bioguide = member["bioguide_id"]
                incumbent_fec_id = cand_id
                incumbent_party = member["party"]
                has_incumbent = True
                break

        # No sitting member running for this seat => open race. Derived
        # structurally rather than from FEC 'O' markers, which inherit the
        # same seat-attribution unreliability as 'I' (see above).
        open_seat = 0 if has_incumbent or office == "P" else 1
        challenger_count = num_candidates - 1 if has_incumbent else num_candidates

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
                district,
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

    # Existing race_ids, so candidates map only into races Phase 3 built
    race_ids = {
        r[0]
        for r in conn.execute(
            f"SELECT race_id FROM election_races {cycle_filter}", params
        ).fetchall()
    }

    seat_map = load_seat_map(conn)

    rows = conn.execute(
        f"""
        SELECT election_cycle, office, office_state, office_district,
               election_year, candidate_id, candidate_name, party_code,
               incumbent_challenger_status, total_receipts,
               cash_on_hand, total_individual_contributions,
               principal_committee_id
        FROM candidates
        WHERE office IN ('H', 'S', 'P')
              AND office_state IS NOT NULL AND office_state != ''
              {"AND election_cycle IN (%s)" % ",".join("?" * len(params)) if params else ""}
        ORDER BY total_receipts DESC NULLS LAST
        """,
        params,
    ).fetchall()

    total = 0
    for row in rows:
        (cycle, office, state, district, eyear, cand_id, name, party, ici,
         receipts, cash, indiv, pcc) = row

        if not _candidate_election_year_ok(eyear, cycle):
            continue

        # Same race-key derivation as Phase 3
        if office == "H":
            norm = normalize_house_district(state, district, seat_map)
            if norm is None:
                d = (district or "").strip()
                if seat_map or not d.isdigit():
                    continue
                norm = d.zfill(2)
            race_id = f"{cycle}-H-{state}-{norm}"
        elif office == "S":
            race_id = f"{cycle}-S-{state}"
        else:
            race_id = f"{cycle}-P"

        if race_id not in race_ids:
            continue

        conn.execute(
            """
            INSERT OR REPLACE INTO race_candidates (
                race_id, candidate_id, election_cycle,
                candidate_name, party_code, incumbent_challenger_status,
                total_receipts, cash_on_hand, total_individual_contributions,
                principal_committee_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (race_id, cand_id, cycle, name, party, ici,
             receipts, cash, indiv, pcc),
        )
        total += 1

    # Normalize incumbent flags to Phase 3's seat-verified attribution: FEC
    # marks any sitting officeholder 'I' even in a race for a DIFFERENT seat
    # (a House member's Senate bid), which the apps would render as a second
    # incumbent. Demote those, then stamp the verified incumbent.
    conn.execute(
        """
        UPDATE race_candidates SET incumbent_challenger_status = 'C'
        WHERE incumbent_challenger_status = 'I'
          AND NOT EXISTS (
            SELECT 1 FROM election_races er
            WHERE er.race_id = race_candidates.race_id
              AND er.incumbent_fec_id = race_candidates.candidate_id
        )
        """
    )
    conn.execute(
        """
        UPDATE race_candidates SET incumbent_challenger_status = 'I'
        WHERE EXISTS (
            SELECT 1 FROM election_races er
            WHERE er.race_id = race_candidates.race_id
              AND er.incumbent_fec_id = race_candidates.candidate_id
        )
        """
    )

    conn.commit()
    print(f"    Inserted {total:,} race_candidates rows")
    return total


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------


# Columns on race_candidates that are populated by LATER pipeline stages
# (candidate_photos, campaign_sites, google_civic) or accumulate across runs.
# They must survive this script's DROP/recreate of race_candidates, so
# ensure_tables() snapshots them first and restore_enrichment() merges them
# back after Phase 4 repopulates the table.
ENRICHMENT_COLUMNS = [
    "image_url", "image_blob", "campaign_url", "campaign_phone",
    "campaign_email", "photo_url", "social_facebook", "social_twitter",
    "social_youtube", "profession", "education", "birthplace", "religion",
    "military_branch", "military_years",
]

_ENRICHMENT_SNAPSHOT = "_race_candidates_enrichment"


def snapshot_enrichment(conn: sqlite3.Connection) -> int:
    """Copy enrichment columns aside before race_candidates is dropped.

    Only columns that exist in the current table are captured (tolerates an
    older schema). Returns the number of rows snapshotted.
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='race_candidates'"
    ).fetchone()
    conn.execute(f"DROP TABLE IF EXISTS {_ENRICHMENT_SNAPSHOT}")
    if not exists:
        return 0

    present = {r[1] for r in conn.execute("PRAGMA table_info(race_candidates)")}
    cols = [c for c in ENRICHMENT_COLUMNS if c in present]
    if not cols:
        return 0

    col_list = ", ".join(cols)
    not_null = " OR ".join(f"{c} IS NOT NULL" for c in cols)
    conn.execute(
        f"CREATE TABLE {_ENRICHMENT_SNAPSHOT} AS "
        f"SELECT race_id, candidate_id, {col_list} "
        f"FROM race_candidates WHERE {not_null}"
    )
    count = conn.execute(f"SELECT COUNT(*) FROM {_ENRICHMENT_SNAPSHOT}").fetchone()[0]
    print(f"  Snapshotted enrichment data for {count:,} race_candidates rows")
    return count


def restore_enrichment(conn: sqlite3.Connection) -> int:
    """Merge snapshotted enrichment back into the rebuilt race_candidates.

    Freshly-imported values win (COALESCE); the snapshot only fills gaps.
    Rows whose (race_id, candidate_id) no longer exist are dropped naturally.
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (_ENRICHMENT_SNAPSHOT,),
    ).fetchone()
    if not exists:
        return 0

    snap_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({_ENRICHMENT_SNAPSHOT})")}
    cols = [c for c in ENRICHMENT_COLUMNS if c in snap_cols]
    if cols:
        sets = ", ".join(f"{c} = COALESCE(rc.{c}, e.{c})" for c in cols)
        conn.execute(
            f"UPDATE race_candidates AS rc SET {sets} "
            f"FROM {_ENRICHMENT_SNAPSHOT} AS e "
            f"WHERE e.race_id = rc.race_id AND e.candidate_id = rc.candidate_id"
        )
    restored = conn.execute(
        f"SELECT COUNT(*) FROM race_candidates rc "
        f"JOIN {_ENRICHMENT_SNAPSHOT} e "
        f"ON e.race_id = rc.race_id AND e.candidate_id = rc.candidate_id"
    ).fetchone()[0]
    conn.execute(f"DROP TABLE {_ENRICHMENT_SNAPSHOT}")
    conn.commit()
    print(f"  Restored enrichment data for {restored:,} race_candidates rows")
    return restored


def validate_primary_winners(conn: sqlite3.Connection) -> int:
    """Data gate: every curated primary winner must survive the rebuild.

    primary_winners is manually curated and intentionally not dropped by this
    import; if the FEC retires a winner's candidate_id (ID merges happen),
    the join the apps rely on breaks. Fail loudly so the winner can be
    remapped via log_primary_loss.py instead of shipping a dangling row.
    """
    try:
        rows = conn.execute(
            """
            SELECT pw.race_id, pw.party, pw.candidate_id
            FROM primary_winners pw
            LEFT JOIN race_candidates rc
                ON rc.race_id = pw.race_id AND rc.candidate_id = pw.candidate_id
            LEFT JOIN candidates c
                ON c.candidate_id = pw.candidate_id
                AND c.election_cycle = pw.election_cycle
            WHERE rc.candidate_id IS NULL OR c.candidate_id IS NULL
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return 0  # no primary_winners table (fresh DB)
    if rows:
        print(f"\n  VALIDATION FAILED: {len(rows)} primary_winners row(s) with no "
              f"matching race_candidates/candidates row:")
        for race_id, party, cand_id in rows:
            print(f"    {race_id} {party}: {cand_id}")
        print("  The FEC likely retired these candidate IDs. Remap each winner "
              "to the surviving ID with scripts/log_primary_loss.py.")
    return len(rows)


def ensure_tables(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist (idempotent)."""

    # Preserve photo/campaign/civic enrichment across the rebuild
    snapshot_enrichment(conn)

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

    # MUST stay column-identical to build_database.py's race_candidates (and
    # the Room schema the Android app validates against) — later pipeline
    # stages write the enrichment columns and fail if any are missing.
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
            campaign_url TEXT,
            campaign_phone TEXT,
            campaign_email TEXT,
            photo_url TEXT,
            social_facebook TEXT,
            social_twitter TEXT,
            social_youtube TEXT,
            profession TEXT,
            education TEXT,
            birthplace TEXT,
            religion TEXT,
            military_branch TEXT,
            military_years TEXT,
            PRIMARY KEY (race_id, candidate_id),
            FOREIGN KEY (race_id) REFERENCES election_races(race_id)
        )
    """)

    # Recreate indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_office ON candidates(office)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_state ON candidates(office_state)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_party ON candidates(party_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_cycle_office ON candidates(election_cycle, office, office_state)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_races_cycle ON election_races(election_cycle)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_races_incumbent ON election_races(incumbent_bioguide_id)")
    # race_id lookups are covered by the (race_id, candidate_id) primary key
    conn.execute("CREATE INDEX IF NOT EXISTS idx_race_cand_candidate ON race_candidates(candidate_id, election_cycle)")

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

        # Phase 4.5: Merge back enrichment preserved across the rebuild
        print("\n--- Phase 4.5: Restore Enrichment ---")
        restore_enrichment(conn)

        # Phase 4.6: Curated primary winners must still join cleanly
        print("\n--- Phase 4.6: Validate Primary Winners ---")
        dangling_winners = validate_primary_winners(conn)
        if dangling_winners == 0:
            print("  All primary winners resolve")

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

        if dangling_winners:
            print(f"\nIMPORT FAILED VALIDATION: {dangling_winners} dangling primary winner(s)")
            return 1

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
