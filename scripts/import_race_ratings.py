#!/usr/bin/env python3
"""
Import race ratings into election_races table.

Sources (in priority order):
  1. FiveThirtyEight election-forecasts GitHub CSV
  2. Local data/race_ratings.csv fallback

Usage:
    python3 scripts/import_race_ratings.py
    python3 scripts/import_race_ratings.py --source local
    python3 scripts/import_race_ratings.py --cycle 2026
"""

import argparse
import csv
import io
import sqlite3
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

from config import DB_PATH, PROJECT_ROOT

# ---------------------------------------------------------------------------
# Rating normalization
# ---------------------------------------------------------------------------

# Map various rating strings to our canonical set
RATING_MAP = {
    # FiveThirtyEight style
    "solid d": "solid_d",
    "solid dem": "solid_d",
    "safe d": "solid_d",
    "safe dem": "solid_d",
    "likely d": "likely_d",
    "likely dem": "likely_d",
    "lean d": "lean_d",
    "lean dem": "lean_d",
    "tilt d": "lean_d",
    "tilt dem": "lean_d",
    "toss-up": "tossup",
    "tossup": "tossup",
    "toss up": "tossup",
    "lean r": "lean_r",
    "lean rep": "lean_r",
    "tilt r": "lean_r",
    "tilt rep": "lean_r",
    "likely r": "likely_r",
    "likely rep": "likely_r",
    "solid r": "solid_r",
    "solid rep": "solid_r",
    "safe r": "solid_r",
    "safe rep": "solid_r",
    # Already canonical
    "solid_d": "solid_d",
    "likely_d": "likely_d",
    "lean_d": "lean_d",
    "lean_r": "lean_r",
    "likely_r": "likely_r",
    "solid_r": "solid_r",
}


def normalize_rating(raw: str) -> str | None:
    """Normalize a rating string to our canonical form."""
    if not raw:
        return None
    key = raw.strip().lower()
    return RATING_MAP.get(key)


# ---------------------------------------------------------------------------
# FiveThirtyEight source
# ---------------------------------------------------------------------------

# Possible URLs for FiveThirtyEight data
FIVETHIRTYEIGHT_URLS = [
    "https://projects.fivethirtyeight.com/2026-election-forecast/data/house_district_toplines_2026.csv",
    "https://projects.fivethirtyeight.com/2026-election-forecast/data/senate_state_toplines_2026.csv",
]


