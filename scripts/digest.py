#!/usr/bin/env python3
"""
CivicCache material-change digest.

Runs after every pipeline tier (see run_pipeline.sh) -- and works standalone:

    python3 scripts/digest.py --tier daily --pipeline-exit 0
    python3 scripts/digest.py --no-notify          # status.json only
    python3 scripts/digest.py --print              # also dump status to stdout

Evaluates the rules below against civic_cache.db (read-only), writes
~/Library/Application Support/CivicSuite/status/civiccache.json for the
CacheStatus menu-bar app, and pushes ntfy/Notification Center alerts for NEW
action/notable findings (alert-once per finding key; see cachecore.digest).

Rules:
  primaries          -- a primary/runoff on the primary_calendar just passed
                        (action: review results, log incumbent losses) or is
                        coming up within 3 days (info)
  pending_winners    -- flagged primary losers with no recorded winner yet
  became_law         -- bills that became public law since the last digest
  chamber_passage    -- bills that passed House/Senate since the last digest
  new_votes          -- new roll-call votes since the last digest (delta)
  membership_change  -- congress_members count moved (resignation/succession)
  vote_staleness     -- votes metadata not updated in 14+ days
  winner_integrity   -- primary_winners rows orphaned by an FEC reimport
                        (race_candidates is dropped/rebuilt on every import)
"""

import argparse
import json
import sys

# config first: loads .env (NTFY_TOPIC) and puts cachecore on sys.path
from config import DB_PATH, PROJECT_ROOT

from cachecore.digest import Context, Finding, Rule, run_digest

# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def rule_primaries(conn, ctx: Context):
    findings = []
    # Just happened (last 3 days, inclusive) -> needs manual curation
    rows = conn.execute(
        """
        SELECT state_code, election_date, election_type, COALESCE(note,'')
        FROM primary_calendar
        WHERE election_date BETWEEN date(?, '-3 day') AND ?
        ORDER BY election_date, state_code
        """,
        (ctx.today.isoformat(), ctx.today.isoformat()),
    ).fetchall()
    for state, edate, etype, note in rows:
        detail = (
            f"Review results; log incumbent losses/nominees with "
            f"scripts/log_primary_loss.py.{(' ' + note) if note else ''}"
        )
        findings.append(
            Finding(
                key=f"primary-done:{state}:{edate}",
                title=f"{state} {etype} was {edate} -- review results",
                detail=detail,
                severity="action",
            )
        )
    # Coming up within 3 days -> heads-up only
    rows = conn.execute(
        """
        SELECT state_code, election_date, election_type
        FROM primary_calendar
        WHERE election_date BETWEEN date(?, '+1 day') AND date(?, '+3 day')
        ORDER BY election_date, state_code
        """,
        (ctx.today.isoformat(), ctx.today.isoformat()),
    ).fetchall()
    for state, edate, etype in rows:
        findings.append(
            Finding(
                key=f"primary-soon:{state}:{edate}",
                title=f"{state} {etype} coming up {edate}",
                severity="info",
            )
        )
    return findings


def rule_pending_winners(conn, ctx: Context):
    rows = conn.execute(
        """
        SELECT id, full_name, state_code, primary_loss_date
        FROM congress_members
        WHERE lost_primary = 1 AND primary_winner_candidate_id IS NULL
        """
    ).fetchall()
    return [
        Finding(
            key=f"pending-winner:{bid}",
            title=f"{name} ({state}) lost primary {lost or '?'} -- winner not yet logged",
            detail=f"log_primary_loss.py --id {bid} --winner <name> once decided.",
            severity="action",
        )
        for bid, name, state, lost in rows
    ]


def rule_became_law(conn, ctx: Context):
    # Key on when WE learned the bill became law (updated_at), not on the
    # action date -- after a catch-up run the action date can be weeks old.
    # The 60-day action-date floor keeps full-table reimports (which bump
    # updated_at on every row) from resurfacing ancient laws.
    since = ctx.state.get("last_digest_at") or None
    rows = conn.execute(
        """
        SELECT bill_type, bill_number, COALESCE(title,''), latest_action_date, COALESCE(law_number,'')
        FROM legislation
        WHERE is_law = 1
          AND updated_at >= COALESCE(?, datetime('now', '-7 day'))
          AND latest_action_date >= date('now', '-60 day')
        ORDER BY latest_action_date DESC
        LIMIT 15
        """,
        (since,),
    ).fetchall()
    return [
        Finding(
            key=f"law:{btype}{bnum}",
            title=f"Became law: {btype.upper()} {bnum} ({law or adate})",
            detail=title[:160],
            severity="notable",
        )
        for btype, bnum, title, adate, law in rows
    ]


