# Park Grid — Boston Parking Dataset

A structured, enriched inventory of Boston parking locations — on-street meters, accessible spaces, EV chargers, MBTA lots, and public garages — built for real-time "is it legal to park now?" map overlay applications.

**~7,500 locations · WGS84 coordinates · Last updated 2026-06-14**

---

## Pipeline Overview

```
Analyze Boston                 Overpass API         Boston Open Data          MBTA API
  Parking_Meters.csv            OSM EV nodes          OSAP (accessible)         Facilities
        │                           │                      │                        │
        ▼                           │                      │                        │
 build_dataset.py                  │                      │                        │
  • parse PAY_POLICY                │                      │                        │
  • parse PARK_NO_PAY               │                      │                        │
  • build parking_policy{}          │                      │                        │
  • write boston_parking.geojson    │                      │                        │
        │                           │                      │                        │
        ▼                           ▼                      ▼                        ▼
 enrich_dataset.py  ◄──────────────────────────────────────────────────────────────┘
  Step 0  Download & cache all raw sources
  Step 1  Snow emergency zones  → snow_emergency_tow / snow_emergency_safe_spot
  Step 2  OSAP proximity match  → accessible=true on nearby meters
  Step 3  OSM EV proximity      → ev_charging=true on nearby meters
  Step 4  OSM parking capacity  → capacity update; save unmatched garages
  Step 5  Permit zones          → permit_zone + restrictions[] entry
  Step 6  BTD citations         → demand_signals.citation_rate_per_space_month
  Step 7  Add off-street records (OSM garages, MBTA lots, city lots)
  Step 8  Add standalone records (EV chargers, accessible spots not near a meter)
        │
        ▼
  boston_parking.geojson   — full schema, nested objects, used for spatial queries
  boston_parking.csv       — flat subset, used for spreadsheets / quick filtering
  enrichment_report.json   — per-step match counts
  holidays.json            — Boston meter holiday calendar
```

---

## Output Files

| File | Description |
|---|---|
| `boston_parking.geojson` | Full dataset as GeoJSON FeatureCollection. Contains all fields including nested `parking_policy{}`, `restrictions[]`, and `demand_signals{}` |
| `boston_parking.csv` | Flat CSV subset — key fields only, nested arrays flattened or omitted. Primary output for spreadsheet/BI use |
| `holidays.json` | Boston meter holiday calendar (referenced by `free_on_holidays[]`) |
| `enrichment_report.json` | Step-by-step match counts from the last enrich run |
| `qa_active_now.json` | Build-time resolver snapshot for QA — **never ship this** |
| `build_dataset/flatten_geojson.py` | Standalone script: converts any boston_parking.geojson into a CSV |

---

## Record Types

| `type` value | Description | Source | Count (approx) |
|---|---|---|---|
| `on_street_meter` | BTD metered on-street space | Analyze Boston BTD | ~6,955 |
| `mbta_lot` | MBTA park-and-ride lot | MBTA Facilities API | ~169 |
| `public_garage` | OSM-mapped public/city parking garage | OpenStreetMap | ~109 |
| `accessible_parking` | Standalone OSAP-designated accessible space | Boston OSAP / BostonGIS | ~140 |
| `ev_charging` | EV charging station not co-located with a meter | OpenStreetMap | ~47 |

> **Accessibility is an attribute, not always a type.** On-street meters near an OSAP spot get `accessible=true` and `accessible_spaces=N` set directly — they stay `type: on_street_meter`. The `accessible_parking` type is only for standalone OSAP spots that have no co-located metered record.

---

## GeoJSON Feature Schema

Each feature's `properties` object:

### Identity

| Field | Type | Example |
|---|---|---|
| `schema_version` | string | `"1"` |
| `spot_id` | string | `"meter_450001"`, `"osap_7"`, `"ev_osm_6615041292"` |
| `parent_facility_id` | string | `"block_400_NEWBURY_ST"` |
| `geometry_type` | enum | `"point"` |

