# Park Grid — Multi-Municipality Parking Dataset

A structured, enriched inventory of parking locations across **Boston, Brookline, and Cambridge, MA** — on-street meters, accessible spaces, EV chargers, MBTA lots, and public garages — built for real-time "is it legal to park now?" map overlay applications.

**~14,000+ locations · WGS84 coordinates · Last updated 2026-06-14**

---

## Pipeline Overview

```
┌─────────────────────────────── BOSTON ───────────────────────────────────┐
│                                                                           │
│  Analyze Boston              Overpass API     Boston Open Data  MBTA API  │
│  Parking_Meters.csv           OSM EV nodes    OSAP (accessible) Facilities│
│        │                          │                 │               │     │
│        ▼                          │                 │               │     │
│ build_dataset.py                  │                 │               │     │
│  • parse PAY_POLICY               │                 │               │     │
│  • parse PARK_NO_PAY              │                 │               │     │
│  • build parking_policy{}         │                 │               │     │
│  • write boston_parking.geojson   │                 │               │     │
│        │                          │                 │               │     │
│        ▼                          ▼                 ▼               ▼     │
│ enrich_dataset.py  ◄──────────────────────────────────────────────────── ┘
│  Step 0  Download & cache all raw sources
│  Step 1  Snow emergency zones  → snow_emergency_tow / snow_emergency_safe_spot
│  Step 2  OSAP proximity match  → accessible=true on nearby meters
│  Step 3  OSM EV proximity      → ev_charging=true on nearby meters
│  Step 4  OSM parking capacity  → capacity update; save unmatched garages
│  Step 5  Permit zones          → permit_zone + restrictions[] entry
│  Step 6  BTD citations         → demand_signals.citation_rate_per_space_month
│  Step 7  Add off-street records (OSM garages, MBTA lots, city lots)
│  Step 8  Add standalone records (EV chargers, accessible spots not near a meter)
│        │
│        ▼
│  boston_parking.geojson   — full schema, nested objects, used for spatial queries
│  boston_parking.csv       — flat CSV, Boston records only
│  enrichment_report.json   — per-step match counts

┌──────────────────────────── BROOKLINE ───────────────────────────────────┐
│                                                                           │
│  ArcGIS FeatureServer                         Overpass API               │
│  Layer 1: Meters                               OSM EV nodes              │
│  Layer 2: ParkingSpaces (ADA)                      │                     │
│  Layer 3: Lots                                     │                     │
│        │                                           │                     │
│        ▼                                           ▼                     │
│ build_brookline.py                                                        │
│  Step 1  Build meter records (Layer 1)                                    │
│  Step 2  Build lot records (Layer 3)                                      │
│  Step 3  Build ADA space records (Layer 2, HAccessible=1)                │
│  Step 4  Spatial-flag meters near ADA spaces                              │
│  Step 5  Build EV charger records (OSM)                                   │
│  Step 6  Write brookline_parking.geojson + brookline_parking.csv         │
│  Step 7  Merge Brookline rows into all_parking.csv                       │

┌──────────────────────────── CAMBRIDGE ───────────────────────────────────┐
│                                                                           │
│  Cambridge MapServer          ArcGIS FeatureServer       Overpass API    │
│  Layer 10: Meters             ADA Spaces                  OSM EV nodes   │
│                               Commercial Parking (garages)     │         │
│        │                          │                            │         │
│        ▼                          ▼                            ▼         │
│ build_cambridge.py                                                        │
│  Step 1  Build meter records (MapServer/10)                              │
│  Step 2  Build ADA space records                                          │
│  Step 3  Build garage records                                             │
│  Step 4  Spatial-flag meters near ADA spaces                              │
│  Step 5  Build EV charger records (OSM)                                   │
│  Step 6  Write cambridge_parking.geojson + cambridge_parking.csv         │
│  Step 7  Merge Cambridge rows into all_parking.csv                       │

                    ┌──────────────────────────┐
                    │       all_parking.csv     │
                    │  Boston + Brookline +     │
                    │  Cambridge combined       │
                    └──────────────────────────┘
```

Alternatively, `merge_parking.py` can rebuild `all_parking.csv` from the three city CSVs in one pass.

---

## Output Files

| File | Description |
|---|---|
| `boston_parking.geojson` | Full Boston dataset as GeoJSON FeatureCollection. Contains all fields including nested `parking_policy{}`, `restrictions[]`, and `demand_signals{}` |
| `boston_parking.csv` | Flat CSV — Boston records only |
| `brookline_parking.geojson` | Full Brookline dataset as GeoJSON FeatureCollection |
| `brookline_parking.csv` | Flat CSV — Brookline records only |
| `cambridge_parking.geojson` | Full Cambridge dataset as GeoJSON FeatureCollection |
| `cambridge_parking.csv` | Flat CSV — Cambridge records only |
| `all_parking.csv` | **Multi-city master CSV** — Boston + Brookline + Cambridge combined, superset of all columns |
| `holidays.json` | Boston meter holiday calendar (referenced by `free_on_holidays[]`) |
| `enrichment_report.json` | Step-by-step match counts from the last Boston enrich run |

