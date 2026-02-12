#!/usr/bin/env python3
"""
Import congressional district demographic data from US Census Bureau API.

This script:
1. Fetches demographic data from Census API (ACS 5-Year estimates)
2. Processes data for all congressional districts
3. Imports into district_demographics table

Census API Documentation:
https://www.census.gov/data/developers/data-sets/acs-5year.html
"""

import os
import sys
import time
import sqlite3
import requests
from pathlib import Path
from typing import Dict, List, Optional

try:
    from config import DB_PATH as _CFG_DB, CENSUS_API_KEY as _CFG_CENSUS
except ImportError:
    _CFG_DB = Path("data/civic_cache.db")
    _CFG_CENSUS = ""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

# Configuration
DB_PATH = _CFG_DB
CENSUS_API_KEY = os.getenv('CENSUS_API_KEY') or _CFG_CENSUS
CENSUS_API_BASE = "https://api.census.gov/data/2022/acs/acs5"

# Census variables to fetch
# https://api.census.gov/data/2022/acs/acs5/variables.html
CENSUS_VARIABLES = {
    'B01003_001E': 'population',                    # Total Population
    'B19013_001E': 'median_household_income',       # Median Household Income
    'B17001_002E': 'poverty_population',            # Population Below Poverty Level
    'B17001_001E': 'poverty_universe',              # Universe for Poverty Calc
    'B23025_005E': 'unemployed',                    # Unemployed
    'B23025_002E': 'labor_force',                   # Labor Force
    'B01002_001E': 'median_age',                    # Median Age
    'B15003_022E': 'bachelors_degree',              # Bachelor's Degree
    'B15003_023E': 'masters_degree',                # Master's Degree
    'B15003_024E': 'professional_degree',           # Professional Degree
    'B15003_025E': 'doctorate_degree',              # Doctorate Degree
    'B15003_001E': 'education_universe',            # Universe for Education
    'B15003_017E': 'high_school_diploma',           # High School Graduate
    'B02001_002E': 'white_alone',                   # White Alone
    'B02001_003E': 'black_alone',                   # Black Alone
    'B03003_003E': 'hispanic',                      # Hispanic or Latino
    'B02001_005E': 'asian_alone',                   # Asian Alone
    'B25077_001E': 'median_home_value',             # Median Home Value
}


