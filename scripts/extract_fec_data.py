#!/usr/bin/env python3
"""
Extract FEC (Federal Election Commission) data from CivicScope resources.

This script processes:
- All-Candidates data (1979-2026)
- Candidate Master files
- Committee Master files
- PAC summaries
- House/Senate campaign data
- Contributions and expenditures
"""

import json
import os
import shutil
import zipfile
from pathlib import Path
from typing import Dict, List, Any

# Source directory (CivicScope resources)
SOURCE_DIR = Path("~/GIT/CivicScope.iOS/ReceiptsPleaseApp/Resources/FEC-Bulk-Data").expanduser()

# Target directories
RAW_DATA_DIR = Path("data/raw/fec")
PROCESSED_DATA_DIR = Path("data/processed/fec")

# FEC data categories to extract
FEC_CATEGORIES = [
    "All-Candidates",
    "Candidate-Master",
    "Committee-Master",
    "Candidate-Committee-Linkages",
    "PAC-Summary",
    "House-Senate-Current-Campaigns",
    "Contributions-from-Committees",
    "Operating-Expenditures",
    "Other-Transactions"
]


def setup_directories() -> None:
    """Create necessary directories if they don't exist."""
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Create subdirectories for each FEC category
    for category in FEC_CATEGORIES:
        (RAW_DATA_DIR / category).mkdir(exist_ok=True)


def get_directory_size(path: Path) -> int:
    """Calculate total size of a directory in bytes."""
    total = 0
    try:
        for entry in path.rglob('*'):
            if entry.is_file():
                total += entry.stat().st_size
    except PermissionError:
        pass
    return total


def extract_category(category: str) -> Dict[str, Any]:
    """Extract files for a specific FEC category."""
    source = SOURCE_DIR / category
    destination = RAW_DATA_DIR / category

    stats = {
        "category": category,
        "exists": False,
        "files_copied": 0,
        "size_mb": 0,
        "file_types": {}
    }

    if not source.exists():
        return stats

    stats["exists"] = True

    print(f"\nExtracting {category}...")

    # Copy all files
    for file_path in source.rglob('*'):
        if file_path.is_file():
            relative_path = file_path.relative_to(source)
            dest_path = destination / relative_path

            # Create parent directories if needed
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            # Copy file
            shutil.copy2(file_path, dest_path)
            stats["files_copied"] += 1

            # Track file types
            ext = file_path.suffix.lower()
            stats["file_types"][ext] = stats["file_types"].get(ext, 0) + 1

    # Calculate total size
    stats["size_mb"] = round(get_directory_size(destination) / (1024*1024), 2)

    print(f"  ✓ Copied {stats['files_copied']} files ({stats['size_mb']} MB)")
    print(f"  File types: {dict(stats['file_types'])}")

    return stats


def analyze_all_candidates(raw_dir: Path) -> None:
    """Analyze All-Candidates data files."""
    category_dir = raw_dir / "All-Candidates"
    if not category_dir.exists():
        return

    print("\n  Analyzing All-Candidates structure...")

    # Find a sample file
    sample_files = list(category_dir.glob("*.txt"))
    if sample_files:
        sample = sample_files[0]
        print(f"    Sample file: {sample.name}")

        # Read first few lines
        with open(sample, 'r', encoding='latin-1') as f:
            lines = [f.readline().strip() for _ in range(3)]

        print(f"    Format: Pipe-delimited")
        print(f"    Sample data (first line):")
        if lines:
            fields = lines[0].split('|')
            print(f"      Fields: {len(fields)}")
            print(f"      First 3 fields: {fields[:3]}")


def analyze_house_senate_data(raw_dir: Path) -> None:
    """Analyze House/Senate campaign CSV files."""
    category_dir = raw_dir / "House-Senate-Current-Campaigns"
    if not category_dir.exists():
        return

    print("\n  Analyzing House-Senate-Current-Campaigns structure...")

    # Find CSV files
    csv_files = list(category_dir.glob("*.csv"))
    print(f"    Total CSV files: {len(csv_files)}")

    if csv_files:
        # Analyze a sample campaign file
        campaign_files = [f for f in csv_files if '20' in f.name and 'contribution' not in f.name.lower()]
        if campaign_files:
            sample = campaign_files[0]
            print(f"    Sample campaign file: {sample.name}")

            with open(sample, 'r', encoding='utf-8') as f:
                header = f.readline().strip()
                first_row = f.readline().strip()

            print(f"      Columns: {header.split(',')[:5]}...")
            print(f"      Sample row fields: {len(first_row.split(','))}")


