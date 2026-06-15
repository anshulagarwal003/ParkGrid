"""
build_brookline.py — Brookline, MA Parking Dataset Builder

Fetches from the Town of Brookline's ArcGIS FeatureService and produces:
  - brookline_parking.geojson
  - brookline_parking.csv

Source app:
  https://brookline.maps.arcgis.com/apps/instant/nearby/index.html?appid=6a679f8ed5e34c68960f47665426459f
  (Town-of-Brookline.MA — Public_Parking_Feeder_Map_New_WFL1)

Layers fetched:
  Layer 1 — ParkingMeter   (points, ~1,734 records)
  Layer 2 — ParkingSpace   (polygons, ~2,723 records — HAccessible=1 → accessible_parking records)
  Layer 3 — PublicParkingLot (polygons, ~13 records)

Run:
    python build_brookline.py
    python build_brookline.py --force   # re-download even if cached

NOTE: Meter service days default to Mon-Sat (no day field in source data).
Verify against posted signage before publishing.
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

BASE_DIR        = Path(__file__).parent
OUT_GEOJSON     = BASE_DIR / "brookline_parking.geojson"
OUT_CSV         = BASE_DIR / "brookline_parking.csv"
BOSTON_CSV_PATH = BASE_DIR / "boston_parking.csv"
RAW_DIR         = BASE_DIR / "raw"
RAW_DIR.mkdir(exist_ok=True)

SCHEMA_VERSION = "1"
TODAY = __import__("datetime").date.today().isoformat()

_FS_BASE = (
    "https://services1.arcgis.com/Oknk0tvfHOElpgGU/arcgis/rest/services"
    "/Public_Parking_Feeder_Map_New_WFL1/FeatureServer"
)
_SOURCE_URL = (
    "https://brookline.maps.arcgis.com/apps/instant/nearby/index.html"
    "?appid=6a679f8ed5e34c68960f47665426459f"
)

# Overpass bounding box covering Brookline, MA (south,west,north,east)
_OVERPASS_URL  = "https://overpass-api.de/api/interpreter"
_BROOKLINE_BBOX = "42.29,-71.18,42.36,-71.08"
_OSM_EV_FILE   = RAW_DIR / "brookline_ev_osm.json"

SOURCES = {
    "meters": {
        "layer": 1,
        "file": RAW_DIR / "brookline_meters.geojson",
        "desc": "Brookline Parking Meters (Layer 1)",
    },
    "spaces": {
        "layer": 2,
        "file": RAW_DIR / "brookline_spaces.geojson",
        "desc": "Brookline Parking Spaces (Layer 2)",
    },
    "lots": {
        "layer": 3,
        "file": RAW_DIR / "brookline_lots.geojson",
        "desc": "Brookline Public Parking Lots (Layer 3)",
    },
}

# ---------------------------------------------------------------------------
# Neighborhoods (Brookline village areas by bounding box)
# ---------------------------------------------------------------------------
NEIGHBORHOODS = [
    ("Coolidge Corner",   42.338, 42.350, -71.133, -71.114),
    ("Brookline Village", 42.325, 42.338, -71.124, -71.105),
    ("Washington Square", 42.332, 42.342, -71.145, -71.130),
    ("Cleveland Circle",  42.329, 42.342, -71.162, -71.143),
    ("Longwood",          42.334, 42.346, -71.115, -71.097),
    ("Chestnut Hill",     42.312, 42.334, -71.173, -71.143),
    ("Brookline Hills",   42.318, 42.334, -71.143, -71.120),
    ("South Brookline",   42.293, 42.320, -71.157, -71.108),
]

def lookup_neighborhood(lat: float, lon: float) -> str:
    for name, s, n, w, e in NEIGHBORHOODS:
        if s <= lat <= n and w <= lon <= e:
            return name
    return "Brookline"


# ---------------------------------------------------------------------------
# Download (paginated)
# ---------------------------------------------------------------------------
def download_layer(key: str, force: bool = False) -> Path | None:
    src = SOURCES[key]
    out = src["file"]
    if out.exists() and not force:
        size_kb = out.stat().st_size // 1024
        print(f"  [cached] {src['desc']} ({size_kb} KB) -> {out.name}")
        return out

    print(f"  [downloading] {src['desc']} ...")
    headers = {"User-Agent": "park-grid-brookline/1.0"}
    all_features = []
    offset = 0

    while True:
        url = (
            f"{_FS_BASE}/{src['layer']}/query"
            f"?f=geojson&where=1%3D1&outFields=*&outSR=4326"
            f"&resultOffset={offset}&resultRecordCount=2000"
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
        if len(feats) < 2000:
            break
        offset += 2000
        time.sleep(0.5)

    gj = {"type": "FeatureCollection", "features": all_features}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(gj, f)
    print(f"  [ok] {len(all_features)} features -> {out.name}")
    return out


# ---------------------------------------------------------------------------
# Download OSM EV chargers for Brookline via Overpass
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
node["amenity"="charging_station"]({_BROOKLINE_BBOX});
out body;
"""
    try:
        r = requests.post(
            _OVERPASS_URL,
            data={"data": query},
            headers={"User-Agent": "park-grid-brookline/1.0"},
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
    print(f"  [ok] {n} EV charger nodes -> {_OSM_EV_FILE.name}")
    return data


# ---------------------------------------------------------------------------
# Add EV charger records from OSM
# ---------------------------------------------------------------------------
def add_ev_chargers(records: list[dict], osm_data: dict) -> int:
    """Create standalone ev_charging records for OSM nodes not already within 25 m."""
    if not osm_data:
        return 0
    nodes = [e for e in osm_data.get("elements", []) if e.get("type") == "node" and "lat" in e]
    added = 0

    existing_pts = [(r["lat"], r["lon"]) for r in records if r.get("lat") is not None]

    for node in nodes:
        lat, lon = node["lat"], node["lon"]
        # Skip if already within 25 m of an existing record
        if any(haversine_m(lat, lon, elat, elon) <= 25 for elat, elon in existing_pts):
            continue

        tags    = node.get("tags", {})
        node_id = node.get("id", "")
        name    = tags.get("name") or tags.get("operator") or "EV Charging Station"
        network = tags.get("network") or tags.get("operator") or None
        access  = tags.get("access", "yes")

        capacity_raw = tags.get("capacity")
        try:
            capacity = int(capacity_raw) if capacity_raw else None
        except (ValueError, TypeError):
            capacity = None

        sockets = [k.split(":")[1] for k in tags if k.startswith("socket:") and k.count(":") == 1]
        ownership = "private" if access in ("customers", "private") else "public"
        pay_methods = ["app"]
        if tags.get("payment:credit_cards") == "yes":
            pay_methods.append("card")

        rec = {
            "schema_version": SCHEMA_VERSION,
            "spot_id": f"brookline_ev_osm_{node_id}",
            "geometry_type": "point",
            "payment_id": None,
            "payment_app": ["network_app"],
            "payment_methods": pay_methods,
            "name": name,
            "address": None,
            "street_side": None,
            "neighborhood": lookup_neighborhood(lat, lon),
            "btd_district": None,
            "municipality": "Brookline",
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
            "ev_charger_count": capacity,
            "overnight_rental_spaces": None,
            "overnight_guest_spaces": None,
            "lot_number": None,
            "permit_zone": None,
            "snow_emergency_tow": False,
            "accessible": False,
            "accessible_spaces": None,
            "accessible_type": None,
            "ev_charging": True,
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
# Rate description parser
# Example: "3 hr limit, 8AM - 8PM, $2.00/hr"
# ---------------------------------------------------------------------------
_RATEDESC_RE = re.compile(
    r"(\d+)\s*hr\s*limit\s*[,;]\s*(\d+)\s*AM\s*[-–]\s*(\d+)\s*PM\s*[,;]\s*\$?([\d.]+)\s*/\s*hr",
    re.IGNORECASE,
)

def parse_ratedesc(
    ratedesc: str,
    svc_start: int,
    svc_end: int,
    use_limit_hrs: int,
    rate_hrly: float,
) -> tuple:
    """
    Returns (start_str, end_str, max_session_minutes, rate, days).
    Days default to Mon-Sat — no day field exists in source data.
    """
    days = ["mon", "tue", "wed", "thu", "fri", "sat"]

    m = _RATEDESC_RE.match((ratedesc or "").strip())
    if m:
        limit_h   = int(m.group(1))
        start_h   = int(m.group(2))
        end_h_raw = int(m.group(3))
        rate      = float(m.group(4))
        # PM conversion: 12PM stays 12, others +12
        end_h = end_h_raw if end_h_raw == 12 else end_h_raw + 12
    else:
        limit_h = use_limit_hrs or 0
        start_h = svc_start or 8
        end_h_raw = svc_end or 8
        # If end <= start, assume it's a PM value
        end_h = (end_h_raw + 12) if end_h_raw <= start_h else end_h_raw
        rate  = rate_hrly or 0.0

    return (
        f"{start_h:02d}:00",
        f"{end_h:02d}:00",
        limit_h * 60 if limit_h else None,
        rate,
        days,
    )


# ---------------------------------------------------------------------------
# Centroid from polygon geometry
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


# ---------------------------------------------------------------------------
# Haversine distance (metres)
# ---------------------------------------------------------------------------
def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    p = math.pi / 180
    a = (
        math.sin((lat2 - lat1) * p / 2) ** 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Build record — ParkingSpace (Layer 2, ADA only)
# ---------------------------------------------------------------------------
def build_space_record(feat: dict, idx: int) -> dict | None:
    p    = feat.get("properties") or {}
    geom = feat.get("geometry") or {}

    if int(p.get("HAccessible") or 0) != 1:
        return None  # only ADA spaces

    lon, lat = polygon_centroid(geom)
    if lat is None:
        return None

    obj_id      = p.get("OBJECTID", idx)
    access_type = (p.get("AccessType") or "").strip()
    ada_subtype = "van_accessible" if access_type.lower() == "van" else "standard_ada"

    return {
        "schema_version": SCHEMA_VERSION,
        "spot_id": f"brookline_space_{obj_id}",
        "geometry_type": "point",
        "payment_id": None,
        "payment_app": ["free"],
        "payment_methods": ["free"],
        "name": f"Accessible Parking Space",
        "address": None,
        "street_side": "unknown",
        "neighborhood": lookup_neighborhood(lat, lon),
        "btd_district": None,
        "municipality": "Brookline",
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
        "accessible_type": ada_subtype,
        "ev_charging": False,
        "ev_charger_count": None,
        "ev_network": None,
        "demand_signals": None,
        "source": "brookline_gis",
        "source_url": _SOURCE_URL,
        "source_date": TODAY,
        "last_updated": TODAY,
        "needs_verification": False,
        "verification_method": "official_source",
        "data_completeness": "high",
    }


# ---------------------------------------------------------------------------
# Spatial-flag meters near ADA spaces
# ---------------------------------------------------------------------------
def flag_nearby_meters(meter_records: list[dict], space_records: list[dict], radius_m: float = 20.0) -> int:
    """Set accessible=True / increment accessible_spaces on meters within radius_m of an ADA space."""
    flagged = 0
    for space in space_records:
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
# Build record — ParkingMeter (Layer 1)
# ---------------------------------------------------------------------------
def build_meter_record(feat: dict, idx: int) -> dict | None:
    p     = feat.get("properties") or {}
    geom  = feat.get("geometry") or {}
    coords = geom.get("coordinates", [])

    if geom.get("type") != "Point" or len(coords) < 2:
        return None
    lon, lat = float(coords[0]), float(coords[1])

    meter_id   = str(p.get("METERID") or "").strip()
    multispace = int(p.get("MULTISPACE") or 0)
    ratedesc   = (p.get("RATEDESC") or "").strip()
    rate_hrly  = float(p.get("RATE_HOURLY") or 0)
    use_lim    = int(p.get("USE_LIMIT_HRS") or 0)
    svc_start  = int(p.get("SERVICE_START_TIME") or 8)
    svc_end    = int(p.get("SERVICE_END_TIME") or 8)
    block      = (p.get("BLOCK") or "").strip()

    start_str, end_str, max_min, rate, days = parse_ratedesc(
        ratedesc, svc_start, svc_end, use_lim, rate_hrly
    )

    if multispace:
        pay_app     = ["pay_and_display", "ParkBoston"]
        pay_methods = ["card", "coin", "app"]
    else:
        pay_app     = ["ParkBoston"]
        pay_methods = ["card", "coin", "app"]

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
                "billing_increment_minutes": 15,
            },
        }
        if max_min:
            rule["constraints"] = {"max_session_minutes": max_min}
        paid_rules.append(rule)

    summary = ratedesc or (
        f"Mon-Sat {start_str}-{end_str} ${rate:.2f}/hr"
        + (f" ({use_lim}h max)" if use_lim else "")
    )

    spot_id = f"brookline_meter_{meter_id}" if meter_id else f"brookline_meter_obj_{idx}"

    return {
        "schema_version": SCHEMA_VERSION,
        "spot_id": spot_id,
        "geometry_type": "point",
        "payment_id": meter_id or None,
        "payment_app": pay_app,
        "payment_methods": pay_methods,
        "name": block,
        "address": block,
        "street_side": "unknown",
        "neighborhood": lookup_neighborhood(lat, lon),
        "btd_district": None,
        "municipality": "Brookline",
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
        "source": "brookline_gis",
        "source_url": _SOURCE_URL,
        "source_date": TODAY,
        "last_updated": TODAY,
        "needs_verification": False,
        "verification_method": "official_source",
        "data_completeness": "partial",
    }


# ---------------------------------------------------------------------------
# Build record — PublicParkingLot (Layer 3, polygon → point centroid)
# ---------------------------------------------------------------------------
_TIMELIMIT_RE = re.compile(r"(\d+)\s*hr", re.IGNORECASE)

def parse_time_limit(s: str) -> int | None:
    m = _TIMELIMIT_RE.match((s or "").strip())
    return int(m.group(1)) * 60 if m else None


def build_lot_record(feat: dict, idx: int) -> dict | None:
    p    = feat.get("properties") or {}
    geom = feat.get("geometry") or {}

    lon, lat = polygon_centroid(geom)
    if lat is None:
        return None

    name       = (p.get("NAME") or "").strip()
    location   = (p.get("LOCATION") or "").strip()
    num_space  = p.get("NUM_SPACE")
    time_limit = (p.get("TIME_LIMIT") or "").strip()
    ovr_rental = int(p.get("OVERNIGHT_RENTAL") or 0) or None
    ovr_guest  = int(p.get("OVERNIGHT_GUEST") or 0) or None
    lot_num    = p.get("LOT_NUM")
    notes      = (p.get("NOTES") or "").strip() or None

    try:
        capacity = int(num_space) if num_space is not None else None
    except (ValueError, TypeError):
        capacity = None

    max_min = parse_time_limit(time_limit)
    summary = f"{time_limit} limit" if time_limit else "See posted signs"
    has_ev  = "EV" in name.upper()

    restrictions = []
    if max_min:
        restrictions.append({
            "rule_type": "time_limit",
            "days": ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],
            "start": None,
            "end": None,
            "note": f"{time_limit} parking limit",
        })

    spot_id = f"brookline_lot_{lot_num}" if lot_num else f"brookline_lot_obj_{idx}"

    return {
        "schema_version": SCHEMA_VERSION,
        "spot_id": spot_id,
        "geometry_type": "point",
        "payment_id": None,
        "payment_app": ["meter"],
        "payment_methods": ["coin", "card"],
        "name": name,
        "address": location,
        "street_side": None,
        "neighborhood": lookup_neighborhood(lat, lon),
        "btd_district": None,
        "municipality": "Brookline",
        "type": "public_garage",
        "ownership": "public",
        "lat": lat,
        "lon": lon,
        "parking_policy": {
            "timezone": "America/New_York",
            "rules": [],
            "pricing_summary": summary,
            "pricing_version": 1,
            "updated_at": TODAY + "T00:00:00Z",
        },
        "restrictions": restrictions,
        "free_on_holidays": [],
        "holiday_calendar": "none",
        "space_count": None,
        "capacity": capacity,
        "overnight_rental_spaces": ovr_rental,
        "overnight_guest_spaces": ovr_guest,
        "lot_number": lot_num,
        "notes": notes,
        "permit_zone": None,
        "snow_emergency_tow": False,
        "accessible": False,
        "accessible_spaces": None,
        "accessible_type": None,
        "ev_charging": has_ev,
        "ev_charger_count": None,
        "ev_network": None,
        "demand_signals": None,
        "source": "brookline_gis",
        "source_url": _SOURCE_URL,
        "source_date": TODAY,
        "last_updated": TODAY,
        "needs_verification": True,
        "verification_method": "official_source",
        "data_completeness": "partial",
    }


