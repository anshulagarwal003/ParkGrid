"""
Boston Parking Dataset — Enrichment Script
==========================================
Downloads five external datasets and enriches boston_parking.geojson in-place:

  1. Snow Emergency Tow Zones     (Analyze Boston GeoJSON)
  2. OSAP Accessible Spaces       (Analyze Boston GeoJSON)
  3. OSM EV Chargers              (Overpass API)
  4. OSM Parking Garages          (Overpass API)
  5. Resident Permit Zones        (Analyze Boston GeoJSON)
  6. BTD Parking Citations        (Analyze Boston CSV — year-to-date)

Run:
    python enrich_dataset.py

Outputs (overwrites):
    boston_parking.geojson   — enriched in-place
    boston_parking.csv       — flat CSV re-exported from enriched GeoJSON
    raw/                     — raw downloaded files cached here (skip re-download if present)
    enrichment_report.json   — summary of what was matched/updated
"""

import csv
import json
import math
import os
import sys
import time
import warnings
from collections import defaultdict
from pathlib import Path

warnings.filterwarnings("ignore")  # suppress requests urllib3 version warning

try:
    import requests
except ImportError:
    sys.exit("Install requests:  pip install requests")

try:
    from shapely.geometry import shape, Point
    from shapely.strtree import STRtree
except ImportError:
    sys.exit("Install shapely:   pip install shapely")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
GEOJSON_PATH   = BASE_DIR / "boston_parking.geojson"
CSV_PATH       = BASE_DIR / "boston_parking.csv"
REPORT_PATH    = BASE_DIR / "enrichment_report.json"
RAW_DIR        = BASE_DIR / "raw"
RAW_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Source URLs
# ---------------------------------------------------------------------------
# ArcGIS FeatureServer base for Boston open data
_ARCGIS = "https://services.arcgis.com/sFnw0xNflSi8J0uh/arcgis/rest/services"
_FS_QUERY = "/FeatureServer/0/query?f=geojson&where=1%3D1&outFields=*&resultRecordCount=10000"

# Overpass bbox: (south,west,north,east) covering Boston
_BBOX = "42.22,-71.19,42.40,-70.98"
_OVERPASS = "https://overpass-api.de/api/interpreter"

SOURCES = {
    "snow_zones": {
        # These are designated SAFE parking spots during snow emergencies (point geometry).
        # snow_emergency_tow=true means: this meter IS on a snow emergency route and will be towed.
        # The SAFE spots dataset is the inverse — meters NEAR a safe spot are less likely to be towed.
        # We store the safe-spot flag as snow_emergency_safe_spot on the matched meter.
        "ckan_search_geojson": "snow emergency parking",
        "urls": [
            f"{_ARCGIS}/Snow_Emergency_Zones{_FS_QUERY}",
            "https://opendata.arcgis.com/datasets/4f3e4492e36f405446b7e3dd48b29f2a_0.geojson",
        ],
        "file": RAW_DIR / "snow_emergency_zones.geojson",
        "desc": "Snow Emergency Parking Spots",
    },
    "osap": {
        "urls": [
            # Canonical BostonGIS service (used by the Public OSAP ArcGIS app)
            f"{_ARCGIS}/On_Street_Accessible_Parking_Spaces{_FS_QUERY}",
            f"{_ARCGIS}/On_Street_Accessible_Parking{_FS_QUERY}",
            "https://bostonopendata-boston.opendata.arcgis.com/api/download/v1/items/a7e29701bda84f20b2da7ad9e18b0bde/geojson",
            "https://opendata.arcgis.com/datasets/a7e29701bda84f20b2da7ad9e18b0bde_0.geojson",
        ],
        "file": RAW_DIR / "osap_accessible.geojson",
        "desc": "OSAP Accessible Parking",
    },
    "permit_zones": {
        "ckan_search_geojson": "resident parking permit zones",
        "urls": [
            f"{_ARCGIS}/Resident_Parking_Zones{_FS_QUERY}",
            f"{_ARCGIS}/Permit_Parking_Zones{_FS_QUERY}",
            "https://opendata.arcgis.com/datasets/51a0e30e5b964c5ba29a7cfc8a09e1cf_0.geojson",
        ],
        "file": RAW_DIR / "permit_zones.geojson",
        "desc": "Resident Permit Zones",
    },
    "osm_ev": {
        "urls": [
            f"{_OVERPASS}?data=[out:json][timeout:60];node[\"amenity\"=\"charging_station\"]({_BBOX});out body;",
        ],
        "file": RAW_DIR / "osm_ev.json",
        "desc": "OSM EV Charging Stations",
    },
    "osm_parking": {
        "urls": [
            # nodes only (ways time out) with explicit timeout
            f"{_OVERPASS}?data=[out:json][timeout:60];node[\"amenity\"=\"parking\"]({_BBOX});out body;",
        ],
        "file": RAW_DIR / "osm_parking.json",
        "desc": "OSM Parking Nodes",
    },
    "citations": {
        "ckan_search_csv": "parking violation citations",
        "urls": [
            "https://data.boston.gov/api/3/action/datastore_search?resource_id=7214e5f7-5dae-4b32-8af0-7c8ad4b5869a&limit=500000",
            "https://data.boston.gov/api/3/action/datastore_search?resource_id=e29e7426-2065-4af0-8960-0d2e3e8e7c49&limit=500000",
        ],
        "file": RAW_DIR / "btd_citations.json",
        "desc": "BTD Parking Citations",
    },
    "mbta_facilities": {
        "urls": [
            "https://api-v3.mbta.com/facilities?filter[type]=PARKING_AREA&page[limit]=200",
        ],
        "file": RAW_DIR / "mbta_facilities.json",
        "desc": "MBTA Park-and-Ride Facilities",
    },
    "osm_city_lots": {
        "urls": [
            f"{_OVERPASS}?data=[out:json][timeout:60];"
            "node[\"amenity\"=\"parking\"][\"operator\"~\"Boston|BTD|City of Boston\",i]"
            f"({_BBOX});out body;",
        ],
        "file": RAW_DIR / "osm_city_lots.json",
        "desc": "OSM City-Operated Parking Lots",
    },
}

# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------
def ckan_discover_geojson(search_term: str) -> str | None:
    """Search data.boston.gov CKAN for a dataset by name, return first GeoJSON resource URL."""
    try:
        r = requests.get(
            "https://data.boston.gov/api/3/action/package_search",
            params={"q": search_term, "rows": 3},
            headers={"User-Agent": "boston-parking-dataset-builder/1.0"},
            timeout=20, verify=False,
        )
        r.raise_for_status()
        results = r.json().get("result", {}).get("results", [])
        for pkg in results:
            for res in pkg.get("resources", []):
                fmt = (res.get("format") or "").upper()
                url = res.get("url", "")
                if fmt in ("GEOJSON", "GEO JSON") or url.endswith(".geojson"):
                    print(f"  [discovered] {search_term} -> {url[:80]}")
                    return url
    except Exception as e:
        print(f"  [discover warn] {e}")
    return None


def ckan_discover_csv(search_term: str) -> str | None:
    """Search data.boston.gov CKAN for a dataset, return first CSV resource download URL."""
    try:
        r = requests.get(
            "https://data.boston.gov/api/3/action/package_search",
            params={"q": search_term, "rows": 3},
            headers={"User-Agent": "boston-parking-dataset-builder/1.0"},
            timeout=20, verify=False,
        )
        r.raise_for_status()
        results = r.json().get("result", {}).get("results", [])
        for pkg in results:
            for res in pkg.get("resources", []):
                fmt = (res.get("format") or "").upper()
                url = res.get("url", "")
                if fmt == "CSV" or url.lower().endswith(".csv"):
                    print(f"  [discovered] {search_term} -> {url[:80]}")
                    return url
    except Exception as e:
        print(f"  [discover warn] {e}")
    return None


