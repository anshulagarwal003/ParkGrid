# Boston Parking Dataset — v1 Schema Specification

Status: **planning / spec only — nothing built yet**
Last updated: 2026-06-14 (rev 4 — street side, coordinate clarity, 9 edge-case fixes)
Scope: City of Boston + immediate MBTA park-and-ride lots
Outputs: `boston_parking.geojson` (map overlay) + `boston_parking.csv` (inspection) + this data dictionary

---

## Design principles
**Overlapping rule layers.** A single spot can be metered AND resident-permit-only AND a street-cleaning
tow zone simultaneously. Rules are modeled as an array of independent, typed objects (`restrictions[]`),
not one flat "is it legal?" verdict.

**The dataset stores rules; the overlay computes "now."** `active_now` is NOT a stored dataset field —
it would go stale the moment the file is generated. The resolver lives in the JS overlay and computes
live status from `pricing_rules[]` + `restrictions[]` + the shared holiday table, using IANA zone
`America/New_York` (handles EST/EDT/DST automatically — never a hardcoded UTC offset). An
`active_now_at_build_time` object is emitted ONLY to a separate QA file for resolver sanity-checking,
never to the shipping GeoJSON/CSV.

**Resolver evaluation order (most-specific wins):**
1. Is today a holiday in `free_on_holidays[]`? → **free, no limit, stop.**
2. Is the current time in a `free_periods[]` window? → **free, no limit, stop.**
3. Otherwise evaluate `pricing_rules[]` for rate + `max_minutes`.
4. Overlay active `restrictions[]` (permit, street-cleaning, snow, etc.) regardless of step 1–3.

**Accessibility is an attribute, not a type.** See the explicit rule under the `type` field below to
prevent double-counting.

**One shared holiday table.** Holiday dates are referenced by `free_on_holidays[]` at the record level,
not baked into each record. The actual date lists live in `holidays.json`.

**Machine-learnable demand signals live in their own sub-object.** `demand_signals{}` is updated by a
separate pipeline (transaction data, citation extracts, OSM POI counts) on a different refresh cycle
than the rules data. Keeping them isolated prevents cross-contamination and lets each pipeline update
independently.

---

## Street side — can we determine it?

**Yes — three complementary approaches, all supportable from existing sources:**

| Approach | How | Reliability |
|---|---|---|
| **BTD meter dataset `SIDE` field** | Analyze Boston BTD meter file carries an explicit `SIDE` column (`N`/`S`/`E`/`W`) | Most reliable; available on all metered on-street records |
| **Geometry offset from street centerline** | For linestring records, compute which side of the MassGIS road centerline the geometry falls on via signed cross-product | Reliable for block-face linestrings; requires MassGIS join at build time |
| **Address number parity** | Odd/even house numbers encode side in most US cities | Fallback only — Boston addressing is irregular in some neighborhoods |

The `street_side` field stores the result. Cardinal direction (`N`/`S`/`E`/`W`) is preferred over
odd/even because it is actionable for navigation ("park on the **north** side of Boylston St").

**Coordinate note:** `lat`/`lon` are scalar fields — they hold one coordinate pair. For `geometry_type:
point` they are the exact location. For `linestring` or `polygon` they hold the **centroid** (for
map-pin placement only). The authoritative geometry for non-point records lives in the GeoJSON
`geometry` object; do not use `lat`/`lon` for spatial computations on linestrings.

---

## Top-level fields (one record per parking spot/facility)

