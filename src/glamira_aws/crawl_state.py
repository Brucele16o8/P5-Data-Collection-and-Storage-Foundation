"""Crawler checkpoint and failure-report helpers."""

from __future__ import annotations

import csv
import json
import os
from collections import Counter
from pathlib import Path
from typing import Iterable


def read_crawl_checkpoint(
    path: str,
    target_product_ids: set[str] | None = None,
) -> dict[str, object]:
    """Read existing crawl output and summarize resumable product state."""

    output_path = Path(path).expanduser()
    product_ids: set[str] = set()
    status_counts: Counter[str] = Counter()
    country_counts: Counter[str] = Counter()
    ok_count = 0
    active_count = 0
    row_count = 0
    invalid_line_count = 0

    if not output_path.exists():
        return {
            "product_ids": product_ids,
            "status_counts": status_counts,
            "country_counts": country_counts,
            "ok_count": ok_count,
            "active_count": active_count,
            "row_count": row_count,
            "invalid_line_count": invalid_line_count,
        }

    with output_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                invalid_line_count += 1
                continue
            requested_product_id = row.get("requested_product_id")
            if requested_product_id is None:
                continue
            requested_product_id = str(requested_product_id)
            if target_product_ids is not None and requested_product_id not in target_product_ids:
                continue
            product_ids.add(requested_product_id)
            status = str(row.get("status"))
            status_counts[status] += 1
            if row.get("country_store"):
                country_counts[str(row.get("country_store"))] += 1
            ok_count += 1 if status == "ok" else 0
            active_count += 1 if row.get("active") else 0
            row_count += 1

    return {
        "product_ids": product_ids,
        "status_counts": status_counts,
        "country_counts": country_counts,
        "ok_count": ok_count,
        "active_count": active_count,
        "row_count": row_count,
        "invalid_line_count": invalid_line_count,
    }


def ensure_append_starts_on_new_line(path: str) -> None:
    """Ensure resumed JSONL output starts after a newline boundary."""

    output_path = Path(path).expanduser()
    if not output_path.exists() or output_path.stat().st_size == 0:
        return
    with output_path.open("rb+") as handle:
        handle.seek(-1, os.SEEK_END)
        if handle.read(1) != b"\n":
            handle.write(b"\n")


def latest_rows_by_product(
    path: str,
    target_product_ids: set[str] | None = None,
) -> dict[str, dict[str, object]]:
    """Return the latest product-info row for each requested product."""

    output_path = Path(path).expanduser()
    latest: dict[str, dict[str, object]] = {}
    if not output_path.exists():
        return latest

    with output_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            requested_product_id = row.get("requested_product_id")
            if requested_product_id is None:
                continue
            product_id = str(requested_product_id)
            if target_product_ids is not None and product_id not in target_product_ids:
                continue
            latest[product_id] = row
    return latest


def write_failed_targets(
    product_info_path: str,
    failed_output_path: str,
    targets: Iterable[dict[str, str]],
) -> int:
    """Write a CSV report for target products whose latest crawl did not succeed."""

    target_by_product_id = {
        str(row.get("product_id")): row
        for row in targets
        if row.get("product_id")
    }
    target_product_ids = set(target_by_product_id)
    latest_rows = latest_rows_by_product(product_info_path, target_product_ids)
    fieldnames = [
        "product_id",
        "candidate_url",
        "last_status",
        "failure_reason",
        "error_message",
        "attempt_count",
        "last_source_url",
        "last_resolved_url",
        "scraped_at",
    ]
    output_path = Path(failed_output_path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    failed_count = 0

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for product_id in sorted(latest_rows):
            row = latest_rows[product_id]
            if row.get("status") == "ok":
                continue
            target = target_by_product_id.get(product_id, {})
            attempts = row.get("attempts")
            writer.writerow(
                {
                    "product_id": product_id,
                    "candidate_url": (
                        target.get("candidate_url")
                        or row.get("original_url")
                        or row.get("source_url")
                        or ""
                    ),
                    "last_status": row.get("status"),
                    "failure_reason": row.get("failure_reason"),
                    "error_message": row.get("error_message"),
                    "attempt_count": len(attempts) if isinstance(attempts, list) else 0,
                    "last_source_url": row.get("source_url"),
                    "last_resolved_url": row.get("resolved_url"),
                    "scraped_at": row.get("scraped_at"),
                }
            )
            failed_count += 1
    return failed_count
