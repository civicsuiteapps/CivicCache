#!/usr/bin/env python3
"""
Build the CivicCache SQLite database schema (and optionally seed data).

Usage:
    python3 scripts/build_database.py                # Create schema + import seed data
    python3 scripts/build_database.py --schema-only  # Create schema only (no data import)
    python3 scripts/build_database.py --fresh         # Delete existing DB first
"""

import argparse
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional

try:
    from config import DB_PATH as _CFG_DB, CONGRESS_NUMBER as _CFG_CONGRESS
except ImportError:
    _CFG_DB = Path("data/civic_cache.db")
    _CFG_CONGRESS = 119

DB_PATH = _CFG_DB

# Data directories
CONGRESS_DATA_DIR = Path("data/raw/congress")
FEC_DATA_DIR = Path("data/raw/fec")


def create_connection(fresh: bool = False) -> sqlite3.Connection:
    """Create and return a database connection.

    Args:
        fresh: If True, delete any existing database first.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    if fresh and DB_PATH.exists():
        print(f"Removing existing database: {DB_PATH}")
        DB_PATH.unlink()

    conn = sqlite3.Connection(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all database tables."""
    print("\nCreating database schema...")

    # ------------------------------------------------------------------
    # Core reference tables (no FKs to other tables)
    # ------------------------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS states (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            fips_code TEXT,
            is_territory INTEGER DEFAULT 0,
            region TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ------------------------------------------------------------------
    # Congress members (FK -> states)
    # ------------------------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS congress_members (
            id TEXT PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            full_name TEXT NOT NULL,
            nickname TEXT,
            middle_name TEXT,
            suffix TEXT,

            chamber TEXT NOT NULL,
            state_code TEXT,
            state_name TEXT NOT NULL,
            district TEXT,

            current_party TEXT NOT NULL,

            office_address TEXT,
            phone TEXT,
            website_url TEXT,
            contact_form_url TEXT,

            twitter_handle TEXT,
            facebook_url TEXT,
            youtube_url TEXT,

            birth_year INTEGER,
            gender TEXT,

            term_start_date TEXT,
            term_end_date TEXT,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,

            image_url TEXT,
            image_blob BLOB,
            first_elected_year INTEGER,
            total_years_served INTEGER,
            total_terms_served INTEGER,
            current_leadership_role TEXT,
            sponsored_bills_count INTEGER,
            cosponsored_bills_count INTEGER,
            depiction_url TEXT,
            depiction_attribution TEXT,
            birthday TEXT,
            wikipedia_id TEXT,
            govtrack_id TEXT,
            opensecrets_id TEXT,
            votesmart_id TEXT,
            fec_id TEXT,
            ballotpedia_id TEXT,
            cspan_id TEXT,
            lis_id TEXT,

            lost_primary INTEGER DEFAULT 0,
            primary_loss_cycle INTEGER,
            primary_loss_date TEXT,
            primary_winner_candidate_id TEXT,

            FOREIGN KEY (state_code) REFERENCES states(code)
        )
    """)

    # ------------------------------------------------------------------
    # Legislation tables (FK -> congress_members)
    # ------------------------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS legislation (
            id TEXT PRIMARY KEY,
            congress INTEGER NOT NULL,
            bill_type TEXT NOT NULL,
            bill_number INTEGER NOT NULL,

            title TEXT NOT NULL,
            origin_chamber TEXT,
            origin_chamber_code TEXT,

            introduced_date TEXT,
            latest_action_date TEXT,
            latest_action_text TEXT,

            policy_area TEXT,

            is_law INTEGER DEFAULT 0,
            law_number TEXT,
            law_type TEXT,

            status TEXT,

            actions_count INTEGER DEFAULT 0,
            amendments_count INTEGER DEFAULT 0,
            cosponsors_count INTEGER DEFAULT 0,
            subjects_count INTEGER DEFAULT 0,
            summaries_count INTEGER DEFAULT 0,

            legislation_url TEXT,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,

            session INTEGER,

            UNIQUE(congress, bill_type, bill_number)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS legislation_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legislation_id TEXT NOT NULL,

            action_date TEXT,
            action_desc TEXT,

            summary_text TEXT NOT NULL,

            update_date TEXT,
            version_code TEXT,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (legislation_id) REFERENCES legislation(id),
            UNIQUE(legislation_id, action_date, action_desc)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS legislation_sponsors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legislation_id TEXT NOT NULL,

            bioguide_id TEXT,
            full_name TEXT NOT NULL,

            first_name TEXT,
            last_name TEXT,
            middle_name TEXT,

            party TEXT,
            state TEXT,
            district INTEGER,

            is_original_cosponsor INTEGER DEFAULT 0,
            sponsorship_date TEXT,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            is_primary_sponsor INTEGER DEFAULT 0,

            FOREIGN KEY (legislation_id) REFERENCES legislation(id),
            FOREIGN KEY (bioguide_id) REFERENCES congress_members(id),
            UNIQUE(legislation_id, bioguide_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS legislation_subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legislation_id TEXT NOT NULL,

            subject_name TEXT NOT NULL,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (legislation_id) REFERENCES legislation(id),
            UNIQUE(legislation_id, subject_name)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS legislation_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legislation_id TEXT NOT NULL,

            action_date TEXT NOT NULL,
            action_code TEXT,
            action_text TEXT NOT NULL,

            source_system TEXT,
            source_system_code TEXT,

            action_type TEXT,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (legislation_id) REFERENCES legislation(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS legislation_related (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legislation_id TEXT NOT NULL,
            related_legislation_id TEXT,

            relationship_type TEXT,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (legislation_id) REFERENCES legislation(id),
            UNIQUE(legislation_id, related_legislation_id)
        )
    """)

    # ------------------------------------------------------------------
    # Congressional committees (from GitHub YAML data)
    # ------------------------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS congressional_committees (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            chamber TEXT NOT NULL,
            committee_type TEXT,
            parent_committee_id TEXT,
            jurisdiction TEXT,
            url TEXT,
            is_subcommittee INTEGER DEFAULT 0,
            congress INTEGER DEFAULT 119,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS member_committee_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id TEXT NOT NULL,
            committee_id TEXT NOT NULL,
            party_side TEXT,
            rank INTEGER,
            title TEXT,
            is_subcommittee INTEGER DEFAULT 0,
            congress INTEGER DEFAULT 119,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (member_id) REFERENCES congress_members(id),
            UNIQUE(member_id, committee_id, congress)
        )
    """)

    # ------------------------------------------------------------------
    # Member enrichment tables (FK -> congress_members)
    # ------------------------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS member_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id TEXT NOT NULL,
            position_title TEXT NOT NULL,
            position_type TEXT,
            organization TEXT,
            district TEXT,
            state TEXT,
            start_year INTEGER,
            end_year INTEGER,
            is_current INTEGER DEFAULT 0,
            source TEXT,
            wikidata_position_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (member_id) REFERENCES congress_members(id)
        )
    """)

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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS candidate_top_contributors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT NOT NULL,
            election_cycle INTEGER NOT NULL,
            rank INTEGER NOT NULL,
            committee_id TEXT NOT NULL,
            committee_name TEXT NOT NULL,
            committee_type TEXT,
            total_amount REAL NOT NULL,
            transaction_count INTEGER DEFAULT 0,
            UNIQUE(candidate_id, election_cycle, rank),
            FOREIGN KEY (candidate_id, election_cycle) REFERENCES candidates(candidate_id, election_cycle)
        )
    """)

    # ------------------------------------------------------------------
    # FEC / election tables
    # ------------------------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS committees (
            committee_id TEXT PRIMARY KEY,
            committee_name TEXT NOT NULL,
            committee_type TEXT,
            committee_type_full TEXT,
            committee_designation TEXT,
            committee_designation_full TEXT,

            party_code TEXT,
            party_full_name TEXT,

            total_receipts REAL,
            total_disbursements REAL,
            cash_on_hand REAL,
            coverage_end_date TEXT,

            connected_org_name TEXT,
            treasurer_name TEXT,

            street_1 TEXT,
            street_2 TEXT,
            city TEXT,
            state TEXT,
            zip TEXT,

            filing_frequency TEXT,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

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

    # Primary winners (party nominees) per race. Kept SEPARATE from
    # race_candidates because import_fec_candidates.py drops and rebuilds the
    # election_races / race_candidates tables on every run -- manually-curated
    # winner flags stored there would be wiped. Maintained by
    # log_primary_loss.py; join to race_candidates at runtime on
    # (race_id, candidate_id). Intentionally no FK to election_races so the
    # FEC reimport's DROP TABLE doesn't conflict.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS primary_winners (
            race_id TEXT NOT NULL,
            party TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            election_cycle INTEGER NOT NULL,
            won_date TEXT,
            decided_by TEXT,
            note TEXT,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,

            PRIMARY KEY (race_id, party)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS candidate_committee_linkages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT NOT NULL,
            committee_id TEXT NOT NULL,
            linkage_type TEXT,
            election_year INTEGER,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (committee_id) REFERENCES committees(committee_id),

            UNIQUE(candidate_id, committee_id, election_year)
        )
    """)

    # ------------------------------------------------------------------
    # Roll call votes (FK -> legislation)
    # ------------------------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS roll_call_votes (
            id TEXT PRIMARY KEY,
            congress INTEGER NOT NULL,
            chamber TEXT NOT NULL,
            session INTEGER NOT NULL,
            roll_number INTEGER NOT NULL,

            vote_date TEXT NOT NULL,
            vote_time TEXT,
            vote_question TEXT,
            vote_question_text TEXT,
            vote_type TEXT,
            vote_result TEXT,
            vote_result_text TEXT,

            yea_count INTEGER DEFAULT 0,
            nay_count INTEGER DEFAULT 0,
            present_count INTEGER DEFAULT 0,
            not_voting_count INTEGER DEFAULT 0,
            required_majority TEXT,

            legislation_id TEXT,
            amendment_number TEXT,
            source_url TEXT,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(congress, chamber, session, roll_number),
            FOREIGN KEY (legislation_id) REFERENCES legislation(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS member_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roll_call_id TEXT NOT NULL,
            bioguide_id TEXT NOT NULL,
            vote_position TEXT NOT NULL,
            member_name TEXT,
            party TEXT,
            state TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(roll_call_id, bioguide_id),
            FOREIGN KEY (roll_call_id) REFERENCES roll_call_votes(id),
            FOREIGN KEY (bioguide_id) REFERENCES congress_members(id)
        )
    """)

    # ------------------------------------------------------------------
    # Congressional staff (FK -> congress_members)
    # ------------------------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS congressional_staff (
            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            full_name TEXT NOT NULL,
            suffix TEXT,
            title TEXT NOT NULL,
            title_normalized TEXT,
            office_type TEXT NOT NULL,
            chamber TEXT,
            member_bioguide_id TEXT,
            member_name TEXT,
            office_location TEXT,
            committee_code TEXT,
            committee_name TEXT,
            subcommittee_name TEXT,
            party_affiliation TEXT,
            institutional_office TEXT,
            congress_number INTEGER NOT NULL,
            source_document TEXT,
            source_section TEXT,
            effective_date TEXT,
            created_at TEXT,
            updated_at TEXT,
            is_current INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (member_bioguide_id) REFERENCES congress_members(id) ON DELETE CASCADE
        )
    """)

    # ------------------------------------------------------------------
    # Geographic tables
    # ------------------------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS congressional_districts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            state_fips TEXT NOT NULL,
            state_code TEXT,
            district_code TEXT NOT NULL,
            district_number INTEGER,
            name TEXT NOT NULL,
            geoid TEXT UNIQUE NOT NULL,
            center_latitude REAL,
            center_longitude REAL,
            population INTEGER,
            geojson TEXT NOT NULL,
            bbox TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (state_code) REFERENCES states(code)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS district_demographics (
            geoid TEXT PRIMARY KEY,
            population INTEGER,
            median_household_income REAL,
            poverty_rate REAL,
            unemployment_rate REAL,
            median_age REAL,
            college_educated_pct REAL,
            high_school_grad_pct REAL,
            white_pct REAL,
            black_pct REAL,
            hispanic_pct REAL,
            asian_pct REAL,
            median_home_value REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (geoid) REFERENCES congressional_districts(geoid)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS zip_districts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zip_code TEXT NOT NULL,
            state_fips TEXT NOT NULL,
            state_abbr TEXT NOT NULL,
            district INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(zip_code, state_abbr, district)
        )
    """)

    print("  Schema created")


def create_indexes(conn: sqlite3.Connection) -> None:
    """Create all database indexes."""
    print("\nCreating indexes...")

    indexes = [
        # congress_members
        "CREATE INDEX IF NOT EXISTS idx_members_chamber ON congress_members(chamber)",
        "CREATE INDEX IF NOT EXISTS idx_members_party ON congress_members(current_party)",
        "CREATE INDEX IF NOT EXISTS idx_members_district ON congress_members(state_code, district)",

        # candidates
        "CREATE INDEX IF NOT EXISTS idx_candidates_office ON candidates(office)",
        "CREATE INDEX IF NOT EXISTS idx_candidates_state ON candidates(office_state)",
        "CREATE INDEX IF NOT EXISTS idx_candidates_party ON candidates(party_code)",
        "CREATE INDEX IF NOT EXISTS idx_candidates_cycle_office ON candidates(election_cycle, office, office_state)",

        # election_races / race_candidates
        # (race_id lookups are covered by the (race_id, candidate_id) primary key)
        "CREATE INDEX IF NOT EXISTS idx_races_cycle ON election_races(election_cycle)",
        "CREATE INDEX IF NOT EXISTS idx_races_incumbent ON election_races(incumbent_bioguide_id)",
        "CREATE INDEX IF NOT EXISTS idx_race_cand_candidate ON race_candidates(candidate_id, election_cycle)",

        # committees (FEC)
        "CREATE INDEX IF NOT EXISTS idx_committees_type ON committees(committee_type)",
        "CREATE INDEX IF NOT EXISTS idx_committees_party ON committees(party_code)",
        "CREATE INDEX IF NOT EXISTS idx_committees_state ON committees(state)",

        # candidate_committee_linkages
        "CREATE INDEX IF NOT EXISTS idx_linkages_candidate ON candidate_committee_linkages(candidate_id, election_year)",
        "CREATE INDEX IF NOT EXISTS idx_linkages_committee ON candidate_committee_linkages(committee_id)",

        # legislation (congress-only lookups are covered by idx_legislation_session)
        "CREATE INDEX IF NOT EXISTS idx_legislation_type ON legislation(bill_type)",
        "CREATE INDEX IF NOT EXISTS idx_legislation_status ON legislation(status)",
        "CREATE INDEX IF NOT EXISTS idx_legislation_policy ON legislation(policy_area)",
        "CREATE INDEX IF NOT EXISTS idx_legislation_law ON legislation(is_law)",
        "CREATE INDEX IF NOT EXISTS idx_legislation_date ON legislation(introduced_date)",
        "CREATE INDEX IF NOT EXISTS idx_legislation_session ON legislation(congress, session)",

        # legislation child tables
        "CREATE INDEX IF NOT EXISTS idx_summaries_legislation ON legislation_summaries(legislation_id)",
        "CREATE INDEX IF NOT EXISTS idx_sponsors_legislation ON legislation_sponsors(legislation_id)",
        "CREATE INDEX IF NOT EXISTS idx_sponsors_bioguide ON legislation_sponsors(bioguide_id)",
        "CREATE INDEX IF NOT EXISTS idx_actions_legislation ON legislation_actions(legislation_id)",
        "CREATE INDEX IF NOT EXISTS idx_actions_date ON legislation_actions(action_date)",
        "CREATE INDEX IF NOT EXISTS idx_subjects_legislation ON legislation_subjects(legislation_id)",
        "CREATE INDEX IF NOT EXISTS idx_subjects_name ON legislation_subjects(subject_name)",

        # roll_call_votes / member_votes
        # (congress-only lookups are covered by idx_votes_session; no query
        # filters member_votes by vote_position alone)
        "CREATE INDEX IF NOT EXISTS idx_votes_chamber ON roll_call_votes(chamber)",
        "CREATE INDEX IF NOT EXISTS idx_votes_session ON roll_call_votes(congress, session)",
        "CREATE INDEX IF NOT EXISTS idx_votes_date ON roll_call_votes(vote_date)",
        "CREATE INDEX IF NOT EXISTS idx_votes_legislation ON roll_call_votes(legislation_id)",
        "CREATE INDEX IF NOT EXISTS idx_member_votes_roll ON member_votes(roll_call_id)",
        "CREATE INDEX IF NOT EXISTS idx_member_votes_bioguide ON member_votes(bioguide_id)",

        # congressional_staff
        "CREATE INDEX IF NOT EXISTS idx_staff_member ON congressional_staff(member_bioguide_id, is_current)",
        "CREATE INDEX IF NOT EXISTS idx_staff_committee ON congressional_staff(committee_code, is_current)",
        "CREATE INDEX IF NOT EXISTS idx_staff_name ON congressional_staff(last_name, first_name)",
        "CREATE INDEX IF NOT EXISTS idx_staff_title ON congressional_staff(title_normalized)",
        "CREATE INDEX IF NOT EXISTS idx_staff_congress ON congressional_staff(congress_number)",
        "CREATE INDEX IF NOT EXISTS idx_staff_current ON congressional_staff(is_current)",

        # congressional_committees / member_committee_assignments
        "CREATE INDEX IF NOT EXISTS idx_cong_committees_chamber ON congressional_committees(chamber)",
        "CREATE INDEX IF NOT EXISTS idx_cong_committees_parent ON congressional_committees(parent_committee_id)",
        "CREATE INDEX IF NOT EXISTS idx_assignments_member ON member_committee_assignments(member_id)",
        "CREATE INDEX IF NOT EXISTS idx_assignments_committee ON member_committee_assignments(committee_id)",

        # member_positions
        "CREATE INDEX IF NOT EXISTS idx_positions_member ON member_positions(member_id)",
        "CREATE INDEX IF NOT EXISTS idx_positions_type ON member_positions(position_type)",
        "CREATE INDEX IF NOT EXISTS idx_positions_current ON member_positions(is_current)",

        # member_fec_summary / member_top_contributors
        "CREATE INDEX IF NOT EXISTS idx_fec_summary_cycle ON member_fec_summary(election_cycle)",
        "CREATE INDEX IF NOT EXISTS idx_top_contrib_member ON member_top_contributors(bioguide_id, election_cycle)",

        # candidate_top_contributors
        "CREATE INDEX IF NOT EXISTS idx_cand_top_contrib ON candidate_top_contributors(candidate_id, election_cycle)",

        # congressional_districts / district_demographics
        # (district_demographics geoid lookups are covered by its primary key)
        "CREATE INDEX IF NOT EXISTS idx_districts_state_district ON congressional_districts(state_code, district_number)",
        "CREATE INDEX IF NOT EXISTS idx_districts_fips ON congressional_districts(state_fips)",
        "CREATE INDEX IF NOT EXISTS idx_districts_geoid ON congressional_districts(geoid)",

        # zip_districts
        "CREATE INDEX IF NOT EXISTS idx_zip_districts_zip ON zip_districts(zip_code)",
        "CREATE INDEX IF NOT EXISTS idx_zip_districts_state_district ON zip_districts(state_abbr, district)",
    ]

    for idx_sql in indexes:
        conn.execute(idx_sql)

    print(f"  Created {len(indexes)} indexes")


def create_fts_tables(conn: sqlite3.Connection) -> None:
    """Create full-text search virtual tables."""
    print("\nCreating full-text search indexes...")

    # Check which FTS tables already exist
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_fts'"
        ).fetchall()
    }

    if "congress_members_fts" not in existing:
        conn.execute("""
            CREATE VIRTUAL TABLE congress_members_fts USING fts5(
                member_id UNINDEXED,
                full_name,
                first_name,
                last_name,
                state_name
            )
        """)

    if "candidates_fts" not in existing:
        conn.execute("""
            CREATE VIRTUAL TABLE candidates_fts USING fts5(
                candidate_id UNINDEXED,
                election_cycle UNINDEXED,
                candidate_name,
                office_state,
                party_code
            )
        """)

    if "committees_fts" not in existing:
        conn.execute("""
            CREATE VIRTUAL TABLE committees_fts USING fts5(
                committee_id UNINDEXED,
                committee_name,
                connected_org_name,
                party_full_name
            )
        """)

    if "legislation_fts" not in existing:
        conn.execute("""
            CREATE VIRTUAL TABLE legislation_fts USING fts5(
                id,
                title,
                content='legislation',
                content_rowid='rowid'
            )
        """)

    if "legislation_summaries_fts" not in existing:
        conn.execute("""
            CREATE VIRTUAL TABLE legislation_summaries_fts USING fts5(
                legislation_id,
                summary_text,
                content='legislation_summaries',
                content_rowid='id'
            )
        """)

    print("  Full-text search indexes created")


def populate_fts(conn: sqlite3.Connection) -> None:
    """Populate FTS tables from base tables (only useful after seed data import)."""
    print("\nPopulating full-text search tables...")

    conn.execute("""
        INSERT OR IGNORE INTO congress_members_fts(member_id, full_name, first_name, last_name, state_name)
        SELECT id, full_name, first_name, last_name, state_name
        FROM congress_members
    """)

    conn.execute("""
        INSERT OR IGNORE INTO candidates_fts(candidate_id, election_cycle, candidate_name, office_state, party_code)
        SELECT candidate_id, election_cycle, candidate_name, office_state, party_code
        FROM candidates
    """)

    conn.execute("""
        INSERT OR IGNORE INTO committees_fts(committee_id, committee_name, connected_org_name, party_full_name)
        SELECT committee_id, committee_name, connected_org_name, party_full_name
        FROM committees
    """)

    print("  FTS tables populated")


def import_states(conn: sqlite3.Connection) -> int:
    """Import state reference data."""
    print("\nImporting states data...")

    states_file = CONGRESS_DATA_DIR / "states_seed.json"
    if not states_file.exists():
        print("  states_seed.json not found")
        return 0

    with open(states_file, 'r') as f:
        data = json.load(f)

    if isinstance(data, dict) and 'states_and_territories' in data:
        states_list = data['states_and_territories']
    elif isinstance(data, list):
        states_list = data
    else:
        print(f"  Unexpected format: {type(data)}")
        return 0

    count = 0
    for state in states_list:
        conn.execute("""
            INSERT OR IGNORE INTO states (code, name, fips_code, is_territory)
            VALUES (?, ?, ?, ?)
        """, (
            state.get('state_abbr'),
            state.get('state_name'),
            state.get('state_code'),
            1 if state.get('is_territory', False) else 0
        ))
        count += 1

    conn.commit()
    print(f"  Imported {count} states/territories")
    return count


def import_congress_members(conn: sqlite3.Connection) -> int:
    """Import Congressional member data."""
    print("\nImporting Congressional members...")

    members_file = CONGRESS_DATA_DIR / f"{_CFG_CONGRESS}th-congress-members.json"
    if not members_file.exists():
        print(f"  {members_file.name} not found")
        return 0

    with open(members_file, 'r') as f:
        data = json.load(f)

    members = data.get('members', [])
    count = 0

    state_map = {}
    cursor = conn.execute("SELECT name, code FROM states")
    for row in cursor:
        state_map[row[0]] = row[1]

    for member in members:
        state_name = member.get('state')
        state_code = state_map.get(state_name, None)

        if not state_code and state_name:
            alt_names = {
                'U.S. Virgin Islands': 'US Virgin Islands',
            }
            alt_name = alt_names.get(state_name, state_name)
            state_code = state_map.get(alt_name, None)

        first_name = member.get('first_name', '')
        last_name = member.get('last_name', '')
        full_name = f"{first_name} {last_name}".strip()

        conn.execute("""
            INSERT OR IGNORE INTO congress_members (
                id, first_name, last_name, full_name,
                chamber, state_code, state_name, district,
                current_party
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            member.get('bioguide_id'),
            first_name,
            last_name,
            full_name,
            member.get('chamber'),
            state_code,
            state_name,
            str(member.get('district')) if member.get('district') is not None else None,
            member.get('party')
        ))
        count += 1

    conn.commit()
    print(f"  Imported {count} members")
    return count


