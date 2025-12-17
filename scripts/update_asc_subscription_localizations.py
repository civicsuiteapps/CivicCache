#!/usr/bin/env python3
"""
Update App Store Connect Subscription Localizations for all apps.

This script:
1. Authenticates with App Store Connect API using JWT
2. Fetches all subscription groups and in-app purchases
3. Updates localizations for all supported languages
4. Uses OpenAI for translations when needed

Requires:
- App Store Connect API Key (.p8 file)
- ISSUER_ID and KEY_ID environment variables
- OpenAI API key for translations
"""

import json
import jwt
import time
import os
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

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
# You'll need to set this - find it in App Store Connect > Users and Access > Keys
ISSUER_ID = os.environ.get("ASC_ISSUER_ID", "")

# OpenAI for translations
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Supported languages (App Store Connect locale codes for subscriptions)
# Note: Subscription localizations use different codes than app localizations
SUPPORTED_LOCALES = [
    "en-US",      # English (US)
    "ar-SA",      # Arabic
    "de-DE",      # German
    "es-ES",      # Spanish (Spain)
    "es-MX",      # Spanish (Mexico)
    "fr-FR",      # French
    "fr-CA",      # French (Canada)
    "it",         # Italian (no region for subscriptions)
    "ja",         # Japanese
    "ko",         # Korean
    "nl-NL",      # Dutch
    "pt-BR",      # Portuguese (Brazil)
    "pt-PT",      # Portuguese (Portugal)
    "ru",         # Russian
    "sv",         # Swedish
    "th",         # Thai
    "tr",         # Turkish
    "vi",         # Vietnamese
    "zh-Hans",    # Chinese (Simplified)
    "zh-Hant",    # Chinese (Traditional)
    "id",         # Indonesian
    "ms",         # Malay
    "da",         # Danish
    "fi",         # Finnish
    "el",         # Greek
    "he",         # Hebrew
    "hu",         # Hungarian
    "no",         # Norwegian
    "ro",         # Romanian
    "sk",         # Slovak
    "cs",         # Czech
    "uk",         # Ukrainian
    "ca",         # Catalan
    "hr",         # Croatian
]

# Subscription configurations for each app
# NOTE: App Store subscription descriptions max 55 characters!
APPS = {
    "CivicScope": {
        "bundle_id": "com.brooksconsulting.civicscope",
        "subscriptions": {
            "starter": {
                "product_id": "com.brooksconsulting.civicscope.starter",
                "name": "Advocate",
                "description": "All districts in your state with sync.",
            },
            "professional": {
                "product_id": "com.brooksconsulting.civicscope.professional",
                "name": "Policy Analyst",
                "description": "All districts nationwide with analytics.",
            }
        }
    },
    "JudicialScope": {
        "bundle_id": "com.brooksconsulting.judicialscope",
        "subscriptions": {
            "starter": {
                "product_id": "com.brooksconsulting.judicialscope.starter",
                "name": "Associate",
                "description": "Top 100 landmark cases with PDF export.",
            },
            "professional": {
                "product_id": "com.brooksconsulting.judicialscope.professional",
                "name": "Justice",
                "description": "All cases plus Harvard CAP archive.",
            }
        }
    },
    "ExecutiveScope": {
        "bundle_id": "com.brooksconsulting.executiveScope",
        "subscriptions": {
            "starter": {
                "product_id": "com.brooksconsulting.executiveScope.starter",
                "name": "Advocate",
                "description": "Current administration with sync.",
            },
            "professional": {
                "product_id": "com.brooksconsulting.executiveScope.professional",
                "name": "Policy Analyst",
                "description": "Complete historical archive access.",
            }
        }
    }
}


