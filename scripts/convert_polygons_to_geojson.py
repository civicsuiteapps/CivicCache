#!/usr/bin/env python3
"""
Convert congressional district polygon data to proper GeoJSON format.

This script:
1. Reads existing polygon data from congressional_districts table
2. Converts polygon arrays to proper GeoJSON Feature format
3. Calculates bounding boxes for efficient spatial queries
4. Updates the database schema to add geojson and bbox columns
5. Populates the new columns with converted data
"""

import json
import sqlite3
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

# Database location
DB_PATH = Path("data/civic_cache.db")


def create_connection() -> sqlite3.Connection:
    """Create and return a database connection."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found at {DB_PATH}")

    conn = sqlite3.Connection(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def calculate_bbox(geometry: Dict[str, Any]) -> List[float]:
    """
    Calculate bounding box [west, south, east, north] from GeoJSON geometry.

    Args:
        geometry: GeoJSON Geometry object (Polygon or MultiPolygon)

    Returns:
        [min_lon, min_lat, max_lon, max_lat]
    """
    all_lons = []
    all_lats = []

    coordinates = geometry.get("coordinates", [])
    geom_type = geometry.get("type")

    if geom_type == "Polygon":
        # Polygon: coordinates is array of rings
        for ring in coordinates:
            for point in ring:
                lon, lat = point[0], point[1]
                all_lons.append(lon)
                all_lats.append(lat)
    elif geom_type == "MultiPolygon":
        # MultiPolygon: coordinates is array of polygons, each with rings
        for polygon in coordinates:
            for ring in polygon:
                for point in ring:
                    lon, lat = point[0], point[1]
                    all_lons.append(lon)
                    all_lats.append(lat)

    if not all_lons or not all_lats:
        return [0, 0, 0, 0]

    return [min(all_lons), min(all_lats), max(all_lons), max(all_lats)]


def convert_polygons_to_geojson_geometry(polygons_json: str) -> Dict[str, Any]:
    """
    Convert polygon array to GeoJSON Geometry object.

    Source format: Array of polygons, where each polygon is an array of rings
                   [  // array of polygons
                     [ // first polygon
                       [[lat, lon], [lat, lon], ...],  // exterior ring
                       [[lat, lon], ...]  // optional holes
                     ],
                     [ // second polygon (for islands)
                       [[lat, lon], ...]
                     ]
                   ]

    GeoJSON format:
    - Single polygon: {"type": "Polygon", "coordinates": [[[lon, lat], ...]]}
    - Multi polygon: {"type": "MultiPolygon", "coordinates": [[[[lon, lat], ...]]]}

    Note: GeoJSON uses [longitude, latitude] order, opposite of [lat, lon]

    Args:
        polygons_json: JSON string of polygon arrays

    Returns:
        GeoJSON Geometry object
    """
    try:
        polygons = json.loads(polygons_json)

        # Source data has inconsistent structure:
        # - Most districts: [[point, point, ...]] - flat array of points
        # - Some districts: [[[point, ...]]] - array with rings
        # Detect which format we have

        if not polygons or len(polygons) == 0:
            return {"type": "Polygon", "coordinates": []}

        # Check structure: polygons[0][0] is either a point [lat, lon] or a ring [[lat, lon], ...]
        first_elem = polygons[0]
        if isinstance(first_elem, list) and len(first_elem) > 0:
            first_sub_elem = first_elem[0]

            # If first_sub_elem is [lat, lon] with numbers, we have flat format
            if isinstance(first_sub_elem, list) and len(first_sub_elem) == 2 and isinstance(first_sub_elem[0], (int, float)):
                # Flat format: [ [[lat, lon], [lat, lon], ...] ]
                # polygons[0] is the ring of points
                geojson_ring = []
                for point in first_elem:  # Use first_elem which is the array of points
                    lat, lon = point[0], point[1]
                    geojson_ring.append([lon, lat])

                return {
                    "type": "Polygon",
                    "coordinates": [geojson_ring]  # Wrap in array for GeoJSON
                }
            elif isinstance(first_sub_elem, list):
                # Nested format: check if single or multi polygon
                if len(polygons) == 1:
                    # Single polygon with rings: [[[point, ...], [hole points, ...]]]
                    polygon = polygons[0]
                    geojson_rings = []
                    for ring in polygon:
                        geojson_ring = []
                        for point in ring:
                            lat, lon = point[0], point[1]
                            geojson_ring.append([lon, lat])
                        geojson_rings.append(geojson_ring)

                    return {
                        "type": "Polygon",
                        "coordinates": geojson_rings
                    }
                else:
                    # Multiple polygons (islands): [[[point, ...]], [[point, ...]]]
                    geojson_polygons = []
                    for polygon in polygons:
                        geojson_rings = []
                        for ring in polygon:
                            geojson_ring = []
                            for point in ring:
                                lat, lon = point[0], point[1]
                                geojson_ring.append([lon, lat])
                            geojson_rings.append(geojson_ring)
                        geojson_polygons.append(geojson_rings)

                    return {
                        "type": "MultiPolygon",
                        "coordinates": geojson_polygons
                    }

        # Fallback
        return {"type": "Polygon", "coordinates": []}
    except json.JSONDecodeError as e:
        print(f"Error parsing polygon JSON: {e}")
        return {"type": "Polygon", "coordinates": []}
    except Exception as e:
        print(f"Error converting polygons: {e}")
        return {"type": "Polygon", "coordinates": []}


def create_geojson_feature(
    district_id: str,
    state_code: str,
    district_number: int,
    name: str,
    geometry: Dict[str, Any],
    bbox: List[float]
) -> Dict[str, Any]:
    """
    Create a complete GeoJSON Feature object.

    Args:
        district_id: Unique identifier (e.g., "TX-32")
        state_code: State code (e.g., "TX")
        district_number: District number (e.g., 32)
        name: District name (e.g., "Texas 32nd")
        geometry: GeoJSON Geometry object
        bbox: Bounding box [west, south, east, north]

    Returns:
        GeoJSON Feature object
    """
    return {
        "type": "Feature",
        "id": district_id,
        "properties": {
            "state": state_code,
            "district": district_number,
            "name": name
        },
        "geometry": geometry,
        "bbox": bbox
    }


def update_schema(conn: sqlite3.Connection) -> None:
    """Add geojson and bbox columns to congressional_districts table."""
    print("\nUpdating database schema...")

    try:
        # Add geojson column
        conn.execute("""
            ALTER TABLE congressional_districts
            ADD COLUMN geojson TEXT
        """)
        print("✓ Added geojson column")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("  Column 'geojson' already exists")
        else:
            raise

    try:
        # Add bbox column
        conn.execute("""
            ALTER TABLE congressional_districts
            ADD COLUMN bbox TEXT
        """)
        print("✓ Added bbox column")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("  Column 'bbox' already exists")
        else:
            raise

    conn.commit()


def convert_all_districts(conn: sqlite3.Connection) -> None:
    """Convert all district polygon data to GeoJSON format."""
    print("\nConverting polygon data to GeoJSON...")

    # Fetch all districts
    cursor = conn.execute("""
        SELECT id, state_code, district_number, name, geoid, polygons
        FROM congressional_districts
        ORDER BY state_code, district_number
    """)

    districts = cursor.fetchall()
    total = len(districts)
    print(f"Found {total} districts to convert")

    converted = 0
    errors = 0

    for row in districts:
        district_db_id, state_code, district_number, name, geoid, polygons_json = row

        try:
            # Create district ID (e.g., "TX-32")
            district_id = f"{state_code}-{district_number:02d}" if district_number > 0 else f"{state_code}-00"

            # Convert polygons to GeoJSON geometry
            geometry = convert_polygons_to_geojson_geometry(polygons_json)

            # Calculate bounding box
            if geometry and geometry.get("coordinates"):
                bbox = calculate_bbox(geometry)
            else:
                bbox = [0, 0, 0, 0]
                print(f"  Warning: Empty geometry for {district_id}")

            # Create full GeoJSON feature
            feature = create_geojson_feature(
                district_id=district_id,
                state_code=state_code,
                district_number=district_number,
                name=name,
                geometry=geometry,
                bbox=bbox
            )

            # Store as JSON strings
            geojson_str = json.dumps(feature)
            bbox_str = json.dumps(bbox)

            # Update database
            conn.execute("""
                UPDATE congressional_districts
                SET geojson = ?, bbox = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (geojson_str, bbox_str, district_db_id))

            converted += 1

            if converted % 50 == 0:
                print(f"  Converted {converted}/{total} districts...")
                conn.commit()

        except Exception as e:
            print(f"  Error converting {geoid}: {e}")
            errors += 1

    conn.commit()
    print(f"\n✓ Successfully converted {converted} districts")
    if errors > 0:
        print(f"  {errors} errors encountered")


