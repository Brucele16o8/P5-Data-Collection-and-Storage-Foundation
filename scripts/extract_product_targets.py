"""Extract ranked product crawl targets from a MongoDB Countly summary collection."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from glamira_aws.mongodb import MongoSettings, extract_product_targets, write_csv, write_jsonl
from glamira_aws.progress import format_duration, write_summary_json


def parse_args() -> argparse.Namespace:
    defaults = MongoSettings.from_env()
    output_directory = Path(os.getenv("OUTPUT_DIRECTORY", "outputs"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default=defaults.uri)
    parser.add_argument("--database", default=defaults.database)
    parser.add_argument("--collection", default=defaults.collection)
    parser.add_argument("--output", default=str(output_directory / "product_targets.csv"))
    parser.add_argument("--format", choices=("csv", "jsonl"), default="csv")
    parser.add_argument("--limit-records", type=int, default=None)
    parser.add_argument("--progress-every", type=int, default=int(os.getenv("PROGRESS_EVERY", "10000")))
    parser.add_argument("--summary-output", default=str(output_directory / "product_targets_summary.json"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started_at = monotonic()
    rows = []
    cancelled = False
    try:
        rows = extract_product_targets(
            uri=args.uri,
            database=args.database,
            collection_name=args.collection,
            limit_records=args.limit_records,
            progress_every=args.progress_every,
        )
    except KeyboardInterrupt:
        cancelled = True
        elapsed = monotonic() - started_at
        summary = {
            "stage": "extract_product_targets",
            "cancelled": cancelled,
            "mongo_uri": args.uri,
            "database": args.database,
            "collection": args.collection,
            "output": args.output,
            "summary_output": args.summary_output,
            "target_rows": 0,
            "unique_products": 0,
            "elapsed_seconds": round(elapsed, 3),
            "elapsed": format_duration(elapsed),
            "note": "Cancelled before aggregation completed; no product target output was written.",
        }
        write_summary_json(args.summary_output, summary)
        print("Cancelled product target extraction before output was written.", flush=True)
        print(f"Wrote cancellation summary to {args.summary_output}", flush=True)
        sys.exit(130)

    print(f"Aggregated {len(rows)} ranked product target rows", flush=True)
    if args.format == "jsonl":
        write_jsonl(args.output, rows)
    else:
        write_csv(
            args.output,
            rows,
            [
                "product_id",
                "candidate_url",
                "source_event_type",
                "url_source_field",
                "event_count",
                "first_seen_at",
                "last_seen_at",
                "url_rank",
            ],
        )
    elapsed = monotonic() - started_at
    summary = {
        "stage": "extract_product_targets",
        "cancelled": cancelled,
        "mongo_uri": args.uri,
        "database": args.database,
        "collection": args.collection,
        "output": args.output,
        "summary_output": args.summary_output,
        "target_rows": len(rows),
        "unique_products": len({row.get("product_id") for row in rows if row.get("product_id")}),
        "elapsed_seconds": round(elapsed, 3),
        "elapsed": format_duration(elapsed),
    }
    write_summary_json(args.summary_output, summary)
    print(f"Wrote {len(rows)} product target rows to {args.output}")
    print(f"Wrote extraction summary to {args.summary_output}")
    print(f"Extraction finished in {format_duration(elapsed)}")


if __name__ == "__main__":
    main()
