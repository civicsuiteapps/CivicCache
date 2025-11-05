# Additional Data Sources for CivicCache

Based on the data types present in CivicScope (Congressional members, campaign finance, districts), here are relevant additional data sources that could enhance the CivicCache database.

## 1. Legislative Data

### Congress.gov API
**URL**: https://api.congress.gov/
**Authentication**: API key required (free)
**Data Available**:
- Bills and resolutions
- Roll call votes
- Congressional Record
- Committee information
- Treaty documents
- Nominations
- Congressional reports

**Use Cases**:
- Track voting records for each member
- Show bill sponsorship and co-sponsorship
- Display committee assignments
- Enable "how did my rep vote?" features

**Rate Limits**:
- 5,000 requests per hour with API key
- Pagination required for large datasets

**Example Integration**:
```python
# Get recent bills
GET https://api.congress.gov/v3/bill/119?api_key=YOUR_KEY

# Get member votes
GET https://api.congress.gov/v3/member/{bioguide_id}/sponsored-legislation
```

### ProPublica Congress API
**URL**: https://projects.propublica.org/api-docs/congress-api/
**Authentication**: API key required (free)
**Data Available**:
- Member votes
- Bills
- Statements
- Committee assignments
- Member comparison tools
- Office expenses

**Use Cases**:
- Enhanced voting record tracking
- Member financial transparency
- Statement archives
- Cross-member analysis

**Rate Limits**:
- 5,000 requests per day

**Example Tables to Add**:
```sql
CREATE TABLE bills (
    bill_id TEXT PRIMARY KEY,
    bill_type TEXT,
    number INTEGER,
    congress INTEGER,
    title TEXT,
    introduced_date TEXT,
    sponsor_id TEXT,
    FOREIGN KEY (sponsor_id) REFERENCES congress_members(id)
);

CREATE TABLE votes (
    vote_id TEXT PRIMARY KEY,
    member_id TEXT,
    bill_id TEXT,
    vote TEXT,  -- 'Yea', 'Nay', 'Present', 'Not Voting'
    vote_date TEXT,
    FOREIGN KEY (member_id) REFERENCES congress_members(id),
    FOREIGN KEY (bill_id) REFERENCES bills(bill_id)
);

CREATE TABLE committee_assignments (
    id INTEGER PRIMARY KEY,
    member_id TEXT,
    committee_name TEXT,
    committee_code TEXT,
    role TEXT,  -- 'Chair', 'Ranking Member', 'Member'
    FOREIGN KEY (member_id) REFERENCES congress_members(id)
);
```

---

## 2. Enhanced Campaign Finance

### OpenFEC API (Real-time)
**URL**: https://api.open.fec.gov/developers/
**Authentication**: API key required (free)
**Data Available**:
- Real-time campaign finance filings
- Candidate financial summaries
- Committee information
- Disbursements and expenditures
- Individual contributions
- Independent expenditures
- Electioneering communications

**Advantages Over Bulk Data**:
- Up-to-date (near real-time)
- RESTful API with powerful filtering
- Aggregated statistics
- Better structured than bulk files

**Use Cases**:
- Current election cycle tracking
- Live contribution updates
- Independent expenditure monitoring
- Super PAC tracking

**Rate Limits**:
- 1,000 requests per hour (120 per minute)

**Example Integration**:
```python
# Get recent filings for a candidate
GET https://api.open.fec.gov/v1/candidate/{candidate_id}/filings/

# Search contributions by zip code
GET https://api.open.fec.gov/v1/schedules/schedule_a/?contributor_zip=94102

# Get independent expenditures
GET https://api.open.fec.gov/v1/schedules/schedule_e/
```

### OpenSecrets API (Center for Responsive Politics)
**URL**: https://www.opensecrets.org/open-data/api
**Authentication**: API key required (donation-based)
**Data Available**:
- Industry contributions
- Top donors by sector
- Lobbying data
- Personal financial disclosures
- Foreign lobbying
- Dark money tracking

**Use Cases**:
- "Who funds my representative?" analysis
- Industry influence tracking
- Lobbying expenditure visualization
- Corporate PAC tracking

