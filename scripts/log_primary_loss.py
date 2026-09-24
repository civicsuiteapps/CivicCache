#!/usr/bin/env python3
"""
Log incumbent members of Congress who lost their primary.

Sets the lost_primary flag (and primary_loss_cycle / primary_loss_date) on
congress_members. Source data for primary outcomes is entered by hand here --
the FEC-derived candidate listings never record who won or lost a primary, so
this is the canonical place to mark a defeated incumbent.

Also records WHO won the primary (primary_winner_candidate_id, an FEC
candidate id resolvable in race_candidates) when known. Leave it unset when
the nominee is still undecided -- e.g. the incumbent was eliminated in round
one but the top two finishers face a runoff.

Winners are additionally upserted into the primary_winners table keyed by
(race_id, party) -- the reimport-safe record of party nominees, since
import_fec_candidates.py drops/rebuilds race_candidates on every run. Join it
to race_candidates at runtime on (race_id, candidate_id). Use --race to log a
nominee for ANY race/party without touching a member row (e.g. the opposing
party's nominee in a race where the incumbent lost).

Usage:
    # Mark a member as having lost their primary (by bioguide id)
    python3 scripts/log_primary_loss.py --id B001234

    # Mark by name (matches full_name / last_name, case-insensitive)
    python3 scripts/log_primary_loss.py --name "Bowman" --date 2026-06-25

    # Record who beat them (name matched within the member's own race;
    # safe to re-run on an already-flagged member to add the winner later)
    python3 scripts/log_primary_loss.py --id B001234 --winner "Latimer" --date 2026-06-25

    # ...or by FEC candidate id directly
    python3 scripts/log_primary_loss.py --id B001234 --winner-id H4NY16087

    # Override cycle / date (defaults: 2026, today)
    python3 scripts/log_primary_loss.py --id B001234 --cycle 2026 --date 2026-08-04

    # Log a party nominee for any race (no member row touched)
    python3 scripts/log_primary_loss.py --race 2026-S-TX --winner "Talarico" --date 2026-03-03

    # Mark how the nomination was decided (default: primary)
    python3 scripts/log_primary_loss.py --id B001234 --winner "Latimer" --decided-by runoff

    # Clear the flag (also clears the winner)
    python3 scripts/log_primary_loss.py --id B001234 --unset

    # List everyone currently flagged / all recorded nominees
    python3 scripts/log_primary_loss.py --list
    python3 scripts/log_primary_loss.py --list-winners
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


def find_member_race(conn: sqlite3.Connection, bioguide_id: str, cycle: int):
    """Find the member's race_id for a cycle.

    Tries election_races.incumbent_bioguide_id first, then falls back to
    locating the race the member themselves filed in (via their FEC id in
    race_candidates) -- the incumbent attribution in election_races is
    FEC-derived and occasionally wrong (e.g. 2026-S-TX listed the state's
    other, not-up senator as incumbent).
    """
    row = conn.execute(
        """
        SELECT race_id FROM election_races
        WHERE incumbent_bioguide_id = ? AND election_cycle = ?
        """,
        (bioguide_id, cycle),
    ).fetchone()
    if row:
        return row[0]
    row = conn.execute(
        """
        SELECT rc.race_id
        FROM race_candidates rc
        JOIN congress_members m ON rc.candidate_id = m.fec_id
        WHERE m.id = ? AND rc.election_cycle = ?
        """,
        (bioguide_id, cycle),
    ).fetchone()
    return row[0] if row else None


def resolve_winner(conn: sqlite3.Connection, race_id: str, name_fragment: str):
    """Return list of (candidate_id, candidate_name, party_code) matches in a race."""
    return conn.execute(
        """
        SELECT candidate_id, candidate_name, party_code
        FROM race_candidates
        WHERE race_id = ? AND candidate_name LIKE ?
        ORDER BY total_receipts DESC
        """,
        (race_id, f"%{name_fragment}%"),
    ).fetchall()


def upsert_primary_winner(
    conn: sqlite3.Connection,
    race_id: str,
    candidate_id: str,
    cycle: int,
    won_date: str,
    decided_by: str,
    note: str = None,
) -> str:
    """Record a party nominee in primary_winners; returns the party stored.

    Refuses candidates with no race_candidates row: a winner that doesn't
    join to the race is invisible in the apps and trips the build's
    primary-winner integrity gate. If the FEC has retired/merged the ID,
    find the surviving one in race_candidates first (--list the race).
    """
    row = conn.execute(
        """
        SELECT party_code, candidate_name FROM race_candidates
        WHERE race_id = ? AND candidate_id = ?
        """,
        (race_id, candidate_id),
    ).fetchone()
    if row is None:
        raise ValueError(
            f"{candidate_id} has no race_candidates row for {race_id} — "
            f"refusing to record a dangling winner. Check the race's "
            f"candidates for the surviving FEC ID (IDs get merged/retired)."
        )
    party = row[0]
    conn.execute(
        """
        INSERT INTO primary_winners
            (race_id, party, candidate_id, election_cycle, won_date, decided_by, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(race_id, party) DO UPDATE SET
            candidate_id = excluded.candidate_id,
            election_cycle = excluded.election_cycle,
            won_date = excluded.won_date,
            decided_by = excluded.decided_by,
            note = excluded.note,
            updated_at = CURRENT_TIMESTAMP
        """,
        (race_id, party, candidate_id, cycle, won_date, decided_by, note),
    )
    return party


def list_winners(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT pw.race_id, pw.party, pw.candidate_id, rc.candidate_name,
               pw.won_date, pw.decided_by
        FROM primary_winners pw
        LEFT JOIN race_candidates rc
            ON rc.race_id = pw.race_id AND rc.candidate_id = pw.candidate_id
        ORDER BY pw.race_id, pw.party
        """
    ).fetchall()
    if not rows:
        print("No primary winners recorded.")
        return 0
    print(f"{len(rows)} primary winner(s) recorded:\n")
    for race_id, party, cid, cname, won_date, decided_by in rows:
        print(
            f"  {race_id}  {party:4s} {cname or '?'} [{cid}]  "
            f"won {won_date or '?'} ({decided_by or 'primary'})"
        )
    return 0


