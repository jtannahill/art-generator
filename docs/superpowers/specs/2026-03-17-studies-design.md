# Studies — Design Spec

**Date:** 2026-03-17
**Project:** art.jamestannahill.com
**Feature:** Weather event tracking across days and geographies

---

## 1. Overview

Studies track weather events across multiple days (7-10) and geographic locations, linking existing artwork into a narrative timeline. An auto-detection system identifies persistent and clustered events; users can also create studies manually.

Each study page shows an animated Mapbox path of the event + a grid comparison of artworks (rows = days, columns = locations).

---

## 2. Detection

"Top 10" refers to the 10 scan points with the highest composite weather score from the weather ingest Lambda. This score is the weighted combination of pressure anomaly (30%), wind speed (25%), temperature anomaly (20%), precipitation (15%), and humidity (10%) — already computed and stored as the `score` field on each `WEATHER#` item.

### 2.1 Persistent Events
After each pipeline run, the Study Detector Lambda scans the last 10 days of weather data in DynamoDB. When the same scan point (within 5° lat/lng) appears in the top 10 (by `score`) for 3+ consecutive days, it flags a suggested study.

### 2.2 Cluster Events
When 3+ scan points within 15° of each other all appear in the top 10 on the same day, it flags a regional event.

### 2.3 Duplicate Prevention
Before creating a suggested study, the detector checks whether an existing study (any status) already covers the same approximate region (center point within 10°) and overlapping date range. If so, it skips creation.

### 2.4 Manual Creation
Users can create a study via an admin Lambda endpoint — providing a name, coordinates, date range, and artist preference.

---

## 3. Data Model

All items in the existing `art-generator` DynamoDB table.

### 3.1 Study Metadata
```
PK: STUDY#{study_id}
SK: META
Fields:
  - study_id: slug-based (e.g., "north-atlantic-low-2026-03-15")
  - name: "North Atlantic Low"
  - status: "suggested" | "active" | "complete"
  - artist: artist key, "rotating", or null (user chooses)
  - start_date: "2026-03-15"
  - end_date: "2026-03-22"
  - detection_type: "persistent" | "cluster" | "manual"
  - coordinates: [{lat, lng, label}]
  - created_at, updated_at
```

### 3.2 Study Day Entries
```
PK: STUDY#{study_id}
SK: DAY#{date}
Fields:
  - date: "2026-03-16"
  - artwork_refs: [{run_id, slug, lat, lng}]
  - weather_summary: {avg_pressure, max_wind, min_temp, max_temp, avg_humidity}
```

Study day entries link to existing weather artworks by `run_id` and `slug` — no new Bedrock generation needed. The detector matches artworks to study coordinates by proximity (within 5° lat/lng).

---

## 4. Lambdas

### 4.1 Study Detector (`art-study-detector`)
- **Trigger:** Step in the Step Function, runs BEFORE site rebuild (so studies appear same-day)
- **Input:** None (reads from DynamoDB)
- **Logic:**
  1. Scan all `WEATHER#` items from the last 10 days
  2. Group by approximate location (5° grid)
  3. Check persistence: same grid cell with `score` in top 10 for 3+ consecutive days → create suggested study (with duplicate check)
  4. Check clusters: 3+ grid cells within 15° all scoring in top 10 on same day → create suggested study (with duplicate check)
  5. For existing active studies, find today's matching artworks and write a `DAY#` entry
  6. Auto-complete studies past their end_date
- **Output:** New/updated STUDY items in DynamoDB
- **Runtime:** Python 3.12, 256MB, 2 min timeout

### 4.2 Study Admin (`art-study-admin`)
- **Trigger:** Lambda Function URL
- **Auth:** API key passed via `Authorization` header (stored as Lambda env var `ADMIN_API_KEY`). Returns 401 if missing/invalid.
- **Endpoints (via query params):**
  - `?action=create&name=...&start=...&end=...&lat=...&lng=...&artist=...` — create manual study
  - `?action=approve&id=...` — promote suggested → active
  - `?action=complete&id=...` — mark complete
  - `?action=delete&id=...` — delete a study
  - `?action=list` — list all studies
- **Runtime:** Python 3.12, 128MB, 10s timeout

---

## 5. Site Rebuild Changes

The site rebuild Lambda scan already reads the full DynamoDB table. It currently filters for `WEATHER#` and `PALETTE#` prefixes. Add `STUDY#` filtering to collect study items.

### 5.1 Study Index Page (`/studies/`)
- Lists all active and complete studies (not suggested)
- Cards with: name, date range, location count, thumbnail mosaic from linked artworks
- Status badges: "Active" (green), "Complete" (grey)
- Empty state: "No studies yet — the system is watching for interesting weather patterns."

### 5.2 Study Detail Page (`/studies/{study_id}/`)

**Top section: Animated Mapbox**
- Mapbox GL JS loaded via CDN (same as `/map/` page)
- Mapbox token from `MAPBOX_TOKEN` env var (same as existing map page)
- Dark globe centered on the study's bounding box (computed from coordinates)
- Play/pause button + day scrubber
- Animation: 2 seconds per day, auto-plays on load
- Per day: markers appear at tracked coordinates, sized by score
- Path line (GeoJSON LineString) connects daily center points showing movement
- Day label overlay: "Day 3 — March 17, 2026"
- Data format: study days passed as inline JS array (same pattern as `/map/` page with `|safe` filter)

**Grid section:**
- Rows = days, columns = locations
- Each cell shows the SVG artwork thumbnail (linked from existing `site/weather/` assets)
- Empty cells (no matching artwork for that day/location): grey placeholder with "—"
- Clickable cells → link to full artwork page
- Below each row: weather summary (pressure, wind, temp)

**Metadata section:**
- Study name, date range, detection type
- Artist attribution (linked to artist gallery page)
- Total artworks in study

### 5.3 Nav Update
Artists | Archive | Map | Studies | About

### 5.4 Sitemap
Add `/studies/` and each `/studies/{id}/` to sitemap.xml

---

## 6. Step Function Update

Add `art-study-detector` BEFORE `art-site-rebuild` in the Step Function so studies appear same-day:

```
Parallel (weather + satellite)
  └── Study Detector
        └── Site Rebuild
```

---

## 7. Infrastructure

| Resource | Name | Purpose |
|----------|------|---------|
| Lambda | `art-study-detector` | Auto-detect + update studies |
| Lambda | `art-study-admin` | Manual study management (Function URL) |

Both Lambdas need DynamoDB read/write access to the existing `art-generator` table. No new tables or S3 buckets needed.

Admin Lambda gets a Function URL with CORS enabled. Auth via `Authorization` header (API key in Lambda env var).

---

## 8. Cost Impact

Minimal — the detector is a DynamoDB scan + writes, no Bedrock calls. Studies reuse existing artwork, no new generation needed. Estimated <$0.01/day additional.