def rule_chamber_passage(conn, ctx: Context):
    since = ctx.state.get("last_digest_at") or None
    rows = conn.execute(
        """
        SELECT bill_type, bill_number, COALESCE(title,''), latest_action_date, latest_action_text
        FROM legislation
        WHERE is_law = 0
          AND updated_at >= COALESCE(?, datetime('now', '-7 day'))
          AND latest_action_date >= date('now', '-60 day')
          AND (latest_action_text LIKE 'Passed/agreed to in House%'
               OR latest_action_text LIKE 'Passed/agreed to in Senate%'
               OR latest_action_text LIKE '%Passed Senate%'
               OR latest_action_text LIKE '%Passed House%')
        ORDER BY latest_action_date DESC
        LIMIT 10
        """,
        (since,),
    ).fetchall()
    return [
        Finding(
            key=f"passed:{btype}{bnum}:{adate}",
            title=f"{btype.upper()} {bnum} passed a chamber ({adate})",
            detail=f"{title[:120]} -- {action[:100]}",
            severity="notable",
        )
        for btype, bnum, title, adate, action in rows
    ]


def rule_new_votes(conn, ctx: Context):
    d = ctx.deltas.get("roll_call_votes") or 0
    if d > 0:
        return [
            Finding(
                key=f"votes:{ctx.today.isoformat()}",
                title=f"{d} new roll-call vote(s) since last digest",
                severity="notable",
            )
        ]
    return []


def rule_membership_change(conn, ctx: Context):
    d = ctx.deltas.get("members") or 0
    if d != 0:
        return [
            Finding(
                key=f"membership:{ctx.today.isoformat()}",
                title=f"congress_members count changed by {d:+d} (resignation/succession?)",
                severity="notable",
            )
        ]
    return []


def rule_vote_staleness(conn, ctx: Context):
    row = conn.execute(
        "SELECT value FROM metadata WHERE key = 'votes_last_updated'"
    ).fetchone()
    if not row or not row[0]:
        return []
    stale_days = conn.execute(
        "SELECT CAST(julianday('now') - julianday(?) AS INTEGER)", (row[0],)
    ).fetchone()[0]
    if stale_days is not None and stale_days >= 14:
        return [
            Finding(
                key="stale:votes",
                title=f"House votes not updated in {stale_days} days",
                detail="Check the daily pipeline / Congress.gov API.",
                severity="notable",
            )
        ]
    return []


def rule_winner_integrity(conn, ctx: Context):
    rows = conn.execute(
        """
        SELECT pw.race_id, pw.party, pw.candidate_id
        FROM primary_winners pw
        LEFT JOIN race_candidates rc
            ON rc.race_id = pw.race_id AND rc.candidate_id = pw.candidate_id
        WHERE rc.candidate_id IS NULL
        """
    ).fetchall()
    return [
        Finding(
            key=f"orphan-winner:{race}:{party}",
            title=f"primary_winners {race} ({party}) orphaned -- {cid} missing from race_candidates",
            detail="FEC reimport likely dropped the candidate row; verify import_fec_candidates run.",
            severity="action",
        )
        for race, party, cid in rows
    ]


RULES = [
    Rule("primaries", rule_primaries),
    Rule("pending_winners", rule_pending_winners),
    Rule("became_law", rule_became_law),
    Rule("chamber_passage", rule_chamber_passage),
    Rule("new_votes", rule_new_votes),
    Rule("membership_change", rule_membership_change),
    Rule("vote_staleness", rule_vote_staleness),
    Rule("winner_integrity", rule_winner_integrity),
]

COUNTS_QUERIES = {
    "members": "SELECT COUNT(*) FROM congress_members",
    "legislation": "SELECT COUNT(*) FROM legislation",
    "laws": "SELECT COUNT(*) FROM legislation WHERE is_law = 1",
    "roll_call_votes": "SELECT COUNT(*) FROM roll_call_votes",
    "member_votes": "SELECT COUNT(*) FROM member_votes",
    "candidates": "SELECT COUNT(*) FROM candidates",
    "election_races": "SELECT COUNT(*) FROM election_races",
    "race_candidates": "SELECT COUNT(*) FROM race_candidates",
    "districts": "SELECT COUNT(*) FROM congressional_districts",
    "staff": "SELECT COUNT(*) FROM congressional_staff",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="CivicCache material-change digest")
    parser.add_argument("--tier", help="Pipeline tier that just ran")
    parser.add_argument(
        "--pipeline-exit", type=int, default=None, help="Exit code of the pipeline run"
    )
    parser.add_argument(
        "--no-notify", action="store_true", help="Write status.json but send nothing"
    )
    parser.add_argument(
        "--print", action="store_true", dest="print_status", help="Dump status JSON to stdout"
    )
    args = parser.parse_args()

    status = run_digest(
        project="civiccache",
        display_name="CivicCache",
        repo_path=PROJECT_ROOT,
        db_path=DB_PATH,
        rules=RULES,
        counts_queries=COUNTS_QUERIES,
        tier=args.tier,
        pipeline_exit=args.pipeline_exit,
        last_run_path=PROJECT_ROOT / "data" / "last_run.json",
        stale_after_days=1.5,  # daily cadence
        commands={
            "daily": "bash scripts/run_pipeline.sh daily",
            "weekly": "bash scripts/run_pipeline.sh weekly",
            "monthly": "bash scripts/run_pipeline.sh monthly",
        },
        send_notifications=not args.no_notify,
    )

    if args.print_status:
        print(json.dumps(status, indent=2, default=str))
    else:
        n = len(status.get("attention", []))
        print(f"digest: wrote status.json ({n} attention item(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
