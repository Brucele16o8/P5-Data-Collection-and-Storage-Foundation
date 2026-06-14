"""MongoDB helpers for the EC2 execution path."""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from glamira_aws.events import (
    PRODUCT_CURRENT_URL_EVENTS,
    RECOMMEND_CLICK_EVENT,
    build_product_url_candidate,
    rank_grouped_product_url_candidates,
    update_product_url_group,
)
from glamira_aws.progress import ProgressReporter


@dataclass(frozen=True)
class MongoSettings:
    uri: str
    database: str
    collection: str

    @classmethod
    def from_env(
        cls,
        default_database: str = "countly",
        default_collection: str = "summary",
    ) -> "MongoSettings":
        return cls(
            uri=os.getenv("MONGO_URI", "mongodb://localhost:27017"),
            database=os.getenv("MONGO_DATABASE", default_database),
            collection=os.getenv("MONGO_COLLECTION", default_collection),
        )


@dataclass(frozen=True)
class ProductTargetExtractionResult:
    rows: list[dict[str, Any]]
    scanned_records: int
    candidate_records: int
    grouped_url_count: int
    unique_products: int
    include_all_candidates: bool


def get_collection(uri: str, database: str, collection: str):
    from pymongo import MongoClient

    client = MongoClient(uri)
    return client[database][collection]


def create_sample_collection(
    uri: str,
    source_db: str,
    source_collection: str,
    target_db: str,
    target_collection: str,
    size: int,
    mode: str = "random",
    seed: int | None = None,
    drop_target: bool = True,
) -> int:
    """Create a sample collection inside MongoDB and return inserted count."""
    source = get_collection(uri, source_db, source_collection)
    target = get_collection(uri, target_db, target_collection)
    if drop_target:
        target.drop()

    if mode == "random":
        # MongoDB's $sample keeps BSON/native document types but does not
        # support deterministic seeding.
        cursor = source.aggregate([{"$sample": {"size": size}}], allowDiskUse=True)
    elif mode == "first":
        cursor = source.find({}, limit=size)
    else:
        raise ValueError("mode must be 'random' or 'first'")

    batch: list[dict[str, Any]] = []
    inserted = 0
    for document in cursor:
        batch.append(document)
        if len(batch) >= 1000:
            result = target.insert_many(batch, ordered=False)
            inserted += len(result.inserted_ids)
            batch = []
    if batch:
        result = target.insert_many(batch, ordered=False)
        inserted += len(result.inserted_ids)
    return inserted


def iter_product_event_records(collection) -> Iterable[dict[str, Any]]:
    projection = {
        "collection": 1,
        "event_type": 1,
        "product_id": 1,
        "viewing_product_id": 1,
        "current_url": 1,
        "referrer_url": 1,
        "time_stamp": 1,
        "event_time": 1,
    }
    query = product_event_query()
    cursor = collection.find(query, projection=projection, no_cursor_timeout=True)
    try:
        yield from cursor
    finally:
        cursor.close()


def product_event_query() -> dict[str, Any]:
    return {
        "collection": {"$in": sorted(PRODUCT_CURRENT_URL_EVENTS | {RECOMMEND_CLICK_EVENT})},
        "$or": [
            {"current_url": {"$type": "string", "$ne": ""}},
            {"referrer_url": {"$type": "string", "$ne": ""}},
        ],
    }


def extract_product_targets(
    uri: str,
    database: str,
    collection_name: str,
    limit_records: int | None = None,
    progress_every: int = 10000,
    include_all_candidates: bool = False,
) -> list[dict[str, Any]]:
    return extract_product_targets_result(
        uri=uri,
        database=database,
        collection_name=collection_name,
        limit_records=limit_records,
        progress_every=progress_every,
        include_all_candidates=include_all_candidates,
    ).rows


def extract_product_targets_result(
    uri: str,
    database: str,
    collection_name: str,
    limit_records: int | None = None,
    progress_every: int = 10000,
    include_all_candidates: bool = False,
) -> ProductTargetExtractionResult:
    collection = get_collection(uri, database, collection_name)
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    candidate_records = 0
    scanned_records = 0
    total = limit_records
    if total is None:
        try:
            total = collection.count_documents(product_event_query())
        except Exception:
            total = None
    progress = ProgressReporter(
        "Scanning and aggregating MongoDB product events",
        total=total,
        every=progress_every,
    )
    for index, record in enumerate(iter_product_event_records(collection), start=1):
        scanned_records = index
        candidate = build_product_url_candidate(record)
        if candidate:
            candidate_records += 1
            update_product_url_group(grouped, candidate)
        progress.report(
            index,
            suffix=f"candidate_records={candidate_records:,}, grouped_urls={len(grouped):,}",
        )
        if limit_records and index >= limit_records:
            break
    progress.report(
        scanned_records,
        force=True,
        suffix=f"candidate_records={candidate_records:,}, grouped_urls={len(grouped):,}",
    )
    rows = rank_grouped_product_url_candidates(
        grouped,
        include_all_candidates=include_all_candidates,
        progress_every=progress_every,
    )
    return ProductTargetExtractionResult(
        rows=rows,
        scanned_records=scanned_records,
        candidate_records=candidate_records,
        grouped_url_count=len(grouped),
        unique_products=len({row.get("product_id") for row in rows if row.get("product_id")}),
        include_all_candidates=include_all_candidates,
    )


def write_jsonl(
    path: str | Path,
    rows: Iterable[dict[str, Any]],
    total: int | None = None,
    progress_every: int = 10000,
    progress_label: str | None = None,
) -> None:
    output_path = Path(path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    progress = (
        ProgressReporter(progress_label, total=total, every=progress_every)
        if progress_label
        else None
    )
    with output_path.open("w", encoding="utf-8") as handle:
        for index, row in enumerate(rows, start=1):
            handle.write(json.dumps(row, default=str, ensure_ascii=False) + "\n")
            if progress:
                progress.report(index)
    if progress:
        progress.report(index if "index" in locals() else 0, force=True)


def write_csv(
    path: str | Path,
    rows: Iterable[dict[str, Any]],
    fieldnames: list[str],
    total: int | None = None,
    progress_every: int = 10000,
    progress_label: str | None = None,
) -> None:
    output_path = Path(path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    progress = (
        ProgressReporter(progress_label, total=total, every=progress_every)
        if progress_label
        else None
    )
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for index, row in enumerate(rows, start=1):
            writer.writerow(row)
            if progress:
                progress.report(index)
    if progress:
        progress.report(index if "index" in locals() else 0, force=True)


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).expanduser().open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).expanduser().open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))