def create_connection() -> sqlite3.Connection:
    """Create and return a database connection."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found at {DB_PATH}")

    conn = sqlite3.Connection(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def fetch_census_data(state_fips: str, district_code: str) -> Optional[Dict]:
    """
    Fetch demographic data for a specific congressional district.

    Args:
        state_fips: Two-digit state FIPS code (e.g., "48" for Texas)
        district_code: Two-digit district code (e.g., "32" for District 32)

    Returns:
        Dictionary of demographic data or None if fetch fails
    """
    if not CENSUS_API_KEY:
        raise ValueError("CENSUS_API_KEY not found in environment")

    # Build variable list
    variables = ','.join(CENSUS_VARIABLES.keys())

    # API endpoint for congressional districts
    # Format: /data/2022/acs/acs5?get=VARIABLES&for=congressional district:CD&in=state:ST
    params = {
        'get': variables,
        'for': f'congressional district:{district_code}',
        'in': f'state:{state_fips}',
        'key': CENSUS_API_KEY
    }

    try:
        response = requests.get(CENSUS_API_BASE, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()

        # First row is headers, second row is data
        if len(data) < 2:
            print(f"  Warning: No data returned for {state_fips}-{district_code}")
            return None

        headers = data[0]
        values = data[1]

        # Create dictionary from headers and values
        result = dict(zip(headers, values))
        return result

    except requests.exceptions.RequestException as e:
        print(f"  Error fetching data for {state_fips}-{district_code}: {e}")
        return None
    except Exception as e:
        print(f"  Error processing data for {state_fips}-{district_code}: {e}")
        return None


def calculate_demographics(raw_data: Dict) -> Dict:
    """
    Calculate demographic percentages and clean data.

    Args:
        raw_data: Raw data from Census API

    Returns:
        Processed demographic data
    """
    def safe_int(value) -> Optional[int]:
        """Safely convert to int, return None for invalid values."""
        try:
            return int(value) if value and value != '-666666666' else None
        except (ValueError, TypeError):
            return None

    def safe_float(value) -> Optional[float]:
        """Safely convert to float, return None for invalid values."""
        try:
            return float(value) if value and value != '-666666666' else None
        except (ValueError, TypeError):
            return None

    def safe_percentage(numerator, denominator) -> Optional[float]:
        """Calculate percentage, return None if invalid."""
        num = safe_int(numerator)
        den = safe_int(denominator)
        if num is not None and den is not None and den > 0:
            return round((num / den) * 100, 2)
        return None

    # Extract raw values
    population = safe_int(raw_data.get('B01003_001E'))
    median_income = safe_float(raw_data.get('B19013_001E'))
    median_age = safe_float(raw_data.get('B01002_001E'))
    median_home_value = safe_float(raw_data.get('B25077_001E'))

    # Calculate poverty rate
    poverty_pop = safe_int(raw_data.get('B17001_002E'))
    poverty_universe = safe_int(raw_data.get('B17001_001E'))
    poverty_rate = safe_percentage(poverty_pop, poverty_universe)

    # Calculate unemployment rate
    unemployed = safe_int(raw_data.get('B23025_005E'))
    labor_force = safe_int(raw_data.get('B23025_002E'))
    unemployment_rate = safe_percentage(unemployed, labor_force)

    # Calculate college education percentage (Bachelor's or higher)
    bachelors = safe_int(raw_data.get('B15003_022E', 0)) or 0
    masters = safe_int(raw_data.get('B15003_023E', 0)) or 0
    professional = safe_int(raw_data.get('B15003_024E', 0)) or 0
    doctorate = safe_int(raw_data.get('B15003_025E', 0)) or 0
    college_educated = bachelors + masters + professional + doctorate
    education_universe = safe_int(raw_data.get('B15003_001E'))
    college_educated_pct = safe_percentage(college_educated, education_universe)

    # Calculate high school graduation rate (HS diploma or higher)
    hs_diploma = safe_int(raw_data.get('B15003_017E', 0)) or 0
    hs_and_higher = hs_diploma + college_educated
    high_school_grad_pct = safe_percentage(hs_and_higher, education_universe)

    # Calculate racial percentages
    white_pct = safe_percentage(raw_data.get('B02001_002E'), population)
    black_pct = safe_percentage(raw_data.get('B02001_003E'), population)
    hispanic_pct = safe_percentage(raw_data.get('B03003_003E'), population)
    asian_pct = safe_percentage(raw_data.get('B02001_005E'), population)

    return {
        'population': population,
        'median_household_income': median_income,
        'poverty_rate': poverty_rate,
        'unemployment_rate': unemployment_rate,
        'median_age': median_age,
        'college_educated_pct': college_educated_pct,
        'high_school_grad_pct': high_school_grad_pct,
        'white_pct': white_pct,
        'black_pct': black_pct,
        'hispanic_pct': hispanic_pct,
        'asian_pct': asian_pct,
        'median_home_value': median_home_value,
    }


def import_demographics(conn: sqlite3.Connection) -> None:
    """Import demographic data for all congressional districts."""
    print("\nFetching congressional districts...")

    # Get all districts from database
    districts = conn.execute("""
        SELECT geoid, state_fips, district_code, state_code, district_number, name
        FROM congressional_districts
        ORDER BY state_code, district_number
    """).fetchall()

    total = len(districts)
    print(f"Found {total} districts to process\n")

    imported = 0
    skipped = 0
    errors = 0

    for geoid, state_fips, district_code, state_code, district_num, name in districts:
        district_id = f"{state_code}-{district_num:02d}"

        print(f"Processing {district_id} ({name})...")

        # Fetch data from Census API
        raw_data = fetch_census_data(state_fips, district_code)

        if not raw_data:
            print(f"  ✗ Skipped (no data)")
            skipped += 1
            continue

        # Calculate demographics
        try:
            demographics = calculate_demographics(raw_data)

            # Insert into database
            conn.execute("""
                INSERT OR REPLACE INTO district_demographics (
                    geoid, population, median_household_income, poverty_rate,
                    unemployment_rate, median_age, college_educated_pct,
                    high_school_grad_pct, white_pct, black_pct, hispanic_pct,
                    asian_pct, median_home_value, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                geoid,
                demographics['population'],
                demographics['median_household_income'],
                demographics['poverty_rate'],
                demographics['unemployment_rate'],
                demographics['median_age'],
                demographics['college_educated_pct'],
                demographics['high_school_grad_pct'],
                demographics['white_pct'],
                demographics['black_pct'],
                demographics['hispanic_pct'],
                demographics['asian_pct'],
                demographics['median_home_value']
            ))

            print(f"  ✓ Imported (pop: {demographics['population']:,})")
            imported += 1

            # Commit every 10 districts
            if imported % 10 == 0:
                conn.commit()
                print(f"\n--- Progress: {imported}/{total} ({imported/total*100:.1f}%) ---\n")

            # Rate limiting: be nice to Census API
            time.sleep(0.5)

        except Exception as e:
            print(f"  ✗ Error: {e}")
            errors += 1

    # Final commit
    conn.commit()

    print(f"\n{'='*60}")
    print("IMPORT COMPLETE")
    print(f"{'='*60}")
    print(f"Total districts: {total}")
    print(f"Successfully imported: {imported}")
    print(f"Skipped: {skipped}")
    print(f"Errors: {errors}")