---

## Record Types

| `type` value | Description | Source | Municipalities |
|---|---|---|---|
| `on_street_meter` | Metered on-street space | BTD / ArcGIS / Cambridge MapServer | Boston, Brookline, Cambridge |
| `mbta_lot` | MBTA park-and-ride lot | MBTA Facilities API | Boston |
| `public_garage` | Public/city parking garage | OpenStreetMap, ArcGIS | Boston, Cambridge |
| `accessible_parking` | Standalone ADA-designated accessible space | Boston OSAP / Brookline ArcGIS / Cambridge ArcGIS | Boston, Brookline, Cambridge |
| `ev_charging` | EV charging station not co-located with a meter | OpenStreetMap | Boston, Brookline, Cambridge |

> **Accessibility is an attribute, not always a type.** On-street meters near an accessible space get `accessible=true` and `accessible_spaces=N` set directly — they stay `type: on_street_meter`. The `accessible_parking` type is only for standalone accessible spots that have no co-located metered record.

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
| `neighborhood` | string | Neighborhood name |
| `btd_district` | string | BTD enforcement district (Boston only) |
| `lat` / `lon` | float | WGS84. For non-point geometry, this is the centroid |

### Classification

| Field | Type | Values |
|---|---|---|
| `municipality` | string | `"Boston"` `"Brookline"` `"Cambridge"` |
| `type` | enum | `on_street_meter` `mbta_lot` `public_garage` `accessible_parking` `ev_charging` |
| `ownership` | enum | `public` `private` |

### Payment

| Field | Type | Notes |
|---|---|---|
| `payment_id` | string | Code typed into a payment app (meter ID, MBTA lot code) |
| `payment_app` | array | `["ParkBoston"]`, `["PayByPhone"]`, `["gate"]` |
| `payment_methods` | array | `["card","coin","app"]`, `["free"]`, `["gate"]` |

### Pricing Policy (`parking_policy{}`)

The machine-readable pricing block.

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

**Day abbreviations:** lowercase 3-letter: `"mon"` `"tue"` `"wed"` `"thu"` `"fri"` `"sat"` `"sun"`.

### Restrictions (`restrictions[]`)

Non-pricing overlapping rules (street cleaning, permit zones, snow emergency, etc.).

`rule_type` values: `street_cleaning` `tow_zone` `permit` `snow_emergency` `loading_zone` `time_limit` `visitor`

### Holidays & Capacity

| Field | Type | Notes |
|---|---|---|
| `free_on_holidays` | array | `["boston_meters"]` or `["mbta"]` — resolved against `holidays.json` |
| `space_count` | int | Spaces in this on-street record |
| `capacity` | int | Total spaces for off-street facilities |
| `permit_zone` | string | Zone code (Boston) |

### Accessibility & EV

| Field | Type | Notes |
|---|---|---|
| `accessible` | bool | True if at least one accessible space is present |
| `accessible_spaces` | int | Count of accessible spaces |
| `accessible_type` | string | `"standard_ada"` or `"van_accessible"` (Brookline/Cambridge; empty for Boston) |
| `ev_charging` | bool | True if EV charging is co-located |
| `ev_charger_count` | int | Number of EV charging ports (from OSM `capacity` tag) |
| `ev_network` | string | Charging network name, e.g. `"Tesla Supercharger"` |

### Brookline-Specific Fields

| Field | Type | Notes |
|---|---|---|
| `overnight_rental_spaces` | int | Spaces available for overnight rental |
| `overnight_guest_spaces` | int | Guest overnight spaces |
| `lot_number` | string | Brookline lot identifier |

### Snow Emergency (Boston)

| Field | Type | Notes |
|---|---|---|
| `snow_emergency_tow` | bool | True if on a snow emergency tow route |
| `snow_emergency_safe_spot` | bool | True if within 30m of a designated safe-parking spot |

### Demand Signals (`demand_signals{}`)

Updated by a separate pipeline; null until computed.

| Field | Type | Notes |
|---|---|---|
| `citation_rate_per_space_month` | float | Avg BTD citations per space per month (Boston only) |
| `demand_signals_date` | date | Date signals were last computed |

### Provenance

| Field | Type | Values / Notes |
|---|---|---|
| `source` | enum | `analyze_boston` `mbta_official` `osm` `osap` `arcgis` `field_visit` `unknown` |
| `source_date` | date | Date the source data reflects |
| `last_updated` | date | Date this record was last processed |
| `data_completeness` | enum | `high` `partial` `low` |

---

## CSV Field Reference

`all_parking.csv` is the superset of all columns. City-specific CSVs (`boston_parking.csv`, `brookline_parking.csv`, `cambridge_parking.csv`) contain only that city's relevant columns.

