#!/usr/bin/env python3
"""
Verify GeoJSON migration accuracy by comparing database data with source API data.

This script:
1. Reads source DistrictData.json file
2. Reads converted data from civic_cache.db
3. Compares polygon coordinates, counts, and metadata
4. Verifies bounding box calculations
5. Generates detailed comparison report
"""

import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Tuple, Any
import sys

# File paths
DB_PATH = Path("data/civic_cache.db")
SOURCE_JSON_PATH = Path("data/raw/congress/DistrictData.json")

# FIPS to state code mapping
FIPS_TO_STATE = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA",
    "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
    "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN",
    "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
    "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS",
    "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY", "60": "AS", "66": "GU", "69": "MP", "72": "PR",
    "78": "VI"
}


class ComparisonResults:
    """Store comparison results."""

    def __init__(self):
        self.total_source_districts = 0
        self.total_db_districts = 0
        self.matched_districts = 0
        self.mismatched_districts = []
        self.missing_from_db = []
        self.coordinate_mismatches = []
        self.bbox_errors = []
        self.metadata_mismatches = []
        self.perfect_matches = []


def create_connection() -> sqlite3.Connection:
    """Create and return a database connection."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found at {DB_PATH}")

    conn = sqlite3.Connection(DB_PATH)
    return conn


def load_source_data() -> Dict[str, Any]:
    """Load source DistrictData.json file."""
    print(f"Loading source data from: {SOURCE_JSON_PATH}")

    if not SOURCE_JSON_PATH.exists():
        raise FileNotFoundError(f"Source file not found at {SOURCE_JSON_PATH}")

    with open(SOURCE_JSON_PATH, 'r') as f:
        data = json.load(f)

    metadata = data.get('metadata', {})
    print(f"\nSource File Metadata:")
    print(f"  Data source: {metadata.get('data_source', 'unknown')}")
    print(f"  Generated: {metadata.get('generation_date', 'unknown')}")
    print(f"  Expected count: {metadata.get('expected_count', 'unknown')}")
    print(f"  Actual districts: {len(data.get('districts', []))}")

    return data


def compare_coordinates(
    source_coords: List[List[float]],
    db_coords: List[List[float]],
    tolerance: float = 0.000001
) -> Tuple[bool, str]:
    """
    Compare coordinate arrays with tolerance for floating point differences.

    Args:
        source_coords: Source polygon ring (array of [lat, lon] points)
        db_coords: Database polygon ring (array of [lon, lat] points in GeoJSON format)
        tolerance: Floating point comparison tolerance

    Returns:
        (is_match, error_message)
    """
    if len(source_coords) != len(db_coords):
        return False, f"Point count mismatch: source={len(source_coords)}, db={len(db_coords)}"

    # Check a sample of points (first 5, last 5, and some in middle)
    sample_indices = list(range(min(5, len(source_coords))))
    if len(source_coords) > 10:
        sample_indices.extend(range(len(source_coords) - 5, len(source_coords)))
    if len(source_coords) > 20:
        sample_indices.extend([len(source_coords) // 2])

    for i in sample_indices:
        source_point = source_coords[i]  # [lat, lon]
        db_point = db_coords[i]  # [lon, lat] in GeoJSON

        # Source is [lat, lon], DB should be [lon, lat]
        source_lat, source_lon = source_point[0], source_point[1]
        db_lon, db_lat = db_point[0], db_point[1]

        # Compare with tolerance
        if abs(source_lat - db_lat) > tolerance or abs(source_lon - db_lon) > tolerance:
            return False, (
                f"Coordinate mismatch at point {i}: "
                f"source=[{source_lat}, {source_lon}], "
                f"db=[{db_lat}, {db_lon}]"
            )

    return True, ""


def verify_bbox(coords: List[List[List[float]]], bbox: List[float]) -> Tuple[bool, str]:
    """
    Verify bounding box calculation is correct.

    Args:
        coords: GeoJSON polygon coordinates (array of rings)
        bbox: Bounding box [west, south, east, north]

    Returns:
        (is_correct, error_message)
    """
    # Calculate expected bbox from coordinates
    all_lons = []
    all_lats = []

    for ring in coords:
        for point in ring:
            lon, lat = point[0], point[1]
            all_lons.append(lon)
            all_lats.append(lat)

    if not all_lons or not all_lats:
        return False, "Empty coordinates"

    expected_bbox = [min(all_lons), min(all_lats), max(all_lons), max(all_lats)]

    # Compare with tolerance
    tolerance = 0.000001
    for i in range(4):
        if abs(expected_bbox[i] - bbox[i]) > tolerance:
            return False, (
                f"BBox mismatch: expected={expected_bbox}, actual={bbox}"
            )

    return True, ""


def compare_district(
    source_district: Dict[str, Any],
    db_data: Tuple[Any, ...],
    results: ComparisonResults
) -> None:
    """
    Compare a single district from source and database.

    Args:
        source_district: District data from source JSON
        db_data: District data from database (geoid, name, polygons, geojson, bbox)
        results: ComparisonResults object to update
    """
    geoid = source_district.get('geoid')
    state_fips = source_district.get('state')
    state_code = FIPS_TO_STATE.get(state_fips, state_fips)
    district_code = source_district.get('district_code')

    db_geoid, db_name, db_polygons_json, db_geojson_json, db_bbox_json = db_data

    # Parse JSON fields
    try:
        source_polygons = source_district.get('polygons', [])
        db_polygons = json.loads(db_polygons_json)
        db_geojson = json.loads(db_geojson_json)
        db_bbox = json.loads(db_bbox_json)
    except json.JSONDecodeError as e:
        results.coordinate_mismatches.append({
            'geoid': geoid,
            'error': f'JSON decode error: {e}'
        })
        return

    errors = []

    # 1. Compare metadata
    if db_geoid != geoid:
        errors.append(f"GeoID mismatch: source={geoid}, db={db_geoid}")

    # 2. Compare polygon count
    if len(source_polygons) != len(db_geojson['geometry']['coordinates']):
        errors.append(
            f"Polygon count mismatch: source={len(source_polygons)}, "
            f"db={len(db_geojson['geometry']['coordinates'])}"
        )

    # 3. Compare coordinate data for each ring
    coordinate_match = True
    for ring_idx, (source_ring, db_ring) in enumerate(
        zip(source_polygons, db_geojson['geometry']['coordinates'])
    ):
        is_match, error_msg = compare_coordinates(source_ring, db_ring)
        if not is_match:
            errors.append(f"Ring {ring_idx}: {error_msg}")
            coordinate_match = False
            break

    # 4. Verify bounding box
    bbox_correct, bbox_error = verify_bbox(
        db_geojson['geometry']['coordinates'],
        db_bbox
    )
    if not bbox_correct:
        errors.append(f"BBox error: {bbox_error}")
        results.bbox_errors.append({
            'geoid': geoid,
            'error': bbox_error
        })

    # 5. Verify GeoJSON structure
    if db_geojson.get('type') != 'Feature':
        errors.append(f"Invalid GeoJSON type: {db_geojson.get('type')}")

    if db_geojson.get('geometry', {}).get('type') != 'Polygon':
        errors.append(f"Invalid geometry type: {db_geojson.get('geometry', {}).get('type')}")

    # Record results
    district_id = f"{state_code}-{district_code}"
    if errors:
        results.mismatched_districts.append({
            'id': district_id,
            'geoid': geoid,
            'errors': errors
        })
    else:
        results.perfect_matches.append(district_id)
        results.matched_districts += 1


def main():
    """Main execution function."""
    print("="*70)
    print("GEOJSON MIGRATION VERIFICATION")
    print("="*70)

    results = ComparisonResults()

    try:
        # Load source data
        source_data = load_source_data()
        source_districts = source_data.get('districts', [])
        results.total_source_districts = len(source_districts)

        # Connect to database
        print(f"\nConnecting to database: {DB_PATH}")
        conn = create_connection()

        # Get database district count
        db_count = conn.execute(
            "SELECT COUNT(*) FROM congressional_districts"
        ).fetchone()[0]
        results.total_db_districts = db_count

        print(f"\n{'='*70}")
        print("COMPARISON SUMMARY")
        print(f"{'='*70}")
        print(f"Source districts: {results.total_source_districts}")
        print(f"Database districts: {results.total_db_districts}")

        # Create lookup map for source districts by geoid
        source_by_geoid = {d['geoid']: d for d in source_districts}

        # Compare each district
        print(f"\nComparing {results.total_source_districts} districts...")
        progress_interval = 50

        for idx, source_district in enumerate(source_districts):
            geoid = source_district.get('geoid')

            # Fetch from database
            db_result = conn.execute("""
                SELECT geoid, name, polygons, geojson, bbox
                FROM congressional_districts
                WHERE geoid = ?
            """, (geoid,)).fetchone()

            if not db_result:
                results.missing_from_db.append(geoid)
                continue

            # Compare district data
            compare_district(source_district, db_result, results)

            if (idx + 1) % progress_interval == 0:
                print(f"  Processed {idx + 1}/{results.total_source_districts}...")

        conn.close()

        # Print results
        print(f"\n{'='*70}")
        print("VERIFICATION RESULTS")
        print(f"{'='*70}")

        print(f"\nTotal districts compared: {results.total_source_districts}")
        print(f"Perfect matches: {results.matched_districts} "
              f"({results.matched_districts/results.total_source_districts*100:.1f}%)")
        print(f"Mismatches: {len(results.mismatched_districts)}")
        print(f"Missing from DB: {len(results.missing_from_db)}")

        # Show mismatches
        if results.mismatched_districts:
            print(f"\n{'-'*70}")
            print("MISMATCHED DISTRICTS:")
            print(f"{'-'*70}")
            for mismatch in results.mismatched_districts[:10]:  # Show first 10
                print(f"\n{mismatch['id']} (GeoID: {mismatch['geoid']}):")
                for error in mismatch['errors']:
                    print(f"  - {error}")

            if len(results.mismatched_districts) > 10:
                print(f"\n... and {len(results.mismatched_districts) - 10} more mismatches")

        # Show missing districts
        if results.missing_from_db:
            print(f"\n{'-'*70}")
            print("MISSING FROM DATABASE:")
            print(f"{'-'*70}")
            for geoid in results.missing_from_db[:20]:  # Show first 20
                print(f"  - {geoid}")

            if len(results.missing_from_db) > 20:
                print(f"... and {len(results.missing_from_db) - 20} more missing")

        # Show sample perfect matches
        if results.perfect_matches:
            print(f"\n{'-'*70}")
            print("SAMPLE PERFECT MATCHES:")
            print(f"{'-'*70}")
            for district_id in results.perfect_matches[:10]:
                print(f"  ✓ {district_id}")

        # Final verdict
        print(f"\n{'='*70}")
        if results.matched_districts == results.total_source_districts:
            print("✓ MIGRATION VERIFIED: All districts match perfectly!")
            print("="*70)
            return 0
        else:
            print("✗ MIGRATION ISSUES DETECTED")
            print(f"  {len(results.mismatched_districts)} districts have mismatches")
            print(f"  {len(results.missing_from_db)} districts missing from database")
            print("="*70)
            return 1

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