def download(key: str, force: bool = False) -> Path:
    src = SOURCES[key]
    out = src["file"]
    if out.exists() and not force:
        size_kb = out.stat().st_size // 1024
        print(f"  [cached] {src['desc']} ({size_kb} KB) -> {out.name}")
        return out
    print(f"  [downloading] {src['desc']} ...")
    headers = {"User-Agent": "boston-parking-dataset-builder/1.0"}
    # Auto-discover current URL from CKAN if a search term is provided
    discovered = []
    if src.get("ckan_search_geojson"):
        u = ckan_discover_geojson(src["ckan_search_geojson"])
        if u:
            discovered = [u]
    elif src.get("ckan_search_csv"):
        u = ckan_discover_csv(src["ckan_search_csv"])
        if u:
            discovered = [u]

    urls = discovered + (src.get("urls") or [src.get("url")])
    last_err = None
    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=120, stream=True, verify=False)
            r.raise_for_status()
            with open(out, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
            size_kb = out.stat().st_size // 1024
            print(f"  [ok] saved {size_kb} KB -> {out.name}")
            time.sleep(1)
            return out
        except Exception as e:
            last_err = e
            print(f"  [try next] {type(e).__name__}: {str(e)[:80]}")
    print(f"  [WARN] All URLs failed for {src['desc']}: {last_err}")
    return None


def load_geojson(path: Path) -> dict | None:
    if not path or not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  [WARN] Could not parse {path.name}: {e}")
        return None


# ---------------------------------------------------------------------------
# Spatial helpers
# ---------------------------------------------------------------------------
def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in metres."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def build_point_index(features: list[dict]) -> tuple[STRtree, list]:
    """Build an STRtree over GeoJSON point features for fast proximity queries."""
    pts = []
    valid = []
    for f in features:
        try:
            geom = shape(f["geometry"])
            pts.append(geom)
            valid.append(f)
        except Exception:
            pass
    return STRtree(pts), valid


def build_polygon_index(features: list[dict]) -> tuple[STRtree, list]:
    polys = []
    valid = []
    for f in features:
        try:
            geom = shape(f["geometry"])
            polys.append(geom)
            valid.append(f)
        except Exception:
            pass
    return STRtree(polys), valid


# ---------------------------------------------------------------------------
# 1. Snow Emergency Parking Spots
# The CKAN dataset contains POINT geometry: designated safe-parking locations
# during a snow emergency. We flag meters within 30m of one as
# snow_emergency_safe_spot=true.  Polygon datasets (tow routes) would instead
# set snow_emergency_tow=true via point-in-polygon.
# ---------------------------------------------------------------------------
def enrich_snow(features: list[dict], gj: dict) -> int:
    snow_feats = gj.get("features", [])
    if not snow_feats:
        return 0

    first_type = (snow_feats[0].get("geometry") or {}).get("type", "")
    print(f"  Snow dataset geometry type: {first_type} ({len(snow_feats)} features)")

    if first_type == "Point":
        # Safe parking spots — flag nearby meters
        snow_pts = []
        for f in snow_feats:
            try:
                coords = f["geometry"]["coordinates"]
                snow_pts.append((coords[1], coords[0]))  # lat, lon
            except Exception:
                pass
        count = 0
        for feat in features:
            geom = feat.get("geometry")
            if not geom:
                continue
            lon, lat = geom["coordinates"]
            for slat, slon in snow_pts:
                if haversine_m(lat, lon, slat, slon) <= 30:
                    feat["properties"]["snow_emergency_safe_spot"] = True
                    count += 1
                    break
        return count
    else:
        # Polygon tow routes — point-in-polygon
        polys, _ = build_polygon_index(snow_feats)
        count = 0
        for feat in features:
            geom = feat.get("geometry")
            if not geom:
                continue
            lon, lat = geom["coordinates"]
            pt = Point(lon, lat)
            hits = polys.query(pt)
            for idx in hits:
                if polys.geometries[idx].contains(pt):
                    feat["properties"]["snow_emergency_tow"] = True
                    count += 1
                    break
        return count


# ---------------------------------------------------------------------------
# 2. OSAP Accessible Spaces — nearest point within 20 m
# ---------------------------------------------------------------------------
def enrich_osap(features: list[dict], gj: dict) -> int:
    print("  Building OSAP point index ...")
    osap_feats = gj.get("features", [])
    # Build a dict keyed by (lat, lon) for quick lookup
    osap_pts = []
    for f in osap_feats:
        try:
            coords = f["geometry"]["coordinates"]
            osap_pts.append((coords[1], coords[0]))  # lat, lon
        except Exception:
            pass

    count = 0
    for feat in features:
        geom = feat.get("geometry")
        if not geom:
            continue
        lon, lat = geom["coordinates"]
        for osap_lat, osap_lon in osap_pts:
            if haversine_m(lat, lon, osap_lat, osap_lon) <= 20:
                feat["properties"]["accessible"] = True
                feat["properties"]["accessible_spaces"] = max(
                    feat["properties"].get("accessible_spaces", 0), 1
                )
                count += 1
                break
    return count


# ---------------------------------------------------------------------------
# 3. OSM EV Chargers — nearest point within 25 m
# ---------------------------------------------------------------------------
def enrich_ev(features: list[dict], osm: dict) -> int:
    print("  Indexing OSM EV charger nodes ...")
    ev_pts = []
    for node in osm.get("elements", []):
        if node.get("type") == "node" and "lat" in node:
            ev_pts.append((node["lat"], node["lon"]))

    count = 0
    for feat in features:
        geom = feat.get("geometry")
        if not geom:
            continue
        lon, lat = geom["coordinates"]
        for ev_lat, ev_lon in ev_pts:
            if haversine_m(lat, lon, ev_lat, ev_lon) <= 25:
                feat["properties"]["ev_charging"] = True
                count += 1
                break
    return count


# ---------------------------------------------------------------------------
# 4. OSM Parking Garages — tag public_garage / private_lot records
#    (meters dataset is on-street only; this adds context notes but doesn't
#     add new records — new garage records would require a separate pass)
# ---------------------------------------------------------------------------
def enrich_osm_parking(features: list[dict], osm: dict) -> int:
    """
    For each metered record within 15 m of an OSM parking node, copy
    the OSM capacity if known. Garages that aren't already in the dataset
    are logged to raw/osm_new_garages.json for manual review.
    """
    print("  Indexing OSM parking nodes ...")
    garage_pts = []
    for el in osm.get("elements", []):
        if el.get("type") == "node" and "lat" in el:
            tags = el.get("tags", {})
            if tags.get("amenity") == "parking":
                garage_pts.append({
                    "lat": el["lat"], "lon": el["lon"],
                    "capacity": tags.get("capacity"),
                    "access": tags.get("access", "unknown"),
                    "name": tags.get("name", ""),
                })

    # Save unmatched OSM garages for review
    new_garages = []
    for g in garage_pts:
        matched = False
        lat, lon = g["lat"], g["lon"]
        for feat in features:
            geom = feat.get("geometry")
            if not geom:
                continue
            if haversine_m(lat, lon, geom["coordinates"][1], geom["coordinates"][0]) <= 15:
                cap = g.get("capacity")
                if cap:
                    try:
                        feat["properties"]["capacity"] = int(cap)
                    except ValueError:
                        pass
                matched = True
                break
        if not matched:
            new_garages.append(g)

    out_path = RAW_DIR / "osm_new_garages.json"
    with open(out_path, "w") as f:
        json.dump(new_garages, f, indent=2)
    print(f"  {len(new_garages)} OSM garage nodes not matched -> {out_path.name} (review for new records)")
    return len(garage_pts) - len(new_garages)


# ---------------------------------------------------------------------------
# 5. Resident Permit Zones — point-in-polygon, fill permit schedule
# ---------------------------------------------------------------------------
PERMIT_SCHEDULE_NOTE = (
    "Permit required during posted hours — verify exact schedule on street signs. "
    "Zone code is the lookup key into permit_zones.geojson."
)

def enrich_permit_zones(features: list[dict], gj: dict) -> int:
    print("  Building permit zone polygon index ...")
    polys = []
    poly_props = []
    for f in gj.get("features", []):
        try:
            polys.append(shape(f["geometry"]))
            poly_props.append(f.get("properties", {}))
        except Exception:
            pass
    tree = STRtree(polys)

    count = 0
    for feat in features:
        geom = feat.get("geometry")
        if not geom:
            continue
        lon, lat = geom["coordinates"]
        pt = Point(lon, lat)
        hits = tree.query(pt)
        for idx in hits:
            if polys[idx].contains(pt):
                pp = poly_props[idx]
                zone_code = (
                    pp.get("ZONE_CODE") or pp.get("zone_code") or
                    pp.get("Zone") or pp.get("ZONE") or
                    str(pp.get("OBJECTID", ""))
                )
                if zone_code:
                    feat["properties"]["permit_zone"] = zone_code
                    # Update or add the permit restriction entry
                    restrictions = feat["properties"].get("restrictions", [])
                    if not any(r.get("rule_type") == "permit" for r in restrictions):
                        restrictions.append({
                            "rule_type": "permit",
                            "days": ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],
                            "start": None,
                            "end": None,
                            "note": PERMIT_SCHEDULE_NOTE,
                        })
                        feat["properties"]["restrictions"] = restrictions
                    count += 1
                break
    return count