| Field | Type | Description | Source |
|---|---|---|---|
| `schema_version` | string | Schema version this record conforms to, e.g. `"1"` — allows readers to detect v1 vs v2 records | generated |
| `spot_id` | string | Internal unique ID for this record | generated |
| `parent_facility_id` | string | Links spots sharing one kiosk/payment point (Parkeon multi-space, lot-carved spaces); enables pricing dedup + map clustering | derived |
| `geometry_type` | enum | `point` \| `polygon` \| `linestring` — drives CSV→GeoJSON conversion (redundant in GeoJSON output; canonical in CSV intermediate) | derived |
| `payment_id` | string | **The number the user types into the app to pay** (METER_ID / MBTA 4-digit code / lot ref) | meter file / MBTA |
| `payment_app` | array | **Which apps can be used**: array of `ParkBoston` \| `PayByPhone` \| `SpotHero` \| `ParkMobile` \| `pay_and_display` \| `gate` \| `unknown` — array because a single spot can support multiple apps (e.g. Parkeon → `["pay_and_display","ParkBoston"]`) | derived from vendor/type |
| `payment_methods` | array | How you can pay: `app`, `card`, `coin`, `gate`, `prepay` | derived |
| `name` | string | Facility/street name | source |
| `address` | string | Street address (where known) | source |
| `street_side` | enum | Which side of the street the spaces are on: `N` \| `S` \| `E` \| `W` \| `unknown` — null for off-street records. Source: BTD SIDE column (primary), or derived from geometry offset against MassGIS road centerline | BTD meter file / MassGIS derived |
| `neighborhood` | string | Boston neighborhood name, e.g. `"Back Bay"`, `"South End"`, `"Fenway"` — avoids reverse-geocoding at query time; also a useful ML feature | derived / reverse-geocode at build |
| `btd_district` | string | BTD enforcement district code (where available) — useful for joining citation data | Boston BTD data |
| `type` | enum | `on_street_meter` \| `mbta_lot` \| `public_garage` \| `private_lot` \| `accessible_space` | derived |
| | | **Rule:** accessibility is an ATTRIBUTE (`accessible`/`accessible_spaces`) on any record. Use `type: accessible_space` ONLY for a standalone ADA space with its own coordinates not otherwise represented. When a standalone `accessible_space` record exists, the parent facility's `accessible_spaces` count MUST exclude it (no double-count). | |
| `ownership` | enum | `public` \| `private` \| `unknown` | OSM access / source |
| `lat`, `lon` | float | WGS84 coordinates. **For `point` geometry:** exact location. **For `linestring`/`polygon`:** centroid, for map-pin display only — use the GeoJSON `geometry` object for spatial computations. | source |
| `pricing_rules` | array | Structured rate windows (see below) | parsed PAY_POLICY / rate tables |
| `pricing_summary` | string | Human-readable, e.g. "Mon–Sat 8a–6p $0.25/hr (4h max) · Free Sun & overnight" | derived |
| `free_periods` | array | Structured windows when parking is free (time-of-day/day-of-week rules only — uniform shape, no calendar entries). See `free_on_holidays` for holiday-based free periods. | parsed PARK_NO_PAY |
| `free_on_holidays` | array | Which holiday calendars grant free parking on this record, e.g. `["boston_meters"]`. Resolved against `holidays.json` at runtime. Kept separate from `free_periods[]` so that array stays uniform in shape. | derived |
| `free_periods_summary` | string | Human-readable version for display only, e.g. "Sun all day; Mon–Sat 8pm–8am; city holidays" — derived from `free_periods[]` + `free_on_holidays[]`, not a separate source of truth | derived |
| `max_minutes` | int | **Convenience summary only** — the most restrictive time limit across all `pricing_rules[]` windows. `pricing_rules[].max_minutes` is authoritative; this field can go stale if `pricing_rules[]` is updated without re-deriving it. Null whenever free-period or holiday rules apply (see resolver order above). | derived from `pricing_rules[]` |
| `time_limit_suspended_when` | string | Human-readable description of when the time limit does NOT apply — derived from `free_periods[]` + `free_on_holidays[]` | derived |
| `space_count` | int | **On-street and linestring records:** estimated number of individual spaces within this record's geometry (a block face may contain 4–8 spaces). Nullable when unknown. **Off-street facilities:** use `capacity` instead — do not populate both. | OSM / field estimate |
| `capacity` | int | **Off-street facilities only** (`mbta_lot`, `public_garage`, `private_lot`): total spaces. For on-street records use `space_count`. If a lot is subdivided into individual-space child records, `capacity` on the parent excludes those broken-out children. | OSM / source |
| `restrictions` | array | All applicable rule objects (see below) | meter file + permit/snow data |
| `permit_zone` | string | Resident-permit zone code only (e.g. `"A-1"`) — lookup reference into `permit_zones.json`. The permit **schedule** (days/hours/season when permit is required) MUST also be captured as a `restrictions[]` entry with `rule_type: "permit"`. Both fields are required together; neither alone is sufficient. | Boston permit data |
| `snow_emergency_tow` | bool | Tow zone during declared snow emergencies | Boston snow dataset |
| `accessible` | bool | Has designated wheelchair/HP-DV space(s) — primary accessibility signal on ANY record | OSAP map / OSM |
| `accessible_spaces` | int | Count of accessible spaces in THIS record, excluding any broken out as standalone `accessible_space` records | OSM capacity:disabled |
| `ev_charging` | bool | EV charging available | OSM |
| `holiday_calendar` | enum | Which holiday list applies: `boston_meters` \| `mbta` \| `none` — convenience field matching the value in `free_on_holidays[]`; do not use for resolver logic (use `free_on_holidays[]` directly) | derived |
| `demand_signals` | object | ML-learnable demand and utilization signals (see `demand_signals{}` section below) | derived / separate pipeline |
| _(active_now)_ | — | NOT stored — computed client-side in overlay (see below) | overlay |
| `spothero_listing_verified_date` | date \| null | Date a SpotHero listing page was last confirmed to exist for this facility. Null = not checked or no listing found. The overlay should display "may be bookable on SpotHero — verify in-app" rather than a definitive "available" claim. SpotHero availability changes daily; this date is a freshness signal, not a live inventory check. | SpotHero public pages |
| `source` | enum | Data origin: `analyze_boston` \| `mbta_official` \| `osm` \| `spothero_public` \| `field_visit` \| `street_view` \| `unknown` | — |
| `source_url` | string | Link to source | — |
| `source_date` | date | Date the source data reflects | — |
| `last_updated` | date | When this record was last refreshed | — |
| `needs_verification` | bool | Record contains data that should be confirmed (e.g. MBTA rates) | derived |
| `verification_method` | enum | How/if verified: `field_visit` \| `street_view` \| `phone_call` \| `official_source` \| `none` | — |
| `data_completeness` | enum | `high` \| `partial` \| `low` — warns when undigitized signs may add rules | derived |

