"""
build_cambridge.py — Cambridge, MA Parking Dataset Builder

Sources (from https://www.cambridgema.gov/iwantto/parkacarincambridge/map):
  Metered Parking Spaces     — MapServer  (polygon footprints, ~3,310 spaces)
  Disability Parking Spaces  — FeatureServer (points, 183 spaces)
  City-Owned Garages & Lots  — FeatureServer (points, 39 garages/lots)
  EV Chargers                — OSM Overpass (amenity=charging_station)

Outputs:
  cambridge_parking.geojson
  cambridge_parking.csv

Run:
    python build_cambridge.py
    python build_cambridge.py --force   # re-download even if cached

NOTE: Meter service days default to Mon-Sat (no day field in source data).
"""

import csv
import json
import math
import re
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

try:
    import requests
except ImportError:
    sys.exit("Install requests:  pip install requests")

BASE_DIR         = Path(__file__).parent
OUT_GEOJSON      = BASE_DIR / "cambridge_parking.geojson"
OUT_CSV          = BASE_DIR / "cambridge_parking.csv"
BOSTON_CSV_PATH  = BASE_DIR / "boston_parking.csv"
RAW_DIR          = BASE_DIR / "raw"
RAW_DIR.mkdir(exist_ok=True)

SCHEMA_VERSION = "1"
TODAY = __import__("datetime").date.today().isoformat()

_SOURCE_URL    = "https://www.cambridgema.gov/iwantto/parkacarincambridge/map"
_OVERPASS_URL  = "https://overpass-api.de/api/interpreter"
_CAMBRIDGE_BBOX = "42.34,-71.16,42.40,-71.06"
_OSM_EV_FILE   = RAW_DIR / "cambridge_ev_osm.json"

SOURCES = {
    "meters": {
        "url": "https://gisserver.cambridgema.gov/arcgis/rest/services/TrafficAGOLLayers/MapServer/10",
        "file": RAW_DIR / "cambridge_meters.geojson",
        "desc": "Cambridge Metered Parking Spaces",
        "page_size": 1000,
    },
    "accessible": {
        "url": "https://services1.arcgis.com/WnzC35krSYGuYov4/arcgis/rest/services/Public_Handicap_Parking_Spaces/FeatureServer/0",
        "file": RAW_DIR / "cambridge_accessible.geojson",
        "desc": "Cambridge Disability Parking Spaces",
        "page_size": 1000,
    },
    "garages": {
        "url": "https://services1.arcgis.com/WnzC35krSYGuYov4/arcgis/rest/services/Commercial_Parking/FeatureServer/0",
        "file": RAW_DIR / "cambridge_garages.geojson",
        "desc": "Cambridge Garages and Lots",
        "page_size": 500,
    },
}

# ---------------------------------------------------------------------------
# Neighborhoods (Cambridge village areas by bounding box)
# ---------------------------------------------------------------------------
NEIGHBORHOODS = [
    ("Harvard Square",          42.370, 42.380, -71.127, -71.117),
    ("Central Square",          42.362, 42.375, -71.112, -71.100),
    ("Kendall Square",          42.358, 42.370, -71.098, -71.082),
    ("Inman Square",            42.370, 42.382, -71.111, -71.097),
    ("Porter Square",           42.382, 42.397, -71.127, -71.110),
    ("East Cambridge",          42.362, 42.380, -71.098, -71.083),
    ("MIT",                     42.353, 42.362, -71.102, -71.082),
    ("Cambridgeport",           42.353, 42.368, -71.120, -71.103),
    ("Riverside",               42.353, 42.368, -71.136, -71.118),
    ("Area IV",                 42.358, 42.368, -71.115, -71.103),
    ("Wellington-Harrington",   42.368, 42.383, -71.100, -71.083),
    ("Agassiz",                 42.378, 42.390, -71.140, -71.122),
    ("North Cambridge",         42.388, 42.406, -71.142, -71.108),
]

def lookup_neighborhood(lat: float, lon: float) -> str:
    for name, s, n, w, e in NEIGHBORHOODS:
        if s <= lat <= n and w <= lon <= e:
            return name
    return "Cambridge"


