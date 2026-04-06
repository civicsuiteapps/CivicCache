#!/usr/bin/env python3
"""
Import candidate campaign websites, social links, bio data, and photos from Ballotpedia.

Two-phase approach:
  Phase 2: Ballotpedia scraping -- constructs URL slugs from FEC names, fetches pages,
           and extracts contact links, bio fields (profession, education, birthplace,
           religion, military), and candidate photos from the sidebar widget.
  Phase 3: URL pattern probing -- for candidates Ballotpedia missed, generates common
           campaign URL patterns ({first}{last}.com, {last}forcongress.com, etc.),
           probes them concurrently, and validates the responses.

Updates race_candidates: campaign_url, social_facebook, social_twitter, social_youtube,
       profession, education, birthplace, religion, military_branch, military_years,
       photo_url, image_blob.

Runs AFTER fec_candidates (same UPDATE pattern as import_candidate_photos.py).

Usage:
    python3 scripts/import_campaign_sites.py                    # All 2026 H/S candidates
    python3 scripts/import_campaign_sites.py --cycle 2026       # Explicit cycle
    python3 scripts/import_campaign_sites.py --state GA         # Single state
    python3 scripts/import_campaign_sites.py --dry-run          # Show matches, don't write
    python3 scripts/import_campaign_sites.py --limit 50         # Process only N candidates
    python3 scripts/import_campaign_sites.py --skip-probe       # Ballotpedia only, no URL probing
    python3 scripts/import_campaign_sites.py --skip-photos      # Don't download photos
"""

import argparse
import html as html_module
import re
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    from config import DB_PATH as _CFG_DB
except ImportError:
    _CFG_DB = Path("data/civic_cache.db")

DB_PATH = _CFG_DB

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# FEC name suffixes to strip
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v", "md", "phd", "esq", "dr"}


# ---------------------------------------------------------------------------
# Name utilities
# ---------------------------------------------------------------------------


def fec_name_to_ballotpedia_slug(candidate_name: str) -> str | None:
    """Convert FEC "LAST, FIRST MIDDLE" to Ballotpedia slug "First_Last".

    Mirrors the Swift `ballotpediaName` computed property in CivicCacheManager.

    Examples:
        "OSSOFF, T. JONATHAN"    -> "Jonathan_Ossoff"
        "GREENE, MARJORIE TAYLOR" -> "Marjorie_Greene"  (may need redirect)
        "CARTER, EARL L. JR."    -> "Earl_Carter"
        "MC BATH, LUCY"          -> "Lucy_McBath"
    """
    cleaned = candidate_name.strip()

    parts = cleaned.split(",", 1)
    if len(parts) != 2:
        return None

    last_raw = parts[0].strip()
    first_raw = parts[1].strip()

    # Tokenize and clean last name
    last_tokens = last_raw.split()
    last_tokens = [t for t in last_tokens if t.lower().rstrip(".") not in SUFFIXES]
    if not last_tokens:
        return None

    # Build last name: title-case each token, join with underscore
    # Handle "MC BATH" -> "McBath" (join adjacent MC/Mac prefixes)
    last_parts = []
    i = 0
    while i < len(last_tokens):
        token = last_tokens[i]
        if token.upper() in ("MC", "MAC") and i + 1 < len(last_tokens):
            combined = token.capitalize() + last_tokens[i + 1].capitalize()
            last_parts.append(combined)
            i += 2
        else:
            last_parts.append(token.capitalize())
            i += 1

    last_name = "_".join(last_parts)

    # Tokenize first name
    first_tokens = first_raw.replace(".", "").split()
    first_tokens = [t for t in first_tokens if t.lower() not in SUFFIXES]

    # Skip single-character initials -- use the next usable token
    first_name = ""
    for token in first_tokens:
        if len(token) > 1:
            first_name = token.capitalize()
            break

    if not first_name:
        return None

    return f"{first_name}_{last_name}"


# ---------------------------------------------------------------------------
# Ballotpedia scraping
# ---------------------------------------------------------------------------


