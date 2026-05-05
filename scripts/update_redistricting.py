#!/usr/bin/env python3
"""
Fetch official state-published redistricting shapefiles and merge new
congressional district boundaries into DistrictData.json.

California (AB 604) and Texas (PLANC2333) enacted new maps in 2025 for
the 2026 elections. Census TIGERweb hasn't updated yet, so this script
pulls the authoritative shapefiles directly from state sources.

Usage:
    python3 scripts/update_redistricting.py                  # Update both CA and TX
    python3 scripts/update_redistricting.py --states TX      # Texas only
    python3 scripts/update_redistricting.py --force          # Re-download cached files
    python3 scripts/update_redistricting.py --dry-run        # Parse & show stats, don't write
    python3 scripts/update_redistricting.py --inspect        # Show shapefile field names and exit
"""

import argparse
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

try:
    import shapefile
except ImportError:
    print("Error: pyshp not installed. Run: pip3 install pyshp")
    sys.exit(1)

try:
    from pyproj import CRS, Transformer
except ImportError:
    print("Error: pyproj not installed. Run: pip3 install pyproj")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Source configuration
# ---------------------------------------------------------------------------

SOURCES = {
    "48": {  # Texas
        "url": "https://data.capitol.texas.gov/dataset/748c952b-e926-4f44-8d01-a738884b3ec8/resource/5712ebe1-d777-4d4a-b836-0534e17bca01/download/planc2333.zip",
        "expected_districts": 38,
        "plan_name": "PLANC2333",
        "district_field_candidates": ["DISTRICT", "DIST", "DIST_NUM", "PLANC2333", "CD"],
    },
    "06": {  # California
        "url": "https://aelc.assembly.ca.gov/media/2608",
        "expected_districts": 52,
        "plan_name": "AB604",
        "district_field_candidates": ["DISTRICT", "DIST", "CD", "DISTRICTNO", "CDIST"],
    },
    "12": {  # Florida
        "url": "https://www.flsenate.gov/PublishedContent/Session/Congressional/EOGPCRP2026.zip",
        "expected_districts": 28,
        "plan_name": "EOGPCRP2026",
        "district_field_candidates": ["DISTRICT", "DIST", "CD", "DISTRICTNO", "EOGPCRP2026"],
    },
}

FIPS_TO_STATE = {
    "06": "CA",
    "12": "FL",
    "48": "TX",
}

# Bounding boxes for coordinate validation [min_lat, max_lat, min_lon, max_lon]
STATE_BOUNDS = {
    "06": (32.0, 42.5, -125.0, -114.0),   # California
    "12": (24.4, 31.1, -87.7, -79.9),      # Florida
    "48": (25.5, 36.6, -106.7, -93.5),     # Texas
}

# Paths
RAW_DIR = Path(__file__).parent.parent / "data" / "raw" / "redistricting"
DISTRICT_JSON_CANDIDATES = [
    Path(__file__).parent.parent / "data" / "DistrictData.json",
    Path(__file__).parent.parent / "data" / "raw" / "congress" / "DistrictData.json",
]

USER_AGENT = "CivicCache/1.0"


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_shapefile(url: str, dest: Path, force: bool = False, retries: int = 3) -> bool:
    """Download a shapefile zip with retry and progress display."""
    if dest.exists() and not force:
        size_mb = dest.stat().st_size / (1024 * 1024)
        print(f"  Skip (cached, {size_mb:.1f} MB): {dest.name}")
        return True

    dest.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(retries):
        try:
            print(f"  Downloading {url} ...")
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 256 * 1024

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
                            print(f"\r    {mb:.1f} MB ({pct:.0f}%)", end="", flush=True)

                size_mb = dest.stat().st_size / (1024 * 1024)
                print(f"\n    Saved: {dest.name} ({size_mb:.1f} MB)")
                return True

        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            print(f"\n    Attempt {attempt + 1}/{retries} failed: {exc}")
            if dest.exists():
                dest.unlink()
            if attempt < retries - 1:
                time.sleep(2 ** attempt)

    return False


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------