### Location

| Field | Type | Notes |
|---|---|---|
| `name` | string | Street name or facility name |
| `address` | string | Full street address where known |
| `street_side` | enum | `N` `S` `E` `W` `unknown` — null for off-street |
| `neighborhood` | string | Boston neighborhood, e.g. `"Back Bay"` |
| `btd_district` | string | BTD enforcement district |
| `lat` / `lon` | float | WGS84. For non-point geometry, this is the centroid — use `geometry` for spatial ops |

### Classification

| Field | Type | Values |
|---|---|---|
| `type` | enum | `on_street_meter` `mbta_lot` `public_garage` `accessible_parking` `ev_charging` |
| `ownership` | enum | `public` `private` |

### Payment

| Field | Type | Notes |
|---|---|---|
| `payment_id` | string | Code typed into a payment app (meter ID, MBTA lot code) |
| `payment_app` | array | `["ParkBoston"]`, `["pay_and_display","ParkBoston"]`, `["PayByPhone"]`, `["gate"]` |
| `payment_methods` | array | `["card","coin","app"]`, `["free"]`, `["gate"]` |

### Pricing Policy (`parking_policy{}`)

The machine-readable pricing block. Replaces the old `pricing_rules[]` / `free_periods[]` flat arrays.

```json
{
  "parking_policy": {
    "timezone": "America/New_York",
    "pricing_summary": "Mon-Sat 08:00-20:00 $0.25/hr (2h max); Sun free",
    "pricing_version": 1,
    "updated_at": "2026-06-14T00:00:00Z",
    "rules": [
      {
        "id": "paid_0",
        "priority": 100,
        "active": true,
        "days": ["mon","tue","wed","thu","fri","sat"],
        "time_window": { "start": "08:00", "end": "20:00" },
        "rate": {
          "kind": "hourly",
          "price_per_hour": { "currency": "USD", "amount": 0.25 },
          "billing_increment_minutes": 15
        },
        "constraints": { "max_session_minutes": 120 }
      },
      {
        "id": "free_0",
        "priority": 50,
        "active": true,
        "days": ["sun"],
        "time_window": { "start": "00:00", "end": "24:00" },
        "rate": { "kind": "free" }
      }
    ]
  }
}
```

**Rule evaluation:** sort `rules[]` by `priority` descending; the first rule matching current day + time wins.

**Rate kinds:**
- `"hourly"` — `price_per_hour.amount`, optional `billing_increment_minutes`, optional `constraints.max_session_minutes`
- `"flat_daily"` — `price_per_day.amount` (used by MBTA lots)
- `"free"` — no charge

**Day abbreviations:** lowercase 3-letter, e.g. `"mon"` `"tue"` `"wed"` `"thu"` `"fri"` `"sat"` `"sun"`.

**`pricing_summary`** is the human-readable string for display only — the `rules[]` array is the source of truth for resolver logic.

### Restrictions (`restrictions[]`)

Non-pricing overlapping rules. A spot can simultaneously be metered, permit-required, and a street-cleaning tow zone.

```json
{
  "restrictions": [
    {
      "rule_type": "street_cleaning",
      "days": ["Tue"],
      "start": "05:00",
      "end": "07:00",
      "season_start": "04-01",
      "season_end": "11-30",
      "consequence": "tow",
      "enforcement_agency": "BTD",
      "note": "Street cleaning — vehicle will be towed"
    },
    {
      "rule_type": "permit",
      "days": ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],
      "start": null,
      "end": null,
      "note": "Permit zone 887 — verify schedule on posted signs"
    }
  ]
}
```

`rule_type` values: `street_cleaning` `tow_zone` `permit` `snow_emergency` `loading_zone` `time_limit` `visitor`

### Holidays & Capacity