def fetch_ballotpedia(slug: str, retries: int = 2) -> str | None:
    """Fetch a Ballotpedia page by slug. Returns HTML or None."""
    url = f"https://ballotpedia.org/{urllib.parse.quote(slug, safe='_')}"

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as resp:
                if resp.status == 200:
                    return resp.read().decode("utf-8", errors="replace")
                return None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429:
                time.sleep(5 * (attempt + 1))
            elif attempt == retries - 1:
                return None
            else:
                time.sleep(2)
        except Exception:
            if attempt == retries - 1:
                return None
            time.sleep(2)

    return None


def download_photo(photo_url: str) -> bytes | None:
    """Download a photo from Ballotpedia S3. Returns image bytes or None."""
    try:
        req = urllib.request.Request(photo_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                data = resp.read()
                if len(data) > 500:  # Skip tiny/broken images
                    return data
    except Exception:
        pass
    return None


def extract_ballotpedia_data(page_html: str) -> dict:
    """Extract all candidate data from a Ballotpedia page.

    Extracts from the sidebar widget:
    - Contact: campaign_url, social_facebook, social_twitter, social_youtube
    - Bio: profession, education, birthplace, religion
    - Military: military_branch, military_years
    - Photo: photo_url (S3 URL)

    Returns dict with all fields (None for missing).
    """
    result = {
        "campaign_url": None,
        "social_facebook": None,
        "social_twitter": None,
        "social_youtube": None,
        "profession": None,
        "education": None,
        "birthplace": None,
        "religion": None,
        "military_branch": None,
        "military_years": None,
        "photo_url": None,
    }

    # --- Contact links ---
    # Pattern: <a href="URL" target="_blank">Label</a> inside widget-row divs
    link_pattern = re.compile(
        r'<a[^>]+href="(https?://[^"]+)"[^>]*target="_blank"[^>]*>\s*'
        r'(Campaign website|Campaign site|Campaign Facebook|Campaign X|'
        r'Campaign Twitter|Campaign YouTube|'
        r'Official website|Official Facebook|Official X|'
        r'Official Twitter|Official YouTube)\s*</a>',
        re.IGNORECASE,
    )
    matches = link_pattern.findall(page_html)

    for url, label in matches:
        ll = label.lower()
        if ("campaign website" in ll or "campaign site" in ll) and not result["campaign_url"]:
            result["campaign_url"] = url
        elif "campaign facebook" in ll and not result["social_facebook"]:
            result["social_facebook"] = url
        elif ("campaign x" in ll or "campaign twitter" in ll) and not result["social_twitter"]:
            result["social_twitter"] = url
        elif "campaign youtube" in ll and not result["social_youtube"]:
            result["social_youtube"] = url

    # Fallback: official links when campaign links not found
    if not result["campaign_url"]:
        for url, label in matches:
            if "official website" in label.lower():
                result["campaign_url"] = url
                break
    if not result["social_facebook"]:
        for url, label in matches:
            if "official facebook" in label.lower():
                result["social_facebook"] = url
                break
    if not result["social_twitter"]:
        for url, label in matches:
            ll = label.lower()
            if "official x" in ll or "official twitter" in ll:
                result["social_twitter"] = url
                break
    if not result["social_youtube"]:
        for url, label in matches:
            if "official youtube" in label.lower():
                result["social_youtube"] = url
                break

    # --- Widget key-value pairs (bio data) ---
    kv_pattern = re.compile(
        r'<div class="widget-row[^"]*">\s*'
        r'<div class="widget-key">([^<]+)</div>\s*'
        r'<div class="widget-value">([^<]+)</div>',
    )
    kv_pairs = kv_pattern.findall(page_html)

    education_parts = []
    for key, val in kv_pairs:
        key_lower = key.strip().lower()
        val_clean = html_module.unescape(val.strip())

        if key_lower == "profession":
            result["profession"] = val_clean
        elif key_lower == "birthplace":
            result["birthplace"] = val_clean
        elif key_lower == "religion":
            result["religion"] = val_clean
        elif key_lower in ("service / branch", "service/branch"):
            result["military_branch"] = val_clean
        elif key_lower in ("years of service",):
            result["military_years"] = val_clean
        elif key_lower in (
            "bachelor's", "bachelors", "master's", "masters",
            "graduate", "law", "doctorate", "associate's", "associates",
            "m.d.", "j.d.", "ph.d.", "mba", "m.b.a.",
        ):
            # Normalize degree label
            degree = key.strip()
            degree = html_module.unescape(degree)
            education_parts.append(f"{degree}: {val_clean}")

    if education_parts:
        result["education"] = "; ".join(education_parts)

    # --- Photo URL ---
    # Look for candidate image in the infobox area
    photo_match = re.search(
        r'<div class="infobox[^"]*".*?<img[^>]+src="(https://s3\.amazonaws\.com/ballotpedia-api4/files/thumbs/[^"]+)"',
        page_html,
        re.DOTALL,
    )
    if photo_match:
        result["photo_url"] = photo_match.group(1)

    return result


# ---------------------------------------------------------------------------
# URL pattern probing
# ---------------------------------------------------------------------------

# Domains that indicate parked/for-sale pages
PARKED_MARKERS = [
    "godaddy", "sedo", "hugedomains", "parkingcrew", "bodis",
    "dan.com", "afternic", "namecheap", "domain for sale",
    "this domain", "buy this", "is for sale", "parked free",
    "under construction", "coming soon", "domainlapse",
]

# Keywords that indicate a real campaign page (require 2+ matches)
CAMPAIGN_KEYWORDS = [
    "congress", "senate", "house of representatives",
    "donate", "volunteer", "contribute",
    "campaign", "endorsements", "grassroots",
    "election", "ballot", "primary",
    "conservative", "progressive", "democrat", "republican",
    "paid for by", "authorized by", "committee",
    "district", "constituent",
]

# Lenient SSL context for probing (some campaign sites have bad certs)
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def generate_candidate_urls(first: str, last: str, office: str, state: str) -> list[str]:
    """Generate common campaign URL patterns for a candidate."""
    f = first.lower()
    l = last.lower()
    fl = f"{f}{l}"
    state_lower = state.lower() if state else ""

    patterns = [
        f"https://{fl}.com",
        f"https://www.{fl}.com",
        f"https://{fl}forcongress.com",
        f"https://www.{fl}forcongress.com",
        f"https://{l}forcongress.com",
        f"https://www.{l}forcongress.com",
        f"https://vote{l}.com",
        f"https://www.vote{l}.com",
        f"https://elect{l}.com",
        f"https://www.elect{l}.com",
        f"https://{fl}foramerica.com",
        f"https://{l}foramerica.com",
        f"https://{f}{l}2026.com",
        f"https://{l}2026.com",
        f"https://{fl}campaign.com",
    ]

    if office == "S" and state_lower:
        patterns.extend([
            f"https://{l}for{state_lower}.com",
            f"https://{fl}for{state_lower}.com",
            f"https://{fl}forsenate.com",
            f"https://{l}forsenate.com",
        ])

    if office == "H":
        patterns.extend([
            f"https://{fl}forhouse.com",
            f"https://{l}forhouse.com",
        ])

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for p in patterns:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def probe_url(
    url: str,
    first_name: str,
    last_name: str,
    rival_first_names: list[str] | None = None,
    timeout: int = 8,
) -> str | None:
    """Probe a URL to see if it's a real campaign website.

    Requires BOTH first and last name to appear on the page to avoid
    false positives (e.g. mitchellforcongress.com for wrong Mitchell).

    If *rival_first_names* is provided (other candidates sharing the same
    last name), the URL is rejected when a rival's full name appears on
    the page — e.g. "Jay Chen" on a page probed for Anita Chen.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type and "text/xhtml" not in content_type:
                return None

            body = resp.read(100_000).decode("utf-8", errors="replace").lower()

        # Require BOTH first and last name on the page
        if first_name.lower() not in body or last_name.lower() not in body:
            return None

        # Reject if a rival candidate's full name appears on the page
        # (e.g. "jay chen" on a page being probed for Anita Chen)
        if rival_first_names:
            last_lower = last_name.lower()
            for rival in rival_first_names:
                rival_full = f"{rival.lower()} {last_lower}"
                if rival_full in body:
                    return None

        for marker in PARKED_MARKERS:
            if marker in body:
                return None

        kw_matches = sum(1 for kw in CAMPAIGN_KEYWORDS if kw in body)
        if kw_matches >= 2:
            return url
        return None

    except Exception:
        return None


def probe_candidate_urls(
    first: str,
    last: str,
    office: str,
    state: str,
    rival_first_names: list[str] | None = None,
) -> str | None:
    """Generate and probe candidate URL patterns concurrently."""
    urls = generate_candidate_urls(first, last, office, state)

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(probe_url, url, first, last, rival_first_names): url
            for url in urls
        }
        for future in as_completed(futures):
            result = future.result()
            if result:
                for f in futures:
                    f.cancel()
                return result

    return None


def fec_name_to_parts(candidate_name: str) -> tuple[str, str]:
    """Convert FEC name to (first, last) for URL pattern generation."""
    cleaned = candidate_name.replace(".", "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)

    parts = cleaned.split(",", 1)
    if len(parts) == 2:
        last_tokens = parts[0].strip().split()
        first_tokens = parts[1].strip().split()
    else:
        tokens = cleaned.split()
        first_tokens = tokens[:1]
        last_tokens = tokens[1:]

    last_tokens = [t for t in last_tokens if t.lower() not in SUFFIXES]
    first_tokens = [t for t in first_tokens if t.lower() not in SUFFIXES]

    first = ""
    for token in first_tokens:
        if len(token) > 1:
            first = token.capitalize()
            break

    last = last_tokens[-1].capitalize() if last_tokens else ""
    return (first, last)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

# All enrichment columns that can be populated
ENRICHMENT_COLS = [
    "campaign_url", "social_facebook", "social_twitter", "social_youtube",
    "profession", "education", "birthplace", "religion",
    "military_branch", "military_years", "photo_url",
]


def get_candidates_needing_enrichment(
    conn: sqlite3.Connection,
    cycle: int | None,
    state: str | None,
    limit: int | None,
) -> list[dict]:
    """Get race_candidates that haven't been enriched yet.

    A candidate is considered un-enriched if campaign_url is NULL.
    """
    sql = """
    SELECT DISTINCT rc.candidate_id, rc.candidate_name, rc.party_code,
           er.state, er.office, rc.image_blob IS NOT NULL as has_photo
    FROM race_candidates rc
    JOIN election_races er ON er.race_id = rc.race_id
    WHERE rc.campaign_url IS NULL
    """
    params = []
    if cycle:
        sql += " AND rc.election_cycle = ?"
        params.append(cycle)
    if state:
        sql += " AND er.state = ?"
        params.append(state.upper())

    sql += " AND er.office != 'P'"
    sql += " ORDER BY rc.total_receipts DESC"

    if limit:
        sql += f" LIMIT {limit}"

    rows = conn.execute(sql, params).fetchall()

    candidates = []
    seen = set()
    for row in rows:
        cid = row[0]
        if cid in seen:
            continue
        seen.add(cid)
        candidates.append({
            "candidate_id": cid,
            "candidate_name": row[1],
            "party_code": row[2],
            "state": row[3],
            "office": row[4],
            "has_photo": bool(row[5]),
        })
    return candidates


def update_candidate_data(
    conn: sqlite3.Connection,
    candidate_id: str,
    data: dict,
    photo_bytes: bytes | None = None,
) -> int:
    """Update enrichment fields for all rows with this candidate_id."""
    sets = []
    params = []
    for col in ENRICHMENT_COLS:
        if data.get(col):
            sets.append(f"{col} = ?")
            params.append(data[col])

    if photo_bytes:
        sets.append("image_blob = ?")
        params.append(photo_bytes)

    if not sets:
        return 0

    params.append(candidate_id)
    sql = f"UPDATE race_candidates SET {', '.join(sets)} WHERE candidate_id = ?"
    result = conn.execute(sql, params)
    return result.rowcount


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_url(url: str, first_name: str, last_name: str, timeout: int = 10) -> bool:
    """Fetch a URL and check if BOTH first and last name appear on the page.

    Also passes if the URL domain itself contains both names (strong signal),
    or if the first name appears in the URL domain (e.g. matthewschultzforcongress.com).
    """
    first_lower = first_name.lower()
    last_lower = last_name.lower()

    # If URL domain contains both names, trust it
    domain = urllib.parse.urlparse(url).netloc.lower()
    if first_lower in domain and last_lower in domain:
        return True

    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type and "text/xhtml" not in content_type:
                return False
            body = resp.read(200_000).decode("utf-8", errors="replace").lower()

        return first_lower in body and last_lower in body

    except Exception:
        return False


def verify_probed_urls(
    conn: sqlite3.Connection,
    cycle: int | None,
    state: str | None,
    dry_run: bool,
) -> int:
    """Re-verify all probed URLs by checking full name on the page.

    Probed URLs are identified as rows with campaign_url but no Ballotpedia data
    (no social links, no profession, no education, no photo_url).
    """
    print("\n--- Verification Mode: Checking probed URLs ---")

    sql = """
    SELECT DISTINCT rc.candidate_id, rc.candidate_name, rc.campaign_url,
           er.state, er.office
    FROM race_candidates rc
    JOIN election_races er ON er.race_id = rc.race_id
    WHERE rc.campaign_url IS NOT NULL
      AND rc.social_facebook IS NULL
      AND rc.social_twitter IS NULL
      AND rc.profession IS NULL
      AND rc.education IS NULL
      AND rc.photo_url IS NULL
    """
    params = []
    if cycle:
        sql += " AND rc.election_cycle = ?"
        params.append(cycle)
    if state:
        sql += " AND er.state = ?"
        params.append(state.upper())

    rows = conn.execute(sql, params).fetchall()

    # Deduplicate by candidate_id
    seen = set()
    candidates = []
    for row in rows:
        cid = row[0]
        if cid in seen:
            continue
        seen.add(cid)
        candidates.append({
            "candidate_id": cid,
            "candidate_name": row[1],
            "campaign_url": row[2],
            "state": row[3],
            "office": row[4],
        })

    print(f"  Probed URLs to verify: {len(candidates)}")
    if not candidates:
        print("  Nothing to verify.")
        conn.close()
        return 0

    verified = 0
    failed = 0
    errors = 0

    # Prepare verification tasks
    tasks = []
    for cand in candidates:
        first, last = fec_name_to_parts(cand["candidate_name"])
        if not first or not last:
            errors += 1
            continue
        tasks.append((cand, first, last))

    # Run verification concurrently (10 workers)
    batch_size = 50
    for batch_start in range(0, len(tasks), batch_size):
        batch = tasks[batch_start:batch_start + batch_size]

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(verify_url, cand["campaign_url"], first, last): (cand, first, last)
                for cand, first, last in batch
            }

            for future in as_completed(futures):
                cand, first, last = futures[future]
                ok = future.result()

                if ok:
                    verified += 1
                else:
                    failed += 1
                    print(
                        f"    FAIL: {cand['candidate_name'][:35]:35s} "
                        f"{cand['state']}-{cand['office']} "
                        f"{cand['campaign_url']}"
                    )

                    if not dry_run:
                        conn.execute(
                            "UPDATE race_candidates SET campaign_url = NULL WHERE candidate_id = ?",
                            (cand["candidate_id"],),
                        )

        if not dry_run:
            conn.commit()

        done = min(batch_start + batch_size, len(tasks))
        print(
            f"  [{done}/{len(tasks)}] "
            f"verified={verified} failed={failed}"
        )

    if not dry_run:
        conn.commit()

    print(f"\n--- Verification Complete ---")
    print(f"  Checked:  {verified + failed + errors}")
    print(f"  Verified: {verified}")
    print(f"  Failed:   {failed} (campaign_url set to NULL)")
    print(f"  Errors:   {errors}")

    conn.close()
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import candidate data from Ballotpedia + URL probing"
    )
    parser.add_argument(
        "--cycle", type=int, default=2026,
        help="Election cycle to process (default: 2026)",
    )
    parser.add_argument(
        "--state", type=str, default=None,
        help="Only process a single state (e.g., GA)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Max candidates to process (ordered by fundraising)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show found data without writing to the database",
    )
    parser.add_argument(
        "--delay", type=float, default=1.5,
        help="Seconds between Ballotpedia requests (default: 1.5)",
    )
    parser.add_argument(
        "--skip-ballotpedia", action="store_true",
        help="Skip Phase 2 Ballotpedia scraping (URL probing only)",
    )
    parser.add_argument(
        "--skip-probe", action="store_true",
        help="Skip Phase 3 URL pattern probing (Ballotpedia only)",
    )
    parser.add_argument(
        "--skip-photos", action="store_true",
        help="Skip downloading candidate photos",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Verify existing probed URLs (check full name on page), NULL out false positives",
    )
    parser.add_argument(
        "--db", type=Path, default=DB_PATH,
        help=f"Database path (default: {DB_PATH})",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("CivicCache - Ballotpedia Candidate Enrichment")
    print("=" * 60)
    print(f"  Database:    {args.db}")
    print(f"  Cycle:       {args.cycle}")
    print(f"  State:       {args.state or 'all'}")
    print(f"  Limit:       {args.limit or 'none'}")
    print(f"  Delay:       {args.delay}s between requests")
    print(f"  Dry run:     {args.dry_run}")
    print(f"  Skip photos: {args.skip_photos}")
    print(f"  Skip probe:  {args.skip_probe}")

    if not args.db.exists():
        print(f"\nERROR: Database not found: {args.db}")
        return 1

    conn = sqlite3.connect(str(args.db))

    # --verify mode: re-check existing probed URLs for full name presence
    if args.verify:
        return verify_probed_urls(conn, args.cycle, args.state, args.dry_run)

    # Phase 1: Get candidates
    print("\n--- Phase 1: Finding candidates needing enrichment ---")
    candidates = get_candidates_needing_enrichment(conn, args.cycle, args.state, args.limit)
    print(f"  Candidates to enrich: {len(candidates)}")

    if not candidates:
        print("  All candidates already enriched (or no candidates found).")
        conn.close()
        return 0

    # Phase 2: Fetch Ballotpedia pages
    stats = {
        "searched": 0, "found": 0, "not_found": 0, "page_404": 0,
        "updated_rows": 0, "skipped": 0, "photos_downloaded": 0,
        "with_website": 0, "with_facebook": 0, "with_twitter": 0, "with_youtube": 0,
        "with_profession": 0, "with_education": 0, "with_birthplace": 0,
        "with_religion": 0, "with_military": 0,
    }

    if args.skip_ballotpedia:
        print("\n--- Phase 2: Ballotpedia scraping (skipped via --skip-ballotpedia) ---")

    if not args.skip_ballotpedia:
        est_minutes = len(candidates) * args.delay / 60
        print(f"\n--- Phase 2: Ballotpedia scraping ({est_minutes:.0f} min estimated) ---")

    for i, cand in enumerate(candidates, 1):
        if args.skip_ballotpedia:
            break
        slug = fec_name_to_ballotpedia_slug(cand["candidate_name"])

        if not slug:
            stats["skipped"] += 1
            continue

        # Progress
        if i % 25 == 0 or i == len(candidates) or i == 1:
            pct = stats["found"] / max(stats["searched"], 1) * 100
            print(
                f"  [{i}/{len(candidates)}] "
                f"found={stats['found']} ({pct:.0f}%) "
                f"404={stats['page_404']} photos={stats['photos_downloaded']}"
            )

        page_html = fetch_ballotpedia(slug)
        stats["searched"] += 1

        if page_html is None:
            stats["page_404"] += 1
            stats["not_found"] += 1
            time.sleep(args.delay)
            continue

        data = extract_ballotpedia_data(page_html)
        has_any = any(data.values())

        if has_any:
            stats["found"] += 1

            # Track field coverage
            if data["campaign_url"]:
                stats["with_website"] += 1
            if data["social_facebook"]:
                stats["with_facebook"] += 1
            if data["social_twitter"]:
                stats["with_twitter"] += 1
            if data["social_youtube"]:
                stats["with_youtube"] += 1
            if data["profession"]:
                stats["with_profession"] += 1
            if data["education"]:
                stats["with_education"] += 1
            if data["birthplace"]:
                stats["with_birthplace"] += 1
            if data["religion"]:
                stats["with_religion"] += 1
            if data["military_branch"]:
                stats["with_military"] += 1

            # Download photo if available and candidate doesn't already have one
            # Validate: photo filename must contain the candidate's last name
            # to prevent Ballotpedia redirects/disambiguations from saving
            # the wrong person's photo (e.g. Qadir getting Elam's photo)
            photo_bytes = None
            if (
                data["photo_url"]
                and not args.skip_photos
                and not cand["has_photo"]
            ):
                # Extract last name from FEC "LAST, FIRST" format
                cand_last = cand["candidate_name"].split(",")[0].strip()
                photo_lower = data["photo_url"].lower()
                last_underscored = cand_last.lower().replace(" ", "_")
                last_joined = cand_last.lower().replace(" ", "")
                if last_underscored in photo_lower or last_joined in photo_lower:
                    photo_bytes = download_photo(data["photo_url"])
                    if photo_bytes:
                        stats["photos_downloaded"] += 1
                else:
                    # Photo filename doesn't match candidate — skip it
                    data["photo_url"] = None
                    stats.setdefault("photos_skipped_mismatch", 0)
                    stats["photos_skipped_mismatch"] += 1

            if args.dry_run:
                parts = [f"{cand['candidate_name'][:30]:30s}"]
                if data["campaign_url"]:
                    parts.append(f"web={data['campaign_url']}")
                if data["profession"]:
                    parts.append(f"job={data['profession']}")
                if data["education"]:
                    parts.append(f"edu={data['education'][:40]}")
                if data["birthplace"]:
                    parts.append(f"born={data['birthplace']}")
                if data["military_branch"]:
                    parts.append(f"mil={data['military_branch']}")
                if photo_bytes:
                    parts.append(f"photo={len(photo_bytes)//1024}KB")
                print(f"    {' | '.join(parts)}")
            else:
                rows = update_candidate_data(
                    conn, cand["candidate_id"], data, photo_bytes
                )
                stats["updated_rows"] += rows

                if stats["found"] % 50 == 0:
                    conn.commit()
        else:
            stats["not_found"] += 1

        time.sleep(args.delay)

    if not args.dry_run:
        conn.commit()

    # Phase 3: URL pattern probing for candidates Ballotpedia missed
    probe_stats = {"probed": 0, "found": 0, "updated_rows": 0}

    if not args.skip_probe:
        still_missing = get_candidates_needing_enrichment(conn, args.cycle, args.state, None)
        if args.limit:
            still_missing = still_missing[:args.limit]

        if still_missing:
            print(f"\n--- Phase 3: URL pattern probing ({len(still_missing)} candidates) ---")

            # Build a lookup of rival first names by last name.
            # If two candidates share a last name (e.g. "Anita Chen" and
            # "Jay Chen"), the probe for one must reject pages that belong
            # to the other.
            _last_to_firsts: dict[str, list[str]] = {}
            for cand in still_missing:
                first, last = fec_name_to_parts(cand["candidate_name"])
                if first and last:
                    _last_to_firsts.setdefault(last.lower(), []).append(first)

            # Prepare tasks (skip candidates with unusable names)
            probe_tasks = []
            for cand in still_missing:
                first, last = fec_name_to_parts(cand["candidate_name"])
                if first and last and len(first) > 1:
                    # Rival first names: others with same last name
                    rivals = [
                        n for n in _last_to_firsts.get(last.lower(), [])
                        if n.lower() != first.lower()
                    ] or None
                    probe_tasks.append((cand, first, last, rivals))

            # Process candidates in parallel batches
            batch_size = 20
            for batch_start in range(0, len(probe_tasks), batch_size):
                batch = probe_tasks[batch_start:batch_start + batch_size]

                with ThreadPoolExecutor(max_workers=20) as executor:
                    futures = {
                        executor.submit(
                            probe_candidate_urls, first, last, cand["office"], cand["state"],
                            rivals,
                        ): cand
                        for cand, first, last, rivals in batch
                    }

                    for future in as_completed(futures):
                        cand = futures[future]
                        found_url = future.result()
                        probe_stats["probed"] += 1

                        if found_url:
                            probe_stats["found"] += 1
                            probe_data = {"campaign_url": found_url}

                            if args.dry_run:
                                first, last = fec_name_to_parts(cand["candidate_name"])
                                print(f"    PROBE: {first} {last} ({cand['state']}) -> {found_url}")
                            else:
                                rows = update_candidate_data(conn, cand["candidate_id"], probe_data)
                                probe_stats["updated_rows"] += rows

                if not args.dry_run:
                    conn.commit()

                done = min(batch_start + batch_size, len(probe_tasks))
                if done % 100 < batch_size or done == len(probe_tasks):
                    pct = probe_stats["found"] / max(probe_stats["probed"], 1) * 100
                    print(
                        f"  [{done}/{len(probe_tasks)}] "
                        f"found={probe_stats['found']} ({pct:.0f}%)"
                    )
        else:
            print("\n--- Phase 3: URL pattern probing (skipped, all candidates covered) ---")
    else:
        print("\n--- Phase 3: URL pattern probing (skipped via --skip-probe) ---")

    if not args.dry_run:
        from datetime import datetime
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            ("campaign_sites_last_import", datetime.now().isoformat()),
        )
        conn.commit()

    # Phase 4: Verification
    print("\n--- Verification ---")
    total = conn.execute(
        "SELECT COUNT(*) FROM race_candidates WHERE election_cycle = ?",
        (args.cycle,),
    ).fetchone()[0]
    with_url = conn.execute(
        "SELECT COUNT(*) FROM race_candidates WHERE election_cycle = ? AND campaign_url IS NOT NULL",
        (args.cycle,),
    ).fetchone()[0]
    with_social = conn.execute(
        "SELECT COUNT(*) FROM race_candidates WHERE election_cycle = ? AND (social_facebook IS NOT NULL OR social_twitter IS NOT NULL)",
        (args.cycle,),
    ).fetchone()[0]
    with_bio = conn.execute(
        "SELECT COUNT(*) FROM race_candidates WHERE election_cycle = ? AND profession IS NOT NULL",
        (args.cycle,),
    ).fetchone()[0]
    with_photo = conn.execute(
        "SELECT COUNT(*) FROM race_candidates WHERE election_cycle = ? AND image_blob IS NOT NULL",
        (args.cycle,),
    ).fetchone()[0]
    print(f"  {args.cycle} candidates with campaign_url:  {with_url} / {total}")
    print(f"  {args.cycle} candidates with social links:  {with_social} / {total}")
    print(f"  {args.cycle} candidates with profession:    {with_bio} / {total}")
    print(f"  {args.cycle} candidates with photo:         {with_photo} / {total}")

    if with_url > 0 and not args.dry_run:
        rows = conn.execute(
            """SELECT candidate_name, campaign_url, profession
               FROM race_candidates
               WHERE election_cycle = ? AND campaign_url IS NOT NULL
               LIMIT 10""",
            (args.cycle,),
        ).fetchall()
        for name, url, prof in rows:
            prof_str = f" ({prof})" if prof else ""
            print(f"    {name:30s} {url}{prof_str}")

    conn.close()

    print("\n" + "=" * 60)
    print("Import Complete")
    print("=" * 60)
    print(f"  Ballotpedia (Phase 2):")
    print(f"    Searched:         {stats['searched']}")
    print(f"    Found (any data): {stats['found']}")
    print(f"      Campaign URL:   {stats['with_website']}")
    print(f"      Facebook:       {stats['with_facebook']}")
    print(f"      X/Twitter:      {stats['with_twitter']}")
    print(f"      YouTube:        {stats['with_youtube']}")
    print(f"      Profession:     {stats['with_profession']}")
    print(f"      Education:      {stats['with_education']}")
    print(f"      Birthplace:     {stats['with_birthplace']}")
    print(f"      Religion:       {stats['with_religion']}")
    print(f"      Military:       {stats['with_military']}")
    print(f"      Photos DL'd:    {stats['photos_downloaded']}")
    if stats.get("photos_skipped_mismatch", 0) > 0:
        print(f"      Photos skipped: {stats['photos_skipped_mismatch']} (name mismatch)")
    print(f"    Page not found:   {stats['page_404']}")
    print(f"    No data found:    {stats['not_found'] - stats['page_404']}")
    print(f"    Skipped (names):  {stats['skipped']}")
    if probe_stats["probed"] > 0:
        print(f"  URL Probing (Phase 3):")
        print(f"    Probed:           {probe_stats['probed']}")
        print(f"    Found:            {probe_stats['found']}")
    total_found = stats["found"] + probe_stats["found"]
    total_searched = stats["searched"] + probe_stats["probed"]
    print(f"  Total: {total_found} / {total_searched} candidates enriched")
    if not args.dry_run:
        print(f"  DB rows updated:  {stats['updated_rows'] + probe_stats['updated_rows']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