def fetch_fivethirtyeight(cycle: int) -> list[dict]:
    """Try to fetch race ratings from FiveThirtyEight."""
    ratings = []

    for url in FIVETHIRTYEIGHT_URLS:
        url = url.replace("2026", str(cycle))
        try:
            print(f"  Trying: {url}")
            req = urllib.request.Request(
                url, headers={"User-Agent": "CivicCache/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read().decode("utf-8")

            reader = csv.DictReader(io.StringIO(data))
            for row in reader:
                # FiveThirtyEight CSV columns vary but typically include:
                # state, district, rating, expression (for confidence)
                state = row.get("state", row.get("State", "")).strip()
                district = row.get("district", row.get("District", "")).strip()
                raw_rating = row.get("rating", row.get("Rating", "")).strip()

                if not state or not raw_rating:
                    continue

                rating = normalize_rating(raw_rating)
                if not rating:
                    continue

                # Determine office from URL/context
                office = "H" if "house" in url.lower() else "S"

                ratings.append({
                    "office": office,
                    "state": state,
                    "district": district if office == "H" else None,
                    "rating": rating,
                    "source": "fivethirtyeight",
                    "date": datetime.now().strftime("%Y-%m-%d"),
                })

            print(f"    Fetched {len(ratings)} ratings from {url}")

        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            print(f"    Not available: {e}")
            continue

    return ratings


# ---------------------------------------------------------------------------
# Local CSV source
# ---------------------------------------------------------------------------

LOCAL_CSV = PROJECT_ROOT / "data" / "race_ratings.csv"


def load_local_csv() -> list[dict]:
    """Load ratings from local CSV fallback.

    Expected format:
        office,state,district,rating,source,date
        H,CA,12,lean_d,cook,2026-01-15
        S,AZ,,tossup,cook,2026-01-15
    """
    if not LOCAL_CSV.exists():
        print(f"  Local CSV not found: {LOCAL_CSV}")
        return []

    ratings = []
    with open(LOCAL_CSV, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            office = row.get("office", "").strip()
            state = row.get("state", "").strip()
            district = row.get("district", "").strip() or None
            raw_rating = row.get("rating", "").strip()
            source = row.get("source", "manual").strip()
            date = row.get("date", datetime.now().strftime("%Y-%m-%d")).strip()

            if not office or not state or not raw_rating:
                continue

            rating = normalize_rating(raw_rating)
            if not rating:
                print(f"    Warning: unknown rating '{raw_rating}' for {office}-{state}-{district}")
                continue

            ratings.append({
                "office": office,
                "state": state,
                "district": district,
                "rating": rating,
                "source": source,
                "date": date,
            })

    print(f"  Loaded {len(ratings)} ratings from {LOCAL_CSV}")
    return ratings


# ---------------------------------------------------------------------------
# Apply ratings to election_races
# ---------------------------------------------------------------------------


def apply_ratings(conn: sqlite3.Connection, ratings: list[dict], cycle: int) -> int:
    """Match ratings to election_races and update."""
    updated = 0

    for r in ratings:
        office = r["office"]
        state = r["state"]
        district = r["district"]

        # Build race_id to match
        if office == "H" and district:
            race_id = f"{cycle}-{office}-{state}-{district.zfill(2)}"
        elif office == "S":
            race_id = f"{cycle}-{office}-{state}"
        elif office == "P":
            race_id = f"{cycle}-{office}"
        else:
            race_id = f"{cycle}-{office}-{state}"

        result = conn.execute(
            """
            UPDATE election_races
            SET rating = ?, rating_source = ?, rating_date = ?
            WHERE race_id = ?
            """,
            (r["rating"], r["source"], r["date"], race_id),
        )

        if result.rowcount > 0:
            updated += 1

    conn.commit()
    return updated


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Import race ratings")
    parser.add_argument(
        "--source",
        choices=["auto", "fivethirtyeight", "local"],
        default="auto",
        help="Rating source (default: auto -- try 538 first, then local)",
    )
    parser.add_argument(
        "--cycle",
        type=int,
        default=2026,
        help="Election cycle (default: 2026)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help=f"Database path (default: {DB_PATH})",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("CivicCache - Race Ratings Import")
    print("=" * 60)
    print(f"  Database: {args.db}")
    print(f"  Cycle:    {args.cycle}")
    print(f"  Source:   {args.source}")

    if not args.db.exists():
        print(f"\nERROR: Database not found: {args.db}")
        return 1

    conn = sqlite3.connect(str(args.db))
    conn.execute("PRAGMA foreign_keys = ON")

    # Check that election_races exists
    table_check = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='election_races'"
    ).fetchone()[0]
    if not table_check:
        print("\nERROR: election_races table not found. Run import_fec_candidates.py first.")
        conn.close()
        return 1

    try:
        ratings = []

        if args.source in ("auto", "fivethirtyeight"):
            print("\n  Trying FiveThirtyEight...")
            ratings = fetch_fivethirtyeight(args.cycle)

        if not ratings and args.source in ("auto", "local"):
            print("\n  Trying local CSV...")
            ratings = load_local_csv()

        if not ratings:
            print("\n  No ratings available from any source.")
            print("  Create data/race_ratings.csv with format:")
            print("    office,state,district,rating,source,date")
            print("    H,CA,12,lean_d,cook,2026-01-15")
            return 0

        print(f"\n  Applying {len(ratings)} ratings...")
        updated = apply_ratings(conn, ratings, args.cycle)

        # Update metadata
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value, updated_at) VALUES (?, ?, ?)",
            ("race_ratings_imported", datetime.now().isoformat(), datetime.now().isoformat()),
        )
        conn.commit()

        print(f"\n  Updated {updated} races with ratings")

        # Show summary
        rated = conn.execute(
            "SELECT rating, COUNT(*) FROM election_races WHERE election_cycle = ? AND rating IS NOT NULL GROUP BY rating ORDER BY rating",
            (args.cycle,),
        ).fetchall()
        if rated:
            print("\n  Rating distribution:")
            for rating, count in rated:
                print(f"    {rating:12s}  {count:>4d}")

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
