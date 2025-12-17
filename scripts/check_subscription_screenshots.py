#!/usr/bin/env python3
"""
Check if subscriptions have App Store Review Screenshots uploaded.

The "Missing Metadata" status on subscriptions is often caused by
missing review screenshots, which are REQUIRED for subscription approval.
"""

import json
import os
import urllib.request
import urllib.error
import time
from pathlib import Path
from typing import Dict, Optional

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value

ASC_API_BASE = "https://api.appstoreconnect.apple.com/v1"
KEY_PATH = Path.home() / "Certificates" / "AppManager-2UDY3SSW5F.p8"
KEY_ID = "2UDY3SSW5F"
ISSUER_ID = os.environ.get("ASC_ISSUER_ID", "")


def generate_jwt_token() -> str:
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
    url = f"{ASC_API_BASE}/{endpoint}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else ""
        print(f"  HTTP Error {e.code}: {e.reason}")
        if e.code != 404:
            print(f"  Response: {error_body[:500]}")
        return None
    except Exception as e:
        print(f"  Error: {e}")
        return None


def main():
    print("=" * 70)
    print("SUBSCRIPTION SCREENSHOT & REVIEW METADATA CHECK")
    print("=" * 70)

    token = generate_jwt_token()
    print("Token generated successfully\n")

    # CivicScope app ID - from previous output
    app_id = None
    apps = api_request("apps", token)
    for app in apps['data']:
        if 'civicscope' in app['attributes']['bundleId'].lower():
            app_id = app['id']
            print(f"Found CivicScope: {app['attributes']['name']} (ID: {app_id})")
            break

    if not app_id:
        print("CivicScope app not found!")
        return 1

    # Get subscription groups
    groups = api_request(f"apps/{app_id}/subscriptionGroups", token)

    for group in groups['data']:
        group_id = group['id']
        print(f"\nSubscription Group: {group['attributes']['referenceName']}")

        # Get subscriptions
        subscriptions = api_request(f"subscriptionGroups/{group_id}/subscriptions", token)

        for sub in subscriptions['data']:
            sub_id = sub['id']
            sub_name = sub['attributes'].get('name', 'Unknown')
            sub_state = sub['attributes'].get('state', 'Unknown')

            print(f"\n  Subscription: {sub_name}")
            print(f"  State: {sub_state}")

            # Check App Store Review Screenshot
            screenshot = api_request(f"subscriptions/{sub_id}/appStoreReviewScreenshot", token)
            if screenshot and 'data' in screenshot and screenshot['data']:
                ss_data = screenshot['data']
                ss_state = ss_data.get('attributes', {}).get('assetDeliveryState', {}).get('state', 'Unknown')
                print(f"  Review Screenshot: UPLOADED (state: {ss_state})")
            else:
                print(f"  Review Screenshot: *** MISSING *** <- This is likely the issue!")

            # Check images (subscription promotional images)
            images = api_request(f"subscriptions/{sub_id}/images", token)
            if images and 'data' in images and len(images['data']) > 0:
                print(f"  Promotional Images: {len(images['data'])} uploaded")
            else:
                print(f"  Promotional Images: None (optional)")

            # Check subscription availability
            availability = api_request(f"subscriptions/{sub_id}/subscriptionAvailability", token)
            if availability and 'data' in availability:
                avail_data = availability['data']
                available_in_new = avail_data.get('attributes', {}).get('availableInNewTerritories', False)
                print(f"  Availability: Configured (availableInNewTerritories: {available_in_new})")
            else:
                print(f"  Availability: Not configured")

    print("\n" + "=" * 70)
    print("DIAGNOSIS")
    print("=" * 70)
    print("""
If 'Review Screenshot' shows 'MISSING' above, that's why you see
'Missing Metadata' in App Store Connect.

To fix this, you need to upload a Review Screenshot for each subscription:

1. Go to App Store Connect > Your App > Subscriptions
2. Click on each subscription (Advocate Annual, Monthly, etc.)
3. Look for 'Review Information' section
4. Upload a screenshot showing the subscription in use

The screenshot should show the subscription purchase flow or the
features that the subscription unlocks in your app.

This CANNOT be done via the API - you must upload manually in
App Store Connect or via Transporter.
""")

    return 0


if __name__ == "__main__":
    exit(main())
