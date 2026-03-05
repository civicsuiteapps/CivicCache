"""
CivicCache shared configuration.

Centralizes hardcoded values (congress number, paths, API keys) that are
scattered across 10+ import scripts.  Environment variables and GitHub Secrets
always take precedence; the .env file fills in anything still missing.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "civic_cache.db"
FEC_DIR = PROJECT_ROOT / "FEC-Bulk-Data"

# ---------------------------------------------------------------------------
# Environment loading
#
# Read .env with setdefault so real env vars (GitHub Secrets) win.
# ---------------------------------------------------------------------------

_env_file = PROJECT_ROOT / ".env"
if _env_file.exists():
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _value = _line.split("=", 1)
                os.environ.setdefault(_key.strip(), _value.strip())

# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------

CONGRESS_API_KEY = os.environ.get("CONGRESS_GOV_API_KEY") or os.environ.get(
    "CONGRESS_API_KEY", ""
)
CENSUS_API_KEY = os.environ.get("CENSUS_API_KEY", "")
GOVINFO_API_KEY = os.environ.get("GOVINFO_API_KEY", "")
GOOGLE_CIVIC_API_KEY = os.environ.get("GOOGLE_CIVIC_API_KEY", "")

# ---------------------------------------------------------------------------
# Congress number
#
# Auto-computed from the current year:
#   ((year - 1) // 2) - 894
#   2025-2026 -> 119,  2027-2028 -> 120
#
# Override with CONGRESS_NUMBER env var for testing / transition.
# ---------------------------------------------------------------------------


def current_congress_number() -> int:
    """Return the current Congress number based on the calendar year.

    ((year + 1) // 2) - 894
    2025-2026 -> 119,  2027-2028 -> 120
    """
    from datetime import date

    year = date.today().year
    return ((year + 1) // 2) - 894


_override = os.environ.get("CONGRESS_NUMBER")
CONGRESS_NUMBER: int = int(_override) if _override else current_congress_number()
