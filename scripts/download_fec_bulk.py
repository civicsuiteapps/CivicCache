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
import re
import sys
import time
import urllib.request
import urllib.error
import zipfile
from pathlib import Path

from config import FEC_DIR

# CivicScope only uses current election data — skip historical cycles
MIN_ELECTION_CYCLE = 2024

# ---------------------------------------------------------------------------
# FEC bulk-data URL patterns
#
# https://www.fec.gov/files/bulk-downloads/{CYCLE}/{filename}
# ---------------------------------------------------------------------------

BASE_URL = "https://www.fec.gov/files/bulk-downloads"

# type-key -> (subdirectory, filename template with {yy} = 2-digit cycle)
FILE_TYPES = {
    "all-candidates": ("All-Candidates", "weball{yy}.zip"),
    "candidate-master": ("Candidate-Master", "cn{yy}.zip"),
    "linkages": ("Candidate-Committee-Linkages", "ccl{yy}.zip"),
    "committee-master": ("Committee-Master", "cm{yy}.zip"),
    "committee-contributions": ("Contributions-from-Committees", "pas2{yy}.zip"),
    "individual-contributions": ("Contributions-by-Individuals", "indiv{yy}.zip"),
}

# Default types (exclude individual-contributions -- 10GB+ per cycle)
DEFAULT_TYPES = [
    "all-candidates",
    "candidate-master",
    "linkages",
    "committee-master",
    "committee-contributions",
]


# Mapping from zip internal names to the standard txt naming convention.
# The import scripts expect files named like "Candidate-Master_2025-2026.txt".
EXTRACT_NAMES = {
    "all-candidates": ("weball{yy}.txt", "All-Candidates_{y1}-{y2}.txt"),
    "candidate-master": ("cn.txt", "Candidate-Master_{y1}-{y2}.txt"),
    "linkages": ("ccl.txt", "Candidate-Committee-Linkages_{y1}-{y2}.txt"),
    "committee-master": ("cm.txt", "Committee-Master_{y1}-{y2}.txt"),
    "committee-contributions": ("itpas2.txt", "Contributions-from-Committees_{y1}-{y2}.txt"),
    "individual-contributions": ("itcont.txt", "Contributions-by-Individuals_{y1}-{y2}.txt"),
}


def extract_zip(zip_path: Path, type_key: str, cycle: int) -> bool:
    """Extract a downloaded zip file to the standard txt naming convention."""
    yy = str(cycle)[-2:]
    y1 = cycle - 1
    y2 = cycle
    dest_dir = zip_path.parent

    if type_key not in EXTRACT_NAMES:
        return False

    inner_name, out_template = EXTRACT_NAMES[type_key]
    inner_name = inner_name.format(yy=yy)
    out_name = out_template.format(y1=y1, y2=y2)
    out_path = dest_dir / out_name

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            # Find the data file inside the zip (match by expected name or first .txt)
            source = None
            for n in names:
                if n.lower() == inner_name.lower():
                    source = n
                    break
            if source is None:
                # Fallback: first .txt file in the archive
                txt_files = [n for n in names if n.lower().endswith(".txt")]
                if txt_files:
                    source = txt_files[0]

            if source is None:
                print(f"    Warning: no .txt file found in {zip_path.name}")
                return False

            # Extract to a temp name and rename
            zf.extract(source, dest_dir)
            extracted = dest_dir / source
            if extracted != out_path:
                if out_path.exists():
                    out_path.unlink()
                extracted.rename(out_path)

            size_mb = out_path.stat().st_size / (1024 * 1024)
            print(f"    Extracted: {out_name} ({size_mb:.1f} MB)")
            return True

    except (zipfile.BadZipFile, OSError) as exc:
        print(f"    Warning: failed to extract {zip_path.name}: {exc}")
        return False


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
        "--force",
        action="store_true",
        help="Delete existing files before downloading (refresh stale data)",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=FEC_DIR,
        help=f"Destination directory (default: {FEC_DIR})",
    )
    args = parser.parse_args()

    cycles = [int(c.strip()) for c in args.cycles.split(",")]
    filtered = [c for c in cycles if c < MIN_ELECTION_CYCLE]
    if filtered:
        print(f"  Warning: skipping cycles before {MIN_ELECTION_CYCLE}: {filtered}")
    cycles = [c for c in cycles if c >= MIN_ELECTION_CYCLE]
    if not cycles:
        print(f"  No cycles >= {MIN_ELECTION_CYCLE} to download.")
        return 0

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

            # --force: fetch into a temp file and only replace the existing
            # zip + extracted txt once the download succeeded. Deleting first
            # (the old behaviour) wiped Candidate-Master_2025-2026.txt when
            # the 2026-09-01 monthly run hit a DNS outage, and the next
            # import_fec_candidates run rebuilt 2026 races from stale files.
            if args.force and dest.exists():
                tmp = dest.with_name(dest.name + ".part")
                if tmp.exists():
                    tmp.unlink()
                success = download_file(url, tmp)
                if success:
                    size_mb = dest.stat().st_size / (1024 * 1024)
                    print(f"  Force: replacing {dest.name} ({size_mb:.1f} MB)")
                    dest.unlink()
                    tmp.rename(dest)
                    if tkey in EXTRACT_NAMES:
                        _, out_tpl = EXTRACT_NAMES[tkey]
                        old_txt = dest.parent / out_tpl.format(y1=cycle - 1, y2=cycle)
                        if old_txt.exists():
                            old_txt.unlink()
                else:
                    if tmp.exists():
                        tmp.unlink()
                    print(f"    keeping existing {dest.name} (download failed)")
            else:
                success = download_file(url, dest)

            if success:
                extract_zip(dest, tkey, cycle)
                ok += 1
            else:
                fail += 1
                print(f"    FAILED: {url}")

    print(f"\nDone: {ok} downloaded, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
