"""
merge_parking.py — Merge Boston, Brookline, and Cambridge parking CSVs into all_parking.csv.

Usage:
    python merge_parking.py                    # default input/output paths
    python merge_parking.py out.csv            # custom output path

Inputs (must exist):
    boston_parking.csv
    brookline_parking.csv
    cambridge_parking.csv

Output:
    all_parking.csv  (or custom path)

Municipality-specific columns are included for all rows; missing values are blank.
"""

import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent

INPUTS = [
    BASE_DIR / "boston_parking.csv",
    BASE_DIR / "brookline_parking.csv",
    BASE_DIR / "cambridge_parking.csv",
]

DEFAULT_OUTPUT = BASE_DIR / "all_parking.csv"

# Superset of both schemas, in logical order.
# Boston-only: btd_district, ev_network, citation_rate_per_space_month
# Brookline-only: overnight_rental_spaces, overnight_guest_spaces, lot_number, accessible_type
MERGED_FIELDS = [
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
    "overnight_rental_spaces",
    "overnight_guest_spaces",
    "lot_number",
    "permit_zone",
    "snow_emergency_tow",
    "accessible",
    "accessible_spaces",
    "accessible_type",
    "ev_charging",
    "ev_charger_count",
    "ev_network",
    "citation_rate_per_space_month",
    "source",
    "source_date",
    "last_updated",
    "data_completeness",
]


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT

    missing = [p for p in INPUTS if not p.exists()]
    if missing:
        for p in missing:
            print(f"  [missing] {p}")
        sys.exit("Run build_dataset.py + enrich_dataset.py, build_brookline.py, and build_cambridge.py first.")

    all_rows = []
    for path in INPUTS:
        rows = read_csv(path)
        all_rows.extend(rows)
        print(f"  {len(rows):>6} rows  <- {path.name}")

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MERGED_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in all_rows:
            # Fill missing columns with empty string
            merged_row = {col: row.get(col, "") for col in MERGED_FIELDS}
            writer.writerow(merged_row)

    print(f"\n  {len(all_rows)} total rows -> {output_path.name}")

    # Quick breakdown
    by_municipality: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for row in all_rows:
        m = row.get("municipality", "unknown")
        t = row.get("type", "unknown")
        by_municipality[m] = by_municipality.get(m, 0) + 1
        by_type[t] = by_type.get(t, 0) + 1

    print("\n  By municipality:")
    for m, n in sorted(by_municipality.items()):
        print(f"    {m:<20} {n}")
    print("\n  By type:")
    for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"    {t:<25} {n}")


if __name__ == "__main__":
    main()