# ---------------------------------------------------------------------------
# 6. BTD Parking Citations — citation_rate_per_space_month
# ---------------------------------------------------------------------------
def enrich_citations(features: list[dict], csv_path: Path) -> int:
    """
    Groups citations by meter location (nearest meter within 30 m),
    computes citations per space per month, writes into demand_signals.
    """
    print("  Reading citations CSV ...")
    # Build a fast lookup: spot_id -> (lat, lon, space_count)
    meter_index = {}
    for feat in features:
        geom = feat.get("geometry")
        if not geom:
            continue
        lon, lat = geom["coordinates"]
        sid = feat["properties"]["spot_id"]
        sc = feat["properties"].get("space_count", 1) or 1
        meter_index[sid] = {"lat": lat, "lon": lon, "space_count": sc, "citations": 0}

    # Simple grid-based bucketing for speed: bucket by 0.001-degree cell (~110m)
    def bucket(lat, lon):
        return (round(lat, 3), round(lon, 3))

    bucket_to_ids = defaultdict(list)
    for sid, m in meter_index.items():
        bucket_to_ids[bucket(m["lat"], m["lon"])].append(sid)

    # Also add neighboring buckets for edge cases
    def neighbors(lat, lon):
        r = round(lat, 3)
        c = round(lon, 3)
        step = 0.001
        return [
            (r, c), (r+step, c), (r-step, c),
            (r, c+step), (r, c-step),
        ]

    citation_count = 0
    skipped = 0
    months_in_dataset = set()

    def iter_rows(path: Path):
        """Yield citation dicts from CSV or CKAN JSON (detected by content, not extension)."""
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            first_char = f.read(1)
        if first_char == "{":
            with open(path, encoding="utf-8", errors="replace") as f:
                data = json.load(f)
            yield from (data.get("result") or {}).get("records") or []
        else:
            # Treat as CSV regardless of file extension
            with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
                yield from csv.DictReader(f)

    try:
        rows_iter = iter_rows(csv_path)
        first = next(rows_iter, None)
        if first is None:
            print("  [WARN] Citations file is empty")
            return 0
        headers = list(first.keys())
        def _col(headers, *candidates):
            """Return first header that exactly matches or ends with one of the candidate names."""
            hl = [h.lower() for h in headers]
            for c in candidates:
                if c in hl:
                    return headers[hl.index(c)]
            return None

        lat_col = _col(headers, "latitude", "lat", "y_coord", "y")
        lon_col = _col(headers, "longitude", "lon", "long", "x_coord", "x")
        date_col = _col(headers, "issue_date", "issuedate", "status_dttm", "date")

        if not lat_col or not lon_col:
            print(f"  [WARN] Citations columns: {headers[:10]}")
            print("  [WARN] Could not find lat/lon columns -- skipping")
            return 0

        print(f"  Citation columns: lat={lat_col}, lon={lon_col}, date={date_col}")

        import itertools
        for row in itertools.chain([first], rows_iter):
                try:
                    clat = float(row[lat_col])
                    clon = float(row[lon_col])
                except (ValueError, KeyError, TypeError):
                    skipped += 1
                    continue

                if date_col and row.get(date_col):
                    try:
                        month_key = row[date_col][:7]  # "YYYY-MM"
                        months_in_dataset.add(month_key)
                    except Exception:
                        pass

                # Find nearest meter in same bucket
                best_sid = None
                best_dist = 31  # meters threshold
                for bkt in neighbors(clat, clon):
                    for sid in bucket_to_ids.get(bkt, []):
                        m = meter_index[sid]
                        d = haversine_m(clat, clon, m["lat"], m["lon"])
                        if d < best_dist:
                            best_dist = d
                            best_sid = sid
                if best_sid:
                    meter_index[best_sid]["citations"] += 1
                    citation_count += 1

    except Exception as e:
        print(f"  [WARN] Error reading citations: {e}")
        return 0

    num_months = max(len(months_in_dataset), 1)
    print(f"  Matched {citation_count} citations across {num_months} months ({skipped} skipped — no coords)")

    # Write citation rate into demand_signals
    enriched = 0
    for feat in features:
        sid = feat["properties"]["spot_id"]
        m = meter_index.get(sid)
        if not m or m["citations"] == 0:
            continue
        rate = round(m["citations"] / m["space_count"] / num_months, 2)
        ds = feat["properties"].get("demand_signals") or {}
        ds["citation_rate_per_space_month"] = rate
        ds["demand_signals_date"] = __import__("datetime").date.today().isoformat()
        feat["properties"]["demand_signals"] = ds
        enriched += 1

    return enriched


# ---------------------------------------------------------------------------
# Step 7 helpers — add off-street facility records
# ---------------------------------------------------------------------------

def _lookup_neighborhood(lat: float, lon: float) -> str:
    """Return a Boston neighborhood name from lat/lon using bounding boxes."""
    NEIGHBORHOODS = [
        ("Downtown",       42.352, 42.362, -71.065, -71.050),
        ("Back Bay",       42.347, 42.354, -71.090, -71.065),
        ("South End",      42.333, 42.350, -71.090, -71.065),
        ("Beacon Hill",    42.355, 42.365, -71.075, -71.060),
        ("North End",      42.360, 42.370, -71.060, -71.045),
        ("Charlestown",    42.370, 42.390, -71.075, -71.045),
        ("East Boston",    42.360, 42.390, -71.045, -70.990),
        ("South Boston",   42.330, 42.355, -71.065, -71.020),
        ("Roxbury",        42.310, 42.335, -71.095, -71.065),
        ("Fenway",         42.340, 42.356, -71.110, -71.090),
        ("Jamaica Plain",  42.290, 42.325, -71.125, -71.090),
        ("Dorchester",     42.285, 42.325, -71.090, -71.040),
    ]
    for name, s, n, w, e in NEIGHBORHOODS:
        if s <= lat <= n and w <= lon <= e:
            return name
    return "Boston"