> Deferred to v2: `currency` (Boston is USD-only; one-line add when non-USD edge cases ever appear).

---

## `pricing_rules[]` — each element

**Midnight rule:** `"end": "24:00"` means end-of-the-same-calendar-day. Windows that cross midnight
(e.g. overnight free parking) MUST be split into two rules: `20:00–24:00` on the start day and
`00:00–08:00` on the next. Never use `"end": "00:00"` to mean midnight — it is ambiguous and will
be interpreted as start-of-day (zero duration).

**`day_type` is a non-canonical display hint only.** `days[]` is the authoritative field. If
`day_type` is present, it must be consistent with `days[]` but the resolver MUST NOT use it
for logic — only `days[]`. Its sole purpose is a human-readable label for the UI.

**Holiday override:** if today is in `free_on_holidays[]`, the resolver skips `pricing_rules[]`
entirely (step 1 of the evaluation order above). No `pricing_rules[]` entry is needed for holidays.

```json
{
  "days": ["Mon","Tue","Wed","Thu","Fri","Sat"],
  "day_type": "weekday_sat",          // display hint ONLY — not used by resolver; days[] is canonical
  "start": "08:00",
  "end": "18:00",                     // "24:00" = end of calendar day; cross-midnight → split into two rules
  "rate_per_hour": 0.25,
  "max_minutes": 240,
  "free": false
}
```

## `restrictions[]` — each element (overlapping rules)

**`season_start` / `season_end`** are MM-DD strings (year-agnostic) so the resolver can compute
in-season without string parsing. Do not use a free-text `enforced_season` field.

**`rule_type` valid values:** `time_limit` \| `permit` \| `street_cleaning` \| `tow_zone` \|
`snow_emergency` \| `loading_zone` \| `visitor`. Note: `meter` has been **removed** — metering
schedules belong in `pricing_rules[]`, not as a restriction object.

**Permit requirement:** if a `restrictions[]` entry has `rule_type: "permit"`, the record MUST
also have a non-null `permit_zone` value. Conversely, any record with `permit_zone` set MUST have
at least one `rule_type: "permit"` entry in `restrictions[]` containing the schedule.

