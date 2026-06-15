"""
flatten_geojson.py — Convert boston_parking.geojson to a flat CSV.

Reads any boston_parking-schema GeoJSON and writes a CSV with all key fields
flattened. Useful when you have the GeoJSON and want a fresh CSV without
re-running the full enrichment pipeline.

Usage:
    python flatten_geojson.py                          # default paths
    python flatten_geojson.py input.geojson out.csv
"""

import csv
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
DEFAULT_INPUT  = BASE_DIR / "boston_parking.geojson"
DEFAULT_OUTPUT = BASE_DIR / "boston_parking.csv"

CSV_FIELDS = [
    "spot_id",
    "payment_id",
    "payment_app",
    "name",
    "address",
    "street_side",
    "neighborhood",
    "btd_district",
    "municipality",
    "type",
    "ownership",
    "lat",
    "lon",
    "pricing_summary",
    "space_count",
    "capacity",
    "permit_zone",
    "snow_emergency_tow",
    "snow_emergency_safe_spot",
    "accessible",
    "accessible_spaces",
    "year_last_confirmed",
    "ev_charging",
    "ev_network",
    "ev_socket_types",
    "citation_rate_per_space_month",
    "free_on_holidays",
    "holiday_calendar",
    "source",
    "source_date",
    "last_updated",
    "needs_verification",
    "data_completeness",
]


def flatten_feature(feat: dict) -> dict:
    p = feat.get("properties") or {}
    geom = feat.get("geometry") or {}
    coords = geom.get("coordinates") or []

    row: dict = {f: p.get(f, "") for f in CSV_FIELDS}

    # Geometry → lat/lon
    if len(coords) >= 2:
        row["lon"] = coords[0]
        row["lat"] = coords[1]

    # parking_policy.pricing_summary
    policy = p.get("parking_policy") or {}
    row["pricing_summary"] = policy.get("pricing_summary", "")

    # demand_signals.citation_rate_per_space_month
    ds = p.get("demand_signals") or {}
    row["citation_rate_per_space_month"] = ds.get("citation_rate_per_space_month", "")

    # Serialize list fields to pipe-separated strings
    for key in ("payment_app", "payment_methods", "free_on_holidays", "ev_socket_types"):
        v = p.get(key)
        if isinstance(v, list):
            row[key] = "|".join(str(x) for x in v)

    return row


def flatten(input_path: Path, output_path: Path) -> int:
    with open(input_path, encoding="utf-8") as f:
        gj = json.load(f)

    features = gj.get("features", [])

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for feat in features:
            writer.writerow(flatten_feature(feat))

    return len(features)


def main():
    args = sys.argv[1:]
    input_path  = Path(args[0]) if len(args) >= 1 else DEFAULT_INPUT
    output_path = Path(args[1]) if len(args) >= 2 else DEFAULT_OUTPUT

    if not input_path.exists():
        sys.exit(f"ERROR: {input_path} not found.")

    print(f"Reading {input_path} ...")
    n = flatten(input_path, output_path)
    print(f"Wrote {n} records -> {output_path}")


if __name__ == "__main__":
    main()