def generate_jwt_token() -> str:
    """Generate a JWT token for App Store Connect API authentication."""
    import time
    import base64
    import hashlib
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    if not KEY_PATH.exists():
        raise FileNotFoundError(f"API key not found at {KEY_PATH}")

    with open(KEY_PATH, 'r') as f:
        private_key_pem = f.read()

    private_key = load_pem_private_key(private_key_pem.encode(), password=None)

    def base64url_encode(data):
        if isinstance(data, str):
            data = data.encode('utf-8')
        return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')

    now = int(time.time())
    header = {
        'alg': 'ES256',
        'kid': KEY_ID,
        'typ': 'JWT'
    }
    payload = {
        'iss': ISSUER_ID,
        'iat': now,
        'exp': now + 1200,  # 20 minutes
        'aud': 'appstoreconnect-v1'
    }

    header_b64 = base64url_encode(json.dumps(header, separators=(',', ':')))
    payload_b64 = base64url_encode(json.dumps(payload, separators=(',', ':')))

    message = f'{header_b64}.{payload_b64}'.encode('utf-8')

    # Sign with ES256
    signature = private_key.sign(message, ec.ECDSA(hashes.SHA256()))

    # Convert DER signature to raw r||s format (64 bytes for P-256)
    r, s = decode_dss_signature(signature)
    signature_bytes = r.to_bytes(32, 'big') + s.to_bytes(32, 'big')
    signature_b64 = base64url_encode(signature_bytes)

    token = f'{header_b64}.{payload_b64}.{signature_b64}'
    return token


def api_request(endpoint: str, method: str = "GET", data: Dict = None, token: str = None) -> Optional[Dict]:
    """Make an App Store Connect API request."""
    url = f"{ASC_API_BASE}/{endpoint}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    request_data = json.dumps(data).encode('utf-8') if data else None

    try:
        req = urllib.request.Request(url, data=request_data, headers=headers, method=method)
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


def translate_text(text: str, target_locale: str) -> str:
    """Translate text using OpenAI API."""
    if not OPENAI_API_KEY:
        return text

    # Map locale codes to language names
    locale_names = {
        "ar-SA": "Arabic",
        "de-DE": "German",
        "es-ES": "Spanish (Spain)",
        "es-MX": "Spanish (Mexico)",
        "fr-FR": "French (France)",
        "fr-CA": "French (Canada)",
        "it": "Italian",
        "ja": "Japanese",
        "ko": "Korean",
        "nl-NL": "Dutch",
        "pt-BR": "Portuguese (Brazil)",
        "pt-PT": "Portuguese (Portugal)",
        "ru": "Russian",
        "sv": "Swedish",
        "th": "Thai",
        "tr": "Turkish",
        "uk": "Ukrainian",
        "vi": "Vietnamese",
        "zh-Hans": "Simplified Chinese",
        "zh-Hant": "Traditional Chinese",
        "id": "Indonesian",
        "ms": "Malay",
        "da": "Danish",
        "fi": "Finnish",
        "el": "Greek",
        "he": "Hebrew",
        "hu": "Hungarian",
        "no": "Norwegian",
        "ro": "Romanian",
        "sk": "Slovak",
        "cs": "Czech",
        "ca": "Catalan",
        "hr": "Croatian",
    }

    lang_name = locale_names.get(target_locale, target_locale)

    prompt = f"""Translate the following subscription text to {lang_name}.
Keep it VERY concise - MAXIMUM 55 characters including spaces.
This is for an App Store subscription description with strict character limits.
Only return the translated text, no explanations.

Text: {text}"""

    try:
        data = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "You are a professional translator for app store descriptions."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3,
            "max_tokens": 500
        }

        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(data).encode('utf-8'),
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json"
            }
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
            return result['choices'][0]['message']['content'].strip()

    except Exception as e:
        print(f"    Translation error: {e}")
        return text


def get_apps(token: str) -> List[Dict]:
    """Get all apps from App Store Connect."""
    result = api_request("apps", token=token)
    if result and 'data' in result:
        return result['data']
    return []


def get_subscription_groups(app_id: str, token: str) -> List[Dict]:
    """Get subscription groups for an app."""
    result = api_request(f"apps/{app_id}/subscriptionGroups", token=token)
    if result and 'data' in result:
        return result['data']
    return []


def get_subscriptions(group_id: str, token: str) -> List[Dict]:
    """Get subscriptions in a subscription group."""
    result = api_request(f"subscriptionGroups/{group_id}/subscriptions", token=token)
    if result and 'data' in result:
        return result['data']
    return []


def get_subscription_localizations(subscription_id: str, token: str) -> List[Dict]:
    """Get existing localizations for a subscription."""
    result = api_request(f"subscriptions/{subscription_id}/subscriptionLocalizations", token=token)
    if result and 'data' in result:
        return result['data']
    return []