def verify_import(conn: sqlite3.Connection) -> None:
    """Verify the demographic data import."""
    print(f"\n{'='*60}")
    print("VERIFICATION")
    print(f"{'='*60}")

    # Count total records
    result = conn.execute("SELECT COUNT(*) FROM district_demographics").fetchone()
    total = result[0] if result else 0
    print(f"\nTotal demographic records: {total}")

    # Show sample data
    print("\nSample districts:")
    samples = conn.execute("""
        SELECT
            d.state_code,
            d.district_number,
            d.name,
            dd.population,
            dd.median_household_income,
            dd.poverty_rate,
            dd.college_educated_pct
        FROM congressional_districts d
        JOIN district_demographics dd ON d.geoid = dd.geoid
        ORDER BY d.state_code, d.district_number
        LIMIT 5
    """).fetchall()

    for state, district, name, pop, income, poverty, college in samples:
        print(f"\n{state}-{district:02d}: {name}")
        print(f"  Population: {pop:,}")
        print(f"  Median Income: ${income:,.0f}" if income else "  Median Income: N/A")
        print(f"  Poverty Rate: {poverty:.1f}%" if poverty else "  Poverty Rate: N/A")
        print(f"  College Educated: {college:.1f}%" if college else "  College Educated: N/A")

    print(f"\n✓ Verification complete")


def main():
    """Main execution function."""
    print("="*60)
    print("CONGRESSIONAL DISTRICT DEMOGRAPHICS IMPORT")
    print("="*60)

    if not CENSUS_API_KEY:
        print("\n✗ Error: CENSUS_API_KEY not found in environment")
        print("  Please add it to your .env file")
        return 1

    print(f"\nUsing Census API key: {CENSUS_API_KEY[:10]}...")
    print(f"Data source: {CENSUS_API_BASE}")

    try:
        # Connect to database
        conn = create_connection()

        # Import demographics
        import_demographics(conn)

        # Verify import
        verify_import(conn)

        conn.close()

        print(f"\n{'='*60}")
        print("✓ IMPORT SUCCESSFUL")
        print(f"{'='*60}")
        return 0

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
