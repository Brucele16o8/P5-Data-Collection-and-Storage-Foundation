"""Process distinct IP addresses from MongoDB with an IP2Location BIN file."""

from __future__ import annotations

import argparse
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

from glamira_aws.ip_locations import IPLocation, lookup_ip2location, lookup_ip2location_with_database, normalize_ip
from glamira_aws.mongodb import MongoSettings, get_collection
from glamira_aws.progress import ProgressReporter, format_duration, write_summary_json
from glamira_aws.s3_io import download_to_tmp, is_s3_uri, parse_s3_uri


def parse_args() -> argparse.Namespace:
    defaults = MongoSettings.from_env()
    output_directory = Path(os.getenv("OUTPUT_DIRECTORY", "outputs"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default=defaults.uri)
    parser.add_argument("--database", default=defaults.database)
    parser.add_argument("--collection", default=defaults.collection)
    parser.add_argument("--ip-field", default="ip")
    parser.add_argument("--ip2location-db-uri", default=os.getenv("IP2LOCATION_DB_URI"))
    parser.add_argument("--output", default=str(output_directory / "ip_locations.jsonl"))
    parser.add_argument("--output-s3-uri", default=os.getenv("IP_LOCATION_OUTPUT_S3_URI"))
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


def iter_distinct_ips_aggregate(collection, ip_field: str, limit: int | None = None):
    pipeline: list[dict[str, Any]] = [
        {"$match": {ip_field: {"$type": "string", "$ne": ""}}},
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
):
    query = {ip_field: {"$type": "string", "$ne": ""}}
    total = None
    try:
        total = collection.count_documents(query)
    except Exception:
        total = None
    projection = {ip_field: 1}
    cursor = collection.find(query, projection=projection, no_cursor_timeout=True)
    progress = ProgressReporter("Scanning MongoDB IP values", total=total, every=progress_every)
    seen_ips: set[str] = set()
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
        progress.report(scanned_count if "scanned_count" in locals() else 0, force=True, suffix=f"unique_ips={len(seen_ips):,}")


def iter_distinct_ips(
    collection,
    ip_field: str,
    limit: int | None = None,
    progress_every: int = 1000,
    discovery_mode: str = "scan",
):
    if discovery_mode == "aggregate":
        print(
            "Discovering distinct IPs with MongoDB aggregation. This mode may be quiet while MongoDB groups values.",
            flush=True,
        )
        yield from iter_distinct_ips_aggregate(collection, ip_field, limit)
        return
    yield from iter_distinct_ips_scan(collection, ip_field, limit, progress_every)


def upload_to_s3(local_path: str, s3_uri: str) -> None:
    if not is_s3_uri(s3_uri):
        raise ValueError("--output-s3-uri must start with s3://")
    import boto3

    bucket, key = parse_s3_uri(s3_uri)
    boto3.client("s3").upload_file(local_path, bucket, key)


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


def main() -> None:
    started_at = monotonic()
    args = parse_args()
    args.mongodb_batch_size = max(1, args.mongodb_batch_size)
    if args.ip2location_db_uri:
        print(f"Preparing IP2Location database from {args.ip2location_db_uri}", flush=True)
    db_path = download_to_tmp(args.ip2location_db_uri, suffix=".BIN") if args.ip2location_db_uri else None
    if db_path:
        print(f"IP2Location database ready at {db_path}", flush=True)
    collection = get_collection(args.uri, args.database, args.collection)
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

    seen_ips: set[str] = set()
    batch: list[dict[str, Any]] = []
    processed_count = 0
    source_ip_count = 0
    skipped_duplicate_count = 0
    mongodb_written_count = 0
    ok_count = 0
    status_counts: Counter[str] = Counter()
    country_counts: Counter[str] = Counter()
    cancelled = False

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
            for ip in iter_distinct_ips(
                collection,
                args.ip_field,
                args.limit,
                progress_every=args.progress_every,
                discovery_mode=args.ip_discovery_mode,
            ):
                source_ip_count += 1
                normalized = normalize_ip(ip) or str(ip)
                if normalized in seen_ips:
                    skipped_duplicate_count += 1
                    continue
                seen_ips.add(normalized)

                row = build_row(str(ip))
                write_jsonl_row(handle, row)
                batch.append(row)
                processed_count += 1
                status = str(row.get("status"))
                status_counts[status] += 1
                if row.get("country"):
                    country_counts[str(row.get("country"))] += 1
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
        "target_db": None if args.skip_mongodb_write else args.target_db,
        "target_collection": None if args.skip_mongodb_write else args.target_collection,
        "source_ip_count": source_ip_count,
        "ip_count": processed_count,
        "processed_count": processed_count,
        "remaining_count": None if args.limit is None else max(args.limit - source_ip_count, 0),
        "skipped_duplicate_count": skipped_duplicate_count,
        "mongodb_written_count": None if args.skip_mongodb_write else mongodb_written_count,
        "status_counts": dict(status_counts),
        "country_counts": dict(country_counts),
        "ip2location_db_uri": args.ip2location_db_uri,
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

    if args.output_s3_uri:
        print(f"Uploading {args.output} to {args.output_s3_uri}", flush=True)
        upload_to_s3(args.output, args.output_s3_uri)
        print(f"Uploaded {args.output} to {args.output_s3_uri}")

    if cancelled:
        sys.exit(130)


if __name__ == "__main__":
    main()