def get_subscription_group_localizations(group_id: str, token: str) -> List[Dict]:
    """Get existing localizations for a subscription group."""
    result = api_request(f"subscriptionGroups/{group_id}/subscriptionGroupLocalizations", token=token)
    if result and 'data' in result:
        return result['data']
    return []


def create_subscription_group_localization(group_id: str, locale: str, name: str, token: str) -> bool:
    """Create a new subscription group localization."""
    data = {
        "data": {
            "type": "subscriptionGroupLocalizations",
            "attributes": {
                "locale": locale,
                "name": name
            },
            "relationships": {
                "subscriptionGroup": {
                    "data": {
                        "type": "subscriptionGroups",
                        "id": group_id
                    }
                }
            }
        }
    }

    result = api_request("subscriptionGroupLocalizations", method="POST", data=data, token=token)
    return result is not None


def update_subscription_group_localization(localization_id: str, name: str, token: str) -> bool:
    """Update an existing subscription group localization."""
    data = {
        "data": {
            "type": "subscriptionGroupLocalizations",
            "id": localization_id,
            "attributes": {
                "name": name
            }
        }
    }

    result = api_request(f"subscriptionGroupLocalizations/{localization_id}", method="PATCH", data=data, token=token)
    return result is not None


def process_subscription_group(group_id: str, group_name: str, token: str) -> Dict[str, int]:
    """Process localizations for a subscription group."""
    stats = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}

    # Get existing localizations
    existing = get_subscription_group_localizations(group_id, token)
    existing_locales = {loc['attributes']['locale']: loc['id'] for loc in existing}

    print(f"  Processing subscription group localizations...")

    for locale in SUPPORTED_LOCALES:
        print(f"    Processing {locale}...")

        # Translate if not English
        if locale == "en-US":
            name = group_name
        else:
            name = translate_text(group_name, locale)
            time.sleep(0.5)  # Rate limit

        if locale in existing_locales:
            # Update existing
            if update_subscription_group_localization(existing_locales[locale], name, token):
                print(f"      Updated: {name[:30]}...")
                stats['updated'] += 1
            else:
                print(f"      Failed to update")
                stats['failed'] += 1
        else:
            # Create new
            if create_subscription_group_localization(group_id, locale, name, token):
                print(f"      Created: {name[:30]}...")
                stats['created'] += 1
            else:
                print(f"      Failed to create")
                stats['failed'] += 1

        time.sleep(0.3)  # Rate limit for ASC API

    return stats


def create_subscription_localization(subscription_id: str, locale: str, name: str, description: str, token: str) -> bool:
    """Create a new subscription localization."""
    data = {
        "data": {
            "type": "subscriptionLocalizations",
            "attributes": {
                "locale": locale,
                "name": name,
                "description": description
            },
            "relationships": {
                "subscription": {
                    "data": {
                        "type": "subscriptions",
                        "id": subscription_id
                    }
                }
            }
        }
    }

    result = api_request("subscriptionLocalizations", method="POST", data=data, token=token)
    return result is not None


def update_subscription_localization(localization_id: str, name: str, description: str, token: str) -> bool:
    """Update an existing subscription localization."""
    data = {
        "data": {
            "type": "subscriptionLocalizations",
            "id": localization_id,
            "attributes": {
                "name": name,
                "description": description
            }
        }
    }

    result = api_request(f"subscriptionLocalizations/{localization_id}", method="PATCH", data=data, token=token)
    return result is not None