def extract_shapefile(zip_path: Path, extract_dir: Path) -> Optional[Path]:
    """Extract zip and return path to the .shp file inside."""
    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
    except zipfile.BadZipFile as exc:
        print(f"    Error: bad zip file {zip_path.name}: {exc}")
        return None

    # Find .shp file (may be nested in subdirectory), skip macOS resource forks
    shp_files = [p for p in extract_dir.rglob("*.shp") if "__MACOSX" not in str(p)]
    if not shp_files:
        print(f"    Error: no .shp file found in {zip_path.name}")
        return None

    if len(shp_files) > 1:
        print(f"    Warning: multiple .shp files found, using first: {shp_files[0].name}")

    print(f"    Found shapefile: {shp_files[0].name}")
    return shp_files[0]


# ---------------------------------------------------------------------------
# CRS / reprojection
# ---------------------------------------------------------------------------

def build_transformer(shp_path: Path) -> Optional[Transformer]:
    """Build a pyproj Transformer from shapefile's .prj to WGS84.

    Returns None if the shapefile is already in geographic (lon/lat) coords.
    """
    prj_path = shp_path.with_suffix(".prj")
    if not prj_path.exists():
        print("    No .prj file found -- assuming WGS84 geographic coordinates")
        return None

    prj_wkt = prj_path.read_text().strip()
    src_crs = CRS.from_wkt(prj_wkt)

    if src_crs.is_geographic:
        print(f"    CRS: {src_crs.name} (geographic, no reprojection needed)")
        return None

    dst_crs = CRS.from_epsg(4326)  # WGS84
    transformer = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    print(f"    CRS: {src_crs.name} -> WGS84 (reprojecting)")
    return transformer


# ---------------------------------------------------------------------------
# Field detection
# ---------------------------------------------------------------------------

def detect_district_field(sf: shapefile.Reader, candidates: List[str]) -> Optional[str]:
    """Auto-detect the district number field from candidate names or numeric fields."""
    field_names = [f[0] for f in sf.fields[1:]]  # skip DeletionFlag

    # Try candidate names (case-insensitive)
    for candidate in candidates:
        for name in field_names:
            if name.upper() == candidate.upper():
                return name

    # Fallback: find numeric fields that could be district numbers
    print(f"    Warning: no candidate field matched. Available fields: {field_names}")
    for field_info in sf.fields[1:]:
        name, ftype, size, decimal = field_info
        if ftype in ("N", "F", "C") and size <= 10:
            sample_vals = set()
            for rec in sf.iterRecords():
                val = rec[field_names.index(name)]
                if val is not None:
                    try:
                        sample_vals.add(int(val))
                    except (ValueError, TypeError):
                        pass
                if len(sample_vals) >= 5:
                    break
            if sample_vals and min(sample_vals) >= 0 and max(sample_vals) <= 60:
                print(f"    Auto-detected district field: {name} (sample values: {sorted(sample_vals)[:5]})")
                return name

    return None


# ---------------------------------------------------------------------------
# Inspect mode
# ---------------------------------------------------------------------------

def inspect_shapefile(shp_path: Path) -> None:
    """Print shapefile field names, types, and sample values."""
    sf = shapefile.Reader(str(shp_path))
    print(f"\n  Shapefile: {shp_path.name}")
    print(f"  Records: {len(sf)}")
    print(f"  Shape type: {sf.shapeTypeName}")

    # Show CRS
    prj_path = shp_path.with_suffix(".prj")
    if prj_path.exists():
        try:
            src_crs = CRS.from_wkt(prj_path.read_text().strip())
            print(f"  CRS: {src_crs.name} (geographic={src_crs.is_geographic})")
        except Exception as e:
            print(f"  CRS: error reading .prj: {e}")

    # Show bounding box
    print(f"  Bounding box: {sf.bbox}")

    print(f"\n  Fields:")
    field_names = [f[0] for f in sf.fields[1:]]
    for field_info in sf.fields[1:]:
        name, ftype, size, decimal = field_info
        print(f"    {name:20s}  type={ftype}  size={size}  decimal={decimal}")

    print(f"\n  Sample records (first 3):")
    for i, rec in enumerate(sf.iterRecords()):
        if i >= 3:
            break
        row = {field_names[j]: rec[j] for j in range(len(field_names))}
        print(f"    {row}")