def already_present(features: list, lat: float, lon: float,
                    type_filter: str | None = None, radius_m: float = 50) -> bool:
    """Return True if an existing feature is within radius_m of (lat, lon)."""
    for feat in features:
        geom = feat.get("geometry")
        if not geom or not geom.get("coordinates"):
            continue
        flon, flat = geom["coordinates"]
        if type_filter and feat["properties"].get("type") != type_filter:
            continue
        if haversine_m(lat, lon, flat, flon) <= radius_m:
            return True
    return False


def add_osm_garages(features: list, garages_path: Path) -> int:
    """Add public/semi-public OSM garage records from raw/osm_new_garages.json."""
    if not garages_path.exists():
        print("  [SKIP] osm_new_garages.json not found")
        return 0

    with open(garages_path, encoding="utf-8") as f:
        garages = json.load(f)

    today = __import__("datetime").date.today().isoformat()
    EXCLUDE_ACCESS = {"private", "employees"}
    added = 0

    for g in garages:
        lat = g.get("lat")
        lon = g.get("lon")
        if lat is None or lon is None:
            continue

        access = (g.get("access") or "unknown").lower()
        if access in EXCLUDE_ACCESS:
            continue

        # Skip if already present in dataset (any type, within 30 m)
        if already_present(features, lat, lon, type_filter=None, radius_m=30):
            continue

        spot_id = f"garage_osm_{round(lat, 5)}_{round(abs(lon), 5)}"
        name = g.get("name") or ""
        capacity_raw = g.get("capacity")
        try:
            capacity = int(capacity_raw) if capacity_raw else None
        except (ValueError, TypeError):
            capacity = None

        # Guess ownership from name
        ownership = "public" if any(w in name.lower() for w in ("municipal", "city", "btd", "public")) else "private"

        data_completeness = "partial" if (name or capacity) else "low"

        feat = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "schema_version": "1",
                "spot_id": spot_id,
                "geometry_type": "point",
                "type": "public_garage",
                "ownership": ownership,
                "municipality": "Boston",
                "payment_app": ["gate"],
                "payment_methods": ["gate"],
                "name": name,
                "lat": lat,
                "lon": lon,
                "capacity": capacity,
                "space_count": None,
                "neighborhood": _lookup_neighborhood(lat, lon),
                "btd_district": None,
                "permit_zone": None,
                "street_side": None,
                "parking_policy": {
                    "timezone": "America/New_York",
                    "rules": [],
                    "pricing_summary": "See operator for rates",
                    "pricing_version": 1,
                    "updated_at": today + "T00:00:00Z",
                },
                "restrictions": [],
                "free_on_holidays": [],
                "holiday_calendar": "none",
                "demand_signals": None,
                "snow_emergency_tow": False,
                "accessible": False,
                "accessible_spaces": None,
                "ev_charging": False,
                "source": "osm",
                "source_date": today,
                "last_updated": today,
                "needs_verification": True,
                "verification_method": "none",
                "data_completeness": data_completeness,
            },
        }
        features.append(feat)
        added += 1

    return added


def add_mbta_lots(features: list, mbta_path: Path) -> int:
    """Add MBTA park-and-ride lot records from the MBTA facilities API JSON."""
    if not mbta_path.exists():
        print("  [SKIP] mbta_facilities.json not found")
        return 0

    with open(mbta_path, encoding="utf-8") as f:
        data = json.load(f)

    today = __import__("datetime").date.today().isoformat()
    lots = data.get("data", [])
    added = 0

    for lot in lots:
        attrs = lot.get("attributes", {})
        lat = attrs.get("latitude")
        lon = attrs.get("longitude")
        if not lat or not lon:
            continue

        # Dedup: skip if an mbta_lot is already present within 50 m
        if already_present(features, lat, lon, type_filter="mbta_lot", radius_m=50):
            continue

        facility_id = lot.get("id", "")
        name = attrs.get("long_name") or attrs.get("short_name") or facility_id

        # Extract capacity and fee from properties list
        capacity = None
        fee_daily = None
        payment_form = None
        paybyphone_code = None
        for prop in attrs.get("properties", []):
            pname = prop.get("name", "")
            pval = prop.get("value")
            if pname == "capacity":
                try:
                    capacity = int(pval)
                except (ValueError, TypeError):
                    pass
            elif pname == "fee-daily":
                fee_daily = pval
            elif pname == "payment-form-accepted":
                payment_form = str(pval)
            elif pname == "payment-app-id":
                paybyphone_code = str(pval)

        # Determine payment apps
        pay_apps = ["PayByPhone"]
        if payment_form and "cash" in payment_form.lower():
            pay_apps.append("cash")

        # Parse fee_daily into a numeric amount or a note string
        flat_amount = None
        flat_note = None
        if fee_daily:
            try:
                flat_amount = float(fee_daily)
            except (ValueError, TypeError):
                flat_note = str(fee_daily)

        mbta_rules = []
        if fee_daily:
            mbta_rules.append({
                "id": "daily_rate",
                "priority": 100,
                "active": True,
                "days": ["mon","tue","wed","thu","fri","sat","sun"],
                "time_window": {"start": "00:00", "end": "24:00"},
                "rate": {
                    "kind": "flat_daily",
                    "price_per_day": {"currency": "USD", "amount": flat_amount} if flat_amount else None,
                    "note": flat_note,
                },
            })
        pricing_summary = (
            f"${flat_amount:.2f}/day" if flat_amount else (flat_note or "See MBTA for rates")
        )

        feat = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "schema_version": "1",
                "spot_id": f"mbta_{facility_id}",
                "geometry_type": "point",
                "type": "mbta_lot",
                "ownership": "public",
                "municipality": "Boston",
                "payment_app": pay_apps,
                "payment_methods": ["app"],
                "payment_id": paybyphone_code,
                "name": name,
                "lat": lat,
                "lon": lon,
                "capacity": capacity,
                "space_count": None,
                "neighborhood": _lookup_neighborhood(lat, lon),
                "btd_district": None,
                "permit_zone": None,
                "street_side": None,
                "parking_policy": {
                    "timezone": "America/New_York",
                    "rules": mbta_rules,
                    "pricing_summary": pricing_summary,
                    "pricing_version": 1,
                    "updated_at": today + "T00:00:00Z",
                },
                "restrictions": [],
                "free_on_holidays": ["mbta"],
                "holiday_calendar": "mbta",
                "demand_signals": None,
                "snow_emergency_tow": False,
                "accessible": False,
                "accessible_spaces": None,
                "ev_charging": False,
                "source": "mbta_official",
                "source_date": today,
                "last_updated": today,
                "needs_verification": True,
                "verification_method": "none",
                "data_completeness": "partial",
            },
        }
        features.append(feat)
        added += 1

    return added


