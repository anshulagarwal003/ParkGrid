"""
Boston Parking Dataset Builder
Reads Parking_Meters.csv and produces:
  - boston_parking.geojson
  - boston_parking.csv
  - holidays.json
  - qa_active_now.json  (resolver QA snapshot, not for shipping)
"""

import csv
import json
import re
import uuid
import math
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).parent
INPUT_CSV = BASE_DIR / "Parking_Meters.csv"
OUT_GEOJSON = BASE_DIR / "boston_parking.geojson"
OUT_CSV = BASE_DIR / "boston_parking.csv"
OUT_HOLIDAYS = BASE_DIR / "holidays.json"
OUT_QA = BASE_DIR / "qa_active_now.json"

SCHEMA_VERSION = "1"
TODAY = date.today().isoformat()
TZ = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Boston meter holidays (city-observed; meters suspended on these dates)
# ---------------------------------------------------------------------------
BOSTON_METER_HOLIDAYS = {
    "name": "boston_meters",
    "description": "City of Boston parking meter holidays — meters suspended, parking free",
    "source": "https://www.boston.gov/departments/transportation/parking-meters",
    "dates_2026": [
        "2026-01-01",  # New Year's Day
        "2026-01-19",  # Martin Luther King Jr. Day
        "2026-02-16",  # Presidents' Day
        "2026-04-20",  # Patriots' Day
        "2026-05-25",  # Memorial Day
        "2026-07-04",  # Independence Day
        "2026-09-07",  # Labor Day
        "2026-10-12",  # Columbus Day
        "2026-11-11",  # Veterans Day
        "2026-11-26",  # Thanksgiving
        "2026-12-25",  # Christmas
    ],
    "note": "Dates shift when holiday falls on weekend. Verify annually against BTD announcements."
}


# ---------------------------------------------------------------------------
# Day abbreviation mappings
# ---------------------------------------------------------------------------
DAY_RANGE_MAP = {
    "MON-SAT": ["mon","tue","wed","thu","fri","sat"],
    "MON-FRI": ["mon","tue","wed","thu","fri"],
    "SAT":     ["sat"],
    "SUN":     ["sun"],
    "SUN-SAT": ["sun","mon","tue","wed","thu","fri","sat"],
    "MON":     ["mon"],
}

def expand_days(day_str: str) -> list[str]:
    day_str = day_str.strip()
    return DAY_RANGE_MAP.get(day_str, [day_str])


def parse_time(t: str) -> str:
    """Convert '08:00AM' or '24:00AM' or '00:00AM' -> 'HH:MM'."""
    t = t.strip().upper()
    if t.startswith("24:00"):
        return "24:00"
    if t.startswith("00:00"):
        return "00:00"
    try:
        dt = datetime.strptime(t, "%I:%M%p")
        return dt.strftime("%H:%M")
    except ValueError:
        return t


# ---------------------------------------------------------------------------
# PAY_POLICY parser
# e.g. "08:00AM-08:00PM MON-SAT $0.25 120"
# e.g. "08:00AM-06:00PM MON-FRI $0.25 120, 08:00AM-08:00PM SAT $0.25 120"
# ---------------------------------------------------------------------------
POLICY_RE = re.compile(
    r"(\d{2}:\d{2}[AP]M)-(\d{2}:\d{2}[AP]M)\s+([\w-]+)\s+\$?([\d.]+)\s+(\d+)"
)

def parse_pay_policy(raw: str) -> list[dict]:
    if not raw or not raw.strip():
        return []
    rules = []
    for i, segment in enumerate(raw.split(",")):
        segment = segment.strip()
        m = POLICY_RE.match(segment)
        if not m:
            continue
        start_raw, end_raw, day_raw, rate_raw, max_raw = m.groups()
        rules.append({
            "id": f"paid_{i}",
            "priority": 100 + i * 10,
            "active": True,
            "days": expand_days(day_raw),
            "time_window": {"start": parse_time(start_raw), "end": parse_time(end_raw)},
            "rate": {
                "kind": "hourly",
                "price_per_hour": {"currency": "USD", "amount": float(rate_raw)},
                "billing_increment_minutes": 15,
            },
            "constraints": {"max_session_minutes": int(max_raw)},
        })
    return rules


