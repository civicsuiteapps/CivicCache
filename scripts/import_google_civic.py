#!/usr/bin/env python3
"""
Import candidate contact info from Google Civic Information API.

Enriches race_candidates with campaign website, phone, email, photo URL,
and social media links. Runs AFTER fec_candidates (same pattern as
import_candidate_photos.py -- UPDATE existing rows, don't INSERT).

Uses the voterInfoQuery endpoint (the representatives endpoint was
retired in April 2025). Contest/candidate data is typically only available
from Google when election officials have loaded it -- often weeks before
an election, not months.

Usage:
    python3 scripts/import_google_civic.py                    # All federal races
    python3 scripts/import_google_civic.py --cycle 2026       # Current cycle only
    python3 scripts/import_google_civic.py --dry-run           # Show matches, don't write
    python3 scripts/import_google_civic.py --state CA          # Single state
"""

import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from config import DB_PATH as _CFG_DB, GOOGLE_CIVIC_API_KEY
except ImportError:
    _CFG_DB = Path("data/civic_cache.db")
    GOOGLE_CIVIC_API_KEY = ""

DB_PATH = _CFG_DB

USER_AGENT = "CivicCache/1.0"

CIVIC_API_BASE = "https://www.googleapis.com/civicinfo/v2"

# FEC name suffixes to strip during normalization
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v", "md", "phd", "esq"}


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------