def add_city_lots(features: list, osm_path: Path) -> int:
    """Add City of Boston–operated parking lots from Overpass query results."""
    if not osm_path.exists():
        print("  [SKIP] osm_city_lots.json not found")
        return 0

    with open(osm_path, encoding="utf-8") as f:
        data = json.load(f)

    today = __import__("datetime").date.today().isoformat()
    elements = [e for e in data.get("elements", []) if e.get("type") == "node"]
    added = 0

    for el in elements:
        lat = el.get("lat")
        lon = el.get("lon")
        if not lat or not lon:
            continue

        # Dedup within 30 m against any existing feature
        if already_present(features, lat, lon, type_filter=None, radius_m=30):
            continue

        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("operator") or ""
        capacity_raw = tags.get("capacity")
        try:
            capacity = int(capacity_raw) if capacity_raw else None
        except (ValueError, TypeError):
            capacity = None

        spot_id = f"city_lot_osm_{round(lat, 5)}_{round(abs(lon), 5)}"

        feat = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "schema_version": "1",
                "spot_id": spot_id,
                "geometry_type": "point",
                "type": "public_garage",
                "ownership": "public",
                "municipality": "Boston",
                "payment_app": ["gate"],
                "payment_methods": ["gate"],
                "name": name,
                "lat": lat,
                "lon": lon,
                "capacity": capacity,
                "space_count": None,
                "neighborhood": _lookup_neighborhood(lat, lon),
                "btd_district": None,
                "permit_zone": None,
                "street_side": None,
                "parking_policy": {
                    "timezone": "America/New_York",
                    "rules": [],
                    "pricing_summary": "See operator for rates",
                    "pricing_version": 1,
                    "updated_at": today + "T00:00:00Z",
                },
                "restrictions": [],
                "free_on_holidays": [],
                "holiday_calendar": "none",
                "demand_signals": None,
                "snow_emergency_tow": False,
                "accessible": False,
                "accessible_spaces": None,
                "ev_charging": False,
                "source": "osm",
                "source_date": today,
                "last_updated": today,
                "needs_verification": True,
                "verification_method": "none",
                "data_completeness": "low",
            },
        }
        features.append(feat)
        added += 1

    return added


# ---------------------------------------------------------------------------
# 8a. OSM EV Charging Stations — add as standalone records
# ---------------------------------------------------------------------------
def add_ev_chargers(features: list, osm_data: dict) -> int:
    """Add OSM EV charger nodes not already within 25 m of an existing feature."""
    today = __import__("datetime").date.today().isoformat()
    elements = [e for e in osm_data.get("elements", []) if e.get("type") == "node" and "lat" in e]
    added = 0

    for el in elements:
        lat, lon = el["lat"], el["lon"]
        if already_present(features, lat, lon, type_filter=None, radius_m=25):
            continue

        tags = el.get("tags", {})
        node_id = el.get("id", "")
        name = tags.get("name") or tags.get("operator") or "EV Charging Station"
        network = tags.get("network") or tags.get("operator") or None
        access = tags.get("access", "yes")

        capacity_raw = tags.get("capacity")
        try:
            capacity = int(capacity_raw) if capacity_raw else None
        except (ValueError, TypeError):
            capacity = None

        # Collect socket types from tags like socket:nacs, socket:type2
        sockets = [k.split(":")[1] for k in tags if k.startswith("socket:") and k.count(":") == 1]

        pay_apps = ["network_app"]
        pay_methods = ["app"]
        if tags.get("payment:credit_cards") == "yes":
            pay_methods.append("card")

        ownership = "private" if access in ("customers", "private") else "public"

        feat = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "schema_version": "1",
                "spot_id": f"ev_osm_{node_id}",
                "geometry_type": "point",
                "type": "ev_charging",
                "ownership": ownership,
                "municipality": "Boston",
                "payment_app": pay_apps,
                "payment_methods": pay_methods,
                "name": name,
                "lat": lat,
                "lon": lon,
                "capacity": capacity,
                "space_count": capacity,
                "ev_charger_count": capacity,
                "ev_network": network,
                "ev_socket_types": sockets,
                "neighborhood": _lookup_neighborhood(lat, lon),
                "btd_district": None,
                "permit_zone": None,
                "street_side": None,
                "parking_policy": {
                    "timezone": "America/New_York",
                    "rules": [],
                    "pricing_summary": "See EV network app for rates",
                    "pricing_version": 1,
                    "updated_at": today + "T00:00:00Z",
                },
                "restrictions": [],
                "free_on_holidays": [],
                "holiday_calendar": "none",
                "demand_signals": None,
                "snow_emergency_tow": False,
                "accessible": False,
                "accessible_spaces": None,
                "ev_charging": True,
                "source": "osm",
                "source_date": today,
                "last_updated": today,
                "needs_verification": True,
                "verification_method": "none",
                "data_completeness": "partial",
            },
        }
        features.append(feat)
        added += 1

    return added