**Example Tables to Add**:
```sql
CREATE TABLE contributions_by_industry (
    id INTEGER PRIMARY KEY,
    candidate_id TEXT,
    industry_name TEXT,
    industry_code TEXT,
    cycle INTEGER,
    total_amount REAL,
    individual_amount REAL,
    pac_amount REAL,
    FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id)
);

CREATE TABLE lobbying_activity (
    id INTEGER PRIMARY KEY,
    committee_id TEXT,
    client_name TEXT,
    registrant TEXT,
    amount REAL,
    year INTEGER,
    quarter INTEGER,
    FOREIGN KEY (committee_id) REFERENCES committees(committee_id)
);
```

---

## 3. Geographic and District Data

### US Census Bureau API
**URL**: https://www.census.gov/data/developers/data-sets.html
**Authentication**: API key required (free)
**Data Available**:
- Population demographics
- Economic indicators
- Housing data
- Education statistics
- Income and poverty data
- District boundaries (TIGER/Line)

**Use Cases**:
- District demographic profiles
- Socioeconomic context for voting patterns
- Population-weighted analysis
- District comparison tools

**Example Tables to Add**:
```sql
CREATE TABLE district_demographics (
    district_id TEXT PRIMARY KEY,
    population INTEGER,
    median_income REAL,
    poverty_rate REAL,
    unemployment_rate REAL,
    median_age REAL,
    college_educated_pct REAL,
    FOREIGN KEY (district_id) REFERENCES congressional_districts(district_id)
);
```

### TIGER/Line Shapefiles (Enhanced Districts)
**URL**: https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html
**Format**: Shapefiles (convert to GeoJSON)
**Data Available**:
- High-precision district boundaries
- ZIP Code Tabulation Areas (ZCTAs)
- State legislative districts
- Census tracts

**Use Cases**:
- Accurate "find your representative" by address
- ZIP code to district mapping
- Boundary visualization improvements

**Integration Note**:
CivicScope already has DistrictData.json (276 MB GeoJSON). Census TIGER/Line would provide even more detailed boundaries if needed.

---

## 4. Voting and Election Results

### MIT Election Data and Science Lab
**URL**: https://electionlab.mit.edu/data
**Data Available**:
- Historical election results
- County-level returns
- Voter turnout statistics
- Precinct-level data

**Use Cases**:
- Election result tracking
- Historical trends
- Margin of victory analysis
- Turnout comparison

### Cook Political Report / 538 (Election Forecasting)
**URLs**:
- Cook Political Report: https://www.cookpolitical.com/
- FiveThirtyEight: https://projects.fivethirtyeight.com/

**Data Available**:
- District competitiveness ratings
- Election forecasts
- Polling aggregates

**Use Cases**:
- "Is my district competitive?" feature
- Election prediction tracking
- Historical rating archives

---

## 5. Social Media and Communication

### Twitter API (X)
**URL**: https://developer.twitter.com/en/docs
**Authentication**: API access (paid tiers for full access)
**Data Available**:
- Member tweets and engagement
- Constituent interactions
- Campaign messaging
- Sentiment analysis

**Use Cases**:
- Member activity tracking
- Constituent communication analysis
- Position tracking on issues

### Congressional Member Websites
**Note**: CivicScope already has website_url and contact_form_url in member data

**Potential Scraping** (with respect to robots.txt):
- Press releases
- Position statements
- Event calendars
- Newsletter archives

---

## 6. Lobbying and Ethics

### Senate Lobbying Disclosure Database
**URL**: https://lda.senate.gov/
**Data Available**:
- Lobbying registrations
- Quarterly activity reports
- Lobbyist-client relationships
- Expenditure amounts

**Use Cases**:
- "Who's lobbying my representative?"
- Industry influence tracking
- Foreign agent activity

### House Office of Congressional Ethics
**URL**: https://oce.house.gov/
**Data Available**:
- Ethics investigations
- Financial disclosures
- Travel reports
- Gift reports

---

## 7. Judicial Appointments and Confirmations

### Federal Judicial Center
**URL**: https://www.fjc.gov/history/judges
**Data Available**:
- Judicial biographies
- Appointment dates
- Senate confirmation votes

**Use Cases**:
- Track senator votes on judges
- Judicial appointment history
- Supreme Court confirmation tracking

---

## Implementation Priority

Based on CivicScope's focus on transparency and civic engagement, recommended priority order:

### High Priority
1. **Congress.gov API** - Essential for voting records and bills
2. **OpenFEC API** - Real-time campaign finance updates
3. **ProPublica Congress API** - Enhanced member data and statements

### Medium Priority
4. **OpenSecrets API** - Industry influence and lobbying
5. **Census API** - District demographics
6. **MIT Election Data** - Historical election results