```json
{
  "rule_type": "street_cleaning",     // time_limit | permit | street_cleaning |
                                      // tow_zone | snow_emergency | loading_zone | visitor
  "days": ["Tue"],
  "start": "05:00",
  "end": "07:00",
  "season_start": "04-01",           // MM-DD, year-agnostic; omit if year-round
  "season_end": "11-30",             // MM-DD, year-agnostic; omit if year-round
  "consequence": "tow",               // tow | ticket | none
  "enforcement_agency": "BTD",        // BTD | BPD | MBTA_Transit_Police | private_contractor | unknown
  "note": "Street cleaning — vehicle will be towed"
}
```

## `free_periods[]` — structured, uniform shape

`free_periods` is an array of objects with a **uniform shape**: every entry has `days`, `start`,
and `end`. Holiday-based free periods are NOT included here — they go in `free_on_holidays[]`.
This keeps the array's shape consistent so the resolver can iterate without branching.

```json
"free_periods": [
  { "days": ["Sun"], "start": "00:00", "end": "24:00" },
  { "days": ["Mon","Tue","Wed","Thu","Fri","Sat"], "start": "20:00", "end": "24:00" },
  { "days": ["Mon","Tue","Wed","Thu","Fri","Sat"], "start": "00:00", "end": "08:00" }
],
"free_on_holidays": ["boston_meters"]
```

`free_periods_summary` is derived from both arrays for UI display and is never the source of truth.

---

## `demand_signals{}` — ML-learnable fields

These fields are updated by a separate pipeline (payment transaction data, BTD citation extracts,
OSM POI queries) and written as a sub-object so that pipeline can update them without touching the
rules data. All fields are nullable — a missing `demand_signals` object means no signal data has
been computed yet (absence ≠ zero demand).

```json
"demand_signals": {
  "demand_index": 0.74,
  "citation_rate_per_space_month": 2.1,
  "avg_dwell_minutes": 47,
  "utilization_rate": 0.68,
  "nearby_poi_count_500m": 12,
  "transit_proximity_m": 180,
  "demand_signals_date": "2026-05-01"
}
```

| Field | Type | Description | ML use |
|---|---|---|---|
| `demand_index` | float 0–1 | Normalized demand score — transactions per space-hour, scaled to [0,1] relative to dataset mean | Primary demand feature; target variable for occupancy forecasting models |
| `citation_rate_per_space_month` | float | Average BTD citations per space per month (from open Boston citation data joined on block face) | Proxy for enforcement intensity; key feature for "citation risk" classifier |
| `avg_dwell_minutes` | float | Mean paid session length in minutes (from payment transaction data) | Turnover/availability signal; input to space-availability nowcast |
| `utilization_rate` | float 0–1 | Fraction of paid time windows where an active payment was on record (kiosk/app data) | Direct occupancy proxy; best single feature for "how busy is this area" |
| `nearby_poi_count_500m` | int | Count of major POIs (restaurants, offices, hospitals, venues) within 500 m of this record | Demand driver; high POI density → elevated baseline demand independent of time |
| `transit_proximity_m` | int | Walking distance in meters to nearest MBTA stop | "Park-and-ride" signal; high transit proximity → different demand curve shape (commuter peak) |
| `demand_signals_date` | date | Date these signals were last computed | Staleness flag for the ML pipeline; exclude stale records from model training |