# ---------------------------------------------------------------------------
# 8b. OSAP Accessible Spaces — add unmatched spots as standalone records
# ---------------------------------------------------------------------------
def add_accessible_spots(features: list, gj: dict) -> int:
    """Add OSAP accessible parking spots not already matched to an existing feature."""
    today = __import__("datetime").date.today().isoformat()
    osap_feats = gj.get("features", [])
    added = 0

    for f in osap_feats:
        try:
            coords = f["geometry"]["coordinates"]
            lon, lat = coords[0], coords[1]
        except Exception:
            continue

        # Skip spots already matched by enrich_osap (any feature within 20 m)
        if already_present(features, lat, lon, type_filter=None, radius_m=20):
            continue

        props = f.get("properties", {})
        obj_id = props.get("ObjectId") or props.get("objectid") or props.get("OBJECTID", "")
        try:
            num_spaces = int(props.get("number_of_ap_spaces") or 1)
        except (ValueError, TypeError):
            num_spaces = 1

        year_confirmed = props.get("year_space_last_confirmed")
        source_date = str(int(year_confirmed)) if year_confirmed else today

        address = props.get("address_full") or props.get("address_street_name") or ""
        neighborhood = props.get("commercial_area") or _lookup_neighborhood(lat, lon)
        spot_id = f"osap_{obj_id}" if obj_id else f"osap_{round(lat,5)}_{round(abs(lon),5)}"

        feat = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "schema_version": "1",
                "spot_id": spot_id,
                "geometry_type": "point",
                "type": "accessible_parking",
                "ownership": "public",
                "municipality": "Boston",
                "payment_app": [],
                "payment_methods": ["free"],
                "name": address or "Accessible Parking",
                "address": address,
                "lat": lat,
                "lon": lon,
                "capacity": num_spaces,
                "space_count": num_spaces,
                "year_last_confirmed": int(year_confirmed) if year_confirmed else None,
                "neighborhood": neighborhood,
                "btd_district": None,
                "permit_zone": None,
                "street_side": None,
                "parking_policy": {
                    "timezone": "America/New_York",
                    "rules": [{
                        "id": "accessible_free",
                        "priority": 100,
                        "active": True,
                        "days": ["mon","tue","wed","thu","fri","sat","sun"],
                        "time_window": {"start": "00:00", "end": "24:00"},
                        "rate": {"kind": "free"},
                    }],
                    "pricing_summary": "Free — accessible parking",
                    "pricing_version": 1,
                    "updated_at": today + "T00:00:00Z",
                },
                "restrictions": [],
                "free_on_holidays": [],
                "holiday_calendar": "none",
                "demand_signals": None,
                "snow_emergency_tow": False,
                "accessible": True,
                "accessible_spaces": num_spaces,
                "ev_charging": False,
                "source": "osap",
                "source_date": source_date,
                "last_updated": today,
                "needs_verification": False,
                "verification_method": "official_source",
                "data_completeness": "partial",
            },
        }
        features.append(feat)
        added += 1

    return added


# ---------------------------------------------------------------------------
# Re-export flat CSV
# ---------------------------------------------------------------------------
CSV_FIELDS = [
    "spot_id", "payment_id", "payment_app", "name", "address",
    "street_side", "neighborhood", "btd_district", "municipality", "type", "ownership",
    "lat", "lon", "pricing_summary", "space_count", "capacity", "permit_zone",
    "snow_emergency_tow", "accessible", "accessible_spaces", "accessible_type",
    "ev_charging", "ev_charger_count", "ev_network", "citation_rate_per_space_month",
    "source", "source_date", "last_updated", "data_completeness",
]