def add_metadata(conn: sqlite3.Connection, stats: Dict[str, int]) -> None:
    """Add database metadata and statistics."""
    print("\nAdding metadata...")

    metadata_items = [
        ('schema_version', '2.0.0'),
        ('database_created', datetime.now().isoformat()),
        ('data_source', 'CivicCache'),
        ('congress_session', str(_CFG_CONGRESS)),
    ]

    for key, value in metadata_items:
        conn.execute("""
            INSERT OR REPLACE INTO metadata (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        """, (key, value))

    conn.commit()
    print("  Metadata added")


def print_statistics(conn: sqlite3.Connection) -> None:
    """Print database statistics."""
    print("\n" + "=" * 60)
    print("Database Statistics")
    print("=" * 60)

    # Query all user tables
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts_%' "
        "ORDER BY name"
    ).fetchall()

    for (table,) in tables:
        try:
            cursor = conn.execute(f"SELECT COUNT(*) FROM [{table}]")
            count = cursor.fetchone()[0]
            print(f"  {table:40} {count:>10,} rows")
        except sqlite3.OperationalError:
            pass

    if DB_PATH.exists():
        db_size = DB_PATH.stat().st_size
        print(f"\n  Database size: {db_size / (1024*1024):.2f} MB")


def main():
    """Main database build process."""
    parser = argparse.ArgumentParser(description="CivicCache database builder")
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="Create tables and indexes without importing seed data",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Delete existing database before building",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("CivicCache - Database Builder")
    print("=" * 60)

    if not args.schema_only:
        if not CONGRESS_DATA_DIR.exists():
            print(f"\nERROR: Congress data not found: {CONGRESS_DATA_DIR}")
            print("  Use --schema-only to create tables without seed data")
            return 1

    print(f"\nDatabase: {DB_PATH}")
    conn = create_connection(fresh=args.fresh)

    try:
        create_schema(conn)
        create_indexes(conn)
        create_fts_tables(conn)

        if not args.schema_only:
            stats = {}
            stats['states'] = import_states(conn)
            stats['members'] = import_congress_members(conn)
            populate_fts(conn)
            add_metadata(conn, stats)
            print_statistics(conn)

        print("\n" + "=" * 60)
        print("Database Build Complete!")
        print("=" * 60)
        print(f"\nDatabase location: {DB_PATH.absolute()}")

        return 0

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        conn.close()


if __name__ == "__main__":
    exit(main())
