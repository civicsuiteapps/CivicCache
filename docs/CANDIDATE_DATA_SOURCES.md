# Candidate Data Sources: Current Pipeline vs. Google Civic Information API

This document compares the existing Wikidata/FEC/Wikipedia pipeline with the Google Civic Information API's `voterInfoQuery` endpoint for populating the Opponent Detail page.

---

## Current Data Pipeline

Candidate data currently comes from three sources, each filling a different role:

### 1. FEC Bulk Data (Primary Source -- Pre-cached in DB)

The FEC pipeline (`import_fec_candidates.py`) runs monthly and populates three tables:

| Field | Column | Example |
|-------|--------|---------|
| Candidate ID | `candidate_id` | `H6CA03284` |
| Name | `candidate_name` | `SMITH, JOHN A` |
| Party | `party_code` | `DEM`, `REP`, `LIB` |
| Office | `office` | `H`, `S`, `P` |
| State/District | `office_state`, `office_district` | `CA`, `03` |
| Incumbent/Challenger | `incumbent_challenger_status` | `I`, `C`, `O` |
| Total Receipts | `total_receipts` | `2,450,000.00` |
| Cash on Hand | `cash_on_hand` | `1,200,000.00` |
| Individual Contributions | `total_individual_contributions` | `1,800,000.00` |
| PAC Contributions | `pac_contributions` | `450,000.00` |
| Self-Funding | `candidate_contributions` | `50,000.00` |
| Disbursements | `total_disbursements` | `1,250,000.00` |
| Debts | `debts_owed` | `0.00` |

**Strength:** Comprehensive campaign finance data. No other source provides this.
**Weakness:** No biographical info, no contact info, no social media, no photos. Names are in `LAST, FIRST` format. Data lags 1-3 weeks behind live filings.

### 2. Wikidata SPARQL (Photos Only -- Pre-cached in DB)

`import_candidate_photos.py` queries Wikidata for entities that have both an FEC candidate ID (property P1839) and an image (property P18). Downloaded thumbnails are stored as BLOBs in `race_candidates.image_blob`.

**Strength:** Free, structured data, no API key needed.
**Weakness:** Very low coverage. Most candidates (especially challengers and down-ballot) don't have Wikidata entries with photos. The `image_blob` column is currently **empty for all 17,465 rows** in the active database -- the photo pipeline hasn't been run against the current FEC import.

### 3. Wikipedia API (Biographical -- Fetched Live in App)

`WikipediaService.swift` fetches biographical data at runtime when the user opens CandidateDetailView. It uses the candidate's `ballotpedia_name` to query Wikipedia's REST API.

| Field | Source | Display Location |
|-------|--------|-----------------|
| Extract (bio paragraph) | Wikipedia API | "About" section |
| Birth date | Wikipedia infobox | Age + generation badge |
| Occupation | Wikipedia infobox | Background table |
| Alma mater | Wikipedia infobox | Background table |
| Prior offices held | Wikipedia infobox | Background timeline |
| Thumbnail photo | Wikipedia API | Fallback when no DB image |

**Strength:** Rich biographical context when available.
**Weakness:** Runtime network call on every view. Only works for candidates with Wikipedia articles. No contact info, no social media links, no campaign URLs.

### What the Opponent Detail Page Currently Shows

| Section | Data | Source |
|---------|------|--------|
| Header | Name, party, incumbent/challenger badge | FEC |
| Photo | Candidate image | Wikidata blob -> Wikipedia fallback -> party-color initial |
| Age & Generation | Birth year | DB -> Wikipedia fallback |
| About | Biographical extract | Wikipedia (live) |
| Background | Occupation, education, prior offices | Wikipedia (live) |
| Campaign Finance | Receipts, cash, individual/PAC/self-funded % | FEC |
| Top Contributors | PAC/committee donors ranked | FEC (candidate_top_contributors) |
| Race Context | Opponent count, race rating | FEC + race_ratings |

---

## Google Civic Information API: `voterInfoQuery`

### What It Returns

The endpoint takes a voter's **registered address** and returns contests on that voter's ballot with candidate information:

| Candidate Field | Type | Notes |
|----------------|------|-------|
| `name` | string | Display-ready format |
| `party` | string | Full party name (not code) |
| `candidateUrl` | string | Campaign website |
| `phone` | string | Campaign office phone |
| `email` | string | Campaign email |
| `photoUrl` | string | URL to candidate photo |
| `orderOnBallot` | integer | Ballot position |
| `channels[]` | array | Social media (Facebook, Twitter, YouTube) |

Each contest also includes:
- Office name, level (federal/state/local), and roles
- District name, scope, and ID
- Whether it's a general, primary, run-off, or special election
- Referendum text (for ballot measures)

### What It Does NOT Return

- **No campaign finance data** -- no receipts, cash on hand, contributions, or donor info
- **No biographical data** -- no birth date, occupation, education, or prior offices
- **No incumbent/challenger status**
- **No FEC candidate ID** -- cannot be joined to FEC financial data without fuzzy name matching

### Quota and Pricing

| Aspect | Detail |
|--------|--------|
| **Cost** | Free |
| **Default quota** | 25,000 requests/day per Google Cloud project |
| **Quota applies to** | **Your API key (project-level), not per-user** |
| **Increase available** | Yes, via Cloud Console; approval takes 1-2 months |
| **Authentication** | API key required (appended as `?key=...`) |