def export_csv_to(features: list[dict], path: Path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for feat in features:
            p = feat["properties"]
            row = {k: p.get(k, "") for k in CSV_FIELDS}
            if isinstance(row.get("payment_app"), list):
                row["payment_app"] = "|".join(row["payment_app"])
            ds = p.get("demand_signals") or {}
            row["citation_rate_per_space_month"] = ds.get("citation_rate_per_space_month", "")
            row["pricing_summary"] = (p.get("parking_policy") or {}).get("pricing_summary", "")
            geom = feat.get("geometry")
            if geom and geom.get("coordinates"):
                row["lon"], row["lat"] = geom["coordinates"]
            writer.writerow(row)

def export_csv(features: list[dict]):
    export_csv_to(features, CSV_PATH)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not GEOJSON_PATH.exists():
        sys.exit(f"ERROR: {GEOJSON_PATH} not found. Run build_dataset.py first.")

    print(f"\nLoading {GEOJSON_PATH.name} ...")
    with open(GEOJSON_PATH, encoding="utf-8") as f:
        geojson = json.load(f)
    features = geojson["features"]
    print(f"  {len(features)} features loaded\n")

    report = {}

    # ---- Download all sources ----
    print("=" * 60)
    print("STEP 0 — Downloading source datasets")
    print("=" * 60)
    paths = {key: download(key) for key in SOURCES}

    # ---- 1. Snow zones ----
    print("\n" + "=" * 60)
    print("STEP 1 — Snow Emergency Tow Zones (point-in-polygon)")
    print("=" * 60)
    gj = load_geojson(paths["snow_zones"])
    if gj:
        n = enrich_snow(features, gj)
        print(f"  {n} meters flagged snow_emergency_tow=true")
        report["snow_emergency_tow"] = n
    else:
        print("  [SKIP] No snow zone data")
        report["snow_emergency_tow"] = "skipped"

    # ---- 2. Accessible spaces ----
    print("\n" + "=" * 60)
    print("STEP 2 — OSAP Accessible Spaces (nearest within 20 m)")
    print("=" * 60)
    gj = load_geojson(paths["osap"])
    if gj:
        n = enrich_osap(features, gj)
        print(f"  {n} meters flagged accessible=true")
        report["accessible"] = n
    else:
        print("  [SKIP] No OSAP data")
        report["accessible"] = "skipped"

    # ---- 3. EV chargers ----
    print("\n" + "=" * 60)
    print("STEP 3 — OSM EV Charging Stations (nearest within 25 m)")
    print("=" * 60)
    if paths["osm_ev"] and paths["osm_ev"].exists():
        try:
            with open(paths["osm_ev"], encoding="utf-8") as f:
                osm_ev = json.load(f)
            n = enrich_ev(features, osm_ev)
            print(f"  {n} meters flagged ev_charging=true")
            report["ev_charging"] = n
        except Exception as e:
            print(f"  [SKIP] {e}")
            report["ev_charging"] = "skipped"
    else:
        print("  [SKIP] No OSM EV data")
        report["ev_charging"] = "skipped"

    # ---- 4. OSM Parking (garages/capacity) ----
    print("\n" + "=" * 60)
    print("STEP 4 — OSM Parking (capacity + new garage candidates)")
    print("=" * 60)
    if paths["osm_parking"] and paths["osm_parking"].exists():
        try:
            with open(paths["osm_parking"], encoding="utf-8") as f:
                osm_park = json.load(f)
            n = enrich_osm_parking(features, osm_park)
            print(f"  {n} meters matched to OSM parking node (capacity updated where available)")
            report["osm_parking_matched"] = n
        except Exception as e:
            print(f"  [SKIP] {e}")
            report["osm_parking_matched"] = "skipped"
    else:
        print("  [SKIP] No OSM parking data")
        report["osm_parking_matched"] = "skipped"

    # ---- 5. Permit zones ----
    print("\n" + "=" * 60)
    print("STEP 5 — Resident Permit Zones (point-in-polygon)")
    print("=" * 60)
    gj = load_geojson(paths["permit_zones"])
    if gj:
        n = enrich_permit_zones(features, gj)
        print(f"  {n} meters matched to a permit zone polygon")
        report["permit_zone_matched"] = n
    else:
        print("  [SKIP] No permit zone data")
        report["permit_zone_matched"] = "skipped"

    # ---- 6. Citations ----
    print("\n" + "=" * 60)
    print("STEP 6 — BTD Parking Citations (demand_signals.citation_rate)")
    print("=" * 60)
    cit_path = paths.get("citations")
    if cit_path and cit_path.exists():
        n = enrich_citations(features, cit_path)
        print(f"  {n} meters received a citation_rate_per_space_month value")
        report["citation_enriched"] = n
    else:
        print("  [SKIP] No citation data")
        report["citation_enriched"] = "skipped"

    # ---- 7. Off-street facilities ----
    print("\n" + "=" * 60)
    print("STEP 7 -- Adding off-street facilities")
    print("=" * 60)

    # 7a. OSM garages already identified in raw/osm_new_garages.json
    n7a = add_osm_garages(features, RAW_DIR / "osm_new_garages.json")
    print(f"  {n7a} OSM garage records added")
    report["osm_garages_added"] = n7a

    # 7b. MBTA park-and-ride lots
    mbta_path = paths.get("mbta_facilities")
    if mbta_path and mbta_path.exists():
        n7b = add_mbta_lots(features, mbta_path)
        print(f"  {n7b} MBTA lot records added")
        report["mbta_lots_added"] = n7b
    else:
        print("  [SKIP] No MBTA facilities data")
        report["mbta_lots_added"] = "skipped"

    # 7c. City of Boston lots via Overpass operator query
    city_path = paths.get("osm_city_lots")
    if city_path and city_path.exists():
        n7c = add_city_lots(features, city_path)
        print(f"  {n7c} city lot records added")
        report["city_lots_added"] = n7c
    else:
        print("  [SKIP] No OSM city lots data")
        report["city_lots_added"] = "skipped"

    # ---- 8. Standalone EV chargers and accessible spots ----
    print("\n" + "=" * 60)
    print("STEP 8 -- Adding EV chargers and accessible spots")
    print("=" * 60)

    # 8a. EV chargers from OSM (not near any existing feature)
    if paths["osm_ev"] and paths["osm_ev"].exists():
        try:
            with open(paths["osm_ev"], encoding="utf-8") as f:
                osm_ev_data = json.load(f)
            n8a = add_ev_chargers(features, osm_ev_data)
            print(f"  {n8a} EV charger records added")
            report["ev_chargers_added"] = n8a
        except Exception as e:
            print(f"  [SKIP] EV chargers: {e}")
            report["ev_chargers_added"] = "skipped"
    else:
        print("  [SKIP] No OSM EV data")
        report["ev_chargers_added"] = "skipped"

    # 8b. OSAP accessible spots not matched to an existing meter
    osap_gj = load_geojson(paths["osap"])
    if osap_gj:
        n8b = add_accessible_spots(features, osap_gj)
        print(f"  {n8b} accessible parking records added")
        report["accessible_spots_added"] = n8b
    else:
        print("  [SKIP] No OSAP data for standalone records")
        report["accessible_spots_added"] = "skipped"

    # ---- Write outputs ----
    print("\n" + "=" * 60)
    print("Writing enriched outputs ...")
    print("=" * 60)

    geojson["enriched"] = __import__("datetime").date.today().isoformat()
    geojson["record_count"] = len(features)
    with open(GEOJSON_PATH, "w", encoding="utf-8") as f:
        json.dump(geojson, f, indent=2, default=str)
    print(f"  {GEOJSON_PATH.name}")

    try:
        export_csv(features)
        print(f"  {CSV_PATH.name}")
    except PermissionError:
        alt = CSV_PATH.parent / "boston_parking_enriched.csv"
        export_csv_to(features, alt)
        print(f"  [NOTE] boston_parking.csv was locked — written to {alt.name} instead")

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"  {REPORT_PATH.name}")

    print("\n=== Enrichment complete ===")
    for k, v in report.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
