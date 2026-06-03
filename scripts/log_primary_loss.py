#!/usr/bin/env python3
"""
Log incumbent members of Congress who lost their primary.

Sets the lost_primary flag (and primary_loss_cycle / primary_loss_date) on
congress_members. Source data for primary outcomes is entered by hand here --
the FEC-derived candidate listings never record who won or lost a primary, so
this is the canonical place to mark a defeated incumbent.

Usage:
    # Mark a member as having lost their primary (by bioguide id)
    python3 scripts/log_primary_loss.py --id B001234

    # Mark by name (matches full_name / last_name, case-insensitive)
    python3 scripts/log_primary_loss.py --name "Bowman" --date 2026-06-25

    # Override cycle / date (defaults: 2026, today)
    python3 scripts/log_primary_loss.py --id B001234 --cycle 2026 --date 2026-08-04

    # Clear the flag
    python3 scripts/log_primary_loss.py --id B001234 --unset

    # List everyone currently flagged
    python3 scripts/log_primary_loss.py --list
"""

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

from config import DB_PATH


def resolve_members(conn: sqlite3.Connection, identifier: str, by_name: bool):
    """Return list of (id, full_name, state_code, district, current_party) matches."""
    if by_name:
        rows = conn.execute(
            """
            SELECT id, full_name, state_code, district, current_party
            FROM congress_members
            WHERE last_name LIKE ? OR full_name LIKE ?
            ORDER BY full_name
            """,
            (f"%{identifier}%", f"%{identifier}%"),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, full_name, state_code, district, current_party
            FROM congress_members
            WHERE id = ?
            """,
            (identifier,),
        ).fetchall()
    return rows


def list_flagged(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT id, full_name, state_code, district, current_party,
               primary_loss_cycle, primary_loss_date
        FROM congress_members
        WHERE lost_primary = 1
        ORDER BY primary_loss_date, full_name
        """
    ).fetchall()
    if not rows:
        print("No members currently flagged as having lost a primary.")
        return 0
    print(f"{len(rows)} member(s) flagged as having lost a primary:\n")
    for r in rows:
        bid, name, st, dist, party, cycle, dt = r
        seat = f"{st}-{dist}" if dist else st
        print(f"  [{cycle}] {name} ({party}, {seat})  lost {dt or '?'}  [{bid}]")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Flag incumbents who lost their primary"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--id", help="Bioguide id of the member")
    group.add_argument("--name", help="Name to match (last_name / full_name)")
    parser.add_argument(
        "--cycle", type=int, default=2026, help="Election cycle (default: 2026)"
    )
    parser.add_argument(
        "--date",
        help="Primary date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--unset", action="store_true", help="Clear the flag instead of setting it"
    )
    parser.add_argument(
        "--list", action="store_true", help="List flagged members and exit"
    )
    parser.add_argument(
        "--db", type=Path, default=DB_PATH, help=f"Database path (default: {DB_PATH})"
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"ERROR: Database not found: {args.db}")
        return 1

    conn = sqlite3.connect(str(args.db))

    if args.list:
        rc = list_flagged(conn)
        conn.close()
        return rc

    if not args.id and not args.name:
        parser.error("provide --id or --name (or --list)")

    identifier = args.id or args.name
    matches = resolve_members(conn, identifier, by_name=bool(args.name))

    if not matches:
        print(f"No member matched '{identifier}'.")
        conn.close()
        return 1
    if len(matches) > 1:
        print(f"'{identifier}' matched {len(matches)} members -- be more specific:\n")
        for bid, name, st, dist, party in matches:
            seat = f"{st}-{dist}" if dist else st
            print(f"  {name} ({party}, {seat})  [{bid}]")
        conn.close()
        return 1

    bid, name, st, dist, party = matches[0]
    seat = f"{st}-{dist}" if dist else st

    if args.unset:
        conn.execute(
            """
            UPDATE congress_members
            SET lost_primary = 0, primary_loss_cycle = NULL, primary_loss_date = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (bid,),
        )
        conn.commit()
        print(f"Cleared primary-loss flag for {name} ({party}, {seat}) [{bid}].")
    else:
        loss_date = args.date or date.today().isoformat()
        conn.execute(
            """
            UPDATE congress_members
            SET lost_primary = 1, primary_loss_cycle = ?, primary_loss_date = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (args.cycle, loss_date, bid),
        )
        conn.commit()
        print(
            f"Flagged {name} ({party}, {seat}) as having lost the {args.cycle} "
            f"primary on {loss_date}. [{bid}]"
        )

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
