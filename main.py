"""Local entry point for the Project 05 Python package."""

from __future__ import annotations


PIPELINE_STEPS = [
    ("01", "restore or sample the MongoDB dump into local Docker MongoDB", "mongorestore --drop <dump-path>"),
    ("02", "explore raw records and maintain the data dictionary", "docs/schema/schema-countly-summary-standardJSON.json"),
    ("03", "create a local MongoDB sample collection", "scripts/create_mongodb_sample.py"),
    ("04", "process distinct visitor IPs from all MongoDB source records", "scripts/process_ip_locations.py"),
    ("05", "extract ranked product crawl targets from MongoDB", "scripts/extract_product_targets.py"),
    ("06", "crawl one active product information row per product ID", "scripts/crawl_products.py"),
    ("07", "split product output into warehouse-friendly CSV and raw react_data JSONL", "scripts/export_product_warehouse.py"),
]


def main() -> None:
    print("Project 05 local MongoDB + Python pipeline")
    for step_id, purpose, path in PIPELINE_STEPS:
        print(f"{step_id}. {purpose}: {path}")


if __name__ == "__main__":
    main()