| Field | Type | Notes |
|---|---|---|
| `free_on_holidays` | array | `["boston_meters"]` or `["mbta"]` — resolved against `holidays.json` |
| `holiday_calendar` | enum | `boston_meters` `mbta` `none` |
| `space_count` | int | Spaces in this on-street record |
| `capacity` | int | Total spaces for off-street facilities |
| `permit_zone` | string | Zone code, e.g. `"887"` |

### Accessibility & EV

| Field | Type | Notes |
|---|---|---|
| `accessible` | bool | True if at least one accessible space is present |
| `accessible_spaces` | int | Count of accessible spaces |
| `year_last_confirmed` | int | Year OSAP last verified this accessible spot (OSAP records only) |
| `ev_charging` | bool | True if EV charging is co-located |
| `ev_network` | string | Charging network name, e.g. `"Tesla Supercharger"` (EV records only) |
| `ev_socket_types` | array | Socket types from OSM, e.g. `["nacs"]` (EV records only) |

### Snow Emergency

| Field | Type | Notes |
|---|---|---|
| `snow_emergency_tow` | bool | True if on a snow emergency tow route |
| `snow_emergency_safe_spot` | bool | True if within 30m of a designated safe-parking spot |

### Demand Signals (`demand_signals{}`)

Updated by a separate pipeline; null until computed.

```json
{
  "demand_signals": {
    "citation_rate_per_space_month": 2.1,
    "demand_signals_date": "2026-06-14"
  }
}
```

| Field | Type | Notes |
|---|---|---|
| `citation_rate_per_space_month` | float | Avg BTD citations per space per month. Null ≠ zero citations |
| `demand_signals_date` | date | Date signals were last computed |

### Provenance

| Field | Type | Values / Notes |
|---|---|---|
| `source` | enum | `analyze_boston` `mbta_official` `osm` `osap` `field_visit` `street_view` `unknown` |
| `source_date` | date | Date the source data reflects |
| `last_updated` | date | Date this record was last processed |
| `needs_verification` | bool | True if data should be confirmed before publishing |
| `verification_method` | enum | `official_source` `field_visit` `street_view` `phone_call` `none` |
| `data_completeness` | enum | `high` `partial` `low` |

---

## CSV Field Reference

The CSV is a flat projection of the GeoJSON — nested fields are either flattened or omitted.

| CSV Column | Source in GeoJSON | Notes |
|---|---|---|
| `spot_id` | `properties.spot_id` | |
| `payment_id` | `properties.payment_id` | |
| `payment_app` | `properties.payment_app` | Pipe-separated: `pay_and_display\|ParkBoston` |
| `name` | `properties.name` | |
| `address` | `properties.address` | |
| `street_side` | `properties.street_side` | |
| `neighborhood` | `properties.neighborhood` | |
| `btd_district` | `properties.btd_district` | |
| `type` | `properties.type` | |
| `ownership` | `properties.ownership` | |
| `lat` | `geometry.coordinates[1]` | |
| `lon` | `geometry.coordinates[0]` | |
| `pricing_summary` | `properties.parking_policy.pricing_summary` | Human-readable; not for resolver logic |
| `space_count` | `properties.space_count` | |
| `capacity` | `properties.capacity` | |
| `permit_zone` | `properties.permit_zone` | |
| `snow_emergency_tow` | `properties.snow_emergency_tow` | |
| `accessible` | `properties.accessible` | |
| `accessible_spaces` | `properties.accessible_spaces` | |
| `ev_charging` | `properties.ev_charging` | |
| `ev_network` | `properties.ev_network` | |
| `citation_rate_per_space_month` | `properties.demand_signals.citation_rate_per_space_month` | |
| `source` | `properties.source` | |
| `source_date` | `properties.source_date` | |
| `last_updated` | `properties.last_updated` | |
| `data_completeness` | `properties.data_completeness` | |

Fields **only in GeoJSON, not in CSV:** `parking_policy.rules[]`, `restrictions[]`, `free_on_holidays[]`, `holiday_calendar`, `ev_socket_types`, `year_last_confirmed`, `snow_emergency_safe_spot`, `demand_signals.demand_signals_date`, `schema_version`, `parent_facility_id`, `needs_verification`, `verification_method`, `source_url`.