def process_subscription(subscription_id: str, subscription_config: Dict, token: str) -> Dict[str, int]:
    """Process localizations for a single subscription."""
    stats = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}

    # Get existing localizations
    existing = get_subscription_localizations(subscription_id, token)
    existing_locales = {loc['attributes']['locale']: loc['id'] for loc in existing}

    base_name = subscription_config['name']
    base_description = subscription_config['description']

    for locale in SUPPORTED_LOCALES:
        print(f"    Processing {locale}...")

        # Translate if not English
        if locale == "en-US":
            name = base_name
            description = base_description
        else:
            name = translate_text(base_name, locale)
            description = translate_text(base_description, locale)
            # Enforce 55 character limit for descriptions
            if len(description) > 55:
                description = description[:52] + "..."
            time.sleep(0.5)  # Rate limit

        if locale in existing_locales:
            # Update existing
            if update_subscription_localization(existing_locales[locale], name, description, token):
                print(f"      Updated: {name[:30]}...")
                stats['updated'] += 1
            else:
                print(f"      Failed to update")
                stats['failed'] += 1
        else:
            # Create new
            if create_subscription_localization(subscription_id, locale, name, description, token):
                print(f"      Created: {name[:30]}...")
                stats['created'] += 1
            else:
                print(f"      Failed to create")
                stats['failed'] += 1

        time.sleep(0.3)  # Rate limit for ASC API

    return stats


def main():
    """Main execution function."""
    print("=" * 60)
    print("APP STORE CONNECT SUBSCRIPTION LOCALIZATION UPDATE")
    print("=" * 60)

    # Check configuration
    if not ISSUER_ID:
        print("\n ERROR: ASC_ISSUER_ID not set!")
        print("Find your Issuer ID at:")
        print("  App Store Connect > Users and Access > Integrations > App Store Connect API")
        print("\nSet it in your .env file:")
        print("  ASC_ISSUER_ID=your-issuer-id-here")
        return 1

    if not KEY_PATH.exists():
        print(f"\n ERROR: API key not found at {KEY_PATH}")
        return 1

    print(f"\nKey ID: {KEY_ID}")
    print(f"Issuer ID: {ISSUER_ID[:10]}...")
    print(f"Key Path: {KEY_PATH}")
    print(f"OpenAI: {'Configured' if OPENAI_API_KEY else 'Not configured (no translations)'}")
    print(f"Languages: {len(SUPPORTED_LOCALES)}")

    try:
        # Generate JWT token
        print("\nGenerating JWT token...")
        token = generate_jwt_token()
        print(" Token generated")

        # Get all apps
        print("\nFetching apps from App Store Connect...")
        apps = get_apps(token)
        print(f" Found {len(apps)} apps")

        # Process each app
        total_stats = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}

        for app in apps:
            app_name = app['attributes']['name']
            app_id = app['id']
            bundle_id = app['attributes']['bundleId']

            print(f"\n{'='*60}")
            print(f"APP: {app_name} ({bundle_id})")
            print("=" * 60)

            # Find matching config
            app_config = None
            for config_name, config in APPS.items():
                if config['bundle_id'] in bundle_id or bundle_id in config['bundle_id']:
                    app_config = config
                    break

            if not app_config:
                print(f"  No subscription config found, skipping...")
                continue

            # Get subscription groups
            groups = get_subscription_groups(app_id, token)
            print(f"  Found {len(groups)} subscription groups")

            for group in groups:
                group_name = group['attributes']['referenceName']
                group_id = group['id']
                print(f"\n  Group: {group_name}")

                # Process subscription group localizations first
                group_stats = process_subscription_group(group_id, group_name, token)
                for key in total_stats:
                    total_stats[key] += group_stats[key]

                # Get subscriptions in group
                subscriptions = get_subscriptions(group_id, token)
                print(f"    Found {len(subscriptions)} subscriptions")

                for subscription in subscriptions:
                    sub_name = subscription['attributes'].get('name', 'Unknown')
                    sub_id = subscription['id']
                    product_id = subscription['attributes'].get('productId', '')

                    print(f"\n    Subscription: {sub_name} ({product_id})")

                    # Find matching subscription config
                    sub_config = None
                    for tier_name, tier_config in app_config['subscriptions'].items():
                        if tier_name in product_id.lower():
                            sub_config = tier_config
                            break

                    if not sub_config:
                        print(f"      No config found for this subscription, skipping...")
                        continue

                    # Process localizations
                    stats = process_subscription(sub_id, sub_config, token)

                    for key in total_stats:
                        total_stats[key] += stats[key]

        # Summary
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"Created: {total_stats['created']}")
        print(f"Updated: {total_stats['updated']}")
        print(f"Skipped: {total_stats['skipped']}")
        print(f"Failed: {total_stats['failed']}")
        print("\n Localization update complete!")

        return 0

    except Exception as e:
        print(f"\n Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
