#!/usr/bin/env python3
"""
Extract Congressional data from CivicScope resources.

This script copies and processes:
- Congressional member data (119th Congress)
- District boundary data (GeoJSON)
- State reference data
"""

import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Any

# Source directory (CivicScope resources)
SOURCE_DIR = Path("~/GIT/CivicScope.iOS/ReceiptsPleaseApp/Resources").expanduser()

# Target directories
RAW_DATA_DIR = Path("data/raw/congress")
PROCESSED_DATA_DIR = Path("data/processed/congress")


def setup_directories() -> None:
    """Create necessary directories if they don't exist."""
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)


def extract_congress_members() -> None:
    """Extract and copy Congress member data."""
    print("Extracting Congressional member data...")

    source_files = [
        "119th-congress-members.json",
        "119th-congress-member-lookup.json"
    ]

    for filename in source_files:
        source = SOURCE_DIR / filename
        destination = RAW_DATA_DIR / filename

        if source.exists():
            shutil.copy2(source, destination)
            print(f"  ✓ Copied {filename}")

            # Load and analyze
            with open(source, 'r') as f:
                data = json.load(f)

            if filename == "119th-congress-members.json":
                analyze_members_data(data)
        else:
            print(f"  ✗ Not found: {filename}")


def analyze_members_data(data: Dict[str, Any]) -> None:
    """Analyze Congress members data structure."""
    print("\n  Congress Members Analysis:")

    if 'results' in data:
        members = data['results']
        print(f"    Total members: {len(members)}")

        # Count by chamber
        chambers = {}
        parties = {}
        states = set()

        for member in members:
            chamber = member.get('terms', [{}])[-1].get('chamber', 'Unknown')
            party = member.get('current_party', 'Unknown')
            state = member.get('terms', [{}])[-1].get('state', 'Unknown')

            chambers[chamber] = chambers.get(chamber, 0) + 1
            parties[party] = parties.get(party, 0) + 1
            states.add(state)

        print(f"    By Chamber: {chambers}")
        print(f"    By Party: {parties}")
        print(f"    States represented: {len(states)}")


def extract_district_data() -> None:
    """Extract congressional district boundary data.

    Note: This is a large file (276 MB) with GeoJSON data.
    We'll copy it but may want to process it differently for the database.
    """
    print("\nExtracting Congressional district data...")

    source = SOURCE_DIR / "DistrictData.json"
    destination = RAW_DATA_DIR / "DistrictData.json"

    if source.exists():
        print(f"  Source file size: {source.stat().st_size / (1024*1024):.1f} MB")
        print("  Copying (this may take a moment)...")
        shutil.copy2(source, destination)
        print("  ✓ Copied DistrictData.json")

        # Analyze structure by reading a small portion
        print("  Analyzing file structure...")
        with open(destination, 'r') as f:
            # Read just enough to understand the structure
            sample = f.read(1000)
            print(f"  First 200 chars: {sample[:200]}...")

            # Try to load full structure (may be slow for large files)
            f.seek(0)
            try:
                data = json.load(f)
                if isinstance(data, dict):
                    print(f"  Top-level keys: {list(data.keys())[:5]}")
                    if data:
                        first_key = list(data.keys())[0]
                        print(f"  Sample entry type: {type(data[first_key])}")
                elif isinstance(data, list):
                    print(f"  Array with {len(data)} elements")
                    if data:
                        print(f"  First element keys: {list(data[0].keys()) if isinstance(data[0], dict) else 'N/A'}")
            except (json.JSONDecodeError, MemoryError) as e:
                print(f"  Could not fully parse (file too large or complex): {e}")
    else:
        print("  ✗ Not found: DistrictData.json")


def extract_states_data() -> None:
    """Extract state reference data."""
    print("\nExtracting state reference data...")

    source = SOURCE_DIR / "states_seed.json"
    destination = RAW_DATA_DIR / "states_seed.json"

    if source.exists():
        shutil.copy2(source, destination)
        print("  ✓ Copied states_seed.json")

        # Load and analyze
        with open(source, 'r') as f:
            data = json.load(f)

        if isinstance(data, list):
            print(f"  Total states/territories: {len(data)}")
            # Sample first entry
            if data:
                print(f"  Sample entry keys: {list(data[0].keys())}")
        elif isinstance(data, dict):
            print(f"  Total states/territories: {len(data)}")
            print(f"  Data keys: {list(data.keys())[:10]}")
            # Sample first entry if values are dict-like
            first_key = list(data.keys())[0] if data else None
            if first_key and isinstance(data[first_key], dict):
                print(f"  Sample entry keys: {list(data[first_key].keys())}")
    else:
        print("  ✗ Not found: states_seed.json")


def extract_corrections_data() -> None:
    """Extract FIPS corrections data."""
    print("\nExtracting FIPS corrections data...")

    source = SOURCE_DIR / "district-fips-corrections.json"
    destination = RAW_DATA_DIR / "district-fips-corrections.json"

    if source.exists():
        shutil.copy2(source, destination)
        print("  ✓ Copied district-fips-corrections.json")
    else:
        print("  ✗ Not found: district-fips-corrections.json")


def create_manifest() -> None:
    """Create a manifest of extracted files."""
    print("\nCreating manifest...")

    manifest = {
        "extraction_date": None,  # Will be set when run
        "source_directory": str(SOURCE_DIR),
        "files": {}
    }

    for file_path in RAW_DATA_DIR.glob("*.json"):
        stat = file_path.stat()
        manifest["files"][file_path.name] = {
            "size_bytes": stat.st_size,
            "size_mb": round(stat.st_size / (1024*1024), 2)
        }

    manifest_path = RAW_DATA_DIR / "manifest.json"
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)

    print(f"  ✓ Created manifest.json")
    print(f"  Total files extracted: {len(manifest['files'])}")


def main():
    """Main extraction process."""
    print("=" * 60)
    print("CivicCache - Congressional Data Extraction")
    print("=" * 60)

    # Check source directory exists
    if not SOURCE_DIR.exists():
        print(f"\n✗ ERROR: Source directory not found: {SOURCE_DIR}")
        print("  Please update SOURCE_DIR in this script or set CIVICSCOPE_DATA_PATH")
        return 1

    print(f"\nSource: {SOURCE_DIR}")
    print(f"Target: {RAW_DATA_DIR}\n")

    # Setup
    setup_directories()

    # Extract data
    extract_congress_members()
    extract_district_data()
    extract_states_data()
    extract_corrections_data()

    # Create manifest
    create_manifest()

    print("\n" + "=" * 60)
    print("Extraction complete!")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    exit(main())