**The quota counts against your Cloud project.** Every `voterInfoQuery` call from any user of your app decrements the same 25,000/day pool. With an iOS app, if 1,000 users each trigger 5 lookups in a day, that's 5,000 requests against your single project quota.

This means the API is better suited for **pre-caching during pipeline runs** than for live user-facing calls, unless your user base is small. At scale, you'd either need a quota increase or a caching layer.

### Data Availability Window

The API only returns data for **upcoming or active elections**. Once an election passes, candidate data for that election is no longer available. This is fundamentally different from FEC data, which is historical and persistent.

---

## Side-by-Side Comparison

| Capability | FEC + Wikidata + Wikipedia | Google Civic API |
|-----------|--------------------------|-----------------|
| **Candidate name** | Yes (LAST, FIRST format) | Yes (display-ready) |
| **Party** | Code only (DEM, REP) | Full name |
| **Photo** | Wikidata BLOB (low coverage) | URL (good coverage for major races) |
| **Campaign website** | No | Yes |
| **Phone** | No | Yes |
| **Email** | No | Yes |
| **Social media** | No | Yes (Facebook, Twitter, YouTube) |
| **Campaign finance** | Yes (comprehensive) | No |
| **Biographical info** | Wikipedia (runtime, partial) | No |
| **Prior offices** | Wikipedia (runtime, partial) | No |
| **Incumbent status** | Yes | No |
| **Race ratings** | Yes (via race_ratings) | No |
| **Top donors** | Yes (candidate_top_contributors) | No |
| **Ballot measures** | No | Yes (full referendum text) |
| **Polling locations** | No | Yes |
| **Coverage** | Federal only | Federal + state + local |
| **Data persistence** | Historical (all cycles) | Current election only |
| **Offline access** | Full (pre-cached in SQLite) | None (requires network) |
| **API key needed** | No (bulk files + SPARQL) | Yes (project-level) |
| **Rate limit concern** | None (files downloaded once) | Yes (25K/day shared across all users) |

---

## Recommendation for the Opponent Detail Page

The two sources are **complementary, not competing**. Google fills the contact/social media gaps that FEC and Wikipedia cannot, while FEC provides financial data that Google does not.

### What Google Would Add

The Opponent Detail page currently has no way to show:
- Campaign website link
- Campaign phone/email
- Social media profiles (Facebook, Twitter, YouTube)

These are exactly the fields Google provides. Adding them would fill the "Learn More" section that currently has limited content.

### Suggested Integration Approach

**Pre-cache during pipeline runs, not at runtime.** During the monthly FEC import cycle, call `voterInfoQuery` for each congressional district (using a representative address) and store the contact/social fields in the database. This avoids quota pressure from user traffic and keeps the app fully offline-capable.

New columns needed on `race_candidates`:

| Column | Type | Avg Size | Purpose |
|--------|------|----------|---------|
| `campaign_url` | TEXT | ~60 bytes | Campaign website |
| `campaign_phone` | TEXT | ~15 bytes | Campaign phone |
| `campaign_email` | TEXT | ~30 bytes | Campaign email |
| `photo_url` | TEXT | ~120 bytes | Google-sourced photo URL |
| `social_facebook` | TEXT | ~40 bytes | Facebook handle/ID |
| `social_twitter` | TEXT | ~25 bytes | Twitter/X handle |
| `social_youtube` | TEXT | ~30 bytes | YouTube channel ID |

### Storage Impact Estimate

| Scenario | Rows | New Data Per Row | Total New Storage |
|----------|------|-----------------|-------------------|
| Current cycle only (2026) | ~17,500 | ~320 bytes | **~5.5 MB** |
| Two cycles (2024 + 2026) | ~35,000 | ~320 bytes | **~11 MB** |
| All historical (if reimported) | ~130,000 | ~320 bytes | **~40 MB** |

These are text-only estimates. If you also cache the `photoUrl` images as BLOBs (like the existing `image_blob` column), that changes dramatically:

| Photo Storage | Avg JPEG Size | Per Cycle (~17.5K) | Two Cycles |
|--------------|--------------|-------------------|------------|
| No photos (URL only) | 0 | 0 | 0 |
| Thumbnails (150px) | ~8 KB | **~140 MB** | ~280 MB |
| Medium (400px) | ~25 KB | **~440 MB** | ~880 MB |

**Recommendation:** Store photo URLs only; download at display time in the app (with caching). This keeps DB size under control.

### Quota Budget

With 444 congressional districts, a single pipeline run calling `voterInfoQuery` once per district costs **444 requests** -- well within the 25,000/day default. Even running daily during election season (180 days) would only use ~80,000 total requests across the entire season, averaging 444/day.

The quota is a non-issue for pipeline-based pre-caching. It would only become a concern if calling the API directly from the app at runtime for every user.

---

## References

- [Google Civic Information API Documentation](https://developers.google.com/civic-information)
- [voterInfoQuery Endpoint](https://developers.google.com/civic-information/docs/v2/elections/voterInfoQuery)
- [API Usage Guide & Quotas](https://developers.google.com/civic-information/docs/using_api)
- [FEC Bulk Data Downloads](https://www.fec.gov/data/browse-data/?tab=bulk-data)
- [Wikidata SPARQL Endpoint](https://query.wikidata.org/)
