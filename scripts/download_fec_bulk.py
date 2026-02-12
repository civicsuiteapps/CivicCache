#!/usr/bin/env python3
"""
Download FEC bulk data files from the FEC S3-backed website.

Usage:
    python3 scripts/download_fec_bulk.py --cycles 2024,2026
    python3 scripts/download_fec_bulk.py --cycles 2026 --types all-candidates,linkages
    python3 scripts/download_fec_bulk.py --cycles 2026 --include-individual  # large files

Files are saved to FEC-Bulk-Data/ matching the directory layout that
import_fec_committees.py and import_fec_summary.py expect.
"""

import argparse
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

from config import FEC_DIR

# ---------------------------------------------------------------------------
# FEC bulk-data URL patterns
#
# https://www.fec.gov/files/bulk-downloads/{CYCLE}/{filename}
# ---------------------------------------------------------------------------

BASE_URL = "https://www.fec.gov/files/bulk-downloads"

# type-key -> (subdirectory, filename template with {yy} = 2-digit cycle)
FILE_TYPES = {
    "all-candidates": ("All-Candidates", "weball{yy}.zip"),
    "linkages": ("Candidate-Committee-Linkages", "ccl{yy}.zip"),
    "committee-master": ("Committee-Master", "cm{yy}.zip"),
    "committee-contributions": ("Contributions-from-Committees", "pas2{yy}.zip"),
    "individual-contributions": ("Contributions-by-Individuals", "indiv{yy}.zip"),
}

# Default types (exclude individual-contributions -- 10GB+ per cycle)
DEFAULT_TYPES = [
    "all-candidates",
    "linkages",
    "committee-master",
    "committee-contributions",
]


def download_file(url: str, dest: Path, retries: int = 3) -> bool:
    """Download a URL to dest. Skips if dest already exists."""
    if dest.exists():
        size_mb = dest.stat().st_size / (1024 * 1024)
        print(f"  Skip (exists, {size_mb:.1f} MB): {dest.name}")
        return True

    dest.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(retries):
        try:
            print(f"  Downloading {url} ...")
            req = urllib.request.Request(
                url, headers={"User-Agent": "CivicCache/1.0"}
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 1024 * 256

                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = downloaded / total * 100
                            mb = downloaded / (1024 * 1024)
                            print(
                                f"\r    {mb:.1f} MB ({pct:.0f}%)",
                                end="",
                                flush=True,
                            )

            print(f"\n    Saved: {dest} ({dest.stat().st_size / (1024*1024):.1f} MB)")
            return True

        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            print(f"\n    Attempt {attempt + 1} failed: {exc}")
            if dest.exists():
                dest.unlink()
            if attempt < retries - 1:
                time.sleep(2 ** attempt)

    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Download FEC bulk data files")
    parser.add_argument(
        "--cycles",
        required=True,
        help="Comma-separated election cycles (e.g. 2024,2026)",
    )
    parser.add_argument(
        "--types",
        default=None,
        help=f"Comma-separated file types. Available: {', '.join(FILE_TYPES)}",
    )
    parser.add_argument(
        "--include-individual",
        action="store_true",
        help="Include individual-contributions (10GB+ per cycle)",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=FEC_DIR,
        help=f"Destination directory (default: {FEC_DIR})",
    )
    args = parser.parse_args()

    cycles = [int(c.strip()) for c in args.cycles.split(",")]

    if args.types:
        type_keys = [t.strip() for t in args.types.split(",")]
        for t in type_keys:
            if t not in FILE_TYPES:
                print(f"Unknown type: {t}")
                print(f"Available: {', '.join(FILE_TYPES)}")
                return 1
    else:
        type_keys = list(DEFAULT_TYPES)
        if args.include_individual:
            type_keys.append("individual-contributions")

    print(f"FEC Bulk Data Download")
    print(f"  Cycles: {cycles}")
    print(f"  Types:  {type_keys}")
    print(f"  Dest:   {args.dest}\n")

    ok = 0
    fail = 0

    for cycle in cycles:
        yy = str(cycle)[-2:]  # e.g. 2024 -> "24"
        print(f"Cycle {cycle}:")

        for tkey in type_keys:
            subdir, filename_tpl = FILE_TYPES[tkey]
            filename = filename_tpl.format(yy=yy)
            url = f"{BASE_URL}/{cycle}/{filename}"
            dest = args.dest / subdir / filename

            success = download_file(url, dest)
            if success:
                ok += 1
            else:
                fail += 1
                print(f"    FAILED: {url}")

    print(f"\nDone: {ok} downloaded, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