# ---------------------------------------------------------------------------
# PARK_NO_PAY parser
# e.g. "00:00AM-24:00AM SUN, 00:00AM-08:00AM MON-SAT, 08:00PM-24:00AM MON-SAT"
# ---------------------------------------------------------------------------
FREE_RE = re.compile(
    r"(\d{2}:\d{2}[AP]M)-(\d{2}:\d{2}[AP]M)\s+([\w-]+)"
)

def parse_park_no_pay(raw: str) -> list[dict]:
    if not raw or not raw.strip():
        return []
    rules = []
    for i, segment in enumerate(raw.split(",")):
        segment = segment.strip()
        m = FREE_RE.match(segment)
        if not m:
            continue
        start_raw, end_raw, day_raw = m.groups()
        rules.append({
            "id": f"free_{i}",
            "priority": 50 + i * 10,
            "active": True,
            "days": expand_days(day_raw),
            "time_window": {"start": parse_time(start_raw), "end": parse_time(end_raw)},
            "rate": {"kind": "free"},
        })
    return rules


# ---------------------------------------------------------------------------
# Pricing summary (human-readable) — kept alongside machine rules
# ---------------------------------------------------------------------------
_DAY_ORDER = ["mon","tue","wed","thu","fri","sat","sun"]
_DAY_CAPS  = {d: d.capitalize() for d in _DAY_ORDER}

def _day_label(days: list[str]) -> str:
    idxs = sorted(_DAY_ORDER.index(d) for d in days if d in _DAY_ORDER)
    if not idxs:
        return "/".join(days)
    if len(idxs) >= 3 and idxs == list(range(idxs[0], idxs[-1] + 1)):
        return f"{_DAY_CAPS[_DAY_ORDER[idxs[0]]]}-{_DAY_CAPS[_DAY_ORDER[idxs[-1]]]}"
    return "/".join(_DAY_CAPS.get(d, d) for d in days)

def build_pricing_summary(paid_rules: list[dict], free_rules: list[dict]) -> str:
    parts = []
    for r in paid_rules:
        tw = r["time_window"]
        amount = r["rate"]["price_per_hour"]["amount"]
        max_min = r.get("constraints", {}).get("max_session_minutes")
        hours = max_min // 60 if max_min and max_min % 60 == 0 else None
        limit = f" ({hours}h max)" if hours else (f" ({max_min}min max)" if max_min else "")
        parts.append(f"{_day_label(r['days'])} {tw['start']}-{tw['end']} ${amount:.2f}/hr{limit}")
    for r in free_rules:
        tw = r["time_window"]
        if tw["start"] == "00:00" and tw["end"] in ("24:00", "23:59"):
            parts.append(f"{_day_label(r['days'])} free")
        else:
            parts.append(f"{_day_label(r['days'])} {tw['start']}-{tw['end']} free")
    return "; ".join(parts) if parts else "unknown"


# ---------------------------------------------------------------------------
# Derive payment_app and payment_methods from VENDOR
# ---------------------------------------------------------------------------
def payment_info(vendor: str, meter_type: str):
    v = vendor.strip().upper()
    if v == "PARKEON":
        return (
            ["pay_and_display", "ParkBoston"],
            ["card", "coin", "app"],
        )
    elif v == "IPS":
        return (
            ["ParkBoston"],
            ["app", "card", "coin"],
        )
    else:
        return (["unknown"], ["unknown"])


# ---------------------------------------------------------------------------
# street_side from DIR column
# ---------------------------------------------------------------------------
def parse_street_side(dir_val: str) -> str:
    d = dir_val.strip().upper()
    return d if d in ("N", "S", "E", "W") else "unknown"


