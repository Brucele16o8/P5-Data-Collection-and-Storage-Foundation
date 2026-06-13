"""Crawl one best product URL per product ID and optionally upload the result to S3."""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from pathlib import Path
from time import monotonic
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from glamira_aws.mongodb import read_csv, read_jsonl, write_jsonl
from glamira_aws.product_crawler import DEFAULT_FALLBACK_DOMAINS, fetch_product_info
from glamira_aws.progress import ProgressReporter, format_duration, write_summary_json
from glamira_aws.s3_io import is_s3_uri, parse_s3_uri


def parse_args() -> argparse.Namespace:
    output_directory = Path(os.getenv("OUTPUT_DIRECTORY", "outputs"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(output_directory / "product_targets.csv"))
    parser.add_argument("--output", default=str(output_directory / "product_information.jsonl"))
    parser.add_argument("--output-s3-uri", default=os.getenv("OUTPUT_S3_URI"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--workers", type=int, default=int(os.getenv("CRAWL_WORKERS", "1")))
    parser.add_argument("--progress-every", type=int, default=int(os.getenv("PROGRESS_EVERY", "25")))
    parser.add_argument("--summary-output", default=str(output_directory / "product_information_summary.json"))
    parser.add_argument(
        "--fallback-domains",
        default=os.getenv("GLAMIRA_FALLBACK_DOMAINS", ",".join(DEFAULT_FALLBACK_DOMAINS)),
        help="Comma-separated Glamira domains for /catalog/product/view/id/{product_id} fallback.",
    )
    return parser.parse_args()


def _read_targets(path: str) -> list[dict[str, str]]:
    if path.endswith(".jsonl"):
        return read_jsonl(path)
    return read_csv(path)


def _pick_best_targets(rows: list[dict[str, str]], limit: int | None) -> list[dict[str, str]]:
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


def _upload_to_s3(local_path: str, s3_uri: str) -> None:
    if not is_s3_uri(s3_uri):
        raise ValueError("--output-s3-uri must start with s3://")
    import boto3

    bucket, key = parse_s3_uri(s3_uri)
    boto3.client("s3").upload_file(local_path, bucket, key)


def main() -> None:
    started_at = monotonic()
    args = parse_args()
    fallback_domains = [domain.strip() for domain in args.fallback_domains.split(",") if domain.strip()]
    targets = _pick_best_targets(_read_targets(args.input), args.limit)
    print(f"Loaded {len(targets)} distinct product targets from {args.input}", flush=True)
    def crawl(row: dict[str, str]) -> dict[str, object]:
        return fetch_product_info(
            product_id=row["product_id"],
            source_url=row["candidate_url"],
            timeout_seconds=args.timeout_seconds,
            fallback_domains=fallback_domains,
        ).to_dict()

    progress = ProgressReporter("Crawling products", total=len(targets), every=args.progress_every)
    active_count = 0
    ok_count = 0

    rows = []
    cancelled = False
    try:
        if args.workers > 1:
            executor = ThreadPoolExecutor(max_workers=args.workers)
            futures = [executor.submit(crawl, row) for row in targets]
            try:
                for completed, future in enumerate(as_completed(futures), start=1):
                    row = future.result()
                    rows.append(row)
                    active_count += 1 if row.get("active") else 0
                    ok_count += 1 if row.get("status") == "ok" else 0
                    progress.report(completed, suffix=f"ok={ok_count:,}, active={active_count:,}")
            except KeyboardInterrupt:
                cancelled = True
                for future in futures:
                    future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                raise
            else:
                executor.shutdown(wait=True)
        else:
            for completed, target in enumerate(targets, start=1):
                row = crawl(target)
                rows.append(row)
                active_count += 1 if row.get("active") else 0
                ok_count += 1 if row.get("status") == "ok" else 0
                progress.report(completed, suffix=f"ok={ok_count:,}, active={active_count:,}")
    except KeyboardInterrupt:
        cancelled = True
        print("Cancellation requested. Writing partial crawl output and summary...", flush=True)

    progress.report(len(rows), force=True, suffix=f"ok={ok_count:,}, active={active_count:,}")
    write_jsonl(args.output, rows)
    print(f"Wrote {len(rows)} product information rows to {args.output}")

    elapsed = monotonic() - started_at
    status_counts = Counter(str(row.get("status")) for row in rows)
    country_counts = Counter(str(row.get("country_store")) for row in rows if row.get("country_store"))
    summary = {
        "stage": "crawl_products",
        "cancelled": cancelled,
        "input": args.input,
        "output": args.output,
        "summary_output": args.summary_output,
        "target_count": len(targets),
        "processed_count": len(rows),
        "remaining_count": max(len(targets) - len(rows), 0),
        "ok_count": ok_count,
        "active_count": active_count,
        "status_counts": dict(status_counts),
        "country_store_counts": dict(country_counts),
        "workers": args.workers,
        "timeout_seconds": args.timeout_seconds,
        "elapsed_seconds": round(elapsed, 3),
        "elapsed": format_duration(elapsed),
    }
    write_summary_json(args.summary_output, summary)
    print(f"Wrote crawl summary to {args.summary_output}")
    print(
        f"Crawl {'cancelled' if cancelled else 'finished'} in {format_duration(elapsed)}: "
        f"processed={len(rows):,}/{len(targets):,}, ok={ok_count:,}, active={active_count:,}",
        flush=True,
    )

    if args.output_s3_uri:
        _upload_to_s3(args.output, args.output_s3_uri)
        print(f"Uploaded {args.output} to {args.output_s3_uri}")

    if cancelled:
        sys.exit(130)


if __name__ == "__main__":
    main()