# ---------------------------------------------------------------------------
# Parse shapefile
# ---------------------------------------------------------------------------

def parse_shapefile_districts(
    shp_path: Path,
    fips: str,
    expected: int,
    field_candidates: List[str],
) -> Optional[List[dict]]:
    """Parse all districts from a shapefile into DistrictData.json format."""
    sf = shapefile.Reader(str(shp_path))

    # Detect district field
    district_field = detect_district_field(sf, field_candidates)
    if district_field is None:
        print(f"    Error: could not detect district number field")
        return None

    field_names = [f[0] for f in sf.fields[1:]]
    field_idx = field_names.index(district_field)
    print(f"    Using district field: {district_field}")

    # Build CRS transformer (None if already geographic)
    transformer = build_transformer(shp_path)

    districts_by_num: Dict[int, dict] = {}

    for shape_rec in sf.iterShapeRecords():
        rec = shape_rec.record
        shape = shape_rec.shape

        # Get district number
        raw_val = rec[field_idx]
        try:
            district_num = int(raw_val)
        except (ValueError, TypeError):
            print(f"    Warning: skipping record with non-numeric district value: {raw_val}")
            continue

        if district_num < 1:
            continue

        # Parse polygon rings from shape.parts / shape.points
        rings = []
        parts = list(shape.parts) + [len(shape.points)]

        for p in range(len(parts) - 1):
            start = parts[p]
            end = parts[p + 1]
            ring_points = shape.points[start:end]

            if transformer:
                # Reproject from source CRS to WGS84
                # shape.points are [x, y] i.e. [easting, northing] in projected CRS
                # transformer with always_xy=True: (x, y) -> (lon, lat)
                xs = [pt[0] for pt in ring_points]
                ys = [pt[1] for pt in ring_points]
                lons, lats = transformer.transform(xs, ys)
                ring = [[round(lat, 6), round(lon, 6)] for lat, lon in zip(lats, lons)]
            else:
                # Already geographic: points are [lon, lat], swap to [lat, lon]
                ring = [[pt[1], pt[0]] for pt in ring_points]

            if len(ring) >= 3:
                rings.append(ring)

        if not rings:
            print(f"    Warning: district {district_num} has no valid rings")
            continue

        # If this district already exists (multi-part polygon), add rings
        if district_num in districts_by_num:
            districts_by_num[district_num]["polygons"].extend(rings)
        else:
            district_code = f"{district_num:02d}"
            districts_by_num[district_num] = {
                "state": fips,
                "district_code": district_code,
                "name": f"Congressional District {district_num}",
                "geoid": f"{fips}{district_code}",
                "center_latitude": 0.0,
                "center_longitude": 0.0,
                "population": 0,
                "polygons": rings,
            }

    # Compute centroids from bounding box of all rings
    for district_num, dist in districts_by_num.items():
        all_lats = []
        all_lons = []
        for ring in dist["polygons"]:
            for pt in ring:
                all_lats.append(pt[0])
                all_lons.append(pt[1])
        dist["center_latitude"] = round((min(all_lats) + max(all_lats)) / 2, 6)
        dist["center_longitude"] = round((min(all_lons) + max(all_lons)) / 2, 6)

    districts = list(districts_by_num.values())
    print(f"    Parsed {len(districts)} districts from shapefile")

    return districts


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_districts(districts: List[dict], fips: str, expected: int) -> bool:
    """Validate parsed districts: count, numbering, coordinate ranges."""
    state_abbr = FIPS_TO_STATE[fips]
    ok = True

    # Count check
    if len(districts) != expected:
        print(f"    FAIL: expected {expected} districts for {state_abbr}, got {len(districts)}")
        ok = False

    # District number coverage
    nums = sorted(int(d["district_code"]) for d in districts)
    expected_nums = list(range(1, expected + 1))
    if nums != expected_nums:
        missing = set(expected_nums) - set(nums)
        extra = set(nums) - set(expected_nums)
        if missing:
            print(f"    FAIL: missing district numbers: {sorted(missing)}")
        if extra:
            print(f"    FAIL: unexpected district numbers: {sorted(extra)}")
        ok = False

    # Coordinate range check
    bounds = STATE_BOUNDS.get(fips)
    if bounds:
        min_lat, max_lat, min_lon, max_lon = bounds
        for dist in districts:
            for ring in dist["polygons"]:
                for pt in ring:
                    lat, lon = pt[0], pt[1]
                    if not (min_lat - 1 <= lat <= max_lat + 1):
                        print(f"    FAIL: district {dist['district_code']} has out-of-range lat {lat}")
                        ok = False
                        break
                    if not (min_lon - 1 <= lon <= max_lon + 1):
                        print(f"    FAIL: district {dist['district_code']} has out-of-range lon {lon}")
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                break

    # Ring size check
    for dist in districts:
        for i, ring in enumerate(dist["polygons"]):
            if len(ring) < 3:
                print(f"    FAIL: district {dist['district_code']} ring {i} has only {len(ring)} points")
                ok = False

    if ok:
        total_points = sum(len(ring) for d in districts for ring in d["polygons"])
        print(f"    OK: {len(districts)} districts, {total_points:,} total points")

    return ok