# ---------------------------------------------------------------------------
# Rough Boston neighborhood lookup by lat/lon bounding boxes
# (Good enough for filtering; replace with point-in-polygon at build time
#  using Boston neighborhood shapefile from Analyze Boston)
# ---------------------------------------------------------------------------
NEIGHBORHOODS = [
    ("Back Bay",        42.346, 42.358, -71.087, -71.066),
    ("Beacon Hill",     42.357, 42.363, -71.074, -71.059),
    ("Downtown",        42.353, 42.362, -71.063, -71.051),
    ("North End",       42.362, 42.373, -71.058, -71.047),
    ("South End",       42.335, 42.352, -71.083, -71.059),
    ("Fenway",          42.342, 42.354, -71.107, -71.087),
    ("Kenmore",         42.347, 42.353, -71.102, -71.092),
    ("Charlestown",     42.370, 42.385, -71.065, -71.042),
    ("East Boston",     42.362, 42.390, -71.044, -70.998),
    ("South Boston",    42.325, 42.345, -71.065, -71.020),
    ("Dorchester",      42.280, 42.325, -71.090, -71.040),
    ("Roxbury",         42.310, 42.340, -71.105, -71.070),
    ("Jamaica Plain",   42.295, 42.326, -71.127, -71.095),
    ("Allston",         42.348, 42.362, -71.140, -71.110),
    ("Brighton",        42.338, 42.358, -71.167, -71.135),
    ("Hyde Park",       42.240, 42.280, -71.140, -71.095),
    ("West Roxbury",    42.268, 42.310, -71.175, -71.130),
    ("Mattapan",        42.265, 42.300, -71.105, -71.065),
    ("Chinatown",       42.348, 42.356, -71.068, -71.056),
    ("Theater District",42.348, 42.354, -71.073, -71.062),
    ("Seaport",         42.345, 42.358, -71.055, -71.033),
]

def lookup_neighborhood(lat: float, lon: float) -> str:
    for name, lat_min, lat_max, lon_min, lon_max in NEIGHBORHOODS:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return name
    return "unknown"


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------
def build_record(row: dict, idx: int) -> dict:
    meter_id = row.get("METER_ID", "").strip()
    vendor = row.get("VENDOR", "").strip()
    meter_type = row.get("METER_TYPE", "").strip()

    try:
        lat = float(row["LATITUDE"])
        lon = float(row["LONGITUDE"])
    except (ValueError, KeyError):
        lat, lon = None, None

    payment_app, payment_methods = payment_info(vendor, meter_type)

    paid_rules = parse_pay_policy(row.get("PAY_POLICY", ""))
    free_rules = parse_park_no_pay(row.get("PARK_NO_PAY", ""))

    street = row.get("STREET", "").strip()
    blk = row.get("BLK_NO", "").strip()
    address = f"{blk} {street}".strip() if blk and blk not in ("", "0") else street

    try:
        space_count = int(row.get("NUMBEROFSPACES") or 1)
    except ValueError:
        space_count = 1

    neighborhood = lookup_neighborhood(lat, lon) if lat and lon else "unknown"

    restrictions = []
    if row.get("STREET_CLEANING", "").strip():
        restrictions.append({
            "rule_type": "street_cleaning",
            "days": [],
            "start": None,
            "end": None,
            "season_start": "04-01",
            "season_end": "11-30",
            "consequence": "tow",
            "enforcement_agency": "BTD",
            "note": row["STREET_CLEANING"].strip(),
        })
    if row.get("TOW_AWAY", "").strip():
        restrictions.append({
            "rule_type": "tow_zone",
            "days": [],
            "start": None,
            "end": None,
            "consequence": "tow",
            "enforcement_agency": "BTD",
            "note": row["TOW_AWAY"].strip(),
        })

    permit_zone = row.get("G_PM_ZONE", "").strip() or None
    if permit_zone:
        restrictions.append({
            "rule_type": "permit",
            "days": ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],
            "start": None,
            "end": None,
            "note": f"Permit zone {permit_zone} — verify schedule on posted signs",
        })

    spot_id = f"meter_{meter_id}" if meter_id else f"meter_obj_{idx}"

    record = {
        "schema_version": SCHEMA_VERSION,
        "spot_id": spot_id,
        "parent_facility_id": f"block_{row.get('BLK_NO','').strip()}_{street.replace(' ','_')}",
        "geometry_type": "point",
        "payment_id": meter_id,
        "payment_app": payment_app,
        "payment_methods": payment_methods,
        "name": street,
        "address": address,
        "street_side": parse_street_side(row.get("DIR", "")),
        "neighborhood": neighborhood,
        "btd_district": row.get("G_DISTRICT", "").strip() or None,
        "type": "on_street_meter",
        "ownership": "public",
        "lat": lat,
        "lon": lon,
        "parking_policy": {
            "timezone": "America/New_York",
            "rules": paid_rules + free_rules,
            "pricing_summary": build_pricing_summary(paid_rules, free_rules),
            "pricing_version": 1,
            "updated_at": TODAY + "T00:00:00Z",
        },
        "free_on_holidays": ["boston_meters"],
        "space_count": space_count,
        "capacity": None,
        "restrictions": restrictions,
        "permit_zone": permit_zone,
        "snow_emergency_tow": False,
        "accessible": False,
        "accessible_spaces": 0,
        "ev_charging": False,
        "holiday_calendar": "boston_meters",
        "demand_signals": None,
        "spothero_listing_verified_date": None,
        "source": "analyze_boston",
        "source_url": "https://data.boston.gov/dataset/parking-meters",
        "source_date": "2026-06-14",
        "last_updated": TODAY,
        "needs_verification": False,
        "verification_method": "official_source",
        "data_completeness": "partial",
    }
    return record


