#!/usr/bin/env python3
"""
Import FEC Committee and Linkage data into CivicCache database.

This script:
1. Processes Committee-Master files (txt and zip)
2. Imports committee data into the database
3. Processes Candidate-Committee-Linkages files
4. Imports linkage data
5. Updates metadata
"""

import csv
import json
import sqlite3
import zipfile
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional
import tempfile
import os

try:
    from config import DB_PATH as _CFG_DB
except ImportError:
    _CFG_DB = Path("data/civic_cache.db")

from utils import create_connection

# Database location
DB_PATH = _CFG_DB

# Data directories
COMMITTEE_DIR = Path("data/raw/fec/Committee-Master")
LINKAGE_DIR = Path("data/raw/fec/Candidate-Committee-Linkages")



def parse_committee_line(line: str) -> Optional[Dict[str, Any]]:
    """Parse a committee record from pipe-delimited format.

    Filters out terminated (T) and administratively terminated (A)
    committees -- these have no current activity and bloat the table.
    """
    fields = line.strip().split('|')

    if len(fields) < 14:
        return None

    # FEC Committee file format:
    # CMTE_ID|CMTE_NM|TRES_NM|CMTE_ST1|CMTE_ST2|CMTE_CITY|CMTE_ST|CMTE_ZIP|
    # CMTE_DSGN|CMTE_TP|CMTE_PTY_AFFILIATION|CMTE_FILING_FREQ|ORG_TP|CONNECTED_ORG_NM|CAND_ID

    committee_id = fields[0].strip()
    if not committee_id:
        return None

    # Skip terminated and administratively terminated committees
    filing_freq = fields[11].strip().upper()
    if filing_freq in ('T', 'A'):
        return None

    return {
        'committee_id': committee_id,
        'committee_name': fields[1].strip(),
        'treasurer_name': fields[2].strip(),
        'street_1': fields[3].strip(),
        'street_2': fields[4].strip(),
        'city': fields[5].strip(),
        'state': fields[6].strip(),
        'zip': fields[7].strip(),
        'committee_designation': fields[8].strip(),
        'committee_type': fields[9].strip(),
        'party_code': fields[10].strip(),
        'filing_frequency': fields[11].strip(),
        'org_type': fields[12].strip(),
        'connected_org_name': fields[13].strip(),
    }


def get_committee_type_full(code: str) -> str:
    """Get full name for committee type code."""
    types = {
        'C': 'Communication Cost',
        'D': 'Delegate Committee',
        'E': 'Electioneering Communication',
        'H': 'House',
        'I': 'Independent Expenditure',
        'N': 'PAC - Nonqualified',
        'O': 'Independent Expenditure-Only (Super PACs)',
        'P': 'Presidential',
        'Q': 'PAC - Qualified',
        'S': 'Senate',
        'U': 'Single Candidate Independent Expenditure',
        'V': 'PAC with Non-Contribution Account - Nonqualified',
        'W': 'PAC with Non-Contribution Account - Qualified',
        'X': 'Party - Nonqualified',
        'Y': 'Party - Qualified',
        'Z': 'National Party Nonfederal Account'
    }
    return types.get(code, '')


def get_committee_designation_full(code: str) -> str:
    """Get full name for committee designation code."""
    designations = {
        'A': 'Authorized by a candidate',
        'J': 'Joint fundraising committee',
        'P': 'Principal campaign committee of a candidate',
        'U': 'Unauthorized',
        'B': 'Lobbyist/Registrant PAC',
        'D': 'Leadership PAC'
    }
    return designations.get(code, '')


def get_party_full_name(code: str) -> str:
    """Get full party name from code."""
    parties = {
        'DEM': 'Democratic Party',
        'REP': 'Republican Party',
        'LIB': 'Libertarian Party',
        'GRE': 'Green Party',
        'IND': 'Independent',
        'UNK': 'Unknown',
        '': 'None'
    }
    return parties.get(code, code)