# ---------------------------------------------------------------------------
# Download ArcGIS layer (paginated, GeoJSON)
# ---------------------------------------------------------------------------
def download_source(key: str, force: bool = False) -> Path | None:
    src = SOURCES[key]
    out = src["file"]
    if out.exists() and not force:
        size_kb = out.stat().st_size // 1024
        print(f"  [cached] {src['desc']} ({size_kb} KB) -> {out.name}")
        return out

    print(f"  [downloading] {src['desc']} ...")
    headers = {"User-Agent": "park-grid-cambridge/1.0"}
    all_features = []
    offset = 0
    page = src["page_size"]

    while True:
        url = (
            f"{src['url']}/query"
            f"?f=geojson&where=1%3D1&outFields=*&outSR=4326"
            f"&resultOffset={offset}&resultRecordCount={page}"
        )
        try:
            r = requests.get(url, headers=headers, timeout=60, verify=False)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  [ERROR] {e}")
            return None

        feats = data.get("features", [])
        all_features.extend(feats)
        if len(feats) < page:
            break
        offset += page
        time.sleep(0.3)

    gj = {"type": "FeatureCollection", "features": all_features}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(gj, f)
    print(f"  [ok] {len(all_features)} features -> {out.name}")
    return out


# ---------------------------------------------------------------------------
# Download OSM EV chargers via Overpass
# ---------------------------------------------------------------------------
def download_osm_ev(force: bool = False) -> dict | None:
    if _OSM_EV_FILE.exists() and not force:
        size_kb = _OSM_EV_FILE.stat().st_size // 1024
        print(f"  [cached] OSM EV chargers ({size_kb} KB) -> {_OSM_EV_FILE.name}")
        with open(_OSM_EV_FILE, encoding="utf-8") as f:
            return json.load(f)

    print("  [downloading] OSM EV chargers (Overpass) ...")
    query = f"""
[out:json][timeout:30];
node["amenity"="charging_station"]({_CAMBRIDGE_BBOX});
out body;
"""
    try:
        r = requests.post(
            _OVERPASS_URL,
            data={"data": query},
            headers={"User-Agent": "park-grid-cambridge/1.0"},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [ERROR] {e}")
        return None

    with open(_OSM_EV_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)
    n = len([e for e in data.get("elements", []) if e.get("type") == "node"])
    print(f"  [ok] {n} EV nodes -> {_OSM_EV_FILE.name}")
    return data


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def polygon_centroid(geom: dict) -> tuple:
    try:
        coords = geom.get("coordinates", [])
        ring = coords[0]
        if geom.get("type") == "MultiPolygon":
            ring = coords[0][0]
        lons = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        return sum(lons) / len(lons), sum(lats) / len(lats)
    except Exception:
        return None, None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    p = math.pi / 180
    a = (
        math.sin((lat2 - lat1) * p / 2) ** 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
_OPHOURS_RE  = re.compile(r"(\d{1,2}:\d{2})\s*(AM|PM)\s*[-–]\s*(\d{1,2}:\d{2})\s*(AM|PM)", re.IGNORECASE)
_MAXTIME_RE  = re.compile(r"(\d+(?:\.\d+)?)\s*(hour|minute|min)\b", re.IGNORECASE)

def _to_24h(time_str: str, ampm: str) -> str:
    h, m = map(int, time_str.split(":"))
    ampm = ampm.upper()
    if ampm == "PM" and h != 12:
        h += 12
    elif ampm == "AM" and h == 12:
        h = 0
    return f"{h:02d}:{m:02d}"

def parse_operation_hours(s: str) -> tuple[str, str]:
    m = _OPHOURS_RE.search((s or "").strip())
    if m:
        return _to_24h(m.group(1), m.group(2)), _to_24h(m.group(3), m.group(4))
    return "08:00", "18:00"  # Cambridge default

def parse_max_time(s: str) -> int | None:
    m = _MAXTIME_RE.search((s or "").strip())
    if not m:
        return None
    val  = float(m.group(1))
    unit = m.group(2).lower()
    return int(val * 60) if unit == "hour" else int(val)

def parse_billing_increment(minutes_per_quarter) -> int:
    """MinutesPer field = minutes per $0.25. Convert to billing_increment_minutes."""
    try:
        return max(1, round(float(minutes_per_quarter)))
    except (TypeError, ValueError):
        return 15  # default


# ---------------------------------------------------------------------------
# Build record — Metered Parking Space (polygon footprint → centroid)
# ---------------------------------------------------------------------------
def build_meter_record(feat: dict, idx: int) -> dict | None:
    p    = feat.get("properties") or {}
    geom = feat.get("geometry") or {}

    status = (p.get("Status") or "").strip()
    if status.lower() == "out of service":
        return None

    # Polygon centroid
    if geom.get("type") == "Point":
        coords = geom.get("coordinates", [])
        if len(coords) < 2:
            return None
        lon, lat = float(coords[0]), float(coords[1])
    else:
        lon, lat = polygon_centroid(geom)
        if lat is None:
            return None

    space_id   = (p.get("SPACE_ID") or "").strip()
    pbp_zone   = p.get("PbyP_Zone")
    rate_raw   = p.get("Rate")
    max_time   = (p.get("MaxTime") or "").strip()
    op_hours   = (p.get("OperationHours") or "").strip()
    min_per    = p.get("MinutesPer")
    smart      = (p.get("SmartMeter") or "").strip().lower() == "yes"

    try:
        rate = float(rate_raw) if rate_raw is not None else 0.0
    except (TypeError, ValueError):
        rate = 0.0

    start_str, end_str = parse_operation_hours(op_hours)
    max_min = parse_max_time(max_time)
    billing = parse_billing_increment(min_per)
    days    = ["mon", "tue", "wed", "thu", "fri", "sat"]  # Mon-Sat assumption

    paid_rules = []
    if rate > 0:
        rule = {
            "id": "paid_0",
            "priority": 100,
            "active": True,
            "days": days,
            "time_window": {"start": start_str, "end": end_str},
            "rate": {
                "kind": "hourly",
                "price_per_hour": {"currency": "USD", "amount": rate},
                "billing_increment_minutes": billing,
            },
        }
        if max_min:
            rule["constraints"] = {"max_session_minutes": max_min}
        paid_rules.append(rule)

    limit_label = f" ({max_time} max)" if max_time else ""
    summary = (
        f"Mon-Sat {start_str}-{end_str} ${rate:.2f}/hr{limit_label}"
        if rate > 0 else op_hours or "See posted signs"
    )

    payment_id = str(int(pbp_zone)) if pbp_zone is not None else None
    spot_id    = f"cambridge_meter_{space_id}" if space_id else f"cambridge_meter_obj_{idx}"

    return {
        "schema_version": SCHEMA_VERSION,
        "spot_id": spot_id,
        "geometry_type": "point",
        "payment_id": payment_id,
        "payment_app": ["PayByPhone"],
        "payment_methods": ["coin", "card", "app"],
        "name": space_id,
        "address": None,
        "street_side": "unknown",
        "neighborhood": lookup_neighborhood(lat, lon),
        "btd_district": None,
        "municipality": "Cambridge",
        "type": "on_street_meter",
        "ownership": "public",
        "lat": lat,
        "lon": lon,
        "parking_policy": {
            "timezone": "America/New_York",
            "rules": paid_rules,
            "pricing_summary": summary,
            "pricing_version": 1,
            "updated_at": TODAY + "T00:00:00Z",
        },
        "restrictions": [],
        "free_on_holidays": [],
        "holiday_calendar": "none",
        "space_count": 1,
        "capacity": None,
        "overnight_rental_spaces": None,
        "overnight_guest_spaces": None,
        "lot_number": None,
        "permit_zone": None,
        "snow_emergency_tow": False,
        "accessible": False,
        "accessible_spaces": 0,
        "accessible_type": None,
        "ev_charging": False,
        "ev_charger_count": None,
        "ev_network": None,
        "demand_signals": None,
        "source": "cambridge_gis",
        "source_url": _SOURCE_URL,
        "source_date": TODAY,
        "last_updated": TODAY,
        "needs_verification": False,
        "verification_method": "official_source",
        "data_completeness": "partial",
    }


# ---------------------------------------------------------------------------
# Build record — Disability Parking Space
# ---------------------------------------------------------------------------
def build_accessible_record(feat: dict, idx: int) -> dict | None:
    p    = feat.get("properties") or {}
    geom = feat.get("geometry") or {}

    coords = (geom.get("coordinates") or [])
    if geom.get("type") != "Point" or len(coords) < 2:
        return None
    lon, lat = float(coords[0]), float(coords[1])

    street_num  = (p.get("StreetNumber") or "").strip()
    street_name = (p.get("StreetName") or "").strip()
    side        = (p.get("SideOfStreet") or "").strip().upper()
    obj_id      = p.get("OBJECTID") or p.get("CartegraphID") or idx

    address = f"{street_num} {street_name}".strip() if street_name else None
    install_date = p.get("InstallEffectiveDate")
    if install_date:
        try:
            # ArcGIS epoch ms → YYYY-MM-DD
            from datetime import datetime, timezone
            install_date = datetime.fromtimestamp(install_date / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            install_date = TODAY

    return {
        "schema_version": SCHEMA_VERSION,
        "spot_id": f"cambridge_ada_{obj_id}",
        "geometry_type": "point",
        "payment_id": None,
        "payment_app": ["free"],
        "payment_methods": ["free"],
        "name": f"Accessible Parking Space",
        "address": address,
        "street_side": side if side in ("N", "S", "E", "W") else "unknown",
        "neighborhood": lookup_neighborhood(lat, lon),
        "btd_district": None,
        "municipality": "Cambridge",
        "type": "accessible_parking",
        "ownership": "public",
        "lat": lat,
        "lon": lon,
        "parking_policy": {
            "timezone": "America/New_York",
            "rules": [
                {
                    "id": "free_0",
                    "priority": 50,
                    "active": True,
                    "days": ["mon","tue","wed","thu","fri","sat","sun"],
                    "time_window": {"start": "00:00", "end": "24:00"},
                    "rate": {"kind": "free"},
                }
            ],
            "pricing_summary": "Free — ADA designated",
            "pricing_version": 1,
            "updated_at": TODAY + "T00:00:00Z",
        },
        "restrictions": [],
        "free_on_holidays": [],
        "holiday_calendar": "none",
        "space_count": 1,
        "capacity": None,
        "overnight_rental_spaces": None,
        "overnight_guest_spaces": None,
        "lot_number": None,
        "permit_zone": None,
        "snow_emergency_tow": False,
        "accessible": True,
        "accessible_spaces": 1,
        "accessible_type": "standard_ada",
        "ev_charging": False,
        "ev_charger_count": None,
        "ev_network": None,
        "demand_signals": None,
        "source": "cambridge_gis",
        "source_url": _SOURCE_URL,
        "source_date": install_date or TODAY,
        "last_updated": TODAY,
        "needs_verification": False,
        "verification_method": "official_source",
        "data_completeness": "high",
    }


# ---------------------------------------------------------------------------
# Build record — Garage / Lot
# ---------------------------------------------------------------------------
def build_garage_record(feat: dict, idx: int) -> dict | None:
    p    = feat.get("properties") or {}
    geom = feat.get("geometry") or {}

    coords = (geom.get("coordinates") or [])
    if geom.get("type") != "Point" or len(coords) < 2:
        return None
    lon, lat = float(coords[0]), float(coords[1])

    name      = (p.get("DESCRIPTIO") or "").strip()
    address   = (p.get("ADDRESS") or "").strip() or None
    structure = (p.get("STRUCTURE") or "Garage").strip()
    owntype   = (p.get("OWNTYPE") or "").strip()
    obj_id    = p.get("OBJECTID") or p.get("Id") or idx

    try:
        capacity = int(p.get("TOTALSP") or 0) or None
    except (TypeError, ValueError):
        capacity = None

    ownership = "public" if owntype.lower() == "municipal" else "private"
    spot_id   = f"cambridge_garage_{obj_id}"

    return {
        "schema_version": SCHEMA_VERSION,
        "spot_id": spot_id,
        "geometry_type": "point",
        "payment_id": None,
        "payment_app": ["gate"],
        "payment_methods": ["card", "cash"],
        "name": name,
        "address": address,
        "street_side": None,
        "neighborhood": lookup_neighborhood(lat, lon),
        "btd_district": None,
        "municipality": "Cambridge",
        "type": "public_garage",
        "ownership": ownership,
        "lat": lat,
        "lon": lon,
        "parking_policy": {
            "timezone": "America/New_York",
            "rules": [],
            "pricing_summary": "See posted rates",
            "pricing_version": 1,
            "updated_at": TODAY + "T00:00:00Z",
        },
        "restrictions": [],
        "free_on_holidays": [],
        "holiday_calendar": "none",
        "space_count": None,
        "capacity": capacity,
        "overnight_rental_spaces": None,
        "overnight_guest_spaces": None,
        "lot_number": None,
        "permit_zone": None,
        "snow_emergency_tow": False,
        "accessible": False,
        "accessible_spaces": None,
        "accessible_type": None,
        "ev_charging": False,
        "ev_charger_count": None,
        "ev_network": None,
        "demand_signals": None,
        "source": "cambridge_gis",
        "source_url": _SOURCE_URL,
        "source_date": TODAY,
        "last_updated": TODAY,
        "needs_verification": True,
        "verification_method": "official_source",
        "data_completeness": "partial",
    }


# ---------------------------------------------------------------------------
# Add EV charger records from OSM
# ---------------------------------------------------------------------------
def add_ev_chargers(records: list[dict], osm_data: dict) -> int:
    if not osm_data:
        return 0
    nodes = [e for e in osm_data.get("elements", []) if e.get("type") == "node" and "lat" in e]
    added = 0
    existing_pts = [(r["lat"], r["lon"]) for r in records if r.get("lat") is not None]

    for node in nodes:
        lat, lon = node["lat"], node["lon"]
        if any(haversine_m(lat, lon, elat, elon) <= 25 for elat, elon in existing_pts):
            continue

        tags     = node.get("tags", {})
        node_id  = node.get("id", "")
        name     = tags.get("name") or tags.get("operator") or "EV Charging Station"
        network  = tags.get("network") or tags.get("operator") or None
        access   = tags.get("access", "yes")
        ownership = "private" if access in ("customers", "private") else "public"

        capacity_raw = tags.get("capacity")
        try:
            capacity = int(capacity_raw) if capacity_raw else None
        except (TypeError, ValueError):
            capacity = None

        sockets = [k.split(":")[1] for k in tags if k.startswith("socket:") and k.count(":") == 1]
        pay_methods = ["app"]
        if tags.get("payment:credit_cards") == "yes":
            pay_methods.append("card")

        rec = {
            "schema_version": SCHEMA_VERSION,
            "spot_id": f"cambridge_ev_osm_{node_id}",
            "geometry_type": "point",
            "payment_id": None,
            "payment_app": ["network_app"],
            "payment_methods": pay_methods,
            "name": name,
            "address": None,
            "street_side": None,
            "neighborhood": lookup_neighborhood(lat, lon),
            "btd_district": None,
            "municipality": "Cambridge",
            "type": "ev_charging",
            "ownership": ownership,
            "lat": lat,
            "lon": lon,
            "parking_policy": {
                "timezone": "America/New_York",
                "rules": [],
                "pricing_summary": "See EV network app for rates",
                "pricing_version": 1,
                "updated_at": TODAY + "T00:00:00Z",
            },
            "restrictions": [],
            "free_on_holidays": [],
            "holiday_calendar": "none",
            "space_count": capacity,
            "capacity": capacity,
            "overnight_rental_spaces": None,
            "overnight_guest_spaces": None,
            "lot_number": None,
            "permit_zone": None,
            "snow_emergency_tow": False,
            "accessible": False,
            "accessible_spaces": None,
            "accessible_type": None,
            "ev_charging": True,
            "ev_charger_count": capacity,
            "ev_network": network,
            "ev_socket_types": sockets,
            "demand_signals": None,
            "source": "osm",
            "source_url": _SOURCE_URL,
            "source_date": TODAY,
            "last_updated": TODAY,
            "needs_verification": True,
            "verification_method": "none",
            "data_completeness": "partial",
        }
        records.append(rec)
        existing_pts.append((lat, lon))
        added += 1

    return added


# ---------------------------------------------------------------------------
# Spatial-flag meters near accessible spaces
# ---------------------------------------------------------------------------
def flag_nearby_meters(meter_records: list[dict], accessible_records: list[dict], radius_m: float = 20.0) -> int:
    flagged = 0
    for space in accessible_records:
        slat, slon = space["lat"], space["lon"]
        for meter in meter_records:
            if haversine_m(slat, slon, meter["lat"], meter["lon"]) <= radius_m:
                if not meter["accessible"]:
                    meter["accessible"] = True
                    meter["accessible_spaces"] = 0
                    flagged += 1
                meter["accessible_spaces"] += 1
    return flagged


# ---------------------------------------------------------------------------
# GeoJSON + CSV export
# ---------------------------------------------------------------------------

# Cambridge-specific CSV — includes all Cambridge fields
CSV_FIELDS = [
    "spot_id", "payment_id", "payment_app", "name", "address",
    "street_side", "neighborhood", "municipality", "type", "ownership",
    "lat", "lon", "pricing_summary", "space_count", "capacity",
    "permit_zone", "snow_emergency_tow", "accessible", "accessible_spaces", "accessible_type",
    "ev_charging", "ev_charger_count", "ev_network",
    "source", "source_date", "last_updated", "data_completeness",
]

# Master schema — must stay in sync with enrich_dataset.py CSV_FIELDS
BOSTON_CSV_FIELDS = [
    "spot_id", "payment_id", "payment_app", "name", "address",
    "street_side", "neighborhood", "btd_district", "municipality", "type", "ownership",
    "lat", "lon", "pricing_summary", "space_count", "capacity", "permit_zone",
    "snow_emergency_tow", "accessible", "accessible_spaces", "accessible_type",
    "ev_charging", "ev_charger_count", "ev_network", "citation_rate_per_space_month",
    "source", "source_date", "last_updated", "data_completeness",
]


def _flatten_record(rec: dict, fields: list[str]) -> dict:
    row = {k: rec.get(k, "") for k in fields}
    row["pricing_summary"] = (rec.get("parking_policy") or {}).get("pricing_summary", "")
    if isinstance(row.get("payment_app"), list):
        row["payment_app"] = "|".join(row["payment_app"])
    return row


def record_to_feature(rec: dict) -> dict:
    lat, lon = rec.get("lat"), rec.get("lon")
    geom = (
        {"type": "Point", "coordinates": [lon, lat]}
        if lat is not None and lon is not None else None
    )
    props = {k: v for k, v in rec.items() if k not in ("lat", "lon")}
    return {"type": "Feature", "geometry": geom, "properties": props}


def export_csv(records: list[dict]):
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            writer.writerow(_flatten_record(rec, CSV_FIELDS))


def update_boston_csv(cambridge_records: list[dict]):
    """
    Merge Cambridge records into boston_parking.csv (the multi-city master file).

    - Reads the existing file, drops any rows where municipality == "Cambridge"
      (avoids duplicates on re-run).
    - Re-writes the file with the updated BOSTON_CSV_FIELDS column set (adds
      accessible_type, ev_charger_count, ev_network if the file predates them).
    - Appends all Cambridge records at the end.
    """
    if not BOSTON_CSV_PATH.exists():
        print(f"  [SKIP] {BOSTON_CSV_PATH.name} not found — run enrich_dataset.py first")
        return

    with open(BOSTON_CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        existing = [row for row in reader if row.get("municipality") != "Cambridge"]

    n_before = len(existing)

    with open(BOSTON_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=BOSTON_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in existing:
            writer.writerow({k: row.get(k, "") for k in BOSTON_CSV_FIELDS})
        for rec in cambridge_records:
            writer.writerow(_flatten_record(rec, BOSTON_CSV_FIELDS))

    print(f"  {BOSTON_CSV_PATH.name}: {n_before} existing + {len(cambridge_records)} Cambridge = {n_before + len(cambridge_records)} total rows")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    force = "--force" in sys.argv

    print("\n=== Cambridge Parking Dataset Builder ===\n")
    print("STEP 0 — Downloading source layers")
    print("=" * 50)
    meter_path  = download_source("meters",     force=force)
    ada_path    = download_source("accessible", force=force)
    garage_path = download_source("garages",    force=force)
    osm_ev      = download_osm_ev(force=force)

    records = []

    print("\nSTEP 1 — Building meter records (MapServer/10)")
    print("=" * 50)
    if meter_path and meter_path.exists():
        with open(meter_path, encoding="utf-8") as f:
            gj = json.load(f)
        feats = gj.get("features", [])
        print(f"  {len(feats)} total features in source")
        skipped = 0
        for i, feat in enumerate(feats):
            rec = build_meter_record(feat, i)
            if rec:
                records.append(rec)
            else:
                skipped += 1
        print(f"  {len(records)} meter records built, {skipped} skipped (out-of-service or no geometry)")
    else:
        print("  [SKIP] No meter data downloaded")

    meter_records = [r for r in records if r["type"] == "on_street_meter"]

    print("\nSTEP 2 — Building garage/lot records (FeatureServer)")
    print("=" * 50)
    garage_start = len(records)
    if garage_path and garage_path.exists():
        with open(garage_path, encoding="utf-8") as f:
            gj = json.load(f)
        feats = gj.get("features", [])
        print(f"  {len(feats)} features in source")
        skipped = 0
        for i, feat in enumerate(feats):
            rec = build_garage_record(feat, i)
            if rec:
                records.append(rec)
            else:
                skipped += 1
        g_count = len(records) - garage_start
        print(f"  {g_count} garage/lot records built, {skipped} skipped")
    else:
        print("  [SKIP] No garage data downloaded")

    print("\nSTEP 3 — Building accessible space records (FeatureServer)")
    print("=" * 50)
    ada_records = []
    if ada_path and ada_path.exists():
        with open(ada_path, encoding="utf-8") as f:
            gj = json.load(f)
        feats = gj.get("features", [])
        print(f"  {len(feats)} features in source")
        skipped = 0
        for i, feat in enumerate(feats):
            rec = build_accessible_record(feat, i)
            if rec:
                ada_records.append(rec)
            else:
                skipped += 1
        print(f"  {len(ada_records)} accessible space records built, {skipped} skipped")
    else:
        print("  [SKIP] No accessible space data downloaded")

    print("\nSTEP 4 — Spatial-flagging meters near accessible spaces (radius=20m)")
    print("=" * 50)
    if ada_records and meter_records:
        n_flagged = flag_nearby_meters(meter_records, ada_records, radius_m=20.0)
        print(f"  {n_flagged} meters flagged accessible")
    else:
        print("  [SKIP]")

    records.extend(ada_records)

    print("\nSTEP 5 — Adding EV charger records (OSM Overpass)")
    print("=" * 50)
    n_ev = add_ev_chargers(records, osm_ev)
    print(f"  {n_ev} EV charger records added")

    print(f"\nTotal records: {len(records)}")

    print("\nSTEP 6 — Writing outputs")
    print("=" * 50)

    features = [record_to_feature(r) for r in records]
    geojson = {
        "type": "FeatureCollection",
        "name": "cambridge_parking",
        "generated": TODAY,
        "schema_version": SCHEMA_VERSION,
        "record_count": len(features),
        "features": features,
    }
    with open(OUT_GEOJSON, "w", encoding="utf-8") as f:
        json.dump(geojson, f, indent=2, default=str)
    print(f"  {OUT_GEOJSON.name}  ({len(features)} features)")

    export_csv(records)
    print(f"  {OUT_CSV.name}")

    print("\nSTEP 7 — Merging into boston_parking.csv (multi-city master)")
    print("=" * 50)
    update_boston_csv(records)

    # Summary
    lot_records = [r for r in records if r["type"] == "public_garage"]
    ev_records  = [r for r in records if r["type"] == "ev_charging"]
    acc_meters  = [r for r in meter_records if r.get("accessible")]
    municipal   = [r for r in lot_records if r.get("ownership") == "public"]
    print(f"\n  on_street_meter   : {len(meter_records)}  ({len(acc_meters)} flagged accessible)")
    print(f"  public_garage     : {len(lot_records)}  ({len(municipal)} municipal)")
    print(f"  accessible_parking: {len(ada_records)}")
    print(f"  ev_charging       : {len(ev_records)}")
    print("\n=== Done ===")


if __name__ == "__main__":
    main()
