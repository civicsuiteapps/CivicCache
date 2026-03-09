#!/usr/bin/env python3
"""
Import congressional roll call vote data from Congress.gov API into CivicCache database.

This script:
1. Fetches roll call votes from the House and Senate
2. Downloads individual member voting records
3. Matches votes to legislation when possible
4. Stores everything in the database for offline access

API Endpoints Used:
- GET /house-vote/{congress}/{session} - List House roll calls
- GET /house-vote/{congress}/{session}/{rollNumber} - Vote details
- GET /house-vote/{congress}/{session}/{rollNumber}/members - Member votes
- GET /senate-vote/{congress}/{session} - List Senate roll calls
- GET /senate-vote/{congress}/{session}/{rollNumber} - Vote details
- GET /senate-vote/{congress}/{session}/{rollNumber}/members - Member votes
"""

import json
import re
import sqlite3
import urllib.request
import urllib.error
import time
import os
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

try:
    from config import CONGRESS_NUMBER as _CFG_CONGRESS, DB_PATH as _CFG_DB, CONGRESS_API_KEY as _CFG_KEY
except ImportError:
    _CFG_CONGRESS = 119
    _CFG_DB = Path("data/civic_cache.db")
    _CFG_KEY = ""

from utils import create_connection, api_request

# Database location
DB_PATH = _CFG_DB

# API configuration
CONGRESS_API_BASE = "https://api.congress.gov/v3"
CONGRESS_NUMBER = _CFG_CONGRESS

# Get API key from environment or use DEMO_KEY
API_KEY = os.environ.get("CONGRESS_GOV_API_KEY") or os.environ.get("CONGRESS_API_KEY") or _CFG_KEY or "DEMO_KEY"

BATCH_COMMIT_SIZE = 50  # Commit to DB every N votes

# Bill reference patterns for matching votes to legislation
BILL_PATTERNS = [
    # House bills
    (r'H\.?\s*R\.?\s*(\d+)', 'hr'),
    (r'H\.?\s*RES\.?\s*(\d+)', 'hres'),
    (r'H\.?\s*J\.?\s*RES\.?\s*(\d+)', 'hjres'),
    (r'H\.?\s*CON\.?\s*RES\.?\s*(\d+)', 'hconres'),
    # Senate bills
    (r'S\.?\s*(\d+)', 's'),
    (r'S\.?\s*RES\.?\s*(\d+)', 'sres'),
    (r'S\.?\s*J\.?\s*RES\.?\s*(\d+)', 'sjres'),
    (r'S\.?\s*CON\.?\s*RES\.?\s*(\d+)', 'sconres'),
]


def fetch_roll_call_list(congress: int, chamber: str, session: int) -> List[Dict]:
    """Fetch list of all roll calls for a chamber and session."""
    chamber_path = "house-vote" if chamber.lower() == "house" else "senate-vote"
    all_votes = []
    offset = 0
    limit = 250

    print(f"  Fetching {chamber} roll call list for Congress {congress}, Session {session}...")

    while True:
        url = f"{CONGRESS_API_BASE}/{chamber_path}/{congress}/{session}?offset={offset}&limit={limit}&api_key={API_KEY}&format=json"
        data = api_request(url)

        if not data:
            break

        # Handle different response structures
        # Congress.gov API uses 'houseRollCallVotes' and 'senateRollCallVotes'
        votes_key = "houseRollCallVotes" if chamber.lower() == "house" else "senateRollCallVotes"
        if votes_key not in data:
            # Try alternate keys
            votes_key = "houseVotes" if chamber.lower() == "house" else "senateVotes"
            if votes_key not in data:
                votes_key = "votes"
                if votes_key not in data:
                    break

        votes = data.get(votes_key, [])
        if not votes:
            break

        all_votes.extend(votes)

        total = data.get('pagination', {}).get('count', 0)
        print(f"    Fetched {len(all_votes)}/{total} roll calls...")

        if len(votes) < limit:
            break

        offset += limit

    return all_votes


def fetch_roll_call_details(congress: int, chamber: str, session: int, roll_number: int) -> Optional[Dict]:
    """Fetch detailed information for a specific roll call."""
    chamber_path = "house-vote" if chamber.lower() == "house" else "senate-vote"
    url = f"{CONGRESS_API_BASE}/{chamber_path}/{congress}/{session}/{roll_number}?api_key={API_KEY}&format=json"
    data = api_request(url)

    if not data:
        return None

    # Handle different response structures
    # Congress.gov uses 'houseRollCallVote' or 'senateRollCallVote'
    vote_key = "houseRollCallVote" if chamber.lower() == "house" else "senateRollCallVote"
    if vote_key in data:
        return data[vote_key]

    # Try alternate keys
    vote_key = "houseVote" if chamber.lower() == "house" else "senateVote"
    if vote_key in data:
        return data[vote_key]

    return data.get("vote")


