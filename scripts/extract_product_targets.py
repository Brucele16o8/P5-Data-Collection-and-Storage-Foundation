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

from glamira_aws.mongodb import (
    MongoSettings,
    extract_product_targets_result,
    get_collection,
    iter_product_targets_mongodb,
    product_event_query,
    write_csv,
    write_jsonl,
)
from glamira_aws.observability import TerminalProgressObserver
from glamira_aws.product_targets import ProductTargetExportRequest, ProductTargetExportService
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
    parser.add_argument(
        "--include-all-candidates",
        action="store_true",
        help="Write every ranked product_id + URL candidate instead of one best URL per product_id.",
    )
    parser.add_argument("--limit-records", type=int, default=None)
    parser.add_argument(
        "--aggregation-engine",
        choices=("mongodb", "python"),
        default=os.getenv("PRODUCT_TARGET_AGGREGATION_ENGINE", "mongodb"),
        help="Use MongoDB aggregation for full runs; Python mode is mainly for small local samples/tests.",
    )
    parser.add_argument("--progress-every", type=int, default=int(os.getenv("PROGRESS_EVERY", "10000")))
    parser.add_argument("--summary-output", default=str(output_directory / "product_targets_summary.json"))
    return parser.parse_args()


def _write_streamed_targets(args: argparse.Namespace) -> dict[str, int | str | bool | None]:
    output_path = Path(args.output).expanduser()

    collection = get_collection(args.uri, args.database, args.collection)
    scanned_records = None
    try:
        scanned_records = collection.count_documents(product_event_query())
        if args.limit_records is not None:
            scanned_records = min(scanned_records, args.limit_records)
    except Exception:
        scanned_records = None

    observer = (
        TerminalProgressObserver(
            "Streaming MongoDB product target aggregation",
            total=None,
            every=args.progress_every,
        )
        if args.progress_every
        else None
    )
    service = ProductTargetExportService(observer=observer)
    result = service.export_stream(
        iter_product_targets_mongodb(
            args.uri,
            args.database,
            args.collection,
            limit_records=args.limit_records,
            include_all_candidates=True,
        ),
        ProductTargetExportRequest(
            output_path=output_path,
            output_format=args.format,
            include_all_candidates=args.include_all_candidates,
        ),
        scanned_records=scanned_records,
    )

    return {
        "aggregation_engine": result.aggregation_engine,
        "output_mode": result.output_mode,
        "scanned_records": result.scanned_records,
        "candidate_records": result.candidate_records,
        "grouped_url_count": result.grouped_url_count,
        "target_rows": result.target_rows,
        "unique_products": result.unique_products,
    }


def main() -> None:
    args = parse_args()
    started_at = monotonic()
    result = None
    cancelled = False
    try:
        if args.aggregation_engine == "mongodb":
            stream_stats = _write_streamed_targets(args)
            elapsed = monotonic() - started_at
            summary = {
                "stage": "extract_product_targets",
                "cancelled": cancelled,
                "mongo_uri": args.uri,
                "database": args.database,
                "collection": args.collection,
                "output": args.output,
                "summary_output": args.summary_output,
                "include_all_candidates": args.include_all_candidates,
                **stream_stats,
                "elapsed_seconds": round(elapsed, 3),
                "elapsed": format_duration(elapsed),
            }
            write_summary_json(args.summary_output, summary)
            print(f"Wrote {stream_stats['target_rows']} product target rows to {args.output}")
            print(f"Wrote extraction summary to {args.summary_output}")
            print(f"Extraction finished in {format_duration(elapsed)}")
            return

        result = extract_product_targets_result(
            uri=args.uri,
            database=args.database,
            collection_name=args.collection,
            limit_records=args.limit_records,
            progress_every=args.progress_every,
            include_all_candidates=args.include_all_candidates,
        )
    except KeyboardInterrupt:
        cancelled = True
        elapsed = monotonic() - started_at
        summary = {
            "stage": "extract_product_targets",
            "cancelled": cancelled,
            "aggregation_engine": args.aggregation_engine,
            "mongo_uri": args.uri,
            "database": args.database,
            "collection": args.collection,
            "output": args.output,
            "summary_output": args.summary_output,
            "include_all_candidates": args.include_all_candidates,
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

    rows = result.rows
    output_mode = "all_ranked_candidates" if args.include_all_candidates else "one_best_target_per_product"
    print(
        f"Grouped {result.grouped_url_count:,} product URL candidates; "
        f"writing {len(rows):,} rows in {output_mode} mode",
        flush=True,
    )
    write_progress_every = max(1, len(rows) // 10) if rows else args.progress_every
    if args.format == "jsonl":
        write_jsonl(
            args.output,
            rows,
            total=len(rows),
            progress_every=write_progress_every,
            progress_label="Writing product target JSONL",
        )
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
            total=len(rows),
            progress_every=write_progress_every,
            progress_label="Writing product target CSV",
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
        "include_all_candidates": args.include_all_candidates,
        "output_mode": output_mode,
        "scanned_records": result.scanned_records,
        "candidate_records": result.candidate_records,
        "grouped_url_count": result.grouped_url_count,
        "target_rows": len(rows),
        "unique_products": result.unique_products,
        "elapsed_seconds": round(elapsed, 3),
        "elapsed": format_duration(elapsed),
    }
    write_summary_json(args.summary_output, summary)
    print(f"Wrote {len(rows)} product target rows to {args.output}")
    print(f"Wrote extraction summary to {args.summary_output}")
    print(f"Extraction finished in {format_duration(elapsed)}")


if __name__ == "__main__":
    main()