### Low Priority (Nice-to-Have)
7. **Senate Lobbying Database** - Detailed lobbying activity
8. **Social Media APIs** - Member communication tracking
9. **House Ethics** - Financial disclosures and ethics

---

## Technical Considerations

### API Rate Limiting
Most APIs have rate limits. Recommendations:
- Implement caching layer (Redis or similar)
- Use background jobs for bulk updates
- Respect rate limits with exponential backoff
- Consider API key rotation for high-volume needs

### Data Freshness Strategy
Different data types have different update frequencies:

| Data Type | Update Frequency | Method |
|-----------|------------------|--------|
| Congressional Members | Per Congress (2 years) | Batch update |
| Bills & Votes | Daily | Incremental API calls |
| Campaign Finance | Weekly during election season | Incremental sync |
| Committee Assignments | When changed (irregular) | Weekly check |
| Demographics | Annually | Annual batch update |
| Election Results | After elections | Event-driven update |

### Storage Considerations

Adding all suggested data sources would significantly increase database size:

| Data Addition | Estimated Size | Total DB Size |
|---------------|----------------|---------------|
| **Current** | 4.6 MB | 4.6 MB |
| + Voting records (10 years) | ~50 MB | ~55 MB |
| + Bills (current Congress) | ~20 MB | ~75 MB |
| + Industry contributions | ~100 MB | ~175 MB |
| + District demographics | ~5 MB | ~180 MB |
| + Election results | ~30 MB | ~210 MB |

For mobile apps, consider:
- Selective sync (user's state/district only)
- Compressed archives for historical data
- Server-side API for less frequent queries
- Progressive data loading

---

## Sample Integration Scripts

### Congress.gov Bills
```python
import requests

def fetch_recent_bills(api_key, congress=119, limit=100):
    url = f"https://api.congress.gov/v3/bill/{congress}"
    params = {
        'api_key': api_key,
        'limit': limit,
        'format': 'json'
    }
    response = requests.get(url, params=params)
    return response.json()
```

### OpenFEC Real-time Contributions
```python
def fetch_candidate_filings(candidate_id, api_key):
    url = f"https://api.open.fec.gov/v1/candidate/{candidate_id}/filings/"
    headers = {'X-Api-Key': api_key}
    response = requests.get(url, headers=headers)
    return response.json()
```

### Census Demographics
```python
def fetch_district_demographics(state_fips, district, api_key):
    url = "https://api.census.gov/data/2021/acs/acs5"
    params = {
        'get': 'B01003_001E,B19013_001E',  # Population, Median Income
        'for': f'congressional district:{district}',
        'in': f'state:{state_fips}',
        'key': api_key
    }
    response = requests.get(url, params=params)
    return response.json()
```

---

## Next Steps

1. **Prioritize APIs** - Start with Congress.gov and OpenFEC
2. **Set up API keys** - Register for free API access
3. **Create integration scripts** - Build data fetchers similar to ExecutiveCache
4. **Design schema updates** - Add tables for new data types
5. **Implement incremental sync** - Don't re-download everything
6. **Test on iOS/Android** - Ensure mobile performance
7. **Document updates** - Keep DATABASE.md current

---

## API Key Setup Instructions

Create a file `docs/API_KEYS.md` with registration links:
- Congress.gov: https://api.congress.gov/sign-up/
- OpenFEC: https://api.open.fec.gov/developers/
- ProPublica: https://www.propublica.org/datastore/api/propublica-congress-api
- Census: https://api.census.gov/data/key_signup.html
- OpenSecrets: https://www.opensecrets.org/open-data/api-documentation

Store keys in `.env` file:
```bash
CONGRESS_GOV_API_KEY=your_key_here
OPENFEC_API_KEY=your_key_here
PROPUBLICA_API_KEY=your_key_here
CENSUS_API_KEY=your_key_here
OPENSECRETS_API_KEY=your_key_here
```

---

## Conclusion

CivicCache currently provides a strong foundation with Congressional members and FEC data. The suggested additional data sources would transform it into a comprehensive civic engagement platform, enabling features like:

- "How did my representative vote?"
- "Who funds my representative?"
- "What bills did my rep sponsor?"
- "How competitive is my district?"
- "What's the demographic makeup of my district?"

All of these enhancements align with CivicScope's mission of democratic transparency and civic education.
