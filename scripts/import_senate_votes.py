#!/usr/bin/env python3
"""
Import Senate roll call vote data from Senate.gov XML into CivicCache database.

Senate.gov publishes complete roll call data as XML at predictable URLs.
The Congress.gov API does not offer Senate vote endpoints, so this script
fetches directly from Senate.gov.

XML Endpoints:
- Vote list: https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{congress}_{session}.xml
- Individual: https://www.senate.gov/legislative/LIS/roll_call_votes/vote{congress}{session}/vote_{congress}_{session}_{vote_number}.xml

Usage:
    python3 scripts/import_senate_votes.py                    # Import 119th Congress (both sessions)
    python3 scripts/import_senate_votes.py --congress 118     # Import 118th Congress
    python3 scripts/import_senate_votes.py --session 1        # Session 1 only
    python3 scripts/import_senate_votes.py --verify-only      # Verification only
"""

import argparse
import re
import sqlite3
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from config import CONGRESS_NUMBER as _CFG_CONGRESS, DB_PATH as _CFG_DB
except ImportError:
    _CFG_CONGRESS = 119
    _CFG_DB = Path("data/civic_cache.db")

from utils import create_connection

# Senate.gov XML base URLs
VOTE_LIST_URL = "https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{congress}_{session}.xml"
VOTE_DETAIL_URL = "https://www.senate.gov/legislative/LIS/roll_call_votes/vote{congress}{session}/vote_{congress}_{session}_{vote_number}.xml"

BATCH_COMMIT_SIZE = 50

# Bill type mapping from Senate XML document_type to our legislation ID format
DOCUMENT_TYPE_MAP = {
    "S.": "s",
    "S.J.Res.": "sjres",
    "S.Con.Res.": "sconres",
    "S.Res.": "sres",
    "H.R.": "hr",
    "H.J.Res.": "hjres",
    "H.Con.Res.": "hconres",
    "H.Res.": "hres",
}

# Bill reference patterns for matching from vote question text
BILL_PATTERNS = [
    (r'H\.?\s*R\.?\s*(\d+)', 'hr'),
    (r'H\.?\s*RES\.?\s*(\d+)', 'hres'),
    (r'H\.?\s*J\.?\s*RES\.?\s*(\d+)', 'hjres'),
    (r'H\.?\s*CON\.?\s*RES\.?\s*(\d+)', 'hconres'),
    (r'S\.?\s*(\d+)', 's'),
    (r'S\.?\s*RES\.?\s*(\d+)', 'sres'),
    (r'S\.?\s*J\.?\s*RES\.?\s*(\d+)', 'sjres'),
    (r'S\.?\s*CON\.?\s*RES\.?\s*(\d+)', 'sconres'),
]

USER_AGENT = "CivicCache/1.0"


