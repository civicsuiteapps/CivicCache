# Spec: Candidate External Links & Photos

## Overview

Enhance `CandidateDetailView` and the inline opponent cards in `RepresentativeDetailView` with external resource links and candidate photos. All URLs are constructed on the fly from data already in `race_candidates` and `candidates` -- no new imports or API keys required for links.

---

## 1. External Links

### Available Link Types

| Link | URL Pattern | Source Field | Coverage |
|------|-------------|--------------|----------|
| **FEC Profile** | `https://www.fec.gov/data/candidate/{candidate_id}/` | `candidate_id` | 100% |
| **FEC Committee** | `https://www.fec.gov/data/committee/{principal_committee_id}/` | `candidates.principal_committee_id` | ~95% (most have a principal committee) |
| **Ballotpedia** | `https://ballotpedia.org/{First_Last}` | Derived from `candidate_name` | ~80% (well-known candidates have pages) |
| **OpenSecrets Race** | `https://www.opensecrets.org/races/candidates?cycle={cycle}&id={state}{district}` | `election_cycle` + `office_state` + `office_district` | 100% of races |

### URL Construction

**FEC candidate ID** (`candidate_id`) is already on `race_candidates`. The others require a lightweight join or name transform:

```
FEC Profile:     "https://www.fec.gov/data/candidate/\(candidate.candidateId)/"
FEC Committee:   "https://www.fec.gov/data/committee/\(principalCommitteeId)/"  -- needs join to candidates table
Ballotpedia:     "https://ballotpedia.org/\(ballotpediaName)"                  -- derived from candidate_name
OpenSecrets:     "https://www.opensecrets.org/races/candidates?cycle=\(race.electionCycle)&id=\(race.state)\(race.district ?? "")"
```

**Ballotpedia name derivation** from FEC-format `"LAST, FIRST MIDDLE"`:
1. Split on `","` to get `["LAST", " FIRST MIDDLE"]`
2. Trim, title-case each part
3. Reorder to `"First_Last"` (drop middle name, suffixes like "JR.", "III")
4. Replace spaces with underscores

This is imperfect (handles ~80% correctly) -- Ballotpedia links should open in Safari and gracefully handle 404s.

### Schema Change

Add `principal_committee_id` to `race_candidates` to avoid a join at display time:

```sql
ALTER TABLE race_candidates ADD COLUMN principal_committee_id TEXT;
```

Update `import_fec_candidates.py` Phase 4 to populate this from the `candidates` table during the denormalization step.

---

## 2. Presentation: Candidate Detail Page (Recommended)

**Recommendation: Expand the existing `CandidateDetailView` rather than inlining links on opponent cards.**

### Rationale

- Opponent cards in `RepresentativeDetailView` are compact summary rows (name, party, fundraising). Adding 3-4 link buttons inline would clutter the member detail page.
- `CandidateDetailView` already exists at `Views/Elections/CandidateDetailView.swift` with header, financial, and race context sections -- it's the natural home for external links.
- The opponent cards should become tappable, navigating to `CandidateDetailView` (they are currently static in `RepresentativeDetailView`; navigation only works from `RaceDetailSheet`).

### Changes

#### A. Make opponent cards tappable in `RepresentativeDetailView`

The `opponentCard()` in `RepresentativeDetailView.swift` (line ~631) is currently a plain `HStack` with no navigation. Wrap it in a `NavigationLink` or `Button` + `navigationDestination` to push `CandidateDetailView`.

Add a small chevron indicator so users know it's tappable:

```
[DEM dot] Alexandria Ocasio-Cortez        $15.4M raised    $8.2M cash   >
          Democrat - Incumbent
```

#### B. Add "Learn More" links section to `CandidateDetailView`

Add a new section below the existing Race Details section:

```
 ┌─────────────────────────────────────────┐
 │  Learn More                             │
 │                                         │
 │  🏛  FEC Candidate Profile         ↗   │
 │  💰  FEC Committee Filings         ↗   │
 │  📖  Ballotpedia                   ↗   │
 │  💵  OpenSecrets Race Overview     ↗   │
 │                                         │
 └─────────────────────────────────────────┘
```

