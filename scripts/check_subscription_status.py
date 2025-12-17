#!/usr/bin/env python3
"""
Check what metadata is missing from subscriptions in App Store Connect.

This script queries the API to see the full subscription state and
identify what fields might be causing "Missing Metadata" status.
"""

import json
import os
import urllib.request
import urllib.error
import time
from pathlib import Path
from typing import Dict, List, Optional

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value

# App Store Connect API Configuration
ASC_API_BASE = "https://api.appstoreconnect.apple.com/v1"
KEY_PATH = Path.home() / "Certificates" / "AppManager-2UDY3SSW5F.p8"
KEY_ID = "2UDY3SSW5F"
ISSUER_ID = os.environ.get("ASC_ISSUER_ID", "")


def generate_jwt_token() -> str:
    """Generate a JWT token for App Store Connect API authentication."""
    import base64
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    with open(KEY_PATH, 'r') as f:
        private_key_pem = f.read()

    private_key = load_pem_private_key(private_key_pem.encode(), password=None)

    def base64url_encode(data):
        if isinstance(data, str):
            data = data.encode('utf-8')
        return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')

    now = int(time.time())
    header = {'alg': 'ES256', 'kid': KEY_ID, 'typ': 'JWT'}
    payload = {'iss': ISSUER_ID, 'iat': now, 'exp': now + 1200, 'aud': 'appstoreconnect-v1'}

    header_b64 = base64url_encode(json.dumps(header, separators=(',', ':')))
    payload_b64 = base64url_encode(json.dumps(payload, separators=(',', ':')))
    message = f'{header_b64}.{payload_b64}'.encode('utf-8')

    signature = private_key.sign(message, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(signature)
    signature_bytes = r.to_bytes(32, 'big') + s.to_bytes(32, 'big')
    signature_b64 = base64url_encode(signature_bytes)

    return f'{header_b64}.{payload_b64}.{signature_b64}'


def api_request(endpoint: str, token: str) -> Optional[Dict]:
    """Make an App Store Connect API request."""
    url = f"{ASC_API_BASE}/{endpoint}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else ""
        print(f"  HTTP Error {e.code}: {e.reason}")
        print(f"  Response: {error_body[:500]}")
        return None
    except Exception as e:
        print(f"  Error: {e}")
        return None


def main():
    print("=" * 70)
    print("SUBSCRIPTION STATUS CHECK")
    print("=" * 70)

    token = generate_jwt_token()
    print("Token generated successfully\n")

    # Get apps
    apps = api_request("apps", token)
    if not apps:
        print("Failed to get apps")
        return 1

    for app in apps['data']:
        app_name = app['attributes']['name']
        app_id = app['id']
        bundle_id = app['attributes']['bundleId']

        if 'civicscope' not in bundle_id.lower():
            continue

        print(f"\n{'='*70}")
        print(f"APP: {app_name} ({bundle_id})")
        print(f"{'='*70}")

        # Get subscription groups
        groups = api_request(f"apps/{app_id}/subscriptionGroups", token)
        if not groups or 'data' not in groups:
            print("  No subscription groups found")
            continue

        for group in groups['data']:
            group_id = group['id']
            group_name = group['attributes']['referenceName']
            print(f"\n  Subscription Group: {group_name} (ID: {group_id})")

            # Check group localizations
            group_locs = api_request(f"subscriptionGroups/{group_id}/subscriptionGroupLocalizations", token)
            if group_locs and 'data' in group_locs:
                print(f"    Group Localizations: {len(group_locs['data'])} locales")
            else:
                print(f"    Group Localizations: NONE")

            # Get subscriptions
            subscriptions = api_request(f"subscriptionGroups/{group_id}/subscriptions", token)
            if not subscriptions or 'data' not in subscriptions:
                print("    No subscriptions found")
                continue

            for sub in subscriptions['data']:
                sub_id = sub['id']
                sub_attrs = sub['attributes']
                product_id = sub_attrs.get('productId', 'Unknown')
                name = sub_attrs.get('name', 'Unknown')
                state = sub_attrs.get('state', 'Unknown')
                review_note = sub_attrs.get('reviewNote', '')
                family_sharable = sub_attrs.get('familySharable', False)
                subscription_period = sub_attrs.get('subscriptionPeriod', 'Unknown')

                print(f"\n    --- Subscription: {name} ---")
                print(f"    Product ID: {product_id}")
                print(f"    State: {state}")
                print(f"    Period: {subscription_period}")
                print(f"    Family Sharable: {family_sharable}")
                print(f"    Review Note: '{review_note}' (empty: {not review_note})")
                print(f"    All attributes: {list(sub_attrs.keys())}")

                # Check subscription localizations
                sub_locs = api_request(f"subscriptions/{sub_id}/subscriptionLocalizations", token)
                if sub_locs and 'data' in sub_locs:
                    print(f"    Localizations: {len(sub_locs['data'])} locales")
                    # Check if any localization is missing fields
                    for loc in sub_locs['data'][:3]:  # First 3
                        loc_attrs = loc['attributes']
                        locale = loc_attrs.get('locale', '?')
                        loc_name = loc_attrs.get('name', '')
                        loc_desc = loc_attrs.get('description', '')
                        print(f"      {locale}: name='{loc_name[:20]}...' desc='{loc_desc[:30]}...'")
                else:
                    print(f"    Localizations: NONE - THIS IS A PROBLEM!")

                # Check subscription prices
                prices = api_request(f"subscriptions/{sub_id}/prices", token)
                if prices and 'data' in prices:
                    print(f"    Prices: {len(prices['data'])} configured")
                else:
                    print(f"    Prices: NONE - MAY BE REQUIRED!")

                # Check introductory offers
                offers = api_request(f"subscriptions/{sub_id}/introductoryOffers", token)
                if offers and 'data' in offers:
                    print(f"    Introductory Offers: {len(offers['data'])}")
                else:
                    print(f"    Introductory Offers: None (optional)")

                # Check promotional offers
                promo = api_request(f"subscriptions/{sub_id}/promotionalOffers", token)
                if promo and 'data' in promo:
                    print(f"    Promotional Offers: {len(promo['data'])}")
                else:
                    print(f"    Promotional Offers: None (optional)")

                # Check submission status - this might reveal issues
                # Get the relationships to see what's linked
                print(f"    Relationships: {list(sub.get('relationships', {}).keys())}")

    print("\n" + "=" * 70)
    print("ANALYSIS COMPLETE")
    print("=" * 70)
    print("\nIf 'Missing Metadata' still shows, check in App Store Connect:")
    print("1. Subscription prices - must be configured for at least one territory")
    print("2. App Store Promotional Image (optional but may cause warnings)")
    print("3. Review notes/screenshot requirements")
    print("4. The subscription must be submitted for review with the app")
    return 0


if __name__ == "__main__":
    exit(main())
