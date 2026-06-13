"""Process distinct IP addresses from MongoDB with an IP2Location BIN file."""

from __future__ import annotations

import argparse
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

from glamira_aws.ip_locations import IPLocation, lookup_ip2location, lookup_ip2location_with_database
from glamira_aws.mongodb import MongoSettings, get_collection, write_jsonl
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
    parser.add_argument("--progress-every", type=int, default=int(os.getenv("PROGRESS_EVERY", "1000")))
    parser.add_argument("--summary-output", default=str(output_directory / "ip_locations_summary.json"))
    return parser.parse_args()


def iter_distinct_ips(collection, ip_field: str, limit: int | None = None):
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
    result = collection.insert_many(rows, ordered=False)
    collection.create_index("ip", unique=True)
    collection.create_index("status")
    return len(result.inserted_ids)


def main() -> None:
    started_at = monotonic()
    args = parse_args()
    db_path = download_to_tmp(args.ip2location_db_uri, suffix=".BIN") if args.ip2location_db_uri else None
    collection = get_collection(args.uri, args.database, args.collection)
    ips = list(iter_distinct_ips(collection, args.ip_field, args.limit))
    print(f"Loaded {len(ips)} distinct IP addresses from {args.database}.{args.collection}", flush=True)
    progress = ProgressReporter("Processing IP locations", total=len(ips), every=args.progress_every)
    rows = []
    cancelled = False
    if db_path:
        try:
            import IP2Location  # type: ignore

            database = IP2Location.IP2Location(db_path)
            ok_count = 0
            try:
                for index, ip in enumerate(ips, start=1):
                    row = lookup_ip2location_with_database(ip, database, allow_private=args.allow_private).to_dict()
                    rows.append(row)
                    ok_count += 1 if row.get("status") == "ok" else 0
                    progress.report(index, suffix=f"ok={ok_count:,}")
            except KeyboardInterrupt:
                cancelled = True
                print("Cancellation requested. Writing partial IP output and summary...", flush=True)
        except ImportError:
            rows = [
                IPLocation(
                    ip=str(ip),
                    status="missing_dependency",
                    error_message="Install ip2location-python.",
                ).to_dict()
                for ip in ips
            ]
    else:
        ok_count = 0
        try:
            for index, ip in enumerate(ips, start=1):
                row = lookup_ip2location(ip, None, allow_private=args.allow_private).to_dict()
                rows.append(row)
                ok_count += 1 if row.get("status") == "ok" else 0
                progress.report(index, suffix=f"ok={ok_count:,}")
        except KeyboardInterrupt:
            cancelled = True
            print("Cancellation requested. Writing partial IP output and summary...", flush=True)
    progress.report(len(rows), force=True)
    write_jsonl(args.output, rows)
    print(f"Wrote {len(rows)} IP location rows to {args.output}")

    if not args.skip_mongodb_write:
        inserted = write_locations_to_mongodb(args.uri, args.target_db, args.target_collection, rows)
        print(f"Wrote {inserted} IP location rows to {args.target_db}.{args.target_collection}")

    elapsed = monotonic() - started_at
    status_counts = Counter(str(row.get("status")) for row in rows)
    country_counts = Counter(str(row.get("country")) for row in rows if row.get("country"))
    summary = {
        "stage": "process_ip_locations",
        "cancelled": cancelled,
        "database": args.database,
        "collection": args.collection,
        "output": args.output,
        "summary_output": args.summary_output,
        "target_db": None if args.skip_mongodb_write else args.target_db,
        "target_collection": None if args.skip_mongodb_write else args.target_collection,
        "ip_count": len(ips),
        "processed_count": len(rows),
        "remaining_count": max(len(ips) - len(rows), 0),
        "status_counts": dict(status_counts),
        "country_counts": dict(country_counts),
        "ip2location_db_uri": args.ip2location_db_uri,
        "elapsed_seconds": round(elapsed, 3),
        "elapsed": format_duration(elapsed),
    }
    write_summary_json(args.summary_output, summary)
    print(f"Wrote IP location summary to {args.summary_output}")
    print(
        f"IP processing {'cancelled' if cancelled else 'finished'} in {format_duration(elapsed)}: "
        f"processed={len(rows):,}/{len(ips):,}",
        flush=True,
    )

    if args.output_s3_uri:
        upload_to_s3(args.output, args.output_s3_uri)
        print(f"Uploaded {args.output} to {args.output_s3_uri}")

    if cancelled:
        sys.exit(130)


if __name__ == "__main__":
    main()