# ---------------------------------------------------------------------------
# Merge into DistrictData.json
# ---------------------------------------------------------------------------

def find_district_json() -> Optional[Path]:
    """Find the DistrictData.json file."""
    for candidate in DISTRICT_JSON_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def merge_districts(existing_data: dict, new_districts: List[dict], fips: str) -> dict:
    """Replace all entries for a state (by FIPS) with new districts."""
    kept = [d for d in existing_data["districts"] if d["state"] != fips]
    kept.extend(new_districts)
    kept.sort(key=lambda d: (d["state"], d["district_code"]))

    existing_data["districts"] = kept

    # Update metadata counts
    by_state = {}
    for d in kept:
        by_state[d["state"]] = by_state.get(d["state"], 0) + 1
    existing_data["metadata"]["total_districts"] = len(kept)
    existing_data["metadata"]["expected_count"] = len(kept)
    existing_data["metadata"]["by_state"] = by_state

    return existing_data


def backup_and_write(data: dict, json_path: Path) -> None:
    """Backup original file and write updated data."""
    bak_path = json_path.with_suffix(".json.bak")
    shutil.copy2(json_path, bak_path)
    print(f"  Backup: {bak_path.name}")

    with open(json_path, "w") as f:
        json.dump(data, f, separators=(",", ":"))

    size_mb = json_path.stat().st_size / (1024 * 1024)
    print(f"  Written: {json_path.name} ({size_mb:.1f} MB)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch redistricting shapefiles and merge into DistrictData.json"
    )
    parser.add_argument(
        "--states",
        default=None,
        help="Comma-separated state abbreviations to update (default: all configured)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download cached shapefile zips",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate but don't write DistrictData.json",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Show shapefile field names and exit",
    )
    args = parser.parse_args()

    # Determine which states to process
    if args.states:
        requested = [s.strip().upper() for s in args.states.split(",")]
        state_to_fips = {v: k for k, v in FIPS_TO_STATE.items()}
        fips_list = []
        for abbr in requested:
            if abbr in state_to_fips:
                fips_list.append(state_to_fips[abbr])
            else:
                print(f"Error: unknown state '{abbr}'. Available: {list(FIPS_TO_STATE.values())}")
                return 1
    else:
        fips_list = list(SOURCES.keys())

    print("=" * 60)
    print("REDISTRICTING SHAPEFILE UPDATE")
    print("=" * 60)
    print(f"States: {', '.join(FIPS_TO_STATE[f] for f in fips_list)}")
    print(f"Force download: {args.force}")
    print(f"Dry run: {args.dry_run}")
    print()

    # Phase 1: Download and extract
    all_new_districts: Dict[str, List[dict]] = {}

    for fips in fips_list:
        src = SOURCES[fips]
        state_abbr = FIPS_TO_STATE[fips]
        plan = src["plan_name"]

        print(f"--- {state_abbr} ({plan}) ---")

        zip_dest = RAW_DIR / f"{plan.lower()}.zip"
        extract_dir = RAW_DIR / plan.lower()

        # Download
        if not download_shapefile(src["url"], zip_dest, force=args.force):
            print(f"  FAILED to download {state_abbr} shapefile")
            return 1

        # Extract
        shp_path = extract_shapefile(zip_dest, extract_dir)
        if shp_path is None:
            return 1

        # Inspect mode: show fields and exit
        if args.inspect:
            inspect_shapefile(shp_path)
            continue

        # Parse
        districts = parse_shapefile_districts(
            shp_path, fips, src["expected_districts"], src["district_field_candidates"]
        )
        if districts is None:
            return 1

        # Validate
        if not validate_districts(districts, fips, src["expected_districts"]):
            print(f"  Validation FAILED for {state_abbr}")
            return 1

        all_new_districts[fips] = districts
        print()

    if args.inspect:
        return 0

    if not all_new_districts:
        print("No districts parsed.")
        return 1

    # Phase 2: Merge into DistrictData.json
    if args.dry_run:
        print("Dry run -- skipping DistrictData.json write")
        for fips, dists in all_new_districts.items():
            state_abbr = FIPS_TO_STATE[fips]
            total_pts = sum(len(r) for d in dists for r in d["polygons"])
            print(f"  {state_abbr}: {len(dists)} districts, {total_pts:,} points")
        return 0

    json_path = find_district_json()
    if json_path is None:
        print("Error: DistrictData.json not found")
        print(f"  Searched: {[str(p) for p in DISTRICT_JSON_CANDIDATES]}")
        return 1

    print(f"Loading: {json_path}")
    with open(json_path, "r") as f:
        data = json.load(f)

    original_count = len(data["districts"])
    print(f"  Existing districts: {original_count}")

    for fips, new_dists in all_new_districts.items():
        state_abbr = FIPS_TO_STATE[fips]
        old_count = sum(1 for d in data["districts"] if d["state"] == fips)
        data = merge_districts(data, new_dists, fips)
        print(f"  Merged {state_abbr}: {old_count} old -> {len(new_dists)} new")

    final_count = len(data["districts"])
    print(f"  Final district count: {final_count}")

    if final_count != 444:
        print(f"  WARNING: expected 444 total districts, got {final_count}")

    # Update metadata
    data["metadata"]["generation_date"] = datetime.now().isoformat()
    data["metadata"]["data_source"] = "Census Bureau TigerWeb API + state redistricting shapefiles"
    if "redistricting_updates" not in data["metadata"]:
        data["metadata"]["redistricting_updates"] = []
    for fips in all_new_districts:
        data["metadata"]["redistricting_updates"].append({
            "state": FIPS_TO_STATE[fips],
            "plan": SOURCES[fips]["plan_name"],
            "updated": datetime.now().strftime("%Y-%m-%d"),
            "districts": SOURCES[fips]["expected_districts"],
        })

    # Write
    print()
    backup_and_write(data, json_path)

    print()
    print("Next step: reload boundaries into database:")
    print("  python3 scripts/import_district_boundaries.py")

    return 0


if __name__ == "__main__":
    sys.exit(main())