def normalize_name(name: str, fec_format: bool = False) -> tuple[str, str]:
    """Normalize a candidate name to (first, last) for matching.

    Args:
        name: Raw name string.
        fec_format: If True, expect FEC "LAST, FIRST MIDDLE" format.
                    If False, expect Google "First Middle Last" format.

    Returns:
        (first, last) tuple, lowercased, with suffixes stripped.
    """
    # Strip periods, extra whitespace
    cleaned = name.replace(".", "").replace("'", "").replace('"', "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned).lower()

    if fec_format:
        # FEC: "LAST, FIRST MIDDLE SUFFIX" or "LAST SUFFIX, FIRST MIDDLE"
        parts = cleaned.split(",", 1)
        if len(parts) == 2:
            last_tokens = parts[0].strip().split()
            first_tokens = parts[1].strip().split()
        else:
            # No comma -- unusual, treat as "First Last"
            tokens = cleaned.split()
            first_tokens = tokens[:1]
            last_tokens = tokens[1:]
    else:
        # Google: "First Middle Last Suffix"
        tokens = cleaned.split()
        # Remove suffix tokens from the end
        while tokens and tokens[-1] in SUFFIXES:
            tokens.pop()
        if len(tokens) >= 2:
            first_tokens = [tokens[0]]
            last_tokens = [tokens[-1]]
        elif tokens:
            first_tokens = tokens
            last_tokens = []
        else:
            return ("", "")

    # Remove suffix tokens
    last_tokens = [t for t in last_tokens if t not in SUFFIXES]
    first_tokens = [t for t in first_tokens if t not in SUFFIXES]

    first = first_tokens[0] if first_tokens else ""
    last = last_tokens[0] if last_tokens else ""

    return (first, last)


# ---------------------------------------------------------------------------
# Google Civic API calls
# ---------------------------------------------------------------------------


def get_elections(api_key: str) -> list[dict]:
    """Fetch available elections from Google Civic API."""
    url = f"{CIVIC_API_BASE}/elections?key={api_key}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return data.get("elections", [])
    except urllib.error.HTTPError as e:
        print(f"  ERROR fetching elections: HTTP {e.code}")
        return []


def get_election_ids(api_key: str) -> list[tuple[str, str]]:
    """Get all available election IDs from the elections endpoint.

    Returns list of (election_id, election_name) tuples.
    Excludes the VIP Test Election (id=2000) unless it's the only one.
    """
    elections = get_elections(api_key)
    if not elections:
        return []

    real = [(e["id"], e.get("name", "?")) for e in elections if e.get("id") != "2000"]
    if real:
        for eid, name in real:
            print(f"  Found election: {name} (id={eid})")
        return real

    # Fall back to test election
    test = [(e["id"], e.get("name", "?")) for e in elections if e.get("id") == "2000"]
    if test:
        print(f"  Using VIP Test Election (only election available)")
    return test


def call_voter_info(
    api_key: str, address: str, election_id: str, retries: int = 3
) -> dict | None:
    """Call the voterInfoQuery endpoint.

    Returns parsed JSON with contests and candidates, or None on failure.
    """
    encoded_addr = urllib.parse.quote(address)
    url = (
        f"{CIVIC_API_BASE}/voterinfo"
        f"?key={api_key}&address={encoded_addr}&electionId={election_id}"
    )

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 2 ** (attempt + 1)
                print(f"  Rate limited, waiting {wait}s...")
                time.sleep(wait)
            elif e.code in (400, 404):
                return None
            else:
                if attempt == retries - 1:
                    return None
                time.sleep(1)
        except Exception:
            if attempt == retries - 1:
                return None
            time.sleep(1)

    return None


# ---------------------------------------------------------------------------
# Data extraction
# ---------------------------------------------------------------------------


def extract_candidate_fields(candidate: dict) -> dict:
    """Extract contact and social fields from a voterInfoQuery candidate dict.

    The candidate object in voterInfoQuery has slightly different field names
    than the representatives endpoint.
    """
    fields = {
        "campaign_url": candidate.get("candidateUrl"),
        "campaign_phone": candidate.get("phone"),
        "campaign_email": candidate.get("email"),
        "photo_url": candidate.get("photoUrl"),
        "social_facebook": None,
        "social_twitter": None,
        "social_youtube": None,
    }

    # Social channels
    for channel in candidate.get("channels", []):
        ctype = channel.get("type", "").lower()
        cid = channel.get("id", "")
        if not cid:
            continue

        if ctype == "facebook":
            fields["social_facebook"] = f"https://www.facebook.com/{cid}"
        elif ctype == "twitter":
            fields["social_twitter"] = f"https://x.com/{cid}"
        elif ctype == "youtube":
            fields["social_youtube"] = f"https://www.youtube.com/{cid}"

    return fields


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def get_district_addresses(conn: sqlite3.Connection, state: str | None = None) -> dict:
    """Get a representative address per (state, district) from zip_districts.

    Returns dict: {(state_abbr, district_int): "zip_code, STATE"}
    The state suffix helps Google geocode the zip correctly.
    """
    sql = "SELECT state_abbr, district, MIN(zip_code) FROM zip_districts"
    params = []
    if state:
        sql += " WHERE state_abbr = ?"
        params.append(state.upper())
    sql += " GROUP BY state_abbr, district"

    rows = conn.execute(sql, params).fetchall()
    # Format as "zip_code, ST" so Google can geocode it
    return {(row[0], row[1]): f"{row[2]}, {row[0]}" for row in rows}


def get_race_candidates(
    conn: sqlite3.Connection, cycle: int | None, state: str | None = None
) -> list[dict]:
    """Get race_candidates for matching.

    Returns list of dicts with candidate info + race info.
    """
    sql = """
    SELECT rc.candidate_id, rc.candidate_name, rc.party_code,
           rc.race_id, er.state, er.district
    FROM race_candidates rc
    JOIN election_races er ON er.race_id = rc.race_id
    WHERE 1=1
    """
    params = []
    if cycle:
        sql += " AND rc.election_cycle = ?"
        params.append(cycle)
    if state:
        sql += " AND er.state = ?"
        params.append(state.upper())

    rows = conn.execute(sql, params).fetchall()
    candidates = []
    for row in rows:
        first, last = normalize_name(row[1], fec_format=True)
        candidates.append({
            "candidate_id": row[0],
            "candidate_name": row[1],
            "party_code": row[2],
            "race_id": row[3],
            "state": row[4],
            "district": row[5],
            "norm_first": first,
            "norm_last": last,
        })
    return candidates


def update_candidate(conn: sqlite3.Connection, candidate_id: str, fields: dict) -> int:
    """UPDATE race_candidates with Google Civic fields.

    Updates all rows for this candidate_id (may span multiple races).
    Only sets non-None values, preserving any existing data.
    """
    set_clauses = []
    values = []
    for col in [
        "campaign_url", "campaign_phone", "campaign_email",
        "photo_url", "social_facebook", "social_twitter", "social_youtube",
    ]:
        if fields.get(col) is not None:
            set_clauses.append(f"{col} = ?")
            values.append(fields[col])

    if not set_clauses:
        return 0

    values.append(candidate_id)
    sql = f"UPDATE race_candidates SET {', '.join(set_clauses)} WHERE candidate_id = ?"
    result = conn.execute(sql, values)
    return result.rowcount


# ---------------------------------------------------------------------------
# Matching logic
# ---------------------------------------------------------------------------

# Map Google party names to FEC party codes
PARTY_MAP = {
    "democratic": "DEM",
    "republican": "REP",
    "libertarian": "LIB",
    "green": "GRE",
    "independent": "IND",
    "democrat": "DEM",
    "democratic party": "DEM",
    "republican party": "REP",
    "nonpartisan": "IND",
}


def google_party_to_fec(party: str) -> str | None:
    """Convert Google Civic party name to FEC 3-letter code."""
    if not party:
        return None
    return PARTY_MAP.get(party.lower().strip())


def match_google_candidate(
    gcandidate: dict, db_candidates: list[dict], state: str
) -> dict | None:
    """Try to match a Google voterInfoQuery candidate to an FEC race_candidate.

    Uses conservative exact-match on (first, last) normalized names.
    Requires party match when available to disambiguate.
    Returns the matched db candidate dict, or None.
    """
    google_name = gcandidate.get("name", "")
    google_first, google_last = normalize_name(google_name, fec_format=False)

    if not google_first or not google_last:
        return None

    # Filter candidates in the same state
    state_candidates = [c for c in db_candidates if c["state"] == state]

    # Find exact name matches
    matches = [
        c for c in state_candidates
        if c["norm_first"] == google_first and c["norm_last"] == google_last
    ]

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        # Try to disambiguate by party
        google_party = google_party_to_fec(gcandidate.get("party", ""))
        if google_party:
            party_matches = [c for c in matches if c["party_code"] == google_party]
            if len(party_matches) == 1:
                return party_matches[0]

        # Ambiguous -- skip rather than risk wrong match
        return None

    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import candidate contact info from Google Civic Information API"
    )
    parser.add_argument(
        "--cycle", type=int, default=None,
        help="Only update candidates in this election cycle (default: all)",
    )
    parser.add_argument(
        "--state", type=str, default=None,
        help="Only process a single state (e.g., CA)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show matches without writing to the database",
    )
    parser.add_argument(
        "--db", type=Path, default=DB_PATH,
        help=f"Database path (default: {DB_PATH})",
    )
    args = parser.parse_args()

    api_key = GOOGLE_CIVIC_API_KEY
    if not api_key:
        print("ERROR: GOOGLE_CIVIC_API_KEY not set in environment or .env")
        return 1

    print("=" * 60)
    print("CivicCache - Google Civic Information Import")
    print("=" * 60)
    print(f"  Database: {args.db}")
    print(f"  Cycle:    {args.cycle or 'all'}")
    print(f"  State:    {args.state or 'all'}")
    print(f"  Dry run:  {args.dry_run}")

    if not args.db.exists():
        print(f"\nERROR: Database not found: {args.db}")
        return 1

    conn = sqlite3.connect(str(args.db))

    # Phase 1: Find available elections
    print("\n--- Phase 1: Finding elections ---")
    election_ids = get_election_ids(api_key)

    if not election_ids:
        print("  No elections available from Google Civic API.")
        print("  Contest data is typically only available near election dates.")
        print("  The pipeline will populate data when elections approach.")
        conn.close()
        return 0  # Not an error -- expected between election cycles

    # Phase 2: Get addresses per district
    print("\n--- Phase 2: Building district queries ---")
    district_addrs = get_district_addresses(conn, args.state)
    print(f"  Districts with addresses: {len(district_addrs)}")

    if not district_addrs:
        print("  ERROR: No zip_districts data found. Run import_zip_districts.py first.")
        conn.close()
        return 1

    # Phase 3: Load FEC candidates for matching
    print("\n--- Phase 3: Loading FEC candidates ---")
    db_candidates = get_race_candidates(conn, args.cycle, args.state)
    print(f"  Candidates loaded: {len(db_candidates)}")

    # Phase 4: Query voterInfoQuery for each election + district
    total_queries = len(election_ids) * len(district_addrs)
    print(f"\n--- Phase 4: Querying Google Civic API ---")
    print(f"  Elections: {len(election_ids)}, Districts: {len(district_addrs)}")
    print(f"  Max API calls: {total_queries}")

    stats = {
        "api_calls": 0,
        "contests_found": 0,
        "federal_contests": 0,
        "candidates_found": 0,
        "matched": 0,
        "unmatched": 0,
        "updated_rows": 0,
        "skipped_no_fields": 0,
    }

    matched_candidates = set()
    sorted_districts = sorted(district_addrs.keys())

    for eid, ename in election_ids:
        print(f"\n  Election: {ename} (id={eid})")

        for i, (state_abbr, district) in enumerate(sorted_districts, 1):
            address = district_addrs[(state_abbr, district)]

            # Progress every 50 districts
            if i % 50 == 0 or i == len(sorted_districts):
                print(
                    f"    Progress: {i}/{len(sorted_districts)} districts "
                    f"({stats['matched']} matched)"
                )

            data = call_voter_info(api_key, address, eid)
            stats["api_calls"] += 1

            if not data:
                time.sleep(0.1)
                continue

            contests = data.get("contests", [])
            if not contests:
                time.sleep(0.1)
                continue

            for contest in contests:
                stats["contests_found"] += 1

                # Filter to federal-level contests
                level = contest.get("level", [])
                roles = contest.get("roles", [])
                office = contest.get("office", "")

                is_federal = (
                    "country" in level
                    or "federal" in level
                    or "legislatorUpperBody" in roles
                    or "legislatorLowerBody" in roles
                    or "headOfState" in roles
                    or "U.S. Senator" in office
                    or "U.S. Representative" in office
                    or "U.S. House" in office
                    or "U.S. Senate" in office
                )

                if not is_federal:
                    continue

                stats["federal_contests"] += 1

                for gcandidate in contest.get("candidates", []):
                    stats["candidates_found"] += 1

                    match = match_google_candidate(gcandidate, db_candidates, state_abbr)

                    if not match:
                        stats["unmatched"] += 1
                        continue

                    if match["candidate_id"] in matched_candidates:
                        continue  # Already processed

                    matched_candidates.add(match["candidate_id"])
                    stats["matched"] += 1

                    fields = extract_candidate_fields(gcandidate)

                    useful_fields = {k: v for k, v in fields.items() if v is not None}
                    if not useful_fields:
                        stats["skipped_no_fields"] += 1
                        continue

                    if args.dry_run:
                        print(
                            f"      MATCH: {gcandidate.get('name')} -> "
                            f"{match['candidate_name']} ({match['candidate_id']})"
                        )
                        for k, v in useful_fields.items():
                            print(f"        {k}: {v}")
                    else:
                        rows = update_candidate(conn, match["candidate_id"], fields)
                        stats["updated_rows"] += rows

                        if stats["matched"] % 50 == 0:
                            conn.commit()

            # Rate limit between API calls
            time.sleep(0.5)

    if not args.dry_run:
        conn.commit()

        # Update metadata
        from datetime import datetime

        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            ("google_civic_last_import", datetime.now().isoformat()),
        )
        conn.commit()

    # Phase 5: Verification
    print("\n--- Verification ---")
    for col in [
        "campaign_url", "campaign_phone", "campaign_email",
        "photo_url", "social_facebook", "social_twitter", "social_youtube",
    ]:
        count = conn.execute(
            f"SELECT COUNT(*) FROM race_candidates WHERE {col} IS NOT NULL"
        ).fetchone()[0]
        print(f"  {col}: {count} candidates")

    conn.close()

    print("\n" + "=" * 60)
    print("Import Complete")
    print("=" * 60)
    print(f"  API calls:            {stats['api_calls']}")
    print(f"  Contests found:       {stats['contests_found']}")
    print(f"  Federal contests:     {stats['federal_contests']}")
    print(f"  Google candidates:    {stats['candidates_found']}")
    print(f"  Matched to FEC:       {stats['matched']}")
    print(f"  Unmatched:            {stats['unmatched']}")
    print(f"  Skipped (no fields):  {stats['skipped_no_fields']}")
    if not args.dry_run:
        print(f"  DB rows updated:      {stats['updated_rows']}")

    if stats["contests_found"] == 0:
        print("\n  NOTE: No contest data available from Google Civic API.")
        print("  This is normal between election cycles. Data will appear")
        print("  as election officials load it (typically weeks before an election).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