def import_committees(conn: sqlite3.Connection) -> int:
    """Import committee data from files."""
    print(f"\nImporting committees from: {COMMITTEE_DIR}")

    if not COMMITTEE_DIR.exists():
        raise FileNotFoundError(f"Committee directory not found: {COMMITTEE_DIR}")

    # Clear existing committee data (linkages first to satisfy FK constraints)
    print("Clearing existing committee data...")
    conn.execute("DELETE FROM candidate_committee_linkages")
    conn.execute("DELETE FROM committees")
    conn.commit()

    imported_count = 0
    skipped_count = 0

    # Process txt files first
    txt_files = list(COMMITTEE_DIR.glob("*.txt"))
    print(f"Found {len(txt_files)} .txt files")

    for txt_file in txt_files:
        print(f"  Processing {txt_file.name}...")
        with open(txt_file, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                committee = parse_committee_line(line)
                if not committee:
                    skipped_count += 1
                    continue

                try:
                    conn.execute("""
                        INSERT OR REPLACE INTO committees (
                            committee_id, committee_name, committee_type,
                            committee_type_full, committee_designation,
                            committee_designation_full, party_code, party_full_name,
                            connected_org_name, treasurer_name, street_1, street_2,
                            city, state, zip, filing_frequency
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        committee['committee_id'],
                        committee['committee_name'],
                        committee['committee_type'],
                        get_committee_type_full(committee['committee_type']),
                        committee['committee_designation'],
                        get_committee_designation_full(committee['committee_designation']),
                        committee['party_code'],
                        get_party_full_name(committee['party_code']),
                        committee['connected_org_name'],
                        committee['treasurer_name'],
                        committee['street_1'],
                        committee['street_2'],
                        committee['city'],
                        committee['state'],
                        committee['zip'],
                        committee['filing_frequency']
                    ))
                    imported_count += 1

                    if imported_count % 1000 == 0:
                        print(f"    Imported {imported_count} committees...")
                        conn.commit()
                except sqlite3.IntegrityError as e:
                    skipped_count += 1

    # Process zip files
    zip_files = list(COMMITTEE_DIR.glob("*.zip"))
    print(f"Found {len(zip_files)} .zip files")

    for zip_file in zip_files:
        print(f"  Processing {zip_file.name}...")
        with zipfile.ZipFile(zip_file, 'r') as zf:
            # Extract to temp directory
            with tempfile.TemporaryDirectory() as tmpdir:
                zf.extractall(tmpdir)

                # Process extracted files
                for extracted_file in Path(tmpdir).glob("*.txt"):
                    with open(extracted_file, 'r', encoding='utf-8', errors='ignore') as f:
                        for line in f:
                            committee = parse_committee_line(line)
                            if not committee:
                                skipped_count += 1
                                continue

                            try:
                                conn.execute("""
                                    INSERT OR REPLACE INTO committees (
                                        committee_id, committee_name, committee_type,
                                        committee_type_full, committee_designation,
                                        committee_designation_full, party_code, party_full_name,
                                        connected_org_name, treasurer_name, street_1, street_2,
                                        city, state, zip, filing_frequency
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, (
                                    committee['committee_id'],
                                    committee['committee_name'],
                                    committee['committee_type'],
                                    get_committee_type_full(committee['committee_type']),
                                    committee['committee_designation'],
                                    get_committee_designation_full(committee['committee_designation']),
                                    committee['party_code'],
                                    get_party_full_name(committee['party_code']),
                                    committee['connected_org_name'],
                                    committee['treasurer_name'],
                                    committee['street_1'],
                                    committee['street_2'],
                                    committee['city'],
                                    committee['state'],
                                    committee['zip'],
                                    committee['filing_frequency']
                                ))
                                imported_count += 1

                                if imported_count % 1000 == 0:
                                    print(f"    Imported {imported_count} committees...")
                                    conn.commit()
                            except sqlite3.IntegrityError:
                                skipped_count += 1

    conn.commit()
    print(f"\n✓ Imported {imported_count} committees (skipped {skipped_count} invalid/duplicate records)")
    return imported_count


def parse_linkage_line(line: str) -> Optional[Dict[str, Any]]:
    """Parse a candidate-committee linkage record."""
    fields = line.strip().split('|')

    if len(fields) < 7:
        return None

    # FEC linkage format:
    # CAND_ID|CAND_ELECTION_YR|FEC_ELECTION_YR|CMTE_ID|CMTE_TP|CMTE_DSGN|LINKAGE_ID

    candidate_id = fields[0].strip()
    committee_id = fields[3].strip()

    if not candidate_id or not committee_id:
        return None

    try:
        election_year = int(fields[1].strip()) if fields[1].strip() else None
    except ValueError:
        election_year = None

    return {
        'candidate_id': candidate_id,
        'committee_id': committee_id,
        'election_year': election_year,
        'linkage_type': fields[6].strip() if len(fields) > 6 else None
    }


def import_linkages(conn: sqlite3.Connection) -> int:
    """Import candidate-committee linkages."""
    print(f"\nImporting linkages from: {LINKAGE_DIR}")

    if not LINKAGE_DIR.exists():
        raise FileNotFoundError(f"Linkage directory not found: {LINKAGE_DIR}")

    # Clear existing linkages
    print("Clearing existing linkage data...")
    conn.execute("DELETE FROM candidate_committee_linkages")
    conn.commit()

    imported_count = 0
    skipped_count = 0

    # Process zip files
    zip_files = list(LINKAGE_DIR.glob("*.zip"))
    print(f"Found {len(zip_files)} .zip files")

    for zip_file in zip_files:
        print(f"  Processing {zip_file.name}...")
        with zipfile.ZipFile(zip_file, 'r') as zf:
            with tempfile.TemporaryDirectory() as tmpdir:
                zf.extractall(tmpdir)

                for extracted_file in Path(tmpdir).glob("*.txt"):
                    with open(extracted_file, 'r', encoding='utf-8', errors='ignore') as f:
                        for line in f:
                            linkage = parse_linkage_line(line)
                            if not linkage:
                                skipped_count += 1
                                continue

                            try:
                                conn.execute("""
                                    INSERT OR IGNORE INTO candidate_committee_linkages (
                                        candidate_id, committee_id, linkage_type, election_year
                                    ) VALUES (?, ?, ?, ?)
                                """, (
                                    linkage['candidate_id'],
                                    linkage['committee_id'],
                                    linkage['linkage_type'],
                                    linkage['election_year']
                                ))
                                imported_count += 1

                                if imported_count % 5000 == 0:
                                    print(f"    Imported {imported_count} linkages...")
                                    conn.commit()
                            except sqlite3.IntegrityError:
                                skipped_count += 1

    conn.commit()
    print(f"\n✓ Imported {imported_count} linkages (skipped {skipped_count} invalid/duplicate records)")
    return imported_count


def update_metadata(conn: sqlite3.Connection, committee_count: int, linkage_count: int) -> None:
    """Update database metadata."""
    print("\nUpdating metadata...")

    conn.execute("""
        INSERT OR REPLACE INTO metadata (key, value, updated_at)
        VALUES ('total_committees', ?, CURRENT_TIMESTAMP)
    """, (str(committee_count),))

    conn.execute("""
        INSERT OR REPLACE INTO metadata (key, value, updated_at)
        VALUES ('total_linkages', ?, CURRENT_TIMESTAMP)
    """, (str(linkage_count),))

    conn.commit()
    print(f"✓ Metadata updated")


def verify_import(conn: sqlite3.Connection) -> None:
    """Verify the import was successful."""
    print("\n" + "="*60)
    print("IMPORT VERIFICATION")
    print("="*60)

    # Count committees
    result = conn.execute("SELECT COUNT(*) FROM committees").fetchone()
    committee_count = result[0] if result else 0
    print(f"Total committees: {committee_count:,}")

    # Count linkages
    result = conn.execute("SELECT COUNT(*) FROM candidate_committee_linkages").fetchone()
    linkage_count = result[0] if result else 0
    print(f"Total linkages: {linkage_count:,}")

    # Show committee type breakdown
    print("\nCommittees by type:")
    results = conn.execute("""
        SELECT committee_type, committee_type_full, COUNT(*) as count
        FROM committees
        WHERE committee_type IS NOT NULL AND committee_type != ''
        GROUP BY committee_type
        ORDER BY count DESC
        LIMIT 10
    """).fetchall()

    for code, full_name, count in results:
        print(f"  {code} ({full_name}): {count:,}")

    # Sample committees
    print("\nSample committees:")
    samples = conn.execute("""
        SELECT committee_id, committee_name, committee_type_full, party_full_name
        FROM committees
        LIMIT 5
    """).fetchall()

    for cid, name, ctype, party in samples:
        print(f"  {cid}: {name[:50]} ({ctype}, {party})")

    # Sample linkages
    print("\nSample linkages:")
    samples = conn.execute("""
        SELECT l.candidate_id, l.committee_id, l.election_year,
               c.candidate_name, cm.committee_name
        FROM candidate_committee_linkages l
        LEFT JOIN candidates c ON l.candidate_id = c.candidate_id
        LEFT JOIN committees cm ON l.committee_id = cm.committee_id
        LIMIT 5
    """).fetchall()

    for cand_id, comm_id, year, cand_name, comm_name in samples:
        print(f"  {cand_id} -> {comm_id} ({year})")
        if cand_name:
            print(f"    Candidate: {cand_name}")
        if comm_name:
            print(f"    Committee: {comm_name[:50]}")

    print("\n✓ Import verification complete")


def main():
    """Main execution function."""
    print("="*60)
    print("FEC COMMITTEE & LINKAGE DATA IMPORT")
    print("="*60)

    try:
        # Connect to database
        conn = create_connection()

        # Import committees
        committee_count = import_committees(conn)

        # Import linkages
        linkage_count = import_linkages(conn)

        # Update metadata
        update_metadata(conn, committee_count, linkage_count)

        # Verify import
        verify_import(conn)

        conn.close()

        print("\n" + "="*60)
        print("✓ IMPORT COMPLETE")
        print("="*60)

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