---

## Active-Now Resolver

Real-time status is computed client-side from `parking_policy.rules[]` + `restrictions[]` + `holidays.json`. **Never store `active_now` in the dataset** — it goes stale the moment the file is written.

```
Evaluation order (first match wins):
  1. Is today in free_on_holidays[]?         → free, no limit, stop
  2. Find highest-priority rule matching today + current time
       rate.kind == "free"                   → free, no limit
       rate.kind == "hourly"/"flat_daily"    → compute cost + max_session_minutes
  3. Overlay active restrictions[]           → always checked, independent of pricing
```

Use IANA timezone `America/New_York` — never a hardcoded UTC offset.

---

## Holiday Calendar

Boston meter enforcement is suspended on 11 city-observed holidays (parking is free, no time limit):

| Date (2026) | Holiday |
|---|---|
| Jan 1 | New Year's Day |
| Jan 19 | Martin Luther King Jr. Day |
| Feb 16 | Presidents' Day |
| Apr 20 | Patriots' Day |
| May 25 | Memorial Day |
| Jul 4 | Independence Day |
| Sep 7 | Labor Day |
| Oct 12 | Columbus Day |
| Nov 11 | Veterans Day |
| Nov 26 | Thanksgiving Day |
| Dec 25 | Christmas Day |

Dates shift when a holiday falls on a weekend. Verify annually against BTD announcements. Full date lists in `holidays.json`.

---

## Important Notes

**Pricing rules, not live state.** The dataset stores when and how much you'd pay — not whether a space is currently occupied.

**Free ≠ no time limit during paid hours.** During paid windows (e.g. Mon–Sat 8am–8pm) the `max_session_minutes` constraint applies. During free windows (Sunday, overnight, holidays) BTD does not enforce a time limit — the resolver must set `current_max_minutes: null` in those cases.

**Off-street records need verification.** OSM garages and some MBTA lots have `needs_verification: true` — pricing may be incomplete. Do not display them as authoritative without further confirmation.

**`permit_zone` alone is incomplete.** The zone code is in the CSV; the enforcement schedule (days, hours, season) is in `restrictions[]` in the GeoJSON.

**`citation_rate_per_space_month` null ≠ zero.** Null means insufficient citation data was matched — not that no enforcement occurs.

**Accessible coverage is partial.** OSAP covers commercial corridors; absence of `accessible=true` does not mean no accessible space exists.

---

## Rebuilding the Dataset

```bash
cd build_dataset

# Step 1 — build base GeoJSON from BTD meter CSV
python build_dataset.py

# Step 2 — download external sources and enrich in-place
python enrich_dataset.py

# Optional — re-export CSV from an existing GeoJSON without re-running enrichment
python flatten_geojson.py                              # uses boston_parking.geojson by default
python flatten_geojson.py path/to/other.geojson out.csv
```

Raw downloads are cached in `build_dataset/raw/` — delete a file to force a fresh download on the next run.

---

## Data Sources

| Source | Dataset | Used for |
|---|---|---|
| Analyze Boston (BTD) | Parking Meters | Base on-street meter records |
| Boston Open Data (OSAP / BostonGIS) | On Street Accessible Parking Spaces | Accessible space locations + counts |
| OpenStreetMap (Overpass API) | `amenity=charging_station` | EV charger locations |
| OpenStreetMap (Overpass API) | `amenity=parking` | Garage capacity; new garage candidates |
| OpenStreetMap (Overpass API) | `amenity=parking` + Boston operator | City-operated lots |
| MBTA Facilities API | `filter[type]=PARKING_AREA` | MBTA park-and-ride lots + rates |
| BTD Parking Citations | Year-to-date citations CSV | `citation_rate_per_space_month` |
| Analyze Boston | Snow Emergency Parking | Snow tow zones + safe spots |
| Analyze Boston | Resident Permit Zones | Permit zone polygons |