def record_to_feature(rec: dict) -> dict:
    """Wrap a record as a GeoJSON Feature."""
    lat, lon = rec.get("lat"), rec.get("lon")
    geometry = (
        {"type": "Point", "coordinates": [lon, lat]}
        if lat is not None and lon is not None
        else None
    )
    # GeoJSON properties: everything except lat/lon (those are in geometry)
    props = {k: v for k, v in rec.items() if k not in ("lat", "lon")}
    return {"type": "Feature", "geometry": geometry, "properties": props}


CSV_EXPORT_FIELDS = [
    "spot_id", "payment_id", "payment_app", "name", "address",
    "street_side", "neighborhood", "btd_district", "type", "ownership",
    "lat", "lon", "pricing_summary", "space_count", "permit_zone",
    "snow_emergency_tow", "accessible", "ev_charging",
    "source", "source_date", "last_updated", "data_completeness",
]


def flatten_for_csv(rec: dict) -> dict:
    flat = {f: rec.get(f, "") for f in CSV_EXPORT_FIELDS}
    for key in ("payment_app",):
        v = flat.get(key)
        if isinstance(v, list):
            flat[key] = "|".join(v)
    flat["pricing_summary"] = (rec.get("parking_policy") or {}).get("pricing_summary", "")
    return flat