Use the existing `DataSourceLink` pattern from `RepresentativeDetailView` (line 1331) and `@Environment(\.openURL)` for opening links. Each row is a `Link(destination:)` with SF Symbol, label, and trailing arrow.

---

## 3. Candidate Photos

### Photo Sources (Tiered)

| Tier | Source | Coverage | License | URL Pattern |
|------|--------|----------|---------|-------------|
| **1** | `unitedstates/images` | Incumbents only | **Public domain (CC0)** | `https://theunitedstates.io/images/congress/225x275/{bioguide_id}.jpg` |
| **2** | Ballotpedia S3 | Incumbents + challengers | Requires license agreement | `https://s3.amazonaws.com/ballotpedia-api4/files/thumbs/200/300/{First_Last}.jpg` |
| **3** | Wikipedia PageImages API | Notable candidates | CC BY-SA | API call required per candidate |

### Recommended Approach: Tier 1 Only (for now)

Use `unitedstates/images` for incumbent photos. This is:
- **Public domain** -- no licensing concerns
- **Deterministic URL** from bioguide ID (already in `election_races.incumbent_bioguide_id`)
- **Consistent quality** -- official GPO portraits, all 225x275

For challengers, use the existing **party-colored initial circle** pattern already in `CandidateDetailView`. This is honest (we don't have their photos) and visually consistent.

### Photo Display

#### Inline opponent cards (RepresentativeDetailView)

No photos on inline cards -- keep them compact. The party color dot is sufficient at this density.

#### CandidateDetailView header

Replace the current 60x60 initial circle with:
- **If incumbent**: Load photo from `unitedstates/images` via `AsyncImage` with the initial circle as placeholder/fallback
- **If challenger**: Keep the party-colored initial circle (current behavior)

```
 ┌─────────────────────────────────────────┐
 │              [Photo/Initial]             │
 │           Alexandria Ocasio-Cortez       │
 │           Democrat  ┃ Incumbent ┃        │
 │                                          │
 │  Campaign Finance                        │
 │  $15.4M raised  $8.2M cash  $12.1M ind  │
 │                                          │
 │  Race Details                            │
 │  District: NY-14    Cycle: 2026          │
 │  Total Candidates: 13                    │
 │                                          │
 │  Learn More                              │
 │  🏛  FEC Candidate Profile          ↗   │
 │  💰  FEC Committee Filings          ↗   │
 │  📖  Ballotpedia                    ↗   │
 │  💵  OpenSecrets Race Overview      ↗   │
 └─────────────────────────────────────────┘
```

### Future: Challenger Photos

If challenger photo coverage becomes a priority, Ballotpedia's S3 bucket has the best coverage but requires a commercial license. Vote Smart has a deterministic URL pattern (`votesmart.org/canphoto/{id}.jpg`) but requires mapping Vote Smart IDs and organizational access fees. Either would require a licensing conversation before integration.

---

## 4. Data Changes Summary

| Change | Where | Impact |
|--------|-------|--------|
| Add `principal_committee_id` to `race_candidates` | `import_fec_candidates.py` Phase 4 + schema | Avoids join for FEC committee link |
| Ballotpedia name derivation helper | Swift utility function | Pure string transform, no data import |
| OpenSecrets URL construction | Swift utility function | Uses existing `race` fields |
| Incumbent photo URL | Swift `AsyncImage` | Uses existing `incumbent_bioguide_id` from `ElectionRace`, no data import |

---

## 5. Implementation Order

1. **Add `principal_committee_id` to `race_candidates`** -- schema + import script + rebuild + sync
2. **Make opponent cards tappable** -- wrap in NavigationLink in `RepresentativeDetailView`
3. **Add "Learn More" links section** to `CandidateDetailView` (FEC, Ballotpedia, OpenSecrets)
4. **Add incumbent photo** to `CandidateDetailView` header via AsyncImage
5. **Ballotpedia name derivation** utility + link
