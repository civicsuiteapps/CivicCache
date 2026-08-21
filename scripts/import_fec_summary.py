#!/usr/bin/env python3
"""
Import FEC financial summary and top contributor data into CivicCache database.

This script:
1. Creates member_fec_summary and member_top_contributors tables
2. Phase 1: Imports financial summaries from All-Candidates files
3. Phase 2: Aggregates PAC/Committee contributions from Committee-Contributions ZIPs
4. Phase 3: Aggregates individual employer contributions from Individual-Contributions ZIPs
5. Phase 4: Merges rankings, writes top contributors, updates summaries
6. Updates metadata

Data chain: Individual -> Committee (CMTE_ID) -> candidate_committee_linkages -> fec_id -> bioguide_id
"""

import sqlite3
import zipfile
import re
from pathlib import Path
from collections import defaultdict
from typing import Dict, Set, Tuple, Optional

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

# Database location
DB_PATH = _CFG_DB

# FEC Bulk Data directories
FEC_DIR = _CFG_FEC
ALL_CANDIDATES_DIR = FEC_DIR / "All-Candidates"
COMMITTEE_CONTRIB_DIR = FEC_DIR / "Contributions-from-Committees"
INDIVIDUAL_CONTRIB_DIR = FEC_DIR / "Contributions-by-Individuals"
LINKAGE_DIR = FEC_DIR / "Candidate-Committee-Linkages"

# Election cycles to process (current + previous)
ELECTION_CYCLES = [2026, 2024]

# Non-employer values to filter out during individual aggregation
NON_EMPLOYERS = {
    "RETIRED", "SELF-EMPLOYED", "SELF EMPLOYED", "NOT EMPLOYED",
    "N/A", "NONE", "HOMEMAKER", "STUDENT", "DISABLED", "UNEMPLOYED",
    "NOT APPLICABLE", "INFORMATION REQUESTED", "INFORMATION REQUESTED PER BEST EFFORTS",
    "REQUESTED", "REFUSED", "", "NA", "SELF",
}

# Suffixes to strip during employer normalization
EMPLOYER_SUFFIXES = re.compile(
    r'\s*,?\s*\b(INC|LLC|CO|CORP|CORPORATION|GROUP|LP|LLP|LTD|PLC|PLLC|PC|PA|NA)\b\.?\s*$',
    re.IGNORECASE
)



