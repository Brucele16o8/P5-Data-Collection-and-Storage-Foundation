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


def _coalesce_fields(*fields: str) -> dict[str, Any]:
    return {"$ifNull": [f"${field}" for field in fields]}


def product_target_aggregation_pipeline(limit_records: int | None = None) -> list[dict[str, Any]]:
    """Build a MongoDB aggregation that groups product URL candidates server-side."""
    pipeline: list[dict[str, Any]] = [
        {"$match": product_event_query()},
    ]
    if limit_records:
        pipeline.append({"$limit": limit_records})
    pipeline.extend(
        [
            {
                "$project": {
                    "source_event_type": {
                        "$toLower": {
                            "$toString": _coalesce_fields(
                                "event_type",
                                "event",
                                "event_name",
                                "collection",
                                "action",
                            )
                        }
                    },
                    "current_url": _coalesce_fields("current_url", "currentUrl", "url"),
                    "referrer_url": _coalesce_fields(
                        "referrer_url",
                        "referrerUrl",
                        "referer_url",
                        "referer",
                    ),
                    "product_id": _coalesce_fields("product_id", "productid", "product"),
                    "viewing_product_id": _coalesce_fields("viewing_product_id", "viewingProductId"),
                    "event_time": _coalesce_fields(
                        "event_time",
                        "time_stamp",
                        "timestamp",
                        "created_at",
                        "time",
                        "datetime",
                    ),
                }
            },
            {
                "$project": {
                    "source_event_type": 1,
                    "url_source_field": {
                        "$cond": [
                            {"$eq": ["$source_event_type", RECOMMEND_CLICK_EVENT]},
                            "referrer_url",
                            "current_url",
                        ]
                    },
                    "product_id": {
                        "$cond": [
                            {"$eq": ["$source_event_type", RECOMMEND_CLICK_EVENT]},
                            "$viewing_product_id",
                            {"$ifNull": ["$product_id", "$viewing_product_id"]},
                        ]
                    },
                    "candidate_url": {
                        "$cond": [
                            {"$eq": ["$source_event_type", RECOMMEND_CLICK_EVENT]},
                            "$referrer_url",
                            "$current_url",
                        ]
                    },
                    "event_time": {"$toString": "$event_time"},
                }
            },
            {
                "$match": {
                    "product_id": {"$nin": [None, "", "none", "null", "nan"]},
                    "candidate_url": {"$nin": [None, "", "none", "null", "nan"]},
                }
            },
            {
                "$group": {
                    "_id": {
                        "product_id": {"$toString": "$product_id"},
                        "candidate_url": {"$toString": "$candidate_url"},
                        "source_event_type": "$source_event_type",
                        "url_source_field": "$url_source_field",
                    },
                    "event_count": {"$sum": 1},
                    "first_seen_at": {"$min": "$event_time"},
                    "last_seen_at": {"$max": "$event_time"},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "product_id": "$_id.product_id",
                    "candidate_url": "$_id.candidate_url",
                    "source_event_type": "$_id.source_event_type",
                    "url_source_field": "$_id.url_source_field",
                    "event_count": 1,
                    "first_seen_at": 1,
                    "last_seen_at": 1,
                }
            },
            {"$sort": {"product_id": 1, "event_count": -1, "last_seen_at": -1}},
        ]
    )
    return pipeline


def iter_product_targets_mongodb(
    uri: str,
    database: str,
    collection_name: str,
    limit_records: int | None = None,
    include_all_candidates: bool = False,
):
    """Yield ranked product targets from MongoDB without storing all groups in Python."""
    collection = get_collection(uri, database, collection_name)
    cursor = collection.aggregate(
        product_target_aggregation_pipeline(limit_records=limit_records),
        allowDiskUse=True,
    )
    previous_product_id = None
    rank = 0
    try:
        for row in cursor:
            product_id = row.get("product_id")
            if product_id != previous_product_id:
                previous_product_id = product_id
                rank = 1
            else:
                rank += 1
            if not include_all_candidates and rank > 1:
                continue
            row["url_rank"] = rank
            yield row
    finally:
        cursor.close()


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
