"""Glue job 03: enrich distinct event IPs with IP2Location data."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from glamira_aws.glue_runtime import parse_job_args
from glamira_aws.ip_locations import lookup_ip2location
from glamira_aws.s3_io import download_to_tmp


def main() -> None:
    args = parse_job_args(
        required=["input_uri", "output_uri"],
        optional=["input_format", "ip_column", "ip2location_db_uri", "allow_private", "write_mode"],
    )

    from pyspark.sql import SparkSession
    from pyspark.sql.functions import col

    spark = SparkSession.builder.appName("project05-process-ip-locations").getOrCreate()
    fmt = args.get("input_format", "parquet").lower()
    ip_column = args.get("ip_column", "ip")
    allow_private = str(args.get("allow_private", "false")).lower() == "true"
    db_uri = args.get("ip2location_db_uri")
    db_path = download_to_tmp(db_uri, suffix=".BIN") if db_uri else None

    if fmt == "csv":
        df = spark.read.option("header", "true").csv(args["input_uri"])
    elif fmt == "json":
        df = spark.read.json(args["input_uri"])
    else:
        df = spark.read.parquet(args["input_uri"])

    ips = [row[ip_column] for row in df.select(ip_column).where(col(ip_column).isNotNull()).distinct().collect()]
    rows = [lookup_ip2location(ip, db_path, allow_private=allow_private).to_dict() for ip in ips]

    output_df = spark.createDataFrame(rows)
    output_df.write.mode(args.get("write_mode", "overwrite")).parquet(args["output_uri"])


if __name__ == "__main__":
    main()
