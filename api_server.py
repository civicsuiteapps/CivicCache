#!/usr/bin/env python3
"""
CivicCache API Server

A Flask-based REST API server providing endpoints for querying congressional
data including members, staff, legislation, and campaign finance data.

Usage:
    python api_server.py                    # Run on default port 8000
    python api_server.py --port 8080        # Run on custom port
    python api_server.py --debug            # Run with debug mode

Endpoints:
    GET /api/v1/staff                       # List congressional staff
    GET /api/v1/staff/latest                # Get latest congress staff
    GET /api/v1/staff/<id>                  # Get specific staff member
    GET /api/v1/staff/search                # Search staff by name
    GET /api/v1/members                     # List congress members
    GET /api/v1/members/<id>                # Get specific member
"""

import argparse
import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any, List

from flask import Flask, request, jsonify, Response
from functools import wraps

# Configuration
STAFF_DB_PATH = Path(__file__).parent / "civic_cache.db"  # Staff data
MAIN_DB_PATH = Path(__file__).parent / "data" / "civic_cache.db"  # Full dataset

app = Flask(__name__)


# ============================================================================
# Database Helpers
# ============================================================================

def get_staff_db():
    """Get connection to staff database."""
    conn = sqlite3.connect(str(STAFF_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_main_db():
    """Get connection to main database."""
    conn = sqlite3.connect(str(MAIN_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert sqlite3.Row to dictionary."""
    return dict(row)


def json_response(data: Any, status: int = 200) -> Response:
    """Create JSON response with proper headers."""
    response = jsonify(data)
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response, status


# ============================================================================
# API Endpoints - Health
# ============================================================================

@app.route('/')
def root():
    """Root endpoint - API info."""
    return json_response({
        'name': 'CivicCache API',
        'version': '1.0.0',
        'docs': '/api/v1',
        'endpoints': {
            '/api/v1/staff': 'Congressional staff data',
            '/api/v1/staff/latest': 'Latest congress staff',
            '/api/v1/staff/search': 'Search staff by name',
            '/api/v1/members': 'Congress members',
            '/health': 'Health check'
        }
    })


@app.route('/health')
def health():
    """Health check endpoint."""
    staff_db_ok = STAFF_DB_PATH.exists()
    main_db_ok = MAIN_DB_PATH.exists()

    return json_response({
        'status': 'ok' if (staff_db_ok and main_db_ok) else 'degraded',
        'databases': {
            'staff': 'connected' if staff_db_ok else 'not found',
            'main': 'connected' if main_db_ok else 'not found'
        },
        'version': '1.0.0'
    })


@app.route('/api/v1')
def api_info():
    """API version info."""
    return json_response({
        'version': '1.0.0',
        'endpoints': [
            {'path': '/api/v1/staff', 'method': 'GET', 'description': 'List congressional staff'},
            {'path': '/api/v1/staff/latest', 'method': 'GET', 'description': 'Get latest congress staff'},
            {'path': '/api/v1/staff/<id>', 'method': 'GET', 'description': 'Get staff by ID'},
            {'path': '/api/v1/staff/search', 'method': 'GET', 'description': 'Search staff by name'},
            {'path': '/api/v1/members', 'method': 'GET', 'description': 'List congress members'},
            {'path': '/api/v1/members/<id>', 'method': 'GET', 'description': 'Get member by bioguide ID'},
        ]
    })


# ============================================================================
# API Endpoints - Congressional Staff
# ============================================================================

@app.route('/api/v1/staff')
def list_staff():
    """
    List congressional staff members.

    Query Parameters:
        page (int): Page number (default: 1)
        per_page (int): Items per page (default: 50, max: 100)
        congress (int): Filter by congress number
        chamber (str): Filter by chamber (senate/house)
        office_type (str): Filter by office type (member/committee/institutional)
        title (str): Filter by normalized title
        member_id (str): Filter by member bioguide ID
        is_current (bool): Filter by current status (default: true)
    """
    # Parse parameters
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 100)
    congress = request.args.get('congress', type=int)
    chamber = request.args.get('chamber')
    office_type = request.args.get('office_type')
    title = request.args.get('title')
    member_id = request.args.get('member_id')
    is_current = request.args.get('is_current', 'true').lower() == 'true'

    conn = get_staff_db()
    cursor = conn.cursor()

    # Build query
    conditions = []
    params = []

    if congress:
        conditions.append("congress_number = ?")
        params.append(congress)
    if chamber:
        conditions.append("chamber = ?")
        params.append(chamber.lower())
    if office_type:
        conditions.append("office_type = ?")
        params.append(office_type.lower())
    if title:
        conditions.append("title_normalized = ?")
        params.append(title.lower())
    if member_id:
        conditions.append("member_bioguide_id = ?")
        params.append(member_id)
    if is_current:
        conditions.append("is_current = 1")

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    # Get total count
    count_query = f"SELECT COUNT(*) FROM congressional_staff WHERE {where_clause}"
    cursor.execute(count_query, params)
    total = cursor.fetchone()[0]

    # Get paginated results
    offset = (page - 1) * per_page
    query = f"""
        SELECT id, first_name, last_name, full_name, suffix,
               title, title_normalized, office_type, chamber,
               member_bioguide_id, member_name, office_location,
               committee_code, committee_name, subcommittee_name,
               party_affiliation, institutional_office,
               congress_number, source_package, effective_date, is_current
        FROM congressional_staff
        WHERE {where_clause}
        ORDER BY congress_number DESC, last_name, first_name
        LIMIT ? OFFSET ?
    """
    cursor.execute(query, params + [per_page, offset])
    rows = cursor.fetchall()

    staff = [row_to_dict(row) for row in rows]
    conn.close()

    return json_response({
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page,
        'congress': congress,
        'staff': staff
    })


@app.route('/api/v1/staff/latest')
def get_latest_staff():
    """
    Get congressional staff from the most recent congress.

    Query Parameters:
        per_page (int): Items per page (default: 50, max: 100)
        chamber (str): Filter by chamber (senate/house)
        office_type (str): Filter by office type
        title (str): Filter by normalized title
    """
    per_page = min(request.args.get('per_page', 50, type=int), 100)
    chamber = request.args.get('chamber')
    office_type = request.args.get('office_type')
    title = request.args.get('title')

    conn = get_staff_db()
    cursor = conn.cursor()

    # Get the latest congress number
    cursor.execute("SELECT MAX(congress_number) FROM congressional_staff")
    result = cursor.fetchone()
    latest_congress = result[0] if result and result[0] else 118

    # Build query
    conditions = ["congress_number = ?", "is_current = 1"]
    params = [latest_congress]

    if chamber:
        conditions.append("chamber = ?")
        params.append(chamber.lower())
    if office_type:
        conditions.append("office_type = ?")
        params.append(office_type.lower())
    if title:
        conditions.append("title_normalized = ?")
        params.append(title.lower())

    where_clause = " AND ".join(conditions)

    # Get count
    count_query = f"SELECT COUNT(*) FROM congressional_staff WHERE {where_clause}"
    cursor.execute(count_query, params)
    total = cursor.fetchone()[0]

    # Get results
    query = f"""
        SELECT id, first_name, last_name, full_name, suffix,
               title, title_normalized, office_type, chamber,
               member_bioguide_id, member_name, office_location,
               committee_code, committee_name, subcommittee_name,
               party_affiliation, institutional_office,
               congress_number, source_package, effective_date, is_current
        FROM congressional_staff
        WHERE {where_clause}
        ORDER BY last_name, first_name
        LIMIT ?
    """
    cursor.execute(query, params + [per_page])
    rows = cursor.fetchall()

    staff = [row_to_dict(row) for row in rows]
    conn.close()

    return json_response({
        'total': total,
        'congress': latest_congress,
        'per_page': per_page,
        'staff': staff
    })


@app.route('/api/v1/staff/<int:staff_id>')
def get_staff_by_id(staff_id: int):
    """Get a specific staff member by ID."""
    conn = get_staff_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, first_name, last_name, full_name, suffix,
               title, title_normalized, office_type, chamber,
               member_bioguide_id, member_name, office_location,
               committee_code, committee_name, subcommittee_name,
               party_affiliation, institutional_office,
               congress_number, source_package, effective_date, is_current,
               created_at, updated_at
        FROM congressional_staff
        WHERE id = ?
    """, (staff_id,))

    row = cursor.fetchone()
    conn.close()

    if not row:
        return json_response({'error': 'Staff member not found'}, 404)

    return json_response(row_to_dict(row))


@app.route('/api/v1/staff/search')
def search_staff():
    """
    Search congressional staff by name.

    Query Parameters:
        q (str): Search query (required, min 2 chars)
        page (int): Page number (default: 1)
        per_page (int): Items per page (default: 50)
    """
    query_text = request.args.get('q', '')
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 100)

    if len(query_text) < 2:
        return json_response({'error': 'Search query must be at least 2 characters'}, 400)

    conn = get_staff_db()
    cursor = conn.cursor()

    search_term = f"%{query_text}%"

    # Count matching records
    count_query = """
        SELECT COUNT(*) FROM congressional_staff
        WHERE full_name LIKE ? OR first_name LIKE ? OR last_name LIKE ?
    """
    cursor.execute(count_query, (search_term, search_term, search_term))
    total = cursor.fetchone()[0]

    # Get results
    offset = (page - 1) * per_page
    query = """
        SELECT id, first_name, last_name, full_name, suffix,
               title, title_normalized, office_type, chamber,
               member_bioguide_id, member_name, office_location,
               congress_number, effective_date, is_current
        FROM congressional_staff
        WHERE full_name LIKE ? OR first_name LIKE ? OR last_name LIKE ?
        ORDER BY congress_number DESC, last_name, first_name
        LIMIT ? OFFSET ?
    """
    cursor.execute(query, (search_term, search_term, search_term, per_page, offset))
    rows = cursor.fetchall()

    staff = [row_to_dict(row) for row in rows]
    conn.close()

    return json_response({
        'total': total,
        'page': page,
        'per_page': per_page,
        'query': query_text,
        'staff': staff
    })


# ============================================================================
# API Endpoints - Congress Members
# ============================================================================

@app.route('/api/v1/members')
def list_members():
    """
    List congress members.

    Query Parameters:
        page (int): Page number (default: 1)
        per_page (int): Items per page (default: 50)
        chamber (str): Filter by chamber (Senate/House)
        state (str): Filter by state code
        party (str): Filter by party
    """
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 100)
    chamber = request.args.get('chamber')
    state = request.args.get('state')
    party = request.args.get('party')

    conn = get_main_db()
    cursor = conn.cursor()

    # Build query
    conditions = []
    params = []

    if chamber:
        conditions.append("chamber = ?")
        params.append(chamber)
    if state:
        conditions.append("state_code = ?")
        params.append(state.upper())
    if party:
        conditions.append("current_party = ?")
        params.append(party)

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    # Get total count
    count_query = f"SELECT COUNT(*) FROM congress_members WHERE {where_clause}"
    cursor.execute(count_query, params)
    total = cursor.fetchone()[0]

    # Get results
    offset = (page - 1) * per_page
    query = f"""
        SELECT id, first_name, last_name, full_name, chamber,
               state_code, state_name, district, current_party,
               office_address, phone, website_url, twitter_handle
        FROM congress_members
        WHERE {where_clause}
        ORDER BY state_code, last_name
        LIMIT ? OFFSET ?
    """
    cursor.execute(query, params + [per_page, offset])
    rows = cursor.fetchall()

    members = [row_to_dict(row) for row in rows]
    conn.close()

    return json_response({
        'total': total,
        'page': page,
        'per_page': per_page,
        'members': members
    })


@app.route('/api/v1/members/<member_id>')
def get_member_by_id(member_id: str):
    """Get a specific congress member by bioguide ID."""
    conn = get_main_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM congress_members WHERE id = ?
    """, (member_id,))

    row = cursor.fetchone()
    conn.close()

    if not row:
        return json_response({'error': 'Member not found'}, 404)

    return json_response(row_to_dict(row))


@app.route('/api/v1/members/<member_id>/staff')
def get_member_staff(member_id: str):
    """Get all staff for a specific congress member."""
    per_page = min(request.args.get('per_page', 50, type=int), 100)

    conn = get_staff_db()
    cursor = conn.cursor()

    # Get latest congress's staff for this member
    cursor.execute("SELECT MAX(congress_number) FROM congressional_staff WHERE member_bioguide_id = ?", (member_id,))
    result = cursor.fetchone()
    latest_congress = result[0] if result and result[0] else 118

    query = """
        SELECT id, first_name, last_name, full_name, suffix,
               title, title_normalized, office_type, chamber,
               member_bioguide_id, member_name, office_location,
               congress_number, effective_date, is_current
        FROM congressional_staff
        WHERE member_bioguide_id = ? AND congress_number = ? AND is_current = 1
        ORDER BY title
        LIMIT ?
    """
    cursor.execute(query, (member_id, latest_congress, per_page))
    rows = cursor.fetchall()

    staff = [row_to_dict(row) for row in rows]
    conn.close()

    return json_response({
        'member_id': member_id,
        'congress': latest_congress,
        'total': len(staff),
        'staff': staff
    })


# ============================================================================
# Error Handlers
# ============================================================================

@app.errorhandler(404)
def not_found(error):
    return json_response({'error': 'Endpoint not found'}, 404)


@app.errorhandler(500)
def internal_error(error):
    return json_response({'error': 'Internal server error'}, 500)


# ============================================================================
# Main
# ============================================================================

def main():
    """Run the API server."""
    parser = argparse.ArgumentParser(description='CivicCache API Server')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', type=int, default=8000, help='Port to bind to')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')

    args = parser.parse_args()

    # Check databases exist
    if not STAFF_DB_PATH.exists():
        print(f"Warning: Staff database not found at {STAFF_DB_PATH}")
    if not MAIN_DB_PATH.exists():
        print(f"Warning: Main database not found at {MAIN_DB_PATH}")

    print(f"\nCivicCache API Server")
    print(f"=" * 50)
    print(f"Staff DB:  {STAFF_DB_PATH}")
    print(f"Main DB:   {MAIN_DB_PATH}")
    print(f"Listening: http://{args.host}:{args.port}")
    print(f"\nEndpoints:")
    print(f"  GET /health              - Health check")
    print(f"  GET /api/v1/staff        - List congressional staff")
    print(f"  GET /api/v1/staff/latest - Latest congress staff")
    print(f"  GET /api/v1/staff/search - Search staff by name")
    print(f"  GET /api/v1/members      - List congress members")
    print(f"=" * 50 + "\n")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == '__main__':
    main()