# ---------------------------------------------------------------------------
# Active-now resolver (for QA snapshot only)
# ---------------------------------------------------------------------------
def resolve_active_now(rec: dict, now: datetime, holiday_dates: set[str]) -> dict:
    today_str = now.date().isoformat()
    day_name = now.strftime("%a")  # Mon, Tue, ...
    hm = now.strftime("%H:%M")

    is_holiday = today_str in holiday_dates
    is_free = False
    rate = None
    max_min = None

    if is_holiday:
        is_free = True
    else:
        policy = rec.get("parking_policy") or {}
        rules = sorted(policy.get("rules", []), key=lambda r: r.get("priority", 0), reverse=True)
        day_lower = day_name.lower()
        for rule in rules:
            if not rule.get("active", True):
                continue
            if day_lower not in rule.get("days", []):
                continue
            tw = rule.get("time_window", {})
            s, e = tw.get("start", "00:00"), tw.get("end", "24:00")
            if s <= hm and (hm < e or e in ("24:00", "23:59") and hm >= s):
                rate_info = rule.get("rate", {})
                if rate_info.get("kind") == "free":
                    is_free = True
                elif rate_info.get("kind") == "hourly":
                    rate = rate_info.get("price_per_hour", {}).get("amount")
                    max_min = rule.get("constraints", {}).get("max_session_minutes")
                break

    pay_with = []
    for app in rec.get("payment_app", []):
        entry: dict = {"app": app}
        if app in ("ParkBoston", "PayByPhone", "ParkMobile", "SpotHero"):
            pid = rec.get("payment_id")
            if pid:
                entry["payment_id"] = pid
        pay_with.append(entry)

    return {
        "spot_id": rec["spot_id"],
        "active_now_at_build_time": {
            "evaluated_at": now.isoformat(),
            "is_free": is_free,
            "is_holiday": is_holiday,
            "current_rate_per_hour": 0.00 if is_free else rate,
            "current_max_minutes": None if is_free else max_min,
            "pay_with": pay_with,
            "active_restrictions": rec.get("restrictions", []),
            "verdict": (
                f"Free ({'holiday' if is_holiday else 'free period'})" if is_free
                else f"${rate:.2f}/hr, {max_min}min max" if rate else "Unknown"
            ),
            "warnings": (
                ["Possible undigitized permit/sign rules — verify on site"]
                if rec.get("data_completeness") in ("partial","low") else []
            ),
        }
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    print(f"Reading {INPUT_CSV} ...")
    rows = []
    with open(INPUT_CSV, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Skip inactive records
            if row.get("METER_STATE","").strip().upper() not in ("ACTIVE", ""):
                continue
            rows.append(row)
    print(f"  {len(rows)} active meter records")

    records = []
    for i, row in enumerate(rows):
        records.append(build_record(row, i))

    # --- GeoJSON ---
    features = [record_to_feature(r) for r in records]
    geojson = {
        "type": "FeatureCollection",
        "name": "boston_parking",
        "generated": TODAY,
        "schema_version": SCHEMA_VERSION,
        "record_count": len(features),
        "features": features,
    }
    with open(OUT_GEOJSON, "w", encoding="utf-8") as f:
        json.dump(geojson, f, indent=2, default=str)
    print(f"  Written: {OUT_GEOJSON}  ({len(features)} features)")

    # --- CSV ---
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_EXPORT_FIELDS)
        writer.writeheader()
        for rec in records:
            writer.writerow(flatten_for_csv(rec))
    print(f"  Written: {OUT_CSV}")

    # --- holidays.json ---
    holidays_payload = {
        "generated": TODAY,
        "calendars": [BOSTON_METER_HOLIDAYS],
    }
    with open(OUT_HOLIDAYS, "w", encoding="utf-8") as f:
        json.dump(holidays_payload, f, indent=2)
    print(f"  Written: {OUT_HOLIDAYS}")

    # --- QA active-now snapshot ---
    now_et = datetime.now(tz=TZ)
    holiday_dates = set(BOSTON_METER_HOLIDAYS["dates_2026"])
    qa_snapshots = [resolve_active_now(r, now_et, holiday_dates) for r in records[:50]]
    qa = {
        "WARNING": "QA file only — never ship this. Contains active_now_at_build_time snapshots.",
        "evaluated_at": now_et.isoformat(),
        "sample_size": len(qa_snapshots),
        "snapshots": qa_snapshots,
    }
    with open(OUT_QA, "w", encoding="utf-8") as f:
        json.dump(qa, f, indent=2, default=str)
    print(f"  Written: {OUT_QA}  (first 50 records, QA only)")

    # --- Stats summary ---
    sides = {}
    hoods = {}
    for r in records:
        sides[r["street_side"]] = sides.get(r["street_side"], 0) + 1
        hoods[r["neighborhood"]] = hoods.get(r["neighborhood"], 0) + 1

    print("\n--- Street side breakdown ---")
    for k, v in sorted(sides.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    print("\n--- Top neighborhoods ---")
    for k, v in sorted(hoods.items(), key=lambda x: -x[1])[:10]:
        print(f"  {k}: {v}")
    print("\nDone.")


if __name__ == "__main__":
    main()
