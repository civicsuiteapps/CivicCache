#!/usr/bin/env python3
"""Test Congress.gov member API to debug metadata extraction."""

import json
import urllib.request
import os
from pathlib import Path

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value

CONGRESS_API_KEY = os.environ.get("CONGRESS_GOV_API_KEY", "")
bioguide_id = "P000197"  # Nancy Pelosi - should have lots of data

url = f"https://api.congress.gov/v3/member/{bioguide_id}?api_key={CONGRESS_API_KEY}&format=json"
print(f"Fetching: {url}\n")

try:
    req = urllib.request.Request(url, headers={'User-Agent': 'CivicCache/1.0'})
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode('utf-8'))
        member = data.get('member', {})

        print("Top-level keys:")
        for key in sorted(member.keys()):
            value = member[key]
            if isinstance(value, (dict, list)):
                print(f"  {key}: {type(value).__name__} ({len(value) if isinstance(value, (dict, list)) else 0} items)")
            else:
                print(f"  {key}: {value}")

        print("\n" + "="*60)
        print("Terms structure:")
        terms_data = member.get('terms')
        if terms_data:
            print(f"Type: {type(terms_data)}")
            print(f"Keys: {list(terms_data.keys()) if isinstance(terms_data, dict) else 'N/A'}")
            if isinstance(terms_data, dict) and 'item' in terms_data:
                print(f"Number of terms: {len(terms_data['item'])}")
                if terms_data['item']:
                    print(f"Sample term: {json.dumps(terms_data['item'][0], indent=2)}")

        print("\n" + "="*60)
        print("Leadership structure:")
        leadership = member.get('leadership')
        if leadership:
            print(f"Type: {type(leadership)}")
            print(f"Number of positions: {len(leadership)}")
            if leadership:
                print(f"Sample: {json.dumps(leadership[0] if isinstance(leadership, list) else leadership, indent=2)}")

        print("\n" + "="*60)
        print("Legislation counts:")
        sponsored = member.get('sponsoredLegislation', {})
        cosponsored = member.get('cosponsoredLegislation', {})
        print(f"Sponsored: {sponsored}")
        print(f"Cosponsored: {cosponsored}")

        print("\n" + "="*60)
        print("Portrait:")
        depiction = member.get('depiction', {})
        print(f"Depiction: {depiction}")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
