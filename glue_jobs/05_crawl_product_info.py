"""Glue job 05: crawl one product page per product_id from ranked URL candidates."""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from glamira_aws.glue_runtime import parse_job_args
from glamira_aws.product_crawler import fetch_product_info
from glamira_aws.s3_io import is_s3_uri, list_s3_objects, read_csv, read_text, write_csv


def _read_candidate_rows(input_uri: str) -> list[dict[str, str]]:
    if input_uri.endswith(".csv"):
        return read_csv(input_uri)

    csv_uris = list_s3_objects(input_uri, suffixes=(".csv",))
    rows: list[dict[str, str]] = []
    for uri in csv_uris:
        rows.extend(read_csv(uri))
    if rows:
        return rows

    raise ValueError(
        "Crawler input must be CSV for Python Shell jobs. "
        "If job 04 writes Parquet, either run this crawler with Spark or export candidates to CSV first."
    )


def _pick_best_candidates(rows: list[dict[str, str]], limit: int | None = None) -> list[dict[str, str]]:
    def sort_key(row: dict[str, str]) -> tuple[str, int, int]:
        rank = int(row.get("url_rank") or 999999)
        count = int(row.get("event_count") or 0)
        return (row.get("product_id", ""), rank, -count)

    best: dict[str, dict[str, str]] = {}
    for row in sorted(rows, key=sort_key):
        product_id = row.get("product_id")
        url = row.get("candidate_url")
        if not product_id or not url or product_id in best:
            continue
        best[product_id] = row
        if limit and len(best) >= limit:
            break
    return list(best.values())


def main() -> None:
    args = parse_job_args(
        required=["input_uri", "output_uri"],
        optional=["limit", "timeout_seconds"],
    )
    limit = int(args["limit"]) if args.get("limit") else None
    timeout_seconds = int(args.get("timeout_seconds", "20"))

    candidates = _pick_best_candidates(_read_candidate_rows(args["input_uri"]), limit=limit)
    rows = [
        fetch_product_info(
            product_id=row["product_id"],
            source_url=row["candidate_url"],
            timeout_seconds=timeout_seconds,
        ).to_dict()
        for row in candidates
    ]

    fieldnames = [
        "product_id",
        "source_url",
        "product_name",
        "category",
        "price",
        "currency",
        "active",
        "scraped_at",
        "status",
        "error_message",
    ]
    write_csv(f"{args['output_uri'].rstrip('/')}/product_info.csv", rows, fieldnames)


if __name__ == "__main__":
    main()