def fetch_member_votes(congress: int, chamber: str, session: int, roll_number: int) -> List[Dict]:
    """Fetch individual member votes for a roll call."""
    chamber_path = "house-vote" if chamber.lower() == "house" else "senate-vote"
    url = f"{CONGRESS_API_BASE}/{chamber_path}/{congress}/{session}/{roll_number}/members?limit=500&api_key={API_KEY}&format=json"
    data = api_request(url)

    if not data:
        return []

    # Handle different response structures
    # Congress.gov uses 'houseRollCallVoteMemberVotes' or 'senateRollCallVoteMemberVotes'
    # with the actual members in a 'results' array
    member_votes_key = "houseRollCallVoteMemberVotes" if chamber.lower() == "house" else "senateRollCallVoteMemberVotes"
    if member_votes_key in data:
        member_data = data[member_votes_key]
        return member_data.get('results', [])

    # Try alternate structures
    if 'members' in data:
        return data['members']
    if 'results' in data:
        return data['results']

    return []


def match_legislation(conn: sqlite3.Connection, vote_data: Dict, congress: int) -> Optional[str]:
    """
    Attempt to match a vote to legislation based on the vote question text.
    Returns legislation_id if match found, None otherwise.
    """
    question_text = vote_data.get('question', '') or ''
    question_text += ' ' + (vote_data.get('questionText', '') or '')

    # Try each bill pattern
    for pattern, bill_type in BILL_PATTERNS:
        match = re.search(pattern, question_text, re.IGNORECASE)
        if match:
            bill_number = match.group(1)
            legislation_id = f"{congress}-{bill_type}-{bill_number}"

            # Verify the bill exists
            result = conn.execute(
                "SELECT id FROM legislation WHERE id = ?",
                (legislation_id,)
            ).fetchone()

            if result:
                return legislation_id

    return None