def extract_zip_contents(category: str, sample_only: bool = False) -> None:
    """Extract contents of ZIP files in a category.

    Args:
        category: The FEC category name
        sample_only: If True, only extract one sample file from each ZIP
    """
    category_dir = RAW_DATA_DIR / category

    if not category_dir.exists():
        return

    zip_files = list(category_dir.glob("*.zip"))
    if not zip_files:
        return

    print(f"\n  Found {len(zip_files)} ZIP files in {category}")

    if sample_only:
        print(f"  Extracting sample from first ZIP only...")
        zip_files = zip_files[:1]

    extracted_dir = category_dir / "extracted"
    extracted_dir.mkdir(exist_ok=True)

    for zip_path in zip_files:
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                if sample_only:
                    # Extract just the first file
                    names = zip_ref.namelist()
                    if names:
                        zip_ref.extract(names[0], extracted_dir)
                        print(f"    ✓ Extracted sample from {zip_path.name}: {names[0]}")
                else:
                    zip_ref.extractall(extracted_dir)
                    print(f"    ✓ Extracted {zip_path.name}")
        except Exception as e:
            print(f"    ✗ Error extracting {zip_path.name}: {e}")


def create_manifest(all_stats: List[Dict[str, Any]]) -> None:
    """Create a manifest of extracted FEC data."""
    print("\nCreating FEC manifest...")

    manifest = {
        "extraction_date": None,
        "source_directory": str(SOURCE_DIR),
        "categories": all_stats,
        "summary": {
            "total_categories": len([s for s in all_stats if s["exists"]]),
            "total_files": sum(s["files_copied"] for s in all_stats),
            "total_size_mb": sum(s["size_mb"] for s in all_stats)
        }
    }

    manifest_path = RAW_DATA_DIR / "manifest.json"
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)

    print(f"  ✓ Created manifest.json")
    print(f"  Total categories: {manifest['summary']['total_categories']}")
    print(f"  Total files: {manifest['summary']['total_files']}")
    print(f"  Total size: {manifest['summary']['total_size_mb']:.2f} MB")


def main():
    """Main extraction process."""
    print("=" * 60)
    print("CivicCache - FEC Data Extraction")
    print("=" * 60)

    # Check source directory exists
    if not SOURCE_DIR.exists():
        print(f"\n✗ ERROR: Source directory not found: {SOURCE_DIR}")
        return 1

    print(f"\nSource: {SOURCE_DIR}")
    print(f"Target: {RAW_DATA_DIR}\n")

    # Setup
    setup_directories()

    # Extract all categories
    all_stats = []
    for category in FEC_CATEGORIES:
        stats = extract_category(category)
        all_stats.append(stats)

    # Analyze specific categories
    print("\n" + "=" * 60)
    print("Data Analysis")
    print("=" * 60)
    analyze_all_candidates(RAW_DATA_DIR)
    analyze_house_senate_data(RAW_DATA_DIR)

    # Extract sample ZIP contents for analysis
    print("\n" + "=" * 60)
    print("Extracting Sample ZIP Contents")
    print("=" * 60)
    extract_zip_contents("Candidate-Master", sample_only=True)
    extract_zip_contents("Committee-Master", sample_only=True)

    # Create manifest
    print("\n" + "=" * 60)
    create_manifest(all_stats)

    print("\n" + "=" * 60)
    print("FEC Data Extraction Complete!")
    print("=" * 60)
    print("\nNote: Large ZIP archives have not been fully extracted.")
    print("Sample files extracted to 'extracted' subdirectories for analysis.")
    print("Run extract_zip_contents() without sample_only=True to extract all.")

    return 0


if __name__ == "__main__":
    exit(main())