def create_indexes(conn: sqlite3.Connection) -> None:
    """Create indexes for efficient spatial queries."""
    print("\nCreating indexes for GeoJSON queries...")

    try:
        # Index on bbox for spatial queries
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_districts_bbox
            ON congressional_districts(bbox)
        """)
        print("✓ Created bbox index")
    except sqlite3.OperationalError as e:
        print(f"  Index may already exist: {e}")

    conn.commit()


def verify_conversion(conn: sqlite3.Connection) -> None:
    """Verify the conversion was successful."""
    print("\n" + "="*60)
    print("CONVERSION VERIFICATION")
    print("="*60)

    # Count districts with geojson
    result = conn.execute("""
        SELECT COUNT(*) FROM congressional_districts WHERE geojson IS NOT NULL
    """).fetchone()
    geojson_count = result[0] if result else 0

    # Count total districts
    result = conn.execute("SELECT COUNT(*) FROM congressional_districts").fetchone()
    total_count = result[0] if result else 0

    print(f"Total districts: {total_count}")
    print(f"Districts with GeoJSON: {geojson_count}")
    print(f"Conversion rate: {(geojson_count/total_count*100):.1f}%" if total_count > 0 else "N/A")

    # Show sample GeoJSON features
    print("\nSample GeoJSON features:")
    samples = conn.execute("""
        SELECT state_code, district_number, name, geojson, bbox
        FROM congressional_districts
        WHERE geojson IS NOT NULL
        ORDER BY state_code, district_number
        LIMIT 3
    """).fetchall()

    for state, district, name, geojson_str, bbox_str in samples:
        print(f"\n{state}-{district:02d}: {name}")

        # Parse and pretty-print a truncated version
        try:
            feature = json.loads(geojson_str)
            bbox = json.loads(bbox_str)

            geom = feature.get("geometry", {})
            geom_type = geom.get('type')
            coords = geom.get("coordinates", [])

            # Calculate coordinate count based on geometry type
            if geom_type == "Polygon" and coords:
                coord_count = len(coords[0])  # First ring
            elif geom_type == "MultiPolygon" and coords:
                coord_count = len(coords)  # Number of polygons
            else:
                coord_count = 0

            print(f"  Feature ID: {feature.get('id')}")
            print(f"  Geometry type: {geom_type}")
            if geom_type == "Polygon":
                print(f"  Coordinate points in first ring: {coord_count}")
            else:
                print(f"  Number of polygons: {coord_count}")
            print(f"  Bounding box: {bbox}")
            print(f"  GeoJSON preview: {json.dumps(feature, indent=2)[:300]}...")
        except json.JSONDecodeError:
            print("  Error: Invalid JSON")

    # Check for specific example (TX-32)
    print("\n" + "-"*60)
    print("Checking for TX-32 example:")
    tx32 = conn.execute("""
        SELECT geojson FROM congressional_districts
        WHERE state_code = 'TX' AND district_number = 32
    """).fetchone()

    if tx32:
        print("✓ TX-32 found")
        try:
            feature = json.loads(tx32[0])
            print(f"  Feature: {json.dumps(feature, indent=2)[:500]}...")
        except:
            print("  Error parsing TX-32 GeoJSON")
    else:
        print("✗ TX-32 not found in database")

    print("\n✓ Verification complete")


def main():
    """Main execution function."""
    print("="*60)
    print("CONGRESSIONAL DISTRICT POLYGON → GEOJSON CONVERSION")
    print("="*60)

    try:
        # Connect to database
        conn = create_connection()

        # Update schema
        update_schema(conn)

        # Convert all districts
        convert_all_districts(conn)

        # Create indexes
        create_indexes(conn)

        # Verify conversion
        verify_conversion(conn)

        conn.close()

        print("\n" + "="*60)
        print("✓ CONVERSION COMPLETE")
        print("="*60)
        print("\nThe congressional_districts table now has:")
        print("  - 'geojson' column: Full GeoJSON Feature objects")
        print("  - 'bbox' column: Bounding boxes for spatial queries")
        print("\nYou can now use these for efficient geospatial queries!")

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
