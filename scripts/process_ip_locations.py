"""Process distinct IP addresses from MongoDB with a local IP2Location BIN file."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from time import monotonic
from collections import Counter
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from glamira_aws.events import build_product_url_candidate
from glamira_aws.ip_locations import IPLocation, lookup_ip2location, lookup_ip2location_with_database, normalize_ip
from glamira_aws.mongodb import MongoSettings, get_collection, product_event_query
from glamira_aws.progress import ProgressReporter, format_duration, write_summary_json


def parse_args() -> argparse.Namespace:
    defaults = MongoSettings.from_env()
    output_directory = Path(os.getenv("OUTPUT_DIRECTORY", "outputs"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default=defaults.uri)
    parser.add_argument("--database", default=defaults.database)
    parser.add_argument("--collection", default=defaults.collection)
    parser.add_argument("--ip-field", default="ip")
    parser.add_argument("--ip2location-db-path", default=os.getenv("IP2LOCATION_DB_PATH"))
    parser.add_argument("--output", default=str(output_directory / "ip_locations.jsonl"))
    parser.add_argument(
        "--product-targets",
        default=os.getenv("PRODUCT_TARGETS_PATH"),
        help=(
            "Optional product_targets CSV/JSONL. When set, discover IPs only from product events "
            "whose extracted product_id is in this target file."
        ),
    )
    parser.add_argument("--product-id-field", default="product_id")
    parser.add_argument("--target-db", default=os.getenv("IP_LOCATION_TARGET_DB", "countly_enriched"))
    parser.add_argument(
        "--target-collection",
        default=os.getenv("IP_LOCATION_TARGET_COLLECTION", "ip_locations"),
    )
    parser.add_argument("--skip-mongodb-write", action="store_true")
    parser.add_argument("--allow-private", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--ip-discovery-mode",
        choices=("scan", "aggregate"),
        default=os.getenv("IP_DISCOVERY_MODE", "scan"),
        help="Use 'scan' for visible progress or 'aggregate' for MongoDB-side distinct grouping.",
    )
    parser.add_argument("--progress-every", type=int, default=int(os.getenv("PROGRESS_EVERY", "1000")))
    parser.add_argument("--mongodb-batch-size", type=int, default=int(os.getenv("MONGODB_BATCH_SIZE", "1000")))
    parser.add_argument("--summary-output", default=str(output_directory / "ip_locations_summary.json"))
    return parser.parse_args()


def read_product_target_ids(path: str | Path, product_id_field: str = "product_id") -> set[str]:
    """Read product IDs from a product target CSV or JSONL file."""
    target_path = Path(path).expanduser()
    product_ids: set[str] = set()
    if target_path.suffix.lower() == ".jsonl":
        with target_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                value = row.get(product_id_field)
                if value is not None and str(value).strip():
                    product_ids.add(str(value).strip())
        return product_ids

    with target_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            value = row.get(product_id_field)
            if value is not None and str(value).strip():
                product_ids.add(str(value).strip())
    return product_ids


def iter_distinct_ips_aggregate(
    collection,
    ip_field: str,
    limit: int | None = None,
    stats: dict[str, int] | None = None,
):
    query = {ip_field: {"$type": "string", "$ne": ""}}
    if stats is not None:
        try:
            stats["mongodb_ip_record_count"] = collection.count_documents(query)
        except Exception:
            pass
    pipeline: list[dict[str, Any]] = [
        {"$match": query},
        {"$group": {"_id": f"${ip_field}"}},
        {"$sort": {"_id": 1}},
    ]
    if limit:
        pipeline.append({"$limit": limit})
    cursor = collection.aggregate(pipeline, allowDiskUse=True)
    for row in cursor:
        yield row["_id"]


def iter_distinct_ips_scan(
    collection,
    ip_field: str,
    limit: int | None = None,
    progress_every: int = 1000,
    stats: dict[str, int] | None = None,
):
    query = {ip_field: {"$type": "string", "$ne": ""}}
    total = None
    try:
        total = collection.count_documents(query)
    except Exception:
        total = None
    if stats is not None and total is not None:
        stats["mongodb_ip_record_count"] = total
    projection = {ip_field: 1}
    cursor = collection.find(query, projection=projection, no_cursor_timeout=True)
    progress = ProgressReporter("Scanning MongoDB IP values", total=total, every=progress_every)
    seen_ips: set[str] = set()
    scanned_count = 0
    try:
        for scanned_count, document in enumerate(cursor, start=1):
            raw_ip = document.get(ip_field)
            normalized = normalize_ip(raw_ip) or str(raw_ip)
            if normalized not in seen_ips:
                seen_ips.add(normalized)
                yield raw_ip
                if limit and len(seen_ips) >= limit:
                    progress.report(scanned_count, force=True, suffix=f"unique_ips={len(seen_ips):,}")
                    break
            progress.report(scanned_count, suffix=f"unique_ips={len(seen_ips):,}")
    finally:
        cursor.close()
        if stats is not None:
            stats["mongodb_ip_scanned_count"] = scanned_count
        progress.report(scanned_count, force=True, suffix=f"unique_ips={len(seen_ips):,}")


def iter_distinct_product_event_ips_scan(
    collection,
    ip_field: str,
    product_ids: set[str],
    limit: int | None = None,
    progress_every: int = 1000,
    stats: dict[str, int] | None = None,
):
    query = {"$and": [product_event_query(), {ip_field: {"$type": "string", "$ne": ""}}]}
    total = None
    try:
        total = collection.count_documents(query)
    except Exception:
        total = None
    projection = {
        ip_field: 1,
        "collection": 1,
        "event_type": 1,
        "event": 1,
        "event_name": 1,
        "action": 1,
        "product_id": 1,
        "productid": 1,
        "product": 1,
        "viewing_product_id": 1,
        "viewingProductId": 1,
        "current_url": 1,
        "currentUrl": 1,
        "url": 1,
        "referrer_url": 1,
        "referrerUrl": 1,
        "referer_url": 1,
        "referer": 1,
        "time_stamp": 1,
        "event_time": 1,
    }
    cursor = collection.find(query, projection=projection, no_cursor_timeout=True)
    progress = ProgressReporter("Scanning product-event IP values", total=total, every=progress_every)
    seen_ips: set[str] = set()
    scanned_count = 0
    candidate_count = 0
    matched_count = 0
    try:
        for scanned_count, document in enumerate(cursor, start=1):
            candidate = build_product_url_candidate(document)
            if not candidate:
                progress.report(
                    scanned_count,
                    suffix=f"matched_events={matched_count:,}, unique_ips={len(seen_ips):,}",
                )
                continue
            candidate_count += 1
            product_id = str(candidate["product_id"]).strip()
            if product_id not in product_ids:
                progress.report(
                    scanned_count,
                    suffix=f"matched_events={matched_count:,}, unique_ips={len(seen_ips):,}",
                )
                continue
            matched_count += 1
            raw_ip = document.get(ip_field)
            normalized = normalize_ip(raw_ip) or str(raw_ip)
            if normalized not in seen_ips:
                seen_ips.add(normalized)
                yield raw_ip
                if limit and len(seen_ips) >= limit:
                    progress.report(
                        scanned_count,
                        force=True,
                        suffix=f"matched_events={matched_count:,}, unique_ips={len(seen_ips):,}",
                    )
                    break
            progress.report(
                scanned_count,
                suffix=f"matched_events={matched_count:,}, unique_ips={len(seen_ips):,}",
            )
    finally:
        cursor.close()
        if stats is not None:
            stats["product_event_scanned_count"] = scanned_count
            stats["product_event_candidate_count"] = candidate_count
            stats["product_event_matched_count"] = matched_count
        progress.report(
            scanned_count,
            force=True,
            suffix=f"matched_events={matched_count:,}, unique_ips={len(seen_ips):,}",
        )


def iter_distinct_ips(
    collection,
    ip_field: str,
    limit: int | None = None,
    progress_every: int = 1000,
    discovery_mode: str = "scan",
    stats: dict[str, int] | None = None,
):
    if discovery_mode == "aggregate":
        print(
            "Discovering distinct IPs with MongoDB aggregation. This mode may be quiet while MongoDB groups values.",
            flush=True,
        )
        yield from iter_distinct_ips_aggregate(collection, ip_field, limit, stats=stats)
        return
    yield from iter_distinct_ips_scan(collection, ip_field, limit, progress_every, stats=stats)


def write_locations_to_mongodb(uri: str, database: str, collection_name: str, rows: list[dict[str, Any]]) -> int:
    collection = get_collection(uri, database, collection_name)
    collection.drop()
    if not rows:
        return 0
    collection.create_index("ip", unique=True)
    collection.create_index("status")
    return upsert_location_batch(collection, rows)


def upsert_location_batch(collection, rows: list[dict[str, Any]]) -> int:
    """Upsert one batch of IP rows and return the number of input rows written."""
    if not rows:
        return 0

    from pymongo import ReplaceOne

    operations = [
        ReplaceOne({"ip": row["ip"]}, row, upsert=True)
        for row in rows
        if row.get("ip")
    ]
    if not operations:
        return 0
    collection.bulk_write(operations, ordered=False)
    return len(operations)


def prepare_location_collection(uri: str, database: str, collection_name: str):
    collection = get_collection(uri, database, collection_name)
    collection.drop()
    collection.create_index("ip", unique=True)
    collection.create_index("status")
    return collection


def write_jsonl_row(handle, row: dict[str, Any]) -> None:
    handle.write(json.dumps(row, default=str, ensure_ascii=False) + "\n")


def location_key(row: dict[str, Any]) -> str | None:
    parts = [
        str(row.get("country") or "").strip(),
        str(row.get("region") or "").strip(),
        str(row.get("city") or "").strip(),
    ]
    if not any(parts):
        latitude = row.get("latitude")
        longitude = row.get("longitude")
        if latitude is None or longitude is None:
            return None
        return f"{latitude},{longitude}"
    return " | ".join(parts)


def main() -> None:
    started_at = monotonic()
    args = parse_args()
    args.mongodb_batch_size = max(1, args.mongodb_batch_size)
    db_path = str(Path(args.ip2location_db_path).expanduser()) if args.ip2location_db_path else None
    if db_path:
        print(f"IP2Location database ready at {db_path}", flush=True)
    collection = get_collection(args.uri, args.database, args.collection)
    product_target_ids = None
    if args.product_targets:
        product_target_ids = read_product_target_ids(args.product_targets, args.product_id_field)
        print(
            f"Loaded {len(product_target_ids):,} product target IDs from {args.product_targets}",
            flush=True,
        )
        print(
            f"Reading distinct product-event IP addresses from {args.database}.{args.collection}",
            flush=True,
        )
    else:
        print(
            f"Reading distinct IP addresses from {args.database}.{args.collection} "
            f"with {args.ip_discovery_mode} discovery",
            flush=True,
        )
    progress = ProgressReporter("Processing IP locations", total=args.limit, every=args.progress_every)

    target_collection = None
    mongodb_progress = None
    if not args.skip_mongodb_write:
        print(f"Preparing MongoDB target {args.target_db}.{args.target_collection}", flush=True)
        target_collection = prepare_location_collection(args.uri, args.target_db, args.target_collection)
        mongodb_progress = ProgressReporter("Writing IP locations to MongoDB", every=args.progress_every)

    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    batch: list[dict[str, Any]] = []
    processed_count = 0
    source_ip_count = 0
    skipped_duplicate_count = 0
    mongodb_written_count = 0
    ok_count = 0
    status_counts: Counter[str] = Counter()
    country_counts: Counter[str] = Counter()
    location_counts: Counter[str] = Counter()
    unique_locations: set[str] = set()
    cancelled = False
    product_ip_stats: dict[str, int] = {}
    all_record_ip_stats: dict[str, int] = {}

    database = None
    missing_dependency = False
    database_error: str | None = None
    if db_path:
        try:
            import IP2Location  # type: ignore

            database = IP2Location.IP2Location(db_path)
        except ImportError:
            missing_dependency = True
        except Exception as exc:
            database_error = str(exc)

    def build_row(ip: str) -> dict[str, Any]:
        normalized = normalize_ip(ip) or str(ip)
        if missing_dependency:
            return IPLocation(
                ip=normalized,
                status="missing_dependency",
                error_message="Install ip2location-python.",
            ).to_dict()
        if database_error:
            return IPLocation(
                ip=normalized,
                status="lookup_failed",
                error_message=database_error,
            ).to_dict()
        if database is not None:
            return lookup_ip2location_with_database(ip, database, allow_private=args.allow_private).to_dict()
        return lookup_ip2location(ip, None, allow_private=args.allow_private).to_dict()

    try:
        with output_path.open("w", encoding="utf-8") as handle:
            ip_iterator = (
                iter_distinct_product_event_ips_scan(
                    collection,
                    args.ip_field,
                    product_target_ids,
                    args.limit,
                    progress_every=args.progress_every,
                    stats=product_ip_stats,
                )
                if product_target_ids is not None
                else iter_distinct_ips(
                    collection,
                    args.ip_field,
                    args.limit,
                    progress_every=args.progress_every,
                    discovery_mode=args.ip_discovery_mode,
                    stats=all_record_ip_stats,
                )
            )
            for ip in ip_iterator:
                source_ip_count += 1
                row = build_row(str(ip))
                write_jsonl_row(handle, row)
                batch.append(row)
                processed_count += 1
                status = str(row.get("status"))
                status_counts[status] += 1
                if row.get("country"):
                    country_counts[str(row.get("country"))] += 1
                key = location_key(row)
                if key:
                    unique_locations.add(key)
                    location_counts[key] += 1
                ok_count += 1 if status == "ok" else 0

                if target_collection is not None and len(batch) >= args.mongodb_batch_size:
                    mongodb_written_count += upsert_location_batch(target_collection, batch)
                    if mongodb_progress:
                        mongodb_progress.report(
                            mongodb_written_count,
                            suffix=f"latest_batch={len(batch):,}",
                        )
                    batch = []

                progress.report(processed_count, suffix=f"ok={ok_count:,}")
    except KeyboardInterrupt:
        cancelled = True
        print("Cancellation requested. Writing partial IP output and summary...", flush=True)

    if target_collection is not None and batch:
        mongodb_written_count += upsert_location_batch(target_collection, batch)
        if mongodb_progress:
            mongodb_progress.report(mongodb_written_count, suffix=f"latest_batch={len(batch):,}")
    if mongodb_progress:
        mongodb_progress.report(mongodb_written_count, force=True)

    progress.report(processed_count, force=True, suffix=f"ok={ok_count:,}")
    print(f"Wrote {processed_count} IP location rows to {args.output}")

    if not args.skip_mongodb_write:
        print(f"Wrote {mongodb_written_count} IP location rows to {args.target_db}.{args.target_collection}")

    elapsed = monotonic() - started_at
    summary = {
        "stage": "process_ip_locations",
        "cancelled": cancelled,
        "database": args.database,
        "collection": args.collection,
        "output": args.output,
        "summary_output": args.summary_output,
        "discovery_scope": "product_target_events" if product_target_ids is not None else "all_collection_ips",
        "task5_scope_note": (
            "Task 5 IP processing reads distinct IPs from all MongoDB source records with an IP field."
            if product_target_ids is None
            else "Optional product-event IP audit mode; not the default Task 5 all-record location pass."
        ),
        "product_targets": args.product_targets,
        "product_target_filter_mode": "product_id_only" if product_target_ids is not None else None,
        "product_target_filter_note": (
            "The product target file is used only to choose eligible product IDs; "
            "IP discovery scans all matching product events for those IDs, not only the best URL rows."
            if product_target_ids is not None
            else None
        ),
        "product_target_id_count": None if product_target_ids is None else len(product_target_ids),
        **all_record_ip_stats,
        **product_ip_stats,
        "target_db": None if args.skip_mongodb_write else args.target_db,
        "target_collection": None if args.skip_mongodb_write else args.target_collection,
        "source_ip_count": source_ip_count,
        "ip_count": processed_count,
        "processed_count": processed_count,
        "unique_location_count": len(unique_locations),
        "remaining_count": None if args.limit is None else max(args.limit - source_ip_count, 0),
        "skipped_duplicate_count": skipped_duplicate_count,
        "mongodb_written_count": None if args.skip_mongodb_write else mongodb_written_count,
        "status_counts": dict(status_counts),
        "country_counts": dict(country_counts),
        "location_counts": dict(location_counts),
        "ip2location_db_path": args.ip2location_db_path,
        "ip_discovery_mode": args.ip_discovery_mode,
        "mongodb_batch_size": args.mongodb_batch_size,
        "elapsed_seconds": round(elapsed, 3),
        "elapsed": format_duration(elapsed),
    }
    write_summary_json(args.summary_output, summary)
    print(f"Wrote IP location summary to {args.summary_output}")
    print(
        f"IP processing {'cancelled' if cancelled else 'finished'} in {format_duration(elapsed)}: "
        f"processed={processed_count:,}",
        flush=True,
    )

    if cancelled:
        sys.exit(130)


if __name__ == "__main__":
    main()