| CSV Column | Notes |
|---|---|
| `spot_id` | Unique record identifier |
| `payment_id` | Code for payment app |
| `payment_app` | Pipe-separated: `pay_and_display\|ParkBoston` |
| `name` | Street or facility name |
| `address` | Street address |
| `street_side` | `N` `S` `E` `W` `unknown` |
| `neighborhood` | Neighborhood name |
| `btd_district` | Boston only |
| `municipality` | `"Boston"` `"Brookline"` `"Cambridge"` |
| `type` | Record type |
| `ownership` | `public` or `private` |
| `lat` / `lon` | WGS84 coordinates |
| `pricing_summary` | Human-readable pricing string |
| `space_count` | Spaces in on-street record |
| `capacity` | Total spaces for off-street facilities |
| `overnight_rental_spaces` | Brookline only |
| `overnight_guest_spaces` | Brookline only |
| `lot_number` | Brookline only |
| `permit_zone` | Boston only |
| `snow_emergency_tow` | Boston only |
| `accessible` | True if accessible space present |
| `accessible_spaces` | Count of accessible spaces |
| `accessible_type` | `"standard_ada"` or `"van_accessible"` (Brookline/Cambridge) |
| `ev_charging` | True if EV charging present |
| `ev_charger_count` | Number of EV ports |
| `ev_network` | Charging network name |
| `citation_rate_per_space_month` | Boston only |
| `source` | Data source |
| `source_date` | Source data date |
| `last_updated` | Last processing date |
| `data_completeness` | `high` `partial` `low` |

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

## Rebuilding the Dataset

```bash
cd build_dataset

# Boston — build base GeoJSON, then enrich
python build_dataset.py
python enrich_dataset.py
# Outputs: boston_parking.geojson, boston_parking.csv

# Brookline — fetch ArcGIS layers + OSM EV, merge into all_parking.csv
python build_brookline.py
# Outputs: brookline_parking.geojson, brookline_parking.csv

# Cambridge — fetch MapServer + ArcGIS + OSM EV, merge into all_parking.csv
python build_cambridge.py
# Outputs: cambridge_parking.geojson, cambridge_parking.csv

# all_parking.csv is written automatically by each city script (Step 7).
# To rebuild it from the three city CSVs in one pass:
python merge_parking.py
# Outputs: all_parking.csv

# Optional — re-export CSV from an existing GeoJSON without re-running enrichment
python flatten_geojson.py                              # uses boston_parking.geojson by default
python flatten_geojson.py path/to/other.geojson out.csv
```

Raw downloads are cached in `build_dataset/raw/` — delete a file to force a fresh download on the next run.

---

## Data Sources

| Source | Dataset | Used for |
|---|---|---|
| Analyze Boston (BTD) | Parking Meters | Boston base on-street meter records |
| Boston Open Data (OSAP / BostonGIS) | On Street Accessible Parking Spaces | Boston accessible space locations + counts |
| OpenStreetMap (Overpass API) | `amenity=charging_station` | EV charger locations (all cities) |
| OpenStreetMap (Overpass API) | `amenity=parking` | Garage capacity; new garage candidates (Boston) |
| MBTA Facilities API | `filter[type]=PARKING_AREA` | MBTA park-and-ride lots + rates |
| BTD Parking Citations | Year-to-date citations CSV | `citation_rate_per_space_month` (Boston) |
| Analyze Boston | Snow Emergency Parking | Snow tow zones + safe spots (Boston) |
| Analyze Boston | Resident Permit Zones | Permit zone polygons (Boston) |
| Brookline ArcGIS FeatureServer | Public Parking Feeder Map (Layers 1–3) | Brookline meters, ADA spaces, lots |
| Cambridge ArcGIS / MapServer | TrafficAGOLLayers/MapServer/10 | Cambridge meters |
| Cambridge ArcGIS FeatureServer | Public Handicap Parking Spaces | Cambridge ADA spaces |
| Cambridge ArcGIS FeatureServer | Commercial Parking | Cambridge garages |

---

## Important Notes

**Pricing rules, not live state.** The dataset stores when and how much you'd pay — not whether a space is currently occupied.

**Free ≠ no time limit during paid hours.** During paid windows (e.g. Mon–Sat 8am–8pm) the `max_session_minutes` constraint applies. During free windows (Sunday, overnight, holidays) BTD does not enforce a time limit.

**Off-street records need verification.** OSM garages and some MBTA lots have `needs_verification: true` — pricing may be incomplete.

**`permit_zone` alone is incomplete.** The zone code is in the CSV; the enforcement schedule is in `restrictions[]` in the GeoJSON.

**`citation_rate_per_space_month` null ≠ zero.** Null means insufficient citation data was matched — not that no enforcement occurs.

**Accessible coverage is partial.** Absence of `accessible=true` does not mean no accessible space exists.

**`accessible_type` is Brookline/Cambridge only.** Boston's OSAP data does not distinguish van-accessible from standard ADA — the field will be empty for Boston records.

**SSL/network issues on Overpass.** If the OSM Overpass query fails (SSL cert error), the script continues with 0 EV chargers added. This is a local network issue — re-run once the connection is restored.