**ML targets these fields enable:**
- **Occupancy prediction** — regression: `utilization_rate` ~ `demand_index` + `nearby_poi_count_500m` + time-of-day + day-of-week
- **Citation risk** — classification: P(citation) ~ `citation_rate_per_space_month` + active restriction schedule + `enforcement_agency`
- **Availability nowcast** — `avg_dwell_minutes` + `utilization_rate` → estimated spaces currently free (Little's Law approximation)
- **Demand elasticity** — if `pricing_rules[]` rate has changed historically, `demand_index` before/after enables price-elasticity estimation

---

## Free periods vs. time limits — a critical distinction

**"Free" and "no time limit" are not the same thing and must not be conflated.**

| Situation | `is_free` | time limit applies? |
|---|---|---|
| Paid window (Mon–Sat 8a–6p) | false | yes — `max_minutes` enforced |
| Free overnight / Sunday (meter off) | true | **no** — BTD does not enforce a stay limit when the meter is not running |
| Free holiday (meter suspended) | true | **no** — same: meter off = enforcement off |
| Permit-only window (meter off, permit required) | true (for cost) | depends on posted signs — usually none, but flag with `data_completeness` |

**Resolver rule:** whenever the current time falls within a `free_periods[]` window OR today is in
`free_on_holidays[]`, the overlay MUST set `current_max_minutes: null` and suppress any `max_minutes`
warning. Surfacing "2-hour max" during a free Sunday is a false alarm and erodes user trust.

```js
if (isHolidayFree(spot, now, holidayTable)) {
  // Step 1 — holiday overrides everything
  active_now.is_free = true;
  active_now.current_max_minutes = null;
  active_now.current_rate_per_hour = 0.00;
} else if (isFreeNow(spot.free_periods, now)) {
  // Step 2 — free period window
  active_now.is_free = true;
  active_now.current_max_minutes = null;
  active_now.current_rate_per_hour = 0.00;
} else {
  // Step 3 — evaluate pricing rules
  const rule = matchPricingRule(spot.pricing_rules, now);
  active_now.is_free = rule?.free ?? false;
  active_now.current_max_minutes = rule?.max_minutes ?? null;
  active_now.current_rate_per_hour = rule?.rate_per_hour ?? null;
}
// Step 4 — always overlay active restrictions
active_now.active_restrictions = getActiveRestrictions(spot.restrictions, now);
```

---

## `active_now` — computed CLIENT-SIDE in the overlay (NOT a stored dataset field)
The overlay computes this live from `pricing_rules[]` + `restrictions[]` + holiday table.
Use IANA timezone `America/New_York` — never a hardcoded offset (DST changes it).
```js
{
  "evaluated_at": "<now in America/New_York, DST-aware — not literal -04:00>",
  "is_free": true,
  "current_rate_per_hour": 0.00,
  "current_max_minutes": null,
  "pay_with": [                             // array — a spot may support multiple apps simultaneously
    { "app": "ParkBoston", "payment_id": "450001" },
    { "app": "pay_and_display" }            // payment_id omitted — not required for pay-and-display or gate
  ],
  "active_restrictions": [
    {"rule_type": "permit", "note": "Resident-permit-only after 6pm"}
  ],
  "verdict": "Free now (Sunday) — but check posted signs",
  "warnings": ["Possible undigitized permit/sign rules — verify on site"]
}
```
`pay_with[].payment_id` is **optional** — omit it when the payment method does not require an app
code (e.g. `pay_and_display`, `gate`).

During build, the same object is emitted to a separate `qa_active_now.json` (labeled
`active_now_at_build_time`) for resolver QA only — never written to the shipping outputs.

---

## Payment-app mapping logic
- `VENDOR = IPS` (single-space)        → `payment_app: ["ParkBoston"]`, `payment_id: METER_ID`, methods: app/card/coin
- `VENDOR = Parkeon` (multi-space)     → `payment_app: ["pay_and_display","ParkBoston"]`, methods: card/coin/app
- `type = mbta_lot`                    → `payment_app: ["PayByPhone"]`, `payment_id: <4-digit lot code>`, methods: app
- `type = public_garage / private_lot`→ `payment_app: ["gate"]` (or add `"SpotHero"` if `spothero_listing_verified_date` is non-null and recent), methods: gate/app

---

## Known limits (carried as flags, not hidden)
- Pricing is a snapshot; stamped with `source_date` + `last_updated`, refreshable from Analyze Boston API.
- Resident-permit and street-cleaning detail comes from sign data Boston has not fully digitized → `data_completeness`.
- Accessible coverage = commercial corridors only (Boston OSAP) + partial OSM; absence ≠ none.
- MBTA per-lot rates from most-recent published table → `needs_verification` on those records.
- `spothero_listing_verified_date` reflects the last check date only — SpotHero availability changes daily; treat as a hint, not a live inventory.
- `demand_signals` fields are null until the ML pipeline runs; absence ≠ zero demand.
- `space_count` for on-street records is an estimate; absence ≠ zero spaces.
- `street_side` is null for off-street facilities and for on-street records where neither the BTD SIDE column nor a MassGIS join was available.
- `lat`/`lon` on linestring/polygon records are centroids only — do not use for turn-by-turn routing; use the full GeoJSON geometry instead.
