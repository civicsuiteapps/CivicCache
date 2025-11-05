#!/usr/bin/env python3
"""
Test API keys and fetch sample data from each source.

This script validates that your API keys are working correctly
and demonstrates how to fetch data from each API.
"""

import os
import sys
import requests
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)


def test_congress_api():
    """Test Congress.gov API - Bills and legislation."""
    print("\n" + "=" * 60)
    print("📜 Testing Congress.gov API")
    print("=" * 60)

    api_key = os.getenv('CONGRESS_GOV_API_KEY')
    if not api_key:
        print("❌ No API key found in .env file")
        return False

    try:
        # Get recent bills from 119th Congress
        url = f"https://api.congress.gov/v3/bill/119"
        params = {
            'api_key': api_key,
            'limit': 5,
            'format': 'json'
        }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if 'bills' not in data:
            print("❌ Unexpected response format")
            return False

        print(f"✅ API Working!")
        print(f"Total bills available: {data.get('pagination', {}).get('count', 'N/A')}")
        print("\nRecent bills:")

        for bill in data['bills']:
            bill_id = f"{bill['type']} {bill['number']}"
            title = bill.get('title', 'No title')[:70]
            print(f"  • {bill_id}: {title}...")

        return True

    except requests.exceptions.RequestException as e:
        print(f"❌ Request failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_openfec_api():
    """Test OpenFEC API - Campaign finance data."""
    print("\n" + "=" * 60)
    print("💰 Testing OpenFEC API")
    print("=" * 60)

    api_key = os.getenv('OPENFEC_API_KEY')
    if not api_key:
        print("❌ No API key found in .env file")
        return False

    try:
        # Get recent candidates
        url = "https://api.open.fec.gov/v1/candidates/"
        params = {
            'api_key': api_key,
            'office': 'P',
            'election_year': 2024,
            'per_page': 5
        }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if 'results' not in data:
            print("❌ Unexpected response format")
            return False

        print(f"✅ API Working!")
        print(f"Total candidates: {data.get('pagination', {}).get('count', 'N/A')}")
        print("\n2024 Presidential Candidates (Top fundraisers):")

        for candidate in data['results'][:5]:
            name = candidate.get('name', 'Unknown')
            party = candidate.get('party_full', 'Unknown')
            print(f"  • {name} ({party})")

        return True

    except requests.exceptions.RequestException as e:
        print(f"❌ Request failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_census_api():
    """Test US Census Bureau API - Demographics."""
    print("\n" + "=" * 60)
    print("📊 Testing Census Bureau API")
    print("=" * 60)

    api_key = os.getenv('CENSUS_API_KEY')
    if not api_key:
        print("❌ No API key found in .env file")
        return False

    try:
        # Get population and median income for selected states
        url = "https://api.census.gov/data/2021/acs/acs5"
        params = {
            'get': 'NAME,B01003_001E,B19013_001E',  # State name, population, median income
            'for': 'state:06,36,48',  # CA, NY, TX
            'key': api_key
        }

        response = requests.get(url, params=params, timeout=10)

        # Check for redirect to invalid_key page
        if 'invalid_key' in response.url:
            print("❌ Invalid API key - check your email for activation link")
            return False

        response.raise_for_status()
        data = response.json()

        if not data or len(data) < 2:
            print("❌ Unexpected response format")
            return False

        print(f"✅ API Working!")
        print("\nState Demographics (Sample):")

        # First row is headers
        headers = data[0]
        for row in data[1:]:
            state_name = row[0]
            population = int(row[1])
            median_income = int(row[2])
            print(f"  • {state_name}")
            print(f"    Population: {population:,}")
            print(f"    Median Income: ${median_income:,}")

        return True

    except requests.exceptions.RequestException as e:
        print(f"❌ Request failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_propublica_api():
    """Test ProPublica Congress API - Enhanced congressional data."""
    print("\n" + "=" * 60)
    print("🏛️ Testing ProPublica Congress API")
    print("=" * 60)

    api_key = os.getenv('PROPUBLICA_API_KEY')
    if not api_key:
        print("⚠️ No API key found in .env file (optional)")
        return None

    try:
        # Get current Senate members
        url = "https://api.propublica.org/congress/v1/119/senate/members.json"
        headers = {'X-API-Key': api_key}

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        if 'results' not in data or not data['results']:
            print("❌ Unexpected response format")
            return False

        members = data['results'][0]['members']

        print(f"✅ API Working!")
        print(f"Senate members: {len(members)}")
        print("\nSample members:")

        for member in members[:5]:
            name = f"{member['first_name']} {member['last_name']}"
            party = member['party']
            state = member['state']
            print(f"  • {name} ({party}-{state})")

        return True

    except requests.exceptions.RequestException as e:
        print(f"❌ Request failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_google_civic_api():
    """Test Google Civic Information API - Representative lookup."""
    print("\n" + "=" * 60)
    print("🏢 Testing Google Civic Information API")
    print("=" * 60)

    api_key = os.getenv('GOOGLE_CIVIC_API_KEY')
    if not api_key:
        print("⚠️ No API key found in .env file (optional)")
        return None

    try:
        # Look up representatives for White House address
        url = "https://www.googleapis.com/civicinfo/v2/representatives"
        params = {
            'address': '1600 Pennsylvania Ave NW, Washington, DC',
            'key': api_key
        }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if 'error' in data:
            error_msg = data['error'].get('message', 'Unknown error')
            print(f"❌ API Error: {error_msg}")
            if 'iOS client' in error_msg or 'blocked' in error_msg:
                print("   💡 Fix: Configure API restrictions in Google Cloud Console")
                print("      https://console.cloud.google.com/apis/credentials")
            return False

        if 'officials' not in data:
            print("❌ Unexpected response format")
            return False

        print(f"✅ API Working!")
        print(f"Officials found: {len(data['officials'])}")
        print("\nSample officials:")

        for i, official in enumerate(data['officials'][:5]):
            name = official.get('name', 'Unknown')
            office = data['offices'][i]['name'] if i < len(data.get('offices', [])) else 'Unknown'
            print(f"  • {name} - {office}")

        return True

    except requests.exceptions.RequestException as e:
        print(f"❌ Request failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_opensecrets_api():
    """Test OpenSecrets API - Lobbying and industry contributions."""
    print("\n" + "=" * 60)
    print("🔍 Testing OpenSecrets API")
    print("=" * 60)

    api_key = os.getenv('OPENSECRETS_API_KEY')
    if not api_key:
        print("⚠️ No API key found in .env file (requires donation)")
        return None

    try:
        # Get top industries contributing to candidates
        url = "https://www.opensecrets.org/api/"
        params = {
            'method': 'candIndustry',
            'cid': 'N00007360',  # Example: Nancy Pelosi
            'cycle': '2024',
            'apikey': api_key,
            'output': 'json'
        }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        print(f"✅ API Working!")
        print("Industry contribution data accessible")

        return True

    except requests.exceptions.RequestException as e:
        print(f"❌ Request failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def main():
    """Run all API tests."""
    print("=" * 60)
    print("CivicCache API Key Validator")
    print("=" * 60)
    print("\nTesting all configured API keys...")

    results = {}

    # Test essential APIs
    results['congress'] = test_congress_api()
    results['openfec'] = test_openfec_api()
    results['census'] = test_census_api()

    # Test optional APIs
    results['propublica'] = test_propublica_api()
    results['google_civic'] = test_google_civic_api()
    results['opensecrets'] = test_opensecrets_api()

    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)

    working = sum(1 for v in results.values() if v is True)
    failed = sum(1 for v in results.values() if v is False)
    skipped = sum(1 for v in results.values() if v is None)

    print(f"✅ Working: {working}")
    print(f"❌ Failed: {failed}")
    print(f"⚠️ Skipped: {skipped}")

    if working >= 2:
        print("\n🎉 You have enough APIs working to start building!")
        print("   Essential APIs working:")
        if results['congress']:
            print("   • Congress.gov - Bills and legislation")
        if results['openfec']:
            print("   • OpenFEC - Campaign finance data")
        if results['census']:
            print("   • Census Bureau - Demographics")

    if failed > 0:
        print("\n💡 Tips to fix failed APIs:")
        if results['census'] is False:
            print("   • Census: Check email for activation link")
        if results['google_civic'] is False:
            print("   • Google Civic: Configure API restrictions in Cloud Console")
        if results['propublica'] is False:
            print("   • ProPublica: Request API key at propublica.org")

    print("\n" + "=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