def fetch_xml(url: str, retries: int = 3, timeout: int = 30) -> Optional[ET.Element]:
    """Fetch and parse an XML document from a URL."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                content = response.read()
                time.sleep(0.3)  # Rate limiting
                return ET.fromstring(content)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429:
                wait = 2 ** attempt
                print(f"  Rate limited, waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"  HTTP Error {e.code}: {e.reason}")
                if attempt == retries - 1:
                    return None
        except Exception as e:
            print(f"  Error fetching {url}: {e}")
            if attempt == retries - 1:
                return None
            time.sleep(1)
    return None


def get_text(element: Optional[ET.Element], tag: str, default: str = "") -> str:
    """Safely extract text from an XML sub-element."""
    if element is None:
        return default
    child = element.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return default


def fetch_vote_list(congress: int, session: int) -> List[int]:
    """Fetch the list of vote numbers for a congress/session from Senate.gov."""
    url = VOTE_LIST_URL.format(congress=congress, session=session)
    print(f"  Fetching vote list: {url}")

    root = fetch_xml(url)
    if root is None:
        print(f"    No vote list found for Congress {congress}, Session {session}")
        return []

    vote_numbers = []
    for vote_elem in root.iter("vote"):
        num_text = get_text(vote_elem, "vote_number")
        if num_text:
            try:
                vote_numbers.append(int(num_text))
            except ValueError:
                pass

    print(f"    Found {len(vote_numbers)} votes")
    return sorted(vote_numbers)


def parse_vote_date(date_str: str, congress_year: str = "") -> str:
    """Parse Senate.gov vote date string to ISO format (YYYY-MM-DD).

    Handles formats like:
    - "January 9, 2025, 02:54 PM"
    - "January  9, 2025, 02:54 PM" (extra space)
    """
    if not date_str:
        return ""

    # Normalize whitespace
    date_str = " ".join(date_str.split())

    # Try full datetime format
    for fmt in [
        "%B %d, %Y, %I:%M %p",
        "%B %d, %Y",
        "%b %d, %Y, %I:%M %p",
        "%b %d, %Y",
    ]:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return date_str


def parse_vote_time(date_str: str) -> Optional[str]:
    """Extract time from Senate.gov vote date string (HH:MM)."""
    if not date_str:
        return None

    date_str = " ".join(date_str.split())

    for fmt in ["%B %d, %Y, %I:%M %p", "%b %d, %Y, %I:%M %p"]:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%H:%M")
        except ValueError:
            continue

    return None


def build_lis_to_bioguide_map(conn: sqlite3.Connection) -> Dict[str, str]:
    """Build a lookup table from lis_member_id to bioguide_id."""
    results = conn.execute(
        "SELECT lis_id, id FROM congress_members WHERE lis_id IS NOT NULL"
    ).fetchall()

    mapping = {row[0]: row[1] for row in results}
    print(f"  Built LIS→bioguide mapping: {len(mapping)} senators")
    return mapping


def match_legislation(conn: sqlite3.Connection, document: Optional[ET.Element],
                      question_text: str, congress: int) -> Optional[str]:
    """Match a Senate vote to legislation using document reference or question text."""
    # First try the structured document reference
    if document is not None:
        doc_type = get_text(document, "document_type")
        doc_number = get_text(document, "document_number")

        if doc_type and doc_number:
            bill_type = DOCUMENT_TYPE_MAP.get(doc_type)
            if bill_type:
                legislation_id = f"{congress}-{bill_type}-{doc_number}"
                result = conn.execute(
                    "SELECT id FROM legislation WHERE id = ?",
                    (legislation_id,)
                ).fetchone()
                if result:
                    return legislation_id

    # Fallback: parse from vote question text
    if question_text:
        for pattern, bill_type in BILL_PATTERNS:
            match = re.search(pattern, question_text, re.IGNORECASE)
            if match:
                bill_number = match.group(1)
                legislation_id = f"{congress}-{bill_type}-{bill_number}"
                result = conn.execute(
                    "SELECT id FROM legislation WHERE id = ?",
                    (legislation_id,)
                ).fetchone()
                if result:
                    return legislation_id

    return None


def get_imported_rolls(conn: sqlite3.Connection, congress: int, session: int) -> set:
    """Get set of roll numbers already imported for resume support."""
    results = conn.execute(
        "SELECT roll_number FROM roll_call_votes "
        "WHERE congress = ? AND chamber = 'Senate' AND session = ?",
        (congress, session)
    ).fetchall()
    return {r[0] for r in results}


def import_single_vote(conn: sqlite3.Connection, congress: int, session: int,
                       vote_number: int, lis_map: Dict[str, str]) -> Tuple[Optional[str], int]:
    """Import a single Senate roll call vote. Returns (vote_id, member_vote_count)."""
    padded = str(vote_number).zfill(5)
    url = VOTE_DETAIL_URL.format(
        congress=congress, session=session, vote_number=padded
    )

    root = fetch_xml(url)
    if root is None:
        return None, 0

    vote_id = f"{congress}-senate-{session}-{vote_number}"

    # Parse vote metadata
    vote_date_raw = get_text(root, "vote_date")
    vote_date = parse_vote_date(vote_date_raw)
    vote_time = parse_vote_time(vote_date_raw)

    question = get_text(root, "question")
    vote_question_text = get_text(root, "vote_question_text")
    vote_title = get_text(root, "vote_title")
    vote_result_text = get_text(root, "vote_result_text")
    majority_requirement = get_text(root, "majority_requirement")

    # Determine simple result from vote_result_text
    vote_result = None
    if vote_result_text:
        lower = vote_result_text.lower()
        if "agreed" in lower or "passed" in lower or "confirmed" in lower or "guilty" in lower:
            vote_result = "Passed"
        elif "rejected" in lower or "not sustained" in lower or "not guilty" in lower:
            vote_result = "Failed"
        else:
            vote_result = vote_result_text.split("(")[0].strip() if "(" in vote_result_text else vote_result_text

    # Parse vote counts
    count_elem = root.find("count")
    yea_count = int(get_text(count_elem, "yeas", "0")) if count_elem is not None else 0
    nay_count = int(get_text(count_elem, "nays", "0")) if count_elem is not None else 0
    present_count = int(get_text(count_elem, "present", "0")) if count_elem is not None else 0
    not_voting_count = int(get_text(count_elem, "absent", "0")) if count_elem is not None else 0

    # Match to legislation
    document = root.find("document")
    search_text = f"{question} {vote_question_text} {vote_title}"
    legislation_id = match_legislation(conn, document, search_text, congress)

    # Parse amendment info
    amendment_elem = root.find("amendment")
    amendment_number = None
    if amendment_elem is not None:
        amdt_num = get_text(amendment_elem, "amendment_number")
        if amdt_num:
            amendment_number = amdt_num

    # Source URL
    source_url = url

    # Insert roll call vote
    conn.execute("""
        INSERT OR REPLACE INTO roll_call_votes (
            id, congress, chamber, session, roll_number,
            vote_date, vote_time, vote_question, vote_question_text,
            vote_type, vote_result, vote_result_text,
            yea_count, nay_count, present_count, not_voting_count,
            required_majority, legislation_id, amendment_number, source_url,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (
        vote_id,
        congress,
        "Senate",
        session,
        vote_number,
        vote_date,
        vote_time,
        question,
        vote_question_text,
        "Senate Vote",
        vote_result,
        vote_result_text,
        yea_count,
        nay_count,
        present_count,
        not_voting_count,
        majority_requirement or None,
        legislation_id,
        amendment_number,
        source_url,
    ))

    # Parse and import member votes
    members_elem = root.find("members")
    member_count = 0

    if members_elem is not None:
        for member_elem in members_elem.findall("member"):
            lis_id = get_text(member_elem, "lis_member_id")
            vote_cast = get_text(member_elem, "vote_cast")

            if not lis_id or not vote_cast:
                continue

            # Map LIS ID to bioguide ID
            bioguide_id = lis_map.get(lis_id)
            if not bioguide_id:
                continue

            first_name = get_text(member_elem, "first_name")
            last_name = get_text(member_elem, "last_name")
            member_name = f"{first_name} {last_name}".strip() if first_name or last_name else None
            party = get_text(member_elem, "party")
            state = get_text(member_elem, "state")

            try:
                conn.execute("""
                    INSERT OR REPLACE INTO member_votes (
                        roll_call_id, bioguide_id, vote_position,
                        member_name, party, state
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    vote_id,
                    bioguide_id,
                    vote_cast,
                    member_name,
                    party,
                    state,
                ))
                member_count += 1
            except sqlite3.Error:
                pass

    return vote_id, member_count


def import_session_votes(conn: sqlite3.Connection, congress: int, session: int,
                         lis_map: Dict[str, str]) -> Dict[str, int]:
    """Import all Senate votes for a session."""
    print(f"\n  Importing Senate votes, Congress {congress}, Session {session}...")

    # Check for already-imported roll calls (resume support)
    imported_rolls = get_imported_rolls(conn, congress, session)
    if imported_rolls:
        print(f"    Already have {len(imported_rolls)} roll calls imported")

    # Fetch list of vote numbers
    vote_numbers = fetch_vote_list(congress, session)

    # Filter out already-imported
    if imported_rolls:
        vote_numbers = [n for n in vote_numbers if n not in imported_rolls]
        print(f"    {len(vote_numbers)} new roll calls to import")

    stats = {
        "total": len(vote_numbers),
        "imported": 0,
        "failed": 0,
        "member_votes": 0,
        "linked_legislation": 0,
    }

    if not vote_numbers:
        return stats

    start_time = time.time()

    for i, vote_num in enumerate(vote_numbers, 1):
        try:
            vote_id, member_count = import_single_vote(
                conn, congress, session, vote_num, lis_map
            )
            if vote_id:
                stats["imported"] += 1
                stats["member_votes"] += member_count

            # Progress update
            if i % 10 == 0:
                elapsed = time.time() - start_time
                rate = i / elapsed if elapsed > 0 else 0
                remaining = (len(vote_numbers) - i) / rate if rate > 0 else 0
                print(
                    f"    [{i}/{len(vote_numbers)}] Vote {vote_num} "
                    f"({member_count} votes) - {remaining:.1f} min remaining"
                )

            # Batch commit
            if i % BATCH_COMMIT_SIZE == 0:
                conn.commit()

        except Exception as e:
            print(f"    Failed vote {vote_num}: {e}")
            stats["failed"] += 1

    conn.commit()

    # Count linked legislation
    result = conn.execute(
        "SELECT COUNT(*) FROM roll_call_votes "
        "WHERE congress = ? AND chamber = 'Senate' AND session = ? "
        "AND legislation_id IS NOT NULL",
        (congress, session),
    ).fetchone()
    stats["linked_legislation"] = result[0] if result else 0

    return stats


def import_all_senate_votes(conn: sqlite3.Connection, congress: int = 119,
                            session: Optional[int] = None) -> Dict[str, int]:
    """Import all Senate votes for the specified Congress."""
    print("\n" + "=" * 60)
    print("IMPORTING SENATE VOTES (Senate.gov XML)")
    print("=" * 60)
    print(f"Congress: {congress}")

    # Build LIS → bioguide mapping
    lis_map = build_lis_to_bioguide_map(conn)
    if not lis_map:
        print("\n  WARNING: No LIS IDs found in congress_members table.")
        print("  Run import_congress_legislators.py first to populate lis_id.")
        return {
            "roll_calls_imported": 0,
            "roll_calls_failed": 0,
            "member_votes": 0,
            "linked_legislation": 0,
        }

    total_stats = {
        "roll_calls_imported": 0,
        "roll_calls_failed": 0,
        "member_votes": 0,
        "linked_legislation": 0,
    }

    sessions = [session] if session else [1, 2]

    for sess in sessions:
        stats = import_session_votes(conn, congress, sess, lis_map)

        total_stats["roll_calls_imported"] += stats["imported"]
        total_stats["roll_calls_failed"] += stats["failed"]
        total_stats["member_votes"] += stats["member_votes"]
        total_stats["linked_legislation"] += stats["linked_legislation"]

        print(f"\n    Senate Session {sess} complete:")
        print(f"      Roll calls: {stats['imported']}/{stats['total']}")
        print(f"      Member votes: {stats['member_votes']:,}")
        print(f"      Linked to legislation: {stats['linked_legislation']}")

    return total_stats


def update_metadata(conn: sqlite3.Connection, stats: Dict[str, int]) -> None:
    """Update database metadata with Senate vote counts."""
    print("\n" + "=" * 60)
    print("UPDATING METADATA")
    print("=" * 60)

    conn.execute("""
        INSERT OR REPLACE INTO metadata (key, value, updated_at)
        VALUES ('senate_roll_calls', ?, CURRENT_TIMESTAMP)
    """, (str(stats["roll_calls_imported"]),))

    conn.execute("""
        INSERT OR REPLACE INTO metadata (key, value, updated_at)
        VALUES ('senate_member_votes', ?, CURRENT_TIMESTAMP)
    """, (str(stats["member_votes"]),))

    conn.execute("""
        INSERT OR REPLACE INTO metadata (key, value, updated_at)
        VALUES ('senate_votes_last_updated', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """)

    conn.commit()
    print("  Metadata updated")


def verify_import(conn: sqlite3.Connection) -> None:
    """Verify the Senate import was successful."""
    print("\n" + "=" * 60)
    print("SENATE VOTE IMPORT VERIFICATION")
    print("=" * 60)

    # Votes by chamber and session
    print("\nVotes by chamber and session:")
    results = conn.execute("""
        SELECT chamber, session, COUNT(*) as vote_count
        FROM roll_call_votes
        GROUP BY chamber, session
        ORDER BY chamber, session
    """).fetchall()

    for chamber, session, count in results:
        print(f"  {chamber} Session {session}: {count:,} votes")

    # Senate member votes by position
    print("\nSenate member votes by position:")
    results = conn.execute("""
        SELECT mv.vote_position, COUNT(*) as count
        FROM member_votes mv
        JOIN roll_call_votes rv ON mv.roll_call_id = rv.id
        WHERE rv.chamber = 'Senate'
        GROUP BY mv.vote_position
        ORDER BY count DESC
    """).fetchall()

    for position, count in results:
        print(f"  {position}: {count:,}")

    # Legislation linkage
    print("\nSenate legislation linkage:")
    result = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN legislation_id IS NOT NULL THEN 1 ELSE 0 END) as linked
        FROM roll_call_votes
        WHERE chamber = 'Senate'
    """).fetchone()

    total = result[0] if result and result[0] else 0
    linked = result[1] if result and result[1] else 0
    pct = (linked / total * 100) if total > 0 else 0
    print(f"  Total Senate roll calls: {total:,}")
    print(f"  Linked to legislation: {linked:,} ({pct:.1f}%)")

    # LIS mapping coverage
    print("\nLIS ID mapping coverage:")
    result = conn.execute("""
        SELECT COUNT(*) FROM congress_members
        WHERE chamber = 'Senate' AND lis_id IS NOT NULL
    """).fetchone()
    print(f"  Senators with LIS ID: {result[0] if result else 0}")

    # Sample recent Senate votes
    print("\nRecent Senate votes:")
    samples = conn.execute("""
        SELECT id, vote_date, vote_question, vote_result
        FROM roll_call_votes
        WHERE chamber = 'Senate'
        ORDER BY vote_date DESC, roll_number DESC
        LIMIT 5
    """).fetchall()

    for vote_id, date, question, result in samples:
        q_short = (question[:50] + "...") if question and len(question) > 50 else question
        print(f"  {vote_id}: {date} - {q_short} [{result}]")

    print("\n  Verification complete")


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Import Senate roll call vote data from Senate.gov XML"
    )
    parser.add_argument(
        "--congress", type=int, default=_CFG_CONGRESS,
        help=f"Congress number (default: {_CFG_CONGRESS})"
    )
    parser.add_argument(
        "--session", type=int, choices=[1, 2],
        help="Session (1 or 2, default: both)"
    )
    parser.add_argument(
        "--verify-only", action="store_true",
        help="Only run verification"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("SENATE VOTE IMPORT (Senate.gov XML)")
    print("=" * 60)

    try:
        conn = create_connection()

        if args.verify_only:
            verify_import(conn)
            conn.close()
            return 0

        # Import votes
        stats = import_all_senate_votes(conn, args.congress, args.session)

        # Update metadata
        update_metadata(conn, stats)

        # Verify import
        verify_import(conn)

        conn.close()

        print("\n" + "=" * 60)
        print("IMPORT COMPLETE")
        print("=" * 60)
        print(f"Roll calls imported: {stats['roll_calls_imported']:,}")
        print(f"Roll calls failed: {stats['roll_calls_failed']:,}")
        print(f"Member votes: {stats['member_votes']:,}")
        print(f"Linked to legislation: {stats['linked_legislation']:,}")

        return 0

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
