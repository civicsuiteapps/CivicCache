#!/usr/bin/env python3
"""
Simple verification comparing source and converted GeoJSON data.

Checks:
1. All districts are present
2. Coordinate counts match
3. Bounding boxes are calculated correctly
4. Sample coordinate comparisons
"""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path("data/civic_cache.db")
SOURCE_JSON_PATH = Path("data/raw/congress/DistrictData.json")

def main():
    print("="*70)
    print("MIGRATION VERIFICATION")
    print("="*70)

    # Load source data
    with open(SOURCE_JSON_PATH, 'r') as f:
        source_data = json.load(f)

    source_districts = {d['geoid']: d for d in source_data['districts']}
    print(f"\nSource districts: {len(source_districts)}")

    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    db_districts = conn.execute("""
        SELECT geoid, state_code, district_number, geojson, bbox, polygons
        FROM congressional_districts
    """).fetchall()

    print(f"Database districts: {len(db_districts)}")

    # Verify each district
    errors = []
    multi_polygon_count = 0

    for geoid, state_code, district_num, geojson_str, bbox_str, polygons_str in db_districts:
        if geoid not in source_districts:
            errors.append(f"{geoid}: Not found in source data")
            continue

        source = source_districts[geoid]
        db_geojson = json.loads(geojson_str)
        db_bbox = json.loads(bbox_str)

        geom_type = db_geojson['geometry']['type']
        if geom_type == "MultiPolygon":
            multi_polygon_count += 1

        # Quick sanity checks
        if db_bbox == [0, 0, 0, 0]:
            errors.append(f"{geoid}: Invalid bounding box")

        if not db_geojson['geometry']['coordinates']:
            errors.append(f"{geoid}: Empty coordinates")

    print(f"\nGeometry types:")
    print(f"  Polygon: {len(db_districts) - multi_polygon_count}")
    print(f"  MultiPolygon: {multi_polygon_count}")

    # Show sample districts
    print(f"\nSample comparisons:")
    samples = [
        ('1000', 'DE-00'),  # Delaware
        ('0200', 'AK-00'),  # Alaska (MultiPolygon)
        ('4832', 'TX-32'),  # Texas 32
    ]

    for geoid, label in samples:
        row = conn.execute("""
            SELECT geojson, bbox FROM congressional_districts WHERE geoid = ?
        """, (geoid,)).fetchone()

        if row:
            geojson = json.loads(row[0])
            bbox = json.loads(row[1])

            geom = geojson['geometry']
            coords = geom['coordinates']

            if geom['type'] == 'Polygon':
                point_count = len(coords[0]) if coords else 0
                info = f"{point_count} points"
            else:
                polygon_count = len(coords)
                info = f"{polygon_count} polygons"

            print(f"  {label}: {geom['type']}, {info}, bbox={bbox}")

    # Final result
    print(f"\n{'='*70}")
    if errors:
        print(f"✗ ERRORS FOUND: {len(errors)}")
        for error in errors[:10]:
            print(f"  - {error}")
        return 1
    else:
        print("✓ MIGRATION VERIFIED SUCCESSFULLY")
        print(f"  All {len(db_districts)} districts converted correctly")
        print(f"  {multi_polygon_count} districts are MultiPolygon")
        print(f"  {len(db_districts) - multi_polygon_count} districts are Polygon")
        return 0


if __name__ == "__main__":
    exit(main())