def import_roll_call(conn: sqlite3.Connection, vote_summary: Dict, congress: int,
                     chamber: str, session: int) -> Tuple[str, int]:
    """
    Import a single roll call vote with all its member votes.
    Returns (vote_id, member_vote_count).
    """
    roll_number = vote_summary.get('rollCallNumber') or vote_summary.get('rollNumber')
    if not roll_number:
        return None, 0

    vote_id = f"{congress}-{chamber.lower()}-{session}-{roll_number}"

    # Fetch detailed vote information
    vote_details = fetch_roll_call_details(congress, chamber, session, roll_number)
    if not vote_details:
        # Use summary data if details not available
        vote_details = vote_summary

    # Match to legislation based on legislationType and legislationNumber
    legislation_id = None
    leg_type = vote_details.get('legislationType', '').lower()
    leg_number = vote_details.get('legislationNumber')
    if leg_type and leg_number:
        legislation_id = f"{congress}-{leg_type}-{leg_number}"
        # Verify the bill exists
        result = conn.execute(
            "SELECT id FROM legislation WHERE id = ?",
            (legislation_id,)
        ).fetchone()
        if not result:
            legislation_id = None

    # If no direct match, try parsing from vote question
    if not legislation_id:
        legislation_id = match_legislation(conn, vote_details, congress)

    # Extract vote counts from votePartyTotal array
    yea_count = 0
    nay_count = 0
    present_count = 0
    not_voting_count = 0

    party_totals = vote_details.get('votePartyTotal', [])
    for party in party_totals:
        yea_count += party.get('yeaTotal', 0) or 0
        nay_count += party.get('nayTotal', 0) or 0
        present_count += party.get('presentTotal', 0) or 0
        not_voting_count += party.get('notVotingTotal', 0) or 0

    # Fallback to other formats if votePartyTotal not present
    if not party_totals:
        totals = vote_details.get('totals', {})
        yea_count = totals.get('yea', 0) or totals.get('yeas', 0) or vote_details.get('yeaCount', 0) or 0
        nay_count = totals.get('nay', 0) or totals.get('nays', 0) or vote_details.get('nayCount', 0) or 0
        present_count = totals.get('present', 0) or vote_details.get('presentCount', 0) or 0
        not_voting_count = totals.get('notVoting', 0) or vote_details.get('notVotingCount', 0) or 0

    # Extract vote date from startDate (ISO format)
    vote_date = vote_details.get('startDate', '')
    if vote_date and 'T' in vote_date:
        vote_date = vote_date.split('T')[0]

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
        chamber,
        session,
        roll_number,
        vote_date,
        None,  # vote_time (not separate in API)
        vote_details.get('voteQuestion'),
        vote_details.get('questionText'),
        vote_details.get('voteType'),
        vote_details.get('result'),
        vote_details.get('resultText'),
        yea_count,
        nay_count,
        present_count,
        not_voting_count,
        vote_details.get('requiredMajority'),
        legislation_id,
        vote_details.get('amendmentNumber'),
        vote_details.get('sourceDataURL')
    ))

    # Fetch and import member votes
    member_votes = fetch_member_votes(congress, chamber, session, roll_number)
    member_count = 0

    for member in member_votes:
        # API uses 'bioguideID' (uppercase D)
        bioguide_id = member.get('bioguideID') or member.get('bioguideId')
        if not bioguide_id:
            continue

        # API uses 'voteCast' for the vote position
        vote_position = member.get('voteCast') or member.get('votePosition') or member.get('vote')
        if not vote_position:
            continue

        # Construct member name from first/last
        first_name = member.get('firstName', '')
        last_name = member.get('lastName', '')
        member_name = f"{first_name} {last_name}".strip() if first_name or last_name else None

        try:
            conn.execute("""
                INSERT OR REPLACE INTO member_votes (
                    roll_call_id, bioguide_id, vote_position,
                    member_name, party, state
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                vote_id,
                bioguide_id,
                vote_position,
                member_name,
                member.get('voteParty') or member.get('party'),
                member.get('voteState') or member.get('state')
            ))
            member_count += 1
        except sqlite3.Error as e:
            # Skip if member doesn't exist in congress_members (foreign key)
            pass

    return vote_id, member_count


def import_chamber_votes(conn: sqlite3.Connection, congress: int, chamber: str,
                         session: int) -> Dict[str, int]:
    """Import all votes for a chamber and session."""
    print(f"\n  Importing {chamber} votes, Congress {congress}, Session {session}...")

    # Check for already-imported roll calls (resume support)
    imported_rolls = get_imported_rolls(conn, congress, chamber, session)
    if imported_rolls:
        print(f"    Already have {len(imported_rolls)} roll calls imported")

    # Fetch list of roll calls
    roll_calls = fetch_roll_call_list(congress, chamber, session)
    print(f"    Found {len(roll_calls)} roll calls from API")

    # Filter out already-imported roll calls
    if imported_rolls:
        roll_calls = [v for v in roll_calls
                      if (v.get('rollCallNumber') or v.get('rollNumber', 0)) not in imported_rolls]
        print(f"    {len(roll_calls)} new roll calls to import")

    stats = {
        'total': len(roll_calls),
        'imported': 0,
        'failed': 0,
        'member_votes': 0,
        'linked_legislation': 0
    }

    if not roll_calls:
        return stats

    start_time = time.time()

    for i, vote in enumerate(roll_calls, 1):
        roll_number = vote.get('rollCallNumber') or vote.get('rollNumber')
        try:
            vote_id, member_count = import_roll_call(conn, vote, congress, chamber, session)
            if vote_id:
                stats['imported'] += 1
                stats['member_votes'] += member_count

            # Progress update
            if i % 10 == 0:
                elapsed = time.time() - start_time
                rate = i / elapsed if elapsed > 0 else 0
                remaining = (len(roll_calls) - i) / rate if rate > 0 else 0
                print(f"    [{i}/{len(roll_calls)}] Roll {roll_number} "
                      f"({member_count} votes) - {remaining:.1f} min remaining")

            # Batch commit
            if i % BATCH_COMMIT_SIZE == 0:
                conn.commit()

        except Exception as e:
            print(f"    Failed roll call {roll_number}: {e}")
            stats['failed'] += 1

    conn.commit()

    # Count linked legislation
    result = conn.execute("""
        SELECT COUNT(*) FROM roll_call_votes
        WHERE congress = ? AND chamber = ? AND session = ? AND legislation_id IS NOT NULL
    """, (congress, chamber, session)).fetchone()
    stats['linked_legislation'] = result[0] if result else 0

    return stats


def get_imported_rolls(conn: sqlite3.Connection, congress: int, chamber: str,
                       session: int) -> set:
    """Get set of roll numbers already imported for resume capability."""
    results = conn.execute("""
        SELECT roll_number FROM roll_call_votes
        WHERE congress = ? AND chamber = ? AND session = ?
    """, (congress, chamber, session)).fetchall()

    return {r[0] for r in results}


def import_all_votes(conn: sqlite3.Connection, congress: int = 119,
                     session: Optional[int] = None,
                     chamber: Optional[str] = None) -> Dict[str, int]:
    """Import all votes for the specified Congress."""
    print("\n" + "=" * 60)
    print("IMPORTING CONGRESSIONAL VOTES")
    print("=" * 60)
    print(f"Congress: {congress}")
    print(f"API Key: {API_KEY[:10]}..." if API_KEY != "DEMO_KEY" else "API Key: DEMO_KEY (limited)")

    total_stats = {
        'roll_calls_imported': 0,
        'roll_calls_failed': 0,
        'member_votes': 0,
        'linked_legislation': 0
    }

    # Determine which sessions to import
    sessions = [session] if session else [1, 2]

    # Determine which chambers to import
    # Note: Congress.gov API only supports House votes as of 2025.
    # Senate vote endpoints are planned but not yet available.
    if chamber:
        chambers = [chamber]
    else:
        chambers = ['House']
        print("\n  Note: Senate vote API not yet available from Congress.gov.")
        print("  Importing House votes only.")

    for sess in sessions:
        for chamb in chambers:
            if chamb.lower() == 'senate':
                print(f"\n  Skipping Senate Session {sess} - API endpoint not yet available")
                continue

            stats = import_chamber_votes(conn, congress, chamb, sess)

            total_stats['roll_calls_imported'] += stats['imported']
            total_stats['roll_calls_failed'] += stats['failed']
            total_stats['member_votes'] += stats['member_votes']
            total_stats['linked_legislation'] += stats['linked_legislation']

            print(f"\n    {chamb} Session {sess} complete:")
            print(f"      Roll calls: {stats['imported']}/{stats['total']}")
            print(f"      Member votes: {stats['member_votes']:,}")
            print(f"      Linked to legislation: {stats['linked_legislation']}")

    return total_stats


def update_metadata(conn: sqlite3.Connection, stats: Dict[str, int]) -> None:
    """Update database metadata."""
    print("\n" + "=" * 60)
    print("UPDATING METADATA")
    print("=" * 60)

    conn.execute("""
        INSERT OR REPLACE INTO metadata (key, value, updated_at)
        VALUES ('total_roll_calls', ?, CURRENT_TIMESTAMP)
    """, (str(stats['roll_calls_imported']),))

    conn.execute("""
        INSERT OR REPLACE INTO metadata (key, value, updated_at)
        VALUES ('total_member_votes', ?, CURRENT_TIMESTAMP)
    """, (str(stats['member_votes']),))

    conn.execute("""
        INSERT OR REPLACE INTO metadata (key, value, updated_at)
        VALUES ('votes_last_updated', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """)

    conn.commit()
    print("  Metadata updated")


def verify_import(conn: sqlite3.Connection) -> None:
    """Verify the import was successful."""
    print("\n" + "=" * 60)
    print("IMPORT VERIFICATION")
    print("=" * 60)

    # Verify vote import by chamber and session
    print("\nVotes by chamber and session:")
    results = conn.execute("""
        SELECT chamber, session, COUNT(*) as vote_count
        FROM roll_call_votes WHERE congress = 119
        GROUP BY chamber, session
        ORDER BY chamber, session
    """).fetchall()

    for chamber, session, count in results:
        print(f"  {chamber} Session {session}: {count:,} votes")

    # Verify member votes by position
    print("\nMember votes by position:")
    results = conn.execute("""
        SELECT vote_position, COUNT(*) as count
        FROM member_votes
        GROUP BY vote_position
        ORDER BY count DESC
    """).fetchall()

    for position, count in results:
        print(f"  {position}: {count:,}")

    # Verify legislation linkage rate
    print("\nLegislation linkage:")
    result = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN legislation_id IS NOT NULL THEN 1 ELSE 0 END) as linked
        FROM roll_call_votes
    """).fetchone()

    total = result[0] if result and result[0] else 0
    linked = result[1] if result and result[1] else 0
    pct = (linked / total * 100) if total > 0 else 0
    print(f"  Total roll calls: {total:,}")
    print(f"  Linked to legislation: {linked:,} ({pct:.1f}%)")

    # Sample recent votes
    print("\nRecent votes:")
    samples = conn.execute("""
        SELECT id, vote_date, chamber, vote_question, vote_result
        FROM roll_call_votes
        ORDER BY vote_date DESC
        LIMIT 5
    """).fetchall()

    for vote_id, date, chamber, question, result in samples:
        q_short = (question[:50] + '...') if question and len(question) > 50 else question
        print(f"  {vote_id}: {date} - {q_short} [{result}]")

    print("\n  Verification complete")


def main():
    """Main execution function."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Import congressional roll call vote data from Congress.gov API'
    )
    parser.add_argument('--congress', type=int, default=119, help='Congress number (default: 119)')
    parser.add_argument('--session', type=int, choices=[1, 2], help='Session (1 or 2, default: both)')
    parser.add_argument('--chamber', choices=['House', 'Senate'],
                        help='Chamber (default: House only; Senate API not yet available)')
    parser.add_argument('--verify-only', action='store_true', help='Only run verification')

    args = parser.parse_args()

    print("=" * 60)
    print("CONGRESSIONAL VOTE IMPORT")
    print("=" * 60)

    try:
        conn = create_connection()

        if args.verify_only:
            verify_import(conn)
            conn.close()
            return 0

        # Import votes
        stats = import_all_votes(conn, args.congress, args.session, args.chamber)

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
