#!/usr/bin/env python3
"""Reconcile (and validate) a CivicCache SQLite DB against the Android Room schema.

The Android app uses Room, which validates the bundled database's structure at
startup against the schema it exported to:

    CivicScope.android/app/schemas/
        com.civicsuite.civicscope.data.local.CivicCacheDatabase/<version>.json

Any structural divergence (foreign keys, indices, column nullability, primary
keys) makes Room throw and the app crash on launch. iOS uses raw SQLite and
ignores foreign keys, indices and column nullability, so reconciling the shipped
database to Room's expectations is safe for iOS.

The Room JSON is treated as the single source of truth. For every managed entity
we recreate the table from Room's *own* ``createSql`` (which has no foreign keys,
explicit ``NOT NULL`` on PK columns, and table-constraint primary keys) and
recreate exactly the indices Room lists. Because we reuse Room's DDL verbatim,
``PRAGMA table_info`` / ``index_list`` end up byte-for-byte what Room's runtime
reader expects.

Only the Room-managed entities are touched. FTS virtual tables, their shadow
tables, and other iOS-only tables (metadata, primary_winners, legislation_actions,
candidate_committee_linkages, ...) are left completely untouched. rowids are
preserved on every recreate so external-content FTS indexes (e.g.
``legislation_fts`` which uses ``content='legislation', content_rowid='rowid'``)
stay valid.

Usage:
    # Read-only: report every divergence, exit 1 if any (use as a CI/sync gate)
    python3 scripts/reconcile_room_schema.py --validate --db data/civic_cache.db

    # Rewrite managed tables to match Room, then validate (exit 1 if still off)
    python3 scripts/reconcile_room_schema.py --reconcile --db /tmp/ship.db

    # --schema defaults to the Android project's exported JSON (latest version)
    python3 scripts/reconcile_room_schema.py --validate            # source DB

Exit codes: 0 = match / reconciled-and-validated, 1 = divergence, 2 = error.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Default locations
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "civic_cache.db"
ROOM_SCHEMA_DIR = Path(
    "/Users/abrooks/GIT/CivicScope.android/app/schemas/"
    "com.civicsuite.civicscope.data.local.CivicCacheDatabase"
)


def _q(identifier: str) -> str:
    """Quote a SQL identifier with backticks (Room's own quoting style)."""
    return "`" + identifier.replace("`", "``") + "`"


# ---------------------------------------------------------------------------
# Room schema model
# ---------------------------------------------------------------------------


def find_latest_schema(schema_dir: Path = ROOM_SCHEMA_DIR) -> Path:
    """Return the highest-numbered <version>.json the Android build exported."""
    candidates = sorted(
        (p for p in schema_dir.glob("*.json") if p.stem.isdigit()),
        key=lambda p: int(p.stem),
    )
    if not candidates:
        raise FileNotFoundError(f"No <version>.json Room schema found in {schema_dir}")
    return candidates[-1]


def load_schema(schema_path: Path) -> dict:
    data = json.loads(schema_path.read_text())["database"]
    return {
        "version": data["version"],
        "identity_hash": data["identityHash"],
        "setup_queries": data.get("setupQueries", []),
        "entities": {e["tableName"]: e for e in data["entities"]},
    }


def expected_columns(entity: dict) -> dict:
    """columnName -> (declared_type, not_null_bool) per Room."""
    return {
        f["columnName"]: (f["affinity"].upper(), bool(f["notNull"]))
        for f in entity["fields"]
    }


def expected_indices(entity: dict) -> dict:
    """index_name -> (unique_bool, [columnNames]) per Room."""
    return {
        idx["name"]: (bool(idx["unique"]), list(idx["columnNames"]))
        for idx in entity.get("indices", [])
    }


# ---------------------------------------------------------------------------
# Live DB introspection
# ---------------------------------------------------------------------------


def db_columns(conn, table: str) -> dict:
    """columnName -> (declared_type_upper, not_null_bool) from PRAGMA table_info."""
    out = {}
    for cid, name, ctype, notnull, dflt, pk in conn.execute(
        f"PRAGMA table_info({_q(table)})"
    ):
        out[name] = ((ctype or "").upper(), bool(notnull))
    return out


def db_pk(conn, table: str) -> list:
    """Primary-key columns in key order."""
    rows = [
        (pk, name)
        for cid, name, ctype, notnull, dflt, pk in conn.execute(
            f"PRAGMA table_info({_q(table)})"
        )
        if pk
    ]
    return [name for _, name in sorted(rows)]


def db_foreign_keys(conn, table: str) -> list:
    return list(conn.execute(f"PRAGMA foreign_key_list({_q(table)})"))


def db_created_indices(conn, table: str) -> dict:
    """index_name -> (unique_bool, [cols]) for indices we created (origin 'c').

    Mirrors Room's TableInfo reader, which only compares CREATE INDEX indices and
    ignores those SQLite auto-creates for UNIQUE/PRIMARY KEY constraints.
    """
    out = {}
    for seq, name, unique, origin, partial in conn.execute(
        f"PRAGMA index_list({_q(table)})"
    ):
        if origin != "c":
            continue
        cols = [
            r[2]  # name column of index_info
            for r in conn.execute(f"PRAGMA index_info({_q(name)})")
        ]
        out[name] = (bool(unique), cols)
    return out


def table_exists(conn, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_table(conn, name: str, entity: dict) -> list:
    """Return a list of human-readable mismatch strings for one entity."""
    problems = []
    if not table_exists(conn, name):
        return [f"table is MISSING from the database"]

    exp_cols = expected_columns(entity)
    act_cols = db_columns(conn, name)

    for col in exp_cols:
        if col not in act_cols:
            problems.append(f"column `{col}` missing")
    for col in act_cols:
        if col not in exp_cols:
            problems.append(f"unexpected extra column `{col}` (Room would reject)")
    for col, (etype, enn) in exp_cols.items():
        if col not in act_cols:
            continue
        atype, ann = act_cols[col]
        if atype != etype:
            problems.append(f"column `{col}` type {atype!r} != Room {etype!r}")
        if ann != enn:
            problems.append(
                f"column `{col}` notNull={ann} != Room notNull={enn}"
            )

    exp_pk = list(entity["primaryKey"]["columnNames"])
    act_pk = db_pk(conn, name)
    if exp_pk != act_pk:
        problems.append(f"primary key {act_pk} != Room {exp_pk}")

    fks = db_foreign_keys(conn, name)
    if fks:  # Room declares foreignKeys: [] on every table
        refs = sorted({row[2] for row in fks})  # table column of foreign_key_list
        problems.append(f"has {len(fks)} foreign key(s) -> {refs}; Room expects none")

    exp_idx = expected_indices(entity)
    act_idx = db_created_indices(conn, name)
    for iname, (eu, ecols) in exp_idx.items():
        if iname not in act_idx:
            problems.append(f"index `{iname}` missing")
        else:
            au, acols = act_idx[iname]
            if au != eu or acols != ecols:
                problems.append(
                    f"index `{iname}` is (unique={au}, {acols}) "
                    f"!= Room (unique={eu}, {ecols})"
                )
    for iname in act_idx:
        if iname not in exp_idx:
            problems.append(f"unexpected extra index `{iname}` (Room would reject)")

    return problems


def validate(conn, schema: dict) -> dict:
    """Return {tableName: [problems]} for every entity that diverges."""
    report = {}
    for name, entity in schema["entities"].items():
        problems = validate_table(conn, name, entity)
        if problems:
            report[name] = problems
    return report


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def reconcile_table(conn, name: str, entity: dict) -> None:
    """Recreate one table from Room's createSql, preserving data and rowids."""
    if not table_exists(conn, name):
        raise RuntimeError(f"{name}: table missing from DB; cannot reconcile")

    exp_cols = list(expected_columns(entity).keys())
    cur_cols = set(db_columns(conn, name).keys())
    missing = [c for c in exp_cols if c not in cur_cols]
    if missing:
        # A NOT NULL column with no source data can't be back-filled here.
        raise RuntimeError(
            f"{name}: Room expects column(s) {missing} that the DB lacks; "
            f"fix the source schema/import before reconciling."
        )

    create_sql = entity["createSql"].replace("${TABLE_NAME}", name)
    tmp = f"_recon_old_{name}"
    col_list = ", ".join(_q(c) for c in exp_cols)

    # rowid is carried explicitly so external-content FTS indexes stay aligned.
    conn.execute(f"ALTER TABLE {_q(name)} RENAME TO {_q(tmp)}")
    conn.execute(create_sql)
    conn.execute(
        f"INSERT INTO {_q(name)} (rowid, {col_list}) "
        f"SELECT rowid, {col_list} FROM {_q(tmp)}"
    )
    conn.execute(f"DROP TABLE {_q(tmp)}")

    for idx in entity.get("indices", []):
        conn.execute(idx["createSql"].replace("${TABLE_NAME}", name))


def reconcile(conn, schema: dict) -> None:
    """Reconcile all managed tables and stamp Room's identity/version."""
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN")
    try:
        for name, entity in schema["entities"].items():
            reconcile_table(conn, name, entity)
        # Make Room accept the prepackaged DB: identity_hash + matching version.
        for stmt in schema["setup_queries"]:
            conn.execute(stmt)
        conn.execute(f"PRAGMA user_version = {int(schema['version'])}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    # VACUUM cannot run inside a transaction; reclaim space from the rewrites.
    conn.execute("VACUUM")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_report(report: dict) -> None:
    if not report:
        print("  ✓ all managed tables match the Room schema")
        return
    total = sum(len(v) for v in report.values())
    print(f"  ✗ {len(report)} table(s) diverge ({total} issue(s)):")
    for name in sorted(report):
        print(f"    {name}:")
        for problem in report[name]:
            print(f"      - {problem}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite DB to operate on")
    ap.add_argument(
        "--schema",
        type=Path,
        default=None,
        help="Room <version>.json (default: latest exported by the Android app)",
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--validate", action="store_true", help="report divergence only")
    mode.add_argument(
        "--reconcile", action="store_true", help="rewrite DB to match Room, then validate"
    )
    args = ap.parse_args()

    if not args.db.exists():
        print(f"error: DB not found: {args.db}", file=sys.stderr)
        return 2

    schema_path = args.schema or find_latest_schema()
    if not schema_path.exists():
        print(f"error: Room schema not found: {schema_path}", file=sys.stderr)
        return 2

    schema = load_schema(schema_path)
    print(
        f"Room schema: {schema_path.name} (v{schema['version']}, "
        f"{len(schema['entities'])} entities)\nDB: {args.db}"
    )

    conn = sqlite3.connect(str(args.db))
    try:
        if args.reconcile:
            print("Reconciling managed tables to Room schema...")
            reconcile(conn, schema)
            print("Reconcile complete; validating result...")
            report = validate(conn, schema)
            print_report(report)
            return 1 if report else 0
        else:  # default + --validate
            report = validate(conn, schema)
            print_report(report)
            return 1 if report else 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
