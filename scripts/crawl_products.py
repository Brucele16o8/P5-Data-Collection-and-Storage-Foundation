"""Crawl one best product URL per product ID into a local JSONL file."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from pathlib import Path
from time import monotonic
from collections import Counter
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from glamira_aws.mongodb import read_csv, read_jsonl
from glamira_aws.crawl_state import ensure_append_starts_on_new_line
from glamira_aws.crawl_state import latest_rows_by_product
from glamira_aws.crawl_state import read_crawl_checkpoint
from glamira_aws.crawl_state import write_failed_targets
from glamira_aws.product_crawler import DEFAULT_FALLBACK_DOMAINS, fetch_product_info
from glamira_aws.progress import ProgressReporter, format_duration, write_summary_json


def parse_args() -> argparse.Namespace:
    output_directory = Path(os.getenv("OUTPUT_DIRECTORY", "outputs"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(output_directory / "product_targets.csv"))
    parser.add_argument("--output", default=str(output_directory / "product_information.jsonl"))
    parser.add_argument("--failed-output", default=str(output_directory / "product_failed_targets.csv"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--resume", action="store_true", help="Skip product IDs already present in the output JSONL.")
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


def _iter_target_rows(path: str) -> Iterable[dict[str, str]]:
    input_path = Path(path).expanduser()
    if path.endswith(".jsonl"):
        with input_path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)
        return

    with input_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        yield from reader


def _iter_best_targets(path: str, limit: int | None = None) -> Iterable[dict[str, str]]:
    last_product_id = None
    yielded = 0
    for row in _iter_target_rows(path):
        product_id = row.get("product_id")
        url = row.get("candidate_url")
        if not product_id or not url:
            continue
        product_id = str(product_id)
        if product_id == last_product_id:
            continue
        last_product_id = product_id
        yield row
        yielded += 1
        if limit and yielded >= limit:
            break


def _count_best_targets(path: str, limit: int | None = None) -> int:
    return sum(1 for _ in _iter_best_targets(path, limit))


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


def _percentage(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 2)


def _read_checkpoint(path: str, target_product_ids: set[str] | None = None) -> dict[str, object]:
    return read_crawl_checkpoint(path, target_product_ids)


def _latest_rows_by_product(path: str, target_product_ids: set[str] | None = None) -> dict[str, dict[str, object]]:
    return latest_rows_by_product(path, target_product_ids)


def _write_failed_targets(
    product_info_path: str,
    failed_output_path: str,
    targets: Iterable[dict[str, str]],
) -> int:
    return write_failed_targets(product_info_path, failed_output_path, targets)


def _write_jsonl_row(handle, row: dict[str, object]) -> None:
    handle.write(json.dumps(row, default=str, ensure_ascii=False) + "\n")
    handle.flush()


def _ensure_append_starts_on_new_line(path: str) -> None:
    ensure_append_starts_on_new_line(path)


def main() -> None:
    started_at = monotonic()
    args = parse_args()
    fallback_domains = [domain.strip() for domain in args.fallback_domains.split(",") if domain.strip()]
    target_count = _count_best_targets(args.input, args.limit)

    checkpoint = _read_checkpoint(args.output) if args.resume else {}
    checkpoint_product_ids = checkpoint.get("product_ids", set())
    if not isinstance(checkpoint_product_ids, set):
        checkpoint_product_ids = set()
    skipped_checkpoint_count = 0

    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if args.resume:
        _ensure_append_starts_on_new_line(args.output)
    output_mode = "a" if args.resume else "w"

    print(f"Loaded {target_count} distinct product targets from {args.input}", flush=True)
    if args.resume:
        print(
            f"Resume enabled: products already in {args.output} will be skipped while streaming targets",
            flush=True,
        )

    def crawl(row: dict[str, str]) -> dict[str, object]:
        return fetch_product_info(
            product_id=row["product_id"],
            source_url=row["candidate_url"],
            timeout_seconds=args.timeout_seconds,
            fallback_domains=fallback_domains,
        ).to_dict()

    def pending_targets() -> Iterable[dict[str, str]]:
        nonlocal skipped_checkpoint_count
        for row in _iter_best_targets(args.input, args.limit):
            if str(row.get("product_id")) in checkpoint_product_ids:
                skipped_checkpoint_count += 1
                continue
            yield row

    progress = ProgressReporter("Crawling products", total=target_count, every=args.progress_every)
    active_count = int(checkpoint.get("active_count", 0)) if args.resume else 0
    ok_count = int(checkpoint.get("ok_count", 0)) if args.resume else 0
    status_counts = checkpoint.get("status_counts", Counter()) if args.resume else Counter()
    country_counts = checkpoint.get("country_counts", Counter()) if args.resume else Counter()
    if not isinstance(status_counts, Counter):
        status_counts = Counter()
    if not isinstance(country_counts, Counter):
        country_counts = Counter()

    processed_this_run = 0
    cancelled = False

    def record_row(handle, row: dict[str, object]) -> None:
        nonlocal active_count, ok_count, processed_this_run
        _write_jsonl_row(handle, row)
        processed_this_run += 1
        status = str(row.get("status"))
        status_counts[status] += 1
        if row.get("country_store"):
            country_counts[str(row.get("country_store"))] += 1
        active_count += 1 if row.get("active") else 0
        ok_count += 1 if status == "ok" else 0

    try:
        with output_path.open(output_mode, encoding="utf-8") as handle:
            if args.workers > 1:
                executor = ThreadPoolExecutor(max_workers=args.workers)
                target_iter = iter(pending_targets())
                futures = set()
                max_pending = max(1, args.workers * 2)

                def submit_next() -> bool:
                    try:
                        target = next(target_iter)
                    except StopIteration:
                        return False
                    futures.add(executor.submit(crawl, target))
                    return True

                for _ in range(max_pending):
                    if not submit_next():
                        break
                try:
                    completed = 0
                    while futures:
                        for future in as_completed(list(futures)):
                            futures.remove(future)
                            row = future.result()
                            record_row(handle, row)
                            completed += 1
                            progress.report(
                                skipped_checkpoint_count + completed,
                                suffix=f"ok={ok_count:,}, active={active_count:,}",
                            )
                            submit_next()
                            break
                except KeyboardInterrupt:
                    cancelled = True
                    for future in futures:
                        future.cancel()
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise
                else:
                    executor.shutdown(wait=True)
            else:
                for completed, target in enumerate(pending_targets(), start=1):
                    row = crawl(target)
                    record_row(handle, row)
                    progress.report(
                        skipped_checkpoint_count + completed,
                        suffix=f"ok={ok_count:,}, active={active_count:,}",
                    )
    except KeyboardInterrupt:
        cancelled = True
        print("Cancellation requested. Partial crawl output has already been checkpointed.", flush=True)

    progress.report(processed_this_run, force=True, suffix=f"ok={ok_count:,}, active={active_count:,}")
    checkpoint_row_count = int(checkpoint.get("row_count", 0)) if args.resume else 0
    processed_total = checkpoint_row_count + processed_this_run
    remaining_count = max(target_count - skipped_checkpoint_count - processed_this_run, 0)
    print(f"Wrote {processed_this_run} product information rows to {args.output}")

    elapsed = monotonic() - started_at
    success_rate_processed = _percentage(ok_count, processed_total)
    success_rate_target = _percentage(ok_count, target_count)
    summary = {
        "stage": "crawl_products",
        "cancelled": cancelled,
        "resume": args.resume,
        "input": args.input,
        "output": args.output,
        "failed_output": args.failed_output,
        "summary_output": args.summary_output,
        "target_count": target_count,
        "pending_target_count": target_count - skipped_checkpoint_count,
        "skipped_checkpoint_count": skipped_checkpoint_count,
        "checkpoint_row_count": checkpoint_row_count,
        "checkpoint_invalid_line_count": int(checkpoint.get("invalid_line_count", 0)) if args.resume else 0,
        "processed_this_run_count": processed_this_run,
        "processed_count": processed_total,
        "remaining_count": remaining_count,
        "failed_target_count": None,
        "ok_count": ok_count,
        "active_count": active_count,
        "success_rate_processed_percent": success_rate_processed,
        "success_rate_target_percent": success_rate_target,
        "status_counts": dict(status_counts),
        "country_store_counts": dict(country_counts),
        "workers": args.workers,
        "timeout_seconds": args.timeout_seconds,
        "elapsed_seconds": round(elapsed, 3),
        "elapsed": format_duration(elapsed),
    }
    failed_target_count = _write_failed_targets(args.output, args.failed_output, _iter_best_targets(args.input, args.limit))
    summary["failed_target_count"] = failed_target_count
    write_summary_json(args.summary_output, summary)
    print(f"Wrote crawl summary to {args.summary_output}")
    print(f"Wrote {failed_target_count} failed crawl targets to {args.failed_output}")
    print(
        f"Crawl {'cancelled' if cancelled else 'finished'} in {format_duration(elapsed)}: "
        f"processed={processed_total:,}/{target_count:,}, "
        f"this_run={processed_this_run:,}, ok={ok_count:,}, active={active_count:,}, "
        f"success={success_rate_processed:.2f}%",
        flush=True,
    )

    if cancelled:
        sys.exit(130)


if __name__ == "__main__":
    main()