def create_tables(conn: sqlite3.Connection) -> None:
    """Create the FEC summary tables if they don't exist."""
    print("Creating tables...")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS member_fec_summary (
            bioguide_id TEXT NOT NULL,
            election_cycle INTEGER NOT NULL,
            fec_candidate_id TEXT NOT NULL,
            total_receipts REAL DEFAULT 0,
            total_disbursements REAL DEFAULT 0,
            cash_on_hand REAL DEFAULT 0,
            debts_owed REAL DEFAULT 0,
            total_individual_contributions REAL DEFAULT 0,
            total_pac_contributions REAL DEFAULT 0,
            total_party_contributions REAL DEFAULT 0,
            candidate_self_funding REAL DEFAULT 0,
            pct_individual REAL DEFAULT 0,
            pct_pac REAL DEFAULT 0,
            pct_self_funded REAL DEFAULT 0,
            top_contributor_name TEXT,
            top_contributor_type TEXT,
            top_contributor_amount REAL DEFAULT 0,
            coverage_end_date TEXT,
            import_date TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (bioguide_id, election_cycle),
            FOREIGN KEY (bioguide_id) REFERENCES congress_members(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS member_top_contributors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bioguide_id TEXT NOT NULL,
            election_cycle INTEGER NOT NULL,
            rank INTEGER NOT NULL,
            contributor_name TEXT NOT NULL,
            contributor_type TEXT NOT NULL,
            total_amount REAL NOT NULL,
            transaction_count INTEGER DEFAULT 0,
            UNIQUE(bioguide_id, election_cycle, rank),
            FOREIGN KEY (bioguide_id) REFERENCES congress_members(id)
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_fec_summary_cycle ON member_fec_summary(election_cycle)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_top_contrib_member ON member_top_contributors(bioguide_id, election_cycle)")

    conn.commit()
    print("  Tables ready.")


def clear_existing_data(conn: sqlite3.Connection) -> None:
    """Clear existing FEC summary data for a clean import."""
    conn.execute("DELETE FROM member_top_contributors")
    conn.execute("DELETE FROM member_fec_summary")
    conn.commit()
    print("  Cleared existing data.")


def build_fec_to_bioguide(conn: sqlite3.Connection) -> Dict[str, str]:
    """Build a mapping from FEC candidate ID to bioguide_id."""
    rows = conn.execute(
        "SELECT id, fec_id FROM congress_members WHERE fec_id IS NOT NULL AND fec_id <> ''"
    ).fetchall()

    mapping = {}
    for bioguide_id, fec_id in rows:
        mapping[fec_id] = bioguide_id

    print(f"  Built FEC->bioguide mapping: {len(mapping)} members")
    return mapping


def build_committee_to_bioguide(conn: sqlite3.Connection, fec_to_bioguide: Dict[str, str],
                                 cycles: list) -> Dict[str, str]:
    """Build a mapping from committee_id to bioguide_id using candidate_committee_linkages."""
    fec_ids = set(fec_to_bioguide.keys())
    mapping = {}

    for cycle in cycles:
        # Load linkages from ZIP files
        zip_path = LINKAGE_DIR / f"Candidate-Committee-Linkages_{cycle}.zip"
        if zip_path.exists():
            with zipfile.ZipFile(zip_path, 'r') as zf:
                for name in zf.namelist():
                    if name.endswith('.txt'):
                        with zf.open(name) as f:
                            for raw_line in f:
                                line = raw_line.decode('utf-8', errors='ignore').strip()
                                if not line:
                                    continue
                                fields = line.split('|')
                                if len(fields) < 4:
                                    continue
                                cand_id = fields[0].strip()
                                cmte_id = fields[3].strip()
                                if cand_id in fec_ids and cmte_id:
                                    mapping[cmte_id] = fec_to_bioguide[cand_id]

    # Also load from the database linkages table
    rows = conn.execute("""
        SELECT candidate_id, committee_id FROM candidate_committee_linkages
    """).fetchall()
    for cand_id, cmte_id in rows:
        if cand_id in fec_ids:
            mapping[cmte_id] = fec_to_bioguide[cand_id]

    print(f"  Built committee->bioguide mapping: {len(mapping)} committees")
    return mapping


def safe_float(value: str) -> float:
    """Safely parse a float value, returning 0.0 on failure."""
    try:
        return float(value.strip()) if value.strip() else 0.0
    except (ValueError, AttributeError):
        return 0.0


def normalize_employer(name: str) -> str:
    """Normalize an employer name for aggregation."""
    if not name:
        return ""
    normalized = name.upper().strip()
    normalized = EMPLOYER_SUFFIXES.sub('', normalized).strip()
    # Remove trailing punctuation
    normalized = normalized.rstrip('.,;')
    return normalized


def get_cycle_for_file(filename: str) -> Optional[int]:
    """Extract the election cycle year from a filename."""
    # Match patterns like _2024-2025, _2022-2023, _2024, _2026
    m = re.search(r'_(\d{4})(?:-\d{4})?', filename)
    if m:
        year = int(m.group(1))
        # FEC uses even years for cycles; odd year files belong to next even year
        if year % 2 == 1:
            return year + 1
        return year
    return None


def map_cycle_to_target(cycle: int) -> Optional[int]:
    """Map a file's cycle to our target cycles (2024 or 2026)."""
    if cycle in ELECTION_CYCLES:
        return cycle
    return None


# ============================================================
# Phase 1: Financial Summaries from All-Candidates
# ============================================================

def phase1_financial_summaries(conn: sqlite3.Connection, fec_to_bioguide: Dict[str, str]) -> int:
    """Import financial summaries from All-Candidates files."""
    print("\n" + "="*60)
    print("PHASE 1: Financial Summaries (All-Candidates)")
    print("="*60)

    imported = 0

    # Map cycle years to their All-Candidates files
    cycle_files = {
        2024: ALL_CANDIDATES_DIR / "All-Candidates_2024-2025.txt",
        2026: ALL_CANDIDATES_DIR / "All-Candidates_2024-2025.txt",  # Use latest available
    }

    # Check for 2022-2023 as the previous cycle alternative
    prev_file = ALL_CANDIDATES_DIR / "All-Candidates_2022-2023.txt"
    if prev_file.exists():
        cycle_files[2024] = ALL_CANDIDATES_DIR / "All-Candidates_2024-2025.txt"

    # Process each target cycle
    for cycle in ELECTION_CYCLES:
        # Find the best file for this cycle
        best_file = None
        if cycle == 2026:
            best_file = ALL_CANDIDATES_DIR / "All-Candidates_2024-2025.txt"
        elif cycle == 2024:
            # Use 2024-2025 file which has 2024 cycle data
            best_file = ALL_CANDIDATES_DIR / "All-Candidates_2024-2025.txt"
            # But prefer 2022-2023 for the previous cycle
            f22 = ALL_CANDIDATES_DIR / "All-Candidates_2022-2023.txt"
            if f22.exists():
                best_file = f22

        if best_file is None or not best_file.exists():
            print(f"  No All-Candidates file found for cycle {cycle}")
            continue

        print(f"\n  Processing {best_file.name} for cycle {cycle}...")
        cycle_imported = 0

        with open(best_file, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                fields = line.strip().split('|')
                if len(fields) < 28:
                    continue

                cand_id = fields[0].strip()
                bioguide_id = fec_to_bioguide.get(cand_id)
                if not bioguide_id:
                    continue

                total_receipts = safe_float(fields[5]) if len(fields) > 5 else 0.0
                total_disbursements = safe_float(fields[7]) if len(fields) > 7 else 0.0
                cash_on_hand = safe_float(fields[10]) if len(fields) > 10 else 0.0
                cand_contrib = safe_float(fields[11]) if len(fields) > 11 else 0.0
                cand_loans = safe_float(fields[12]) if len(fields) > 12 else 0.0
                debts_owed = safe_float(fields[16]) if len(fields) > 16 else 0.0
                indiv_contrib = safe_float(fields[17]) if len(fields) > 17 else 0.0
                pac_contrib = safe_float(fields[25]) if len(fields) > 25 else 0.0
                party_contrib = safe_float(fields[26]) if len(fields) > 26 else 0.0
                coverage_end = fields[27].strip() if len(fields) > 27 else ''

                self_funding = cand_contrib + cand_loans

                # Compute percentages
                total = total_receipts if total_receipts > 0 else 1.0
                pct_individual = round((indiv_contrib / total) * 100, 1) if total > 0 else 0.0
                pct_pac = round((pac_contrib / total) * 100, 1) if total > 0 else 0.0
                pct_self = round((self_funding / total) * 100, 1) if total > 0 else 0.0

                try:
                    conn.execute("""
                        INSERT OR REPLACE INTO member_fec_summary
                        (bioguide_id, election_cycle, fec_candidate_id,
                         total_receipts, total_disbursements, cash_on_hand, debts_owed,
                         total_individual_contributions, total_pac_contributions,
                         total_party_contributions, candidate_self_funding,
                         pct_individual, pct_pac, pct_self_funded,
                         coverage_end_date, import_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (
                        bioguide_id, cycle, cand_id,
                        total_receipts, total_disbursements, cash_on_hand, debts_owed,
                        indiv_contrib, pac_contrib, party_contrib, self_funding,
                        pct_individual, pct_pac, pct_self,
                        coverage_end
                    ))
                    cycle_imported += 1
                except sqlite3.IntegrityError:
                    pass

        conn.commit()
        imported += cycle_imported
        print(f"  Imported {cycle_imported} member summaries for cycle {cycle}")

    print(f"\n  Phase 1 complete: {imported} total summaries")
    return imported


# ============================================================
# Phase 2: PAC/Committee Top Contributors
# ============================================================

def phase2_committee_contributions(conn: sqlite3.Connection,
                                    fec_to_bioguide: Dict[str, str]) -> Dict[Tuple[str, int], Dict[str, Tuple[float, int]]]:
    """Aggregate PAC/Committee contributions per member per cycle.

    Returns: {(bioguide_id, cycle): {cmte_id: (total_amount, count)}}
    """
    print("\n" + "="*60)
    print("PHASE 2: Committee/PAC Contributions")
    print("="*60)

    # {(bioguide_id, cycle): {cmte_id: (sum, count)}}
    pac_agg: Dict[Tuple[str, int], Dict[str, Tuple[float, int]]] = defaultdict(lambda: defaultdict(lambda: (0.0, 0)))

    fec_ids = set(fec_to_bioguide.keys())
    total_matched = 0

    for cycle in ELECTION_CYCLES:
        zip_path = COMMITTEE_CONTRIB_DIR / f"Committee-Contributions_{cycle}.zip"
        if not zip_path.exists():
            print(f"  File not found: {zip_path}")
            continue

        print(f"  Processing {zip_path.name}...")
        line_count = 0
        matched = 0

        with zipfile.ZipFile(zip_path, 'r') as zf:
            for name in zf.namelist():
                if not name.endswith('.txt'):
                    continue
                with zf.open(name) as f:
                    for raw_line in f:
                        line_count += 1
                        if line_count % 500000 == 0:
                            print(f"    Processed {line_count:,} lines, {matched:,} matched...")

                        line = raw_line.decode('utf-8', errors='ignore').strip()
                        if not line:
                            continue

                        fields = line.split('|')
                        if len(fields) < 17:
                            continue

                        cmte_id = fields[0].strip()
                        cand_id = fields[16].strip()

                        if cand_id not in fec_ids:
                            continue

                        try:
                            amount = float(fields[14].strip())
                        except (ValueError, IndexError):
                            continue

                        bioguide_id = fec_to_bioguide[cand_id]
                        key = (bioguide_id, cycle)
                        prev_sum, prev_count = pac_agg[key][cmte_id]
                        pac_agg[key][cmte_id] = (prev_sum + amount, prev_count + 1)
                        matched += 1

        total_matched += matched
        print(f"  Cycle {cycle}: {line_count:,} lines, {matched:,} matched to current members")

    print(f"\n  Phase 2 complete: {total_matched:,} total transactions matched")
    return dict(pac_agg)


# ============================================================
# Phase 3: Individual Employer Aggregation
# ============================================================

def phase3_individual_contributions(conn: sqlite3.Connection,
                                     committee_to_bioguide: Dict[str, str]) -> Dict[Tuple[str, int], Dict[str, Tuple[float, int]]]:
    """Aggregate individual contributions by employer per member per cycle.

    Returns: {(bioguide_id, cycle): {normalized_employer: (total_amount, count)}}
    """
    print("\n" + "="*60)
    print("PHASE 3: Individual Contributions (Employer Aggregation)")
    print("="*60)

    # {(bioguide_id, cycle): {employer: (sum, count)}}
    emp_agg: Dict[Tuple[str, int], Dict[str, Tuple[float, int]]] = defaultdict(lambda: defaultdict(lambda: (0.0, 0)))

    cmte_set = set(committee_to_bioguide.keys())
    total_matched = 0

    for cycle in ELECTION_CYCLES:
        zip_path = INDIVIDUAL_CONTRIB_DIR / f"Individual-Contributions_{cycle}.zip"
        if not zip_path.exists():
            print(f"  File not found: {zip_path}")
            continue

        print(f"  Processing {zip_path.name} (this may take several minutes)...")
        line_count = 0
        matched = 0

        with zipfile.ZipFile(zip_path, 'r') as zf:
            for name in zf.namelist():
                if not name.endswith('.txt'):
                    continue
                with zf.open(name) as f:
                    for raw_line in f:
                        line_count += 1
                        if line_count % 2000000 == 0:
                            print(f"    Processed {line_count:,} lines, {matched:,} matched...")

                        line = raw_line.decode('utf-8', errors='ignore').strip()
                        if not line:
                            continue

                        fields = line.split('|')
                        if len(fields) < 19:
                            continue

                        # Skip memo items (field 18 = MEMO_CD)
                        memo_cd = fields[18].strip().upper() if len(fields) > 18 else ''
                        if memo_cd == 'X':
                            continue

                        cmte_id = fields[0].strip()

                        # Check if this committee maps to a current member
                        bioguide_id = committee_to_bioguide.get(cmte_id)

                        # Also check OTHER_ID (field 15) for ActBlue/WinRed earmarks
                        if not bioguide_id and len(fields) > 15:
                            other_id = fields[15].strip()
                            if other_id:
                                bioguide_id = committee_to_bioguide.get(other_id)

                        if not bioguide_id:
                            continue

                        try:
                            amount = float(fields[14].strip())
                        except (ValueError, IndexError):
                            continue

                        # Get employer (field 11)
                        employer = fields[11].strip() if len(fields) > 11 else ''
                        normalized = normalize_employer(employer)

                        if not normalized or normalized in NON_EMPLOYERS:
                            continue

                        key = (bioguide_id, cycle)
                        prev_sum, prev_count = emp_agg[key][normalized]
                        emp_agg[key][normalized] = (prev_sum + amount, prev_count + 1)
                        matched += 1

        total_matched += matched
        print(f"  Cycle {cycle}: {line_count:,} lines, {matched:,} matched to current members")

    print(f"\n  Phase 3 complete: {total_matched:,} total transactions matched")
    return dict(emp_agg)


# ============================================================
# Phase 4: Merge & Write
# ============================================================

def phase4_merge_and_write(conn: sqlite3.Connection,
                           pac_agg: Dict[Tuple[str, int], Dict[str, Tuple[float, int]]],
                           emp_agg: Dict[Tuple[str, int], Dict[str, Tuple[float, int]]]) -> int:
    """Merge PAC and employer rankings, write top contributors, update summaries."""
    print("\n" + "="*60)
    print("PHASE 4: Merge Rankings & Write Top Contributors")
    print("="*60)

    # Build committee name lookup from database
    cmte_names = {}
    rows = conn.execute("SELECT committee_id, committee_name FROM committees").fetchall()
    for cmte_id, cmte_name in rows:
        cmte_names[cmte_id] = cmte_name
    print(f"  Loaded {len(cmte_names):,} committee names")

    # Get all member/cycle pairs from member_fec_summary
    summary_rows = conn.execute(
        "SELECT bioguide_id, election_cycle FROM member_fec_summary"
    ).fetchall()

    total_contributors = 0
    api_resolved: Dict[str, Optional[str]] = {}  # cmte_id -> name or None

    def _resolve_pac_name(cmte_id: str) -> str:
        """Committee display name, falling back to the FEC API for committees
        newer than the local bulk master. Never lets a raw ID ship silently."""
        name = cmte_names.get(cmte_id)
        if name and not is_unresolved_committee_name(name, cmte_id):
            return name
        if cmte_id not in api_resolved:
            info = fetch_openfec_committee(cmte_id)
            if info and info.get("name"):
                api_resolved[cmte_id] = info["name"]
                cmte_names[cmte_id] = info["name"]
                upsert_committee_from_openfec(conn, cmte_id, info)
                print(f"    Resolved via FEC API: {cmte_id} -> {info['name']}")
            else:
                api_resolved[cmte_id] = None
                print(f"    UNRESOLVED committee: {cmte_id} (not in master or API)")
        return api_resolved[cmte_id] or cmte_id

    for bioguide_id, cycle in summary_rows:
        key = (bioguide_id, cycle)

        # Collect all contributors
        contributors = []  # [(name_or_cmte_id, type, amount, count, cmte_id)]

        # Add PAC contributors (names resolved lazily, only for ranked rows)
        if key in pac_agg:
            for cmte_id, (amount, count) in pac_agg[key].items():
                contributors.append((cmte_id, 'pac', amount, count, cmte_id))

        # Add employer-aggregated individual contributors
        if key in emp_agg:
            for employer, (amount, count) in emp_agg[key].items():
                contributors.append((employer.title(), 'employer', amount, count, None))

        # Sort by amount descending and take top 10
        contributors.sort(key=lambda x: x[2], reverse=True)
        top10 = [
            (_resolve_pac_name(cmte_id) if cmte_id else name, ctype, amount, count)
            for name, ctype, amount, count, cmte_id in contributors[:10]
        ]

        # Write to member_top_contributors
        for rank, (name, ctype, amount, count) in enumerate(top10, 1):
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO member_top_contributors
                    (bioguide_id, election_cycle, rank, contributor_name,
                     contributor_type, total_amount, transaction_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (bioguide_id, cycle, rank, name, ctype, amount, count))
                total_contributors += 1
            except sqlite3.IntegrityError:
                pass

        # Update summary with top contributor
        if top10:
            top_name, top_type, top_amount, _ = top10[0]
            conn.execute("""
                UPDATE member_fec_summary
                SET top_contributor_name = ?,
                    top_contributor_type = ?,
                    top_contributor_amount = ?
                WHERE bioguide_id = ? AND election_cycle = ?
            """, (top_name, top_type, top_amount, bioguide_id, cycle))

    conn.commit()
    print(f"  Wrote {total_contributors:,} top contributor records")
    return total_contributors


def validate_contributor_names(conn: sqlite3.Connection) -> int:
    """Data gate: no PAC contributor may ship with an ID-shaped or empty name."""
    rows = conn.execute(
        """
        SELECT bioguide_id, election_cycle, rank, contributor_name
        FROM member_top_contributors
        WHERE contributor_name IS NULL OR contributor_name = ''
           OR contributor_name GLOB 'C[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'
        """
    ).fetchall()
    if rows:
        print(f"\n  VALIDATION FAILED: {len(rows)} member_top_contributors row(s) with unresolved names:")
        for bid, cycle, rank, name in rows[:20]:
            print(f"    {bid} cycle {cycle} rank {rank}: '{name}'")
        if len(rows) > 20:
            print(f"    ... and {len(rows) - 20} more")
    return len(rows)


# ============================================================
# Metadata & Verification
# ============================================================

def update_metadata(conn: sqlite3.Connection, summary_count: int, contributor_count: int) -> None:
    """Update database metadata."""
    print("\nUpdating metadata...")

    conn.execute("""
        INSERT OR REPLACE INTO metadata (key, value, updated_at)
        VALUES ('fec_summary_count', ?, CURRENT_TIMESTAMP)
    """, (str(summary_count),))

    conn.execute("""
        INSERT OR REPLACE INTO metadata (key, value, updated_at)
        VALUES ('fec_top_contributors_count', ?, CURRENT_TIMESTAMP)
    """, (str(contributor_count),))

    conn.execute("""
        INSERT OR REPLACE INTO metadata (key, value, updated_at)
        VALUES ('fec_summary_import_date', datetime('now'), CURRENT_TIMESTAMP)
    """)

    conn.commit()
    print("  Metadata updated.")


def verify_import(conn: sqlite3.Connection) -> None:
    """Verify the import was successful."""
    print("\n" + "="*60)
    print("IMPORT VERIFICATION")
    print("="*60)

    # Count summaries
    result = conn.execute("SELECT COUNT(*) FROM member_fec_summary").fetchone()
    print(f"Total member_fec_summary rows: {result[0]:,}")

    # Count by cycle
    results = conn.execute("""
        SELECT election_cycle, COUNT(*) as count
        FROM member_fec_summary
        GROUP BY election_cycle
        ORDER BY election_cycle
    """).fetchall()
    for cycle, count in results:
        print(f"  Cycle {cycle}: {count:,} members")

    # Count top contributors
    result = conn.execute("SELECT COUNT(*) FROM member_top_contributors").fetchone()
    print(f"\nTotal member_top_contributors rows: {result[0]:,}")

    # Show members with highest total receipts
    print("\nTop 10 fundraisers (most recent cycle):")
    results = conn.execute("""
        SELECT mfs.bioguide_id, cm.full_name, mfs.election_cycle,
               mfs.total_receipts, mfs.top_contributor_name, mfs.top_contributor_type
        FROM member_fec_summary mfs
        JOIN congress_members cm ON mfs.bioguide_id = cm.id
        ORDER BY mfs.total_receipts DESC
        LIMIT 10
    """).fetchall()
    for bid, name, cycle, receipts, top_name, top_type in results:
        top_str = f" | Top: {top_name} ({top_type})" if top_name else ""
        print(f"  {name}: ${receipts:,.0f} (cycle {cycle}){top_str}")

    # Show members with no data
    result = conn.execute("""
        SELECT COUNT(*) FROM congress_members
        WHERE id NOT IN (SELECT bioguide_id FROM member_fec_summary)
        AND fec_id IS NOT NULL AND fec_id <> ''
    """).fetchone()
    if result[0] > 0:
        print(f"\n  Members with FEC ID but no summary data: {result[0]}")

    # Sample a known member
    pelosi = conn.execute("""
        SELECT mfs.*, cm.full_name
        FROM member_fec_summary mfs
        JOIN congress_members cm ON mfs.bioguide_id = cm.id
        WHERE mfs.bioguide_id = 'P000197'
        ORDER BY mfs.election_cycle DESC
        LIMIT 1
    """).fetchone()
    if pelosi:
        print(f"\nSpot check - {pelosi[-1]}:")
        print(f"  Total receipts: ${pelosi[3]:,.0f}")
        print(f"  Individual: {pelosi[11]}% | PAC: {pelosi[12]}% | Self: {pelosi[13]}%")
        print(f"  Top contributor: {pelosi[14]} ({pelosi[15]})")


# ============================================================
# Main
# ============================================================

def main():
    """Main execution function."""
    print("="*60)
    print("FEC SUMMARY DATA IMPORT")
    print("="*60)

    try:
        conn = create_connection(use_wal=True)

        # Setup
        create_tables(conn)
        clear_existing_data(conn)

        # Build mappings
        print("\nBuilding lookup mappings...")
        fec_to_bioguide = build_fec_to_bioguide(conn)
        committee_to_bioguide = build_committee_to_bioguide(conn, fec_to_bioguide, ELECTION_CYCLES)

        # Phase 1: Financial summaries
        summary_count = phase1_financial_summaries(conn, fec_to_bioguide)

        # Phase 2: PAC/Committee contributions
        pac_agg = phase2_committee_contributions(conn, fec_to_bioguide)

        # Phase 3: Individual employer aggregation
        emp_agg = phase3_individual_contributions(conn, committee_to_bioguide)

        # Phase 4: Merge & write
        contributor_count = phase4_merge_and_write(conn, pac_agg, emp_agg)

        # Metadata & verification
        update_metadata(conn, summary_count, contributor_count)
        verify_import(conn)

        # Data gate: raw committee IDs must never ship as display names
        bad_rows = validate_contributor_names(conn)

        conn.close()

        if bad_rows:
            print("\n" + "="*60)
            print(f"IMPORT FAILED VALIDATION: {bad_rows} unresolved contributor name(s)")
            print("="*60)
            return 1

        print("\n" + "="*60)
        print("IMPORT COMPLETE")
        print("="*60)

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