# ---------------------------------------------------------------------------
# GeoJSON + CSV export
# ---------------------------------------------------------------------------
CSV_FIELDS = [
    "spot_id", "payment_id", "payment_app", "name", "address",
    "street_side", "neighborhood", "municipality", "type", "ownership",
    "lat", "lon", "pricing_summary", "space_count", "capacity",
    "overnight_rental_spaces", "overnight_guest_spaces", "lot_number",
    "permit_zone", "snow_emergency_tow", "accessible", "accessible_spaces", "accessible_type",
    "ev_charging", "ev_charger_count", "ev_network", "source", "source_date", "last_updated", "data_completeness",
]


def record_to_feature(rec: dict) -> dict:
    lat, lon = rec.get("lat"), rec.get("lon")
    geom = (
        {"type": "Point", "coordinates": [lon, lat]}
        if lat is not None and lon is not None else None
    )
    props = {k: v for k, v in rec.items() if k not in ("lat", "lon")}
    return {"type": "Feature", "geometry": geom, "properties": props}


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


def export_csv(records: list[dict]):
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            writer.writerow(_flatten_record(rec, CSV_FIELDS))


def update_boston_csv(brookline_records: list[dict]):
    """
    Merge Brookline records into boston_parking.csv (the multi-city master file).
    Drops any existing Brookline rows first to avoid duplicates on re-run.
    """
    if not BOSTON_CSV_PATH.exists():
        print(f"  [SKIP] {BOSTON_CSV_PATH.name} not found — run enrich_dataset.py first")
        return

    with open(BOSTON_CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        existing = [row for row in reader if row.get("municipality") != "Brookline"]

    n_before = len(existing)

    with open(BOSTON_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=BOSTON_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in existing:
            writer.writerow({k: row.get(k, "") for k in BOSTON_CSV_FIELDS})
        for rec in brookline_records:
            writer.writerow(_flatten_record(rec, BOSTON_CSV_FIELDS))

    print(f"  {BOSTON_CSV_PATH.name}: {n_before} existing + {len(brookline_records)} Brookline = {n_before + len(brookline_records)} total rows")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    force = "--force" in sys.argv

    print("\n=== Brookline Parking Dataset Builder ===\n")
    print("STEP 0 — Downloading source layers")
    print("=" * 50)
    meter_path = download_layer("meters", force=force)
    space_path = download_layer("spaces", force=force)
    lot_path   = download_layer("lots",   force=force)
    osm_ev     = download_osm_ev(force=force)

    records = []

    print("\nSTEP 1 — Building meter records (Layer 1)")
    print("=" * 50)
    if meter_path and meter_path.exists():
        with open(meter_path, encoding="utf-8") as f:
            gj = json.load(f)
        feats = gj.get("features", [])
        print(f"  {len(feats)} features in source")
        skipped = 0
        for i, feat in enumerate(feats):
            rec = build_meter_record(feat, i)
            if rec:
                records.append(rec)
            else:
                skipped += 1
        print(f"  {len(records)} meter records built, {skipped} skipped (no geometry)")
    else:
        print("  [SKIP] No meter data downloaded")

    meter_records = [r for r in records if r["type"] == "on_street_meter"]

    print("\nSTEP 2 — Building lot records (Layer 3)")
    print("=" * 50)
    lot_start = len(records)
    if lot_path and lot_path.exists():
        with open(lot_path, encoding="utf-8") as f:
            gj = json.load(f)
        feats = gj.get("features", [])
        print(f"  {len(feats)} features in source")
        skipped = 0
        for i, feat in enumerate(feats):
            rec = build_lot_record(feat, i)
            if rec:
                records.append(rec)
            else:
                skipped += 1
        lot_count = len(records) - lot_start
        print(f"  {lot_count} lot records built, {skipped} skipped (no geometry)")
    else:
        print("  [SKIP] No lot data downloaded")

    print("\nSTEP 3 — Building ADA accessible space records (Layer 2)")
    print("=" * 50)
    space_records = []
    if space_path and space_path.exists():
        with open(space_path, encoding="utf-8") as f:
            gj = json.load(f)
        feats = gj.get("features", [])
        print(f"  {len(feats)} total space features in source")
        for i, feat in enumerate(feats):
            rec = build_space_record(feat, i)
            if rec:
                space_records.append(rec)
        print(f"  {len(space_records)} ADA accessible space records built")
    else:
        print("  [SKIP] No space data downloaded")

    print("\nSTEP 4 — Spatial-flagging meters near ADA spaces (radius=20m)")
    print("=" * 50)
    if space_records and meter_records:
        n_flagged = flag_nearby_meters(meter_records, space_records, radius_m=20.0)
        print(f"  {n_flagged} meters flagged accessible")
    else:
        print("  [SKIP] No space or meter records to cross-reference")

    records.extend(space_records)

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
        "name": "brookline_parking",
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
    lot_records   = [r for r in records if r["type"] == "public_garage"]
    ada_records   = [r for r in records if r["type"] == "accessible_parking"]
    ev_records    = [r for r in records if r["type"] == "ev_charging"]
    van_records   = [r for r in ada_records if r.get("accessible_type") == "van_accessible"]
    ev_lots       = [r for r in lot_records if r.get("ev_charging")]
    acc_meters    = [r for r in meter_records if r.get("accessible")]
    print(f"\n  on_street_meter   : {len(meter_records)}  ({len(acc_meters)} flagged accessible)")
    print(f"  public_garage     : {len(lot_records)}  ({len(ev_lots)} with EV charging)")
    print(f"  accessible_parking: {len(ada_records)}  ({len(van_records)} van-accessible)")
    print(f"  ev_charging       : {len(ev_records)}")
    print("\n=== Done ===")


if __name__ == "__main__":
    main()
