"""
CivicCache shared utilities.

Deduplicated helper functions used across 14+ import scripts.
"""

import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

try:
    from config import DB_PATH as _CFG_DB
except ImportError:
    _CFG_DB = Path("data/civic_cache.db")

USER_AGENT = "CivicCache/1.0"


def create_connection(
    db_path: Optional[Path] = None,
    foreign_keys: bool = True,
    use_wal: bool = False,
) -> sqlite3.Connection:
    """Create and return a database connection.

    Args:
        db_path: Path to the SQLite database. Defaults to config.DB_PATH.
        foreign_keys: Enable PRAGMA foreign_keys (default True).
        use_wal: Enable WAL journal mode for bulk writes (default False).

    Raises:
        FileNotFoundError: If the database file does not exist.
    """
    path = db_path or _CFG_DB
    if not path.exists():
        raise FileNotFoundError(f"Database not found at {path}")

    conn = sqlite3.Connection(path)
    conn.execute("PRAGMA busy_timeout = 30000")
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys = ON")
    if use_wal:
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def api_request(
    url: str,
    retries: int = 3,
    delay: float = 0.5,
    timeout: int = 30,
) -> Optional[Dict]:
    """Make an HTTP JSON API request with retries and rate limiting.

    Args:
        url: The URL to fetch.
        retries: Number of retry attempts.
        delay: Seconds to sleep after each successful request (rate limiting).
        timeout: HTTP timeout in seconds.

    Returns:
        Parsed JSON dict, or None on failure.
    """
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT},
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
                time.sleep(delay)
                return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait_time = 2 ** attempt
                print(f"  Rate limited, waiting {wait_time}s...")
                time.sleep(wait_time)
            elif e.code == 404:
                return None
            else:
                print(f"  HTTP Error {e.code}: {e.reason}")
                if attempt == retries - 1:
                    return None
        except Exception as e:
            print(f"  Error: {e}")
            if attempt == retries - 1:
                return None
            time.sleep(1)

    return None


_HTML_TAG_RE = re.compile(r"<[^>]+>")
FEC_COMMITTEE_ID_RE = re.compile(r"^C[0-9]{8}$")

OPENFEC_BASE = "https://api.open.fec.gov/v1"


def strip_html(text: Optional[str]) -> Optional[str]:
    """Strip HTML tags from a string, collapsing leftover whitespace."""
    if not text:
        return text
    plain = _HTML_TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", plain).strip()


def fetch_openfec_committee(committee_id: str) -> Optional[Dict[str, Any]]:
    """Resolve a committee via the OpenFEC per-committee endpoint.

    Used as a fallback when a committee is newer than the local FEC bulk
    committee master snapshot. Requires OPENFEC_API_KEY in the environment.

    Returns the committee result dict (name, committee_type, designation, ...)
    or None if unset key / not found / API error.
    """
    api_key = os.environ.get("OPENFEC_API_KEY")
    if not api_key:
        return None

    url = f"{OPENFEC_BASE}/committee/{committee_id}/?api_key={api_key}"
    data = api_request(url)
    if not data:
        return None
    results = data.get("results") or []
    return results[0] if results else None


def upsert_committee_from_openfec(
    conn: sqlite3.Connection, committee_id: str, info: Dict[str, Any]
) -> None:
    """Insert/refresh a committees row from an OpenFEC committee result.

    Keeps detail lookups working for committees missing from the bulk master.
    """
    conn.execute(
        """
        INSERT INTO committees (
            committee_id, committee_name, committee_type, committee_type_full,
            committee_designation, committee_designation_full,
            party_code, party_full_name, connected_org_name, treasurer_name,
            street_1, street_2, city, state, zip, filing_frequency
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(committee_id) DO UPDATE SET
            committee_name = excluded.committee_name,
            committee_type = excluded.committee_type,
            committee_type_full = excluded.committee_type_full,
            committee_designation = excluded.committee_designation,
            committee_designation_full = excluded.committee_designation_full,
            party_code = excluded.party_code,
            party_full_name = excluded.party_full_name,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            committee_id,
            info.get("name") or committee_id,
            info.get("committee_type") or "",
            info.get("committee_type_full") or "",
            info.get("designation") or "",
            info.get("designation_full") or "",
            info.get("party") or "",
            info.get("party_full") or "",
            info.get("affiliated_committee_name") or "",
            info.get("treasurer_name") or "",
            (info.get("street_1") or ""),
            (info.get("street_2") or ""),
            (info.get("city") or ""),
            (info.get("state") or ""),
            (info.get("zip") or ""),
            info.get("filing_frequency") or "",
        ),
    )


def is_unresolved_committee_name(name: Optional[str], committee_id: str = "") -> bool:
    """True when a display name is empty, the raw committee ID, or ID-shaped."""
    if not name or not name.strip():
        return True
    name = name.strip()
    return name == committee_id or bool(FEC_COMMITTEE_ID_RE.match(name))


def download_image(
    url: str,
    timeout: int = 15,
    min_size: int = 100,
    user_agent: Optional[str] = None,
) -> Optional[bytes]:
    """Download an image and return bytes, or None on failure.

    Args:
        url: Image URL to download.
        timeout: HTTP timeout in seconds.
        min_size: Reject responses smaller than this (bytes). 0 to disable.
        user_agent: Override default User-Agent header.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": user_agent or USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = response.read()
            if min_size and len(data) < min_size:
                return None
            return data
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def download_yaml(
    url: str,
    item_name: str = "entries",
    timeout: int = 60,
) -> Union[List[Dict], Dict[str, Any]]:
    """Download and parse a YAML file from a URL.

    Args:
        url: URL of the YAML file.
        item_name: Label for progress messages (e.g. "legislators").
        timeout: HTTP timeout in seconds.

    Returns:
        Parsed YAML data (list or dict).
    """
    try:
        import yaml
    except ImportError:
        raise ImportError("PyYAML not installed. Run: pip3 install pyyaml")

    print(f"Downloading: {url}")
    with urllib.request.urlopen(url, timeout=timeout) as response:
        content = response.read().decode("utf-8")
    print(f"  Downloaded {len(content):,} bytes")
    data = yaml.safe_load(content)
    if isinstance(data, list):
        print(f"  Parsed {len(data)} {item_name}")
    elif isinstance(data, dict):
        print(f"  Parsed {len(data)} {item_name}")
    return data
