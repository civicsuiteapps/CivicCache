#!/usr/bin/env python3
"""
Test App Store Connect API for updates to apps and metadata.

This script tests connectivity and retrieves app information for:
- CivicScope (6754943707)
- JudicialScope (6753639085)
- ExecutiveScope (6753639248)
"""

import json
import time
import urllib.request
import urllib.error
from pathlib import Path

try:
    import jwt
except ImportError:
    print("ERROR: PyJWT not installed. Run: pip3 install PyJWT")
    exit(1)

# App Store Connect API configuration
API_BASE = "https://api.appstoreconnect.apple.com/v1"
KEY_ID = "F54HLQ627M"
ISSUER_ID = "69a6de6f-3e67-47e3-e053-5b8c7c11a4d1"
KEY_PATH = Path.home() / ".appstoreconnect/private_keys/AuthKey_F54HLQ627M.p8"

# App IDs
APPS = {
    "CivicScope": "6754943707",
    "JudicialScope": "6753639085",
    "ExecutiveScope": "6753639248"
}


def generate_token() -> str:
    """Generate a JWT token for App Store Connect API."""
    if not KEY_PATH.exists():
        raise FileNotFoundError(f"API key not found at {KEY_PATH}")

    with open(KEY_PATH, 'r') as f:
        private_key = f.read()

    # Token expires in 20 minutes
    expiration_time = int(time.time()) + 1200

    payload = {
        "iss": ISSUER_ID,
        "iat": int(time.time()),
        "exp": expiration_time,
        "aud": "appstoreconnect-v1"
    }

    headers = {
        "alg": "ES256",
        "kid": KEY_ID,
        "typ": "JWT"
    }

    token = jwt.encode(payload, private_key, algorithm="ES256", headers=headers)
    return token


def api_request(endpoint: str, token: str) -> dict:
    """Make an authenticated API request."""
    url = f"{API_BASE}{endpoint}"

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        print(f"HTTP Error {e.code}: {e.reason}")
        print(f"Response: {error_body}")
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None


def get_app_info(app_id: str, token: str) -> dict:
    """Get app information."""
    return api_request(f"/apps/{app_id}", token)


def get_app_versions(app_id: str, token: str) -> dict:
    """Get app version information."""
    return api_request(f"/apps/{app_id}/appStoreVersions", token)


def get_app_builds(app_id: str, token: str) -> dict:
    """Get recent builds for an app."""
    return api_request(f"/apps/{app_id}/builds?limit=5", token)


def get_app_localizations(version_id: str, token: str) -> dict:
    """Get app store version localizations (metadata)."""
    return api_request(f"/appStoreVersions/{version_id}/appStoreVersionLocalizations", token)


def main():
    print("=" * 70)
    print("APP STORE CONNECT API TEST")
    print("=" * 70)

    # Generate token
    print("\nGenerating JWT token...")
    try:
        token = generate_token()
        print(f"  Token generated (first 50 chars): {token[:50]}...")
    except Exception as e:
        print(f"  ERROR: Failed to generate token: {e}")
        return 1

    # Test each app
    for app_name, app_id in APPS.items():
        print(f"\n{'=' * 70}")
        print(f"{app_name} (ID: {app_id})")
        print("=" * 70)

        # Get app info
        print("\n[App Info]")
        app_data = get_app_info(app_id, token)
        if app_data:
            attrs = app_data.get('data', {}).get('attributes', {})
            print(f"  Name: {attrs.get('name', 'N/A')}")
            print(f"  Bundle ID: {attrs.get('bundleId', 'N/A')}")
            print(f"  SKU: {attrs.get('sku', 'N/A')}")
            print(f"  Primary Locale: {attrs.get('primaryLocale', 'N/A')}")
            print(f"  Content Rights: {attrs.get('contentRightsDeclaration', 'N/A')}")
        else:
            print("  Failed to retrieve app info")
            continue

        # Get app versions
        print("\n[App Store Versions]")
        versions_data = get_app_versions(app_id, token)
        if versions_data:
            versions = versions_data.get('data', [])
            if versions:
                for version in versions[:3]:  # Show last 3 versions
                    attrs = version.get('attributes', {})
                    version_id = version.get('id')
                    print(f"  Version: {attrs.get('versionString', 'N/A')}")
                    print(f"    State: {attrs.get('appStoreState', 'N/A')}")
                    print(f"    Release Type: {attrs.get('releaseType', 'N/A')}")
                    print(f"    Created: {attrs.get('createdDate', 'N/A')}")

                    # Get localizations for this version
                    if version_id:
                        localizations = get_app_localizations(version_id, token)
                        if localizations:
                            loc_data = localizations.get('data', [])
                            if loc_data:
                                print(f"    Localizations: {len(loc_data)} languages")
                                # Show English metadata
                                for loc in loc_data:
                                    loc_attrs = loc.get('attributes', {})
                                    if loc_attrs.get('locale') == 'en-US':
                                        print(f"    [en-US] Description (first 100 chars):")
                                        desc = loc_attrs.get('description', '')
                                        print(f"      {desc[:100]}..." if len(desc) > 100 else f"      {desc}")
                                        print(f"    [en-US] Keywords: {loc_attrs.get('keywords', 'N/A')[:80]}...")
                                        break
                    print()
            else:
                print("  No versions found")
        else:
            print("  Failed to retrieve versions")

        # Get recent builds
        print("[Recent Builds]")
        builds_data = get_app_builds(app_id, token)
        if builds_data:
            builds = builds_data.get('data', [])
            if builds:
                for build in builds:
                    attrs = build.get('attributes', {})
                    print(f"  Build: {attrs.get('version', 'N/A')}")
                    print(f"    Uploaded: {attrs.get('uploadedDate', 'N/A')}")
                    print(f"    Processing State: {attrs.get('processingState', 'N/A')}")
                    print(f"    Uses Non-Exempt Encryption: {attrs.get('usesNonExemptEncryption', 'N/A')}")
                    print()
            else:
                print("  No builds found")
        else:
            print("  Failed to retrieve builds")

    print("=" * 70)
    print("API TEST COMPLETE")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    exit(main())