def list_flagged(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT m.id, m.full_name, m.state_code, m.district, m.current_party,
               m.primary_loss_cycle, m.primary_loss_date,
               m.primary_winner_candidate_id, rc.candidate_name
        FROM congress_members m
        LEFT JOIN race_candidates rc
            ON rc.candidate_id = m.primary_winner_candidate_id
           AND rc.election_cycle = m.primary_loss_cycle
        WHERE m.lost_primary = 1
        ORDER BY m.primary_loss_date, m.full_name
        """
    ).fetchall()
    if not rows:
        print("No members currently flagged as having lost a primary.")
        return 0
    print(f"{len(rows)} member(s) flagged as having lost a primary:\n")
    for r in rows:
        bid, name, st, dist, party, cycle, dt, winner_id, winner_name = r
        seat = f"{st}-{dist}" if dist else st
        if winner_id:
            beat_by = f"  beaten by {winner_name or '?'} [{winner_id}]"
        else:
            beat_by = "  (winner not yet recorded)"
        print(f"  [{cycle}] {name} ({party}, {seat})  lost {dt or '?'}  [{bid}]{beat_by}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Flag incumbents who lost their primary"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--id", help="Bioguide id of the member")
    group.add_argument("--name", help="Name to match (last_name / full_name)")
    group.add_argument(
        "--race",
        help="Race id (e.g. 2026-S-TX) -- log a nominee for any race/party "
        "without touching a member row; requires --winner or --winner-id",
    )
    parser.add_argument(
        "--cycle", type=int, default=2026, help="Election cycle (default: 2026)"
    )
    parser.add_argument(
        "--date",
        help="Primary date YYYY-MM-DD (default: today)",
    )
    winner_group = parser.add_mutually_exclusive_group()
    winner_group.add_argument(
        "--winner",
        help="Name of the primary winner (matched within the member's race)",
    )
    winner_group.add_argument(
        "--winner-id",
        help="FEC candidate id of the primary winner (skips name resolution)",
    )
    parser.add_argument(
        "--decided-by",
        default="primary",
        choices=["primary", "runoff", "top-two", "top-four", "rcv", "convention"],
        help="How the nomination was decided (default: primary)",
    )
    parser.add_argument(
        "--note", help="Optional note stored with the primary_winners row"
    )
    parser.add_argument(
        "--unset", action="store_true", help="Clear the flag instead of setting it"
    )
    parser.add_argument(
        "--list", action="store_true", help="List flagged members and exit"
    )
    parser.add_argument(
        "--list-winners",
        action="store_true",
        help="List all recorded primary winners and exit",
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

    if args.list_winners:
        rc = list_winners(conn)
        conn.close()
        return rc

    # Standalone race mode: record a nominee without touching a member row.
    if args.race:
        if not args.winner and not args.winner_id:
            parser.error("--race requires --winner or --winner-id")
        if args.winner_id:
            winner_id = args.winner_id
        else:
            matches = resolve_winner(conn, args.race, args.winner)
            if not matches:
                print(f"ERROR: no candidate matching '{args.winner}' in race {args.race}.")
                conn.close()
                return 1
            if len(matches) > 1:
                print(f"'{args.winner}' matched {len(matches)} candidates in {args.race}:\n")
                for cid, cname, cparty in matches:
                    print(f"  {cname} ({cparty})  [{cid}]")
                print("\nRe-run with --winner-id to disambiguate.")
                conn.close()
                return 1
            winner_id = matches[0][0]
        won_date = args.date or date.today().isoformat()
        try:
            party = upsert_primary_winner(
                conn, args.race, winner_id, args.cycle, won_date, args.decided_by, args.note
            )
        except ValueError as e:
            print(f"ERROR: {e}")
            conn.close()
            return 1
        conn.commit()
        print(
            f"Recorded {party} nominee for {args.race}: [{winner_id}] "
            f"won {won_date} ({args.decided_by})."
        )
        conn.close()
        return 0

    if not args.id and not args.name:
        parser.error("provide --id, --name, or --race (or --list / --list-winners)")

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
                primary_winner_candidate_id = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (bid,),
        )
        conn.commit()
        print(f"Cleared primary-loss flag for {name} ({party}, {seat}) [{bid}].")
        conn.close()
        return 0

    # Resolve the winner (if given) before writing anything.
    winner_id = None
    winner_label = ""
    winner_race_id = None
    if args.winner_id:
        winner_id = args.winner_id
        winner_race_id = find_member_race(conn, bid, args.cycle)
        row = conn.execute(
            """
            SELECT candidate_name, party_code FROM race_candidates
            WHERE candidate_id = ? AND election_cycle = ?
            """,
            (winner_id, args.cycle),
        ).fetchone()
        if row is None:
            print(
                f"ERROR: {winner_id} not found in race_candidates for cycle "
                f"{args.cycle} -- refusing to store a dangling winner ID. "
                f"FEC IDs get merged/retired; use --list on the race to find "
                f"the surviving ID."
            )
            conn.close()
            return 1
        winner_label = f"{row[0]} ({row[1]}) [{winner_id}]"
    elif args.winner:
        race_id = find_member_race(conn, bid, args.cycle)
        if race_id is None:
            print(f"ERROR: no {args.cycle} race found for {name} [{bid}].")
            conn.close()
            return 1
        matches = resolve_winner(conn, race_id, args.winner)
        if not matches:
            print(f"ERROR: no candidate matching '{args.winner}' in race {race_id}.")
            conn.close()
            return 1
        if len(matches) > 1:
            print(f"'{args.winner}' matched {len(matches)} candidates in {race_id}:\n")
            for cid, cname, cparty in matches:
                print(f"  {cname} ({cparty})  [{cid}]")
            print("\nRe-run with --winner-id to disambiguate.")
            conn.close()
            return 1
        winner_id, winner_name, winner_party = matches[0]
        winner_label = f"{winner_name} ({winner_party}) [{winner_id}]"
        winner_race_id = race_id

    loss_date = args.date or date.today().isoformat()
    if winner_id:
        conn.execute(
            """
            UPDATE congress_members
            SET lost_primary = 1, primary_loss_cycle = ?, primary_loss_date = ?,
                primary_winner_candidate_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (args.cycle, loss_date, winner_id, bid),
        )
        # Mirror into the reimport-safe nominee record.
        if winner_race_id:
            try:
                upsert_primary_winner(
                    conn, winner_race_id, winner_id, args.cycle, loss_date,
                    args.decided_by, args.note,
                )
            except ValueError as e:
                print(f"ERROR: {e}")
                conn.close()
                return 1
        else:
            print(
                f"WARNING: could not determine {args.cycle} race for {name}; "
                "primary_winners row not written."
            )
    else:
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
    beat_by = f", beaten by {winner_label}" if winner_id else ""
    print(
        f"Flagged {name} ({party}, {seat}) as having lost the {args.cycle} "
        f"primary on {loss_date}{beat_by}. [{bid}]"
    )

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
