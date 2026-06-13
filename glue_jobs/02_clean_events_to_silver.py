"""Glue job 02: normalize raw Glamira events and write clean Parquet to S3 silver."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from glamira_aws.glue_runtime import parse_job_args


def _first_existing_column(df, names: list[str]):
    from pyspark.sql.functions import col, lit

    existing = [col(name).cast("string") for name in names if name in df.columns]
    if not existing:
        return lit(None).cast("string")
    if len(existing) == 1:
        return existing[0]

    from pyspark.sql.functions import coalesce

    return coalesce(*existing)


def main() -> None:
    args = parse_job_args(
        required=["input_uri", "output_uri"],
        optional=["input_format", "write_mode"],
    )

    from pyspark.sql import SparkSession
    from pyspark.sql.functions import current_timestamp, input_file_name, lower, to_timestamp, trim

    spark = SparkSession.builder.appName("project05-clean-events-to-silver").getOrCreate()
    fmt = args.get("input_format", "auto").lower()
    input_uri = args["input_uri"]

    if fmt == "auto":
        if input_uri.endswith(".csv"):
            fmt = "csv"
        elif input_uri.endswith(".parquet"):
            fmt = "parquet"
        else:
            fmt = "json"

    if fmt == "csv":
        df = spark.read.option("header", "true").csv(input_uri)
    elif fmt == "parquet":
        df = spark.read.parquet(input_uri)
    else:
        df = spark.read.json(input_uri)

    cleaned = df.select(
        lower(trim(_first_existing_column(df, ["event_type", "event", "event_name", "collection", "action"]))).alias("event_type"),
        to_timestamp(_first_existing_column(df, ["event_time", "timestamp", "created_at", "time", "datetime"])).alias("event_time"),
        trim(_first_existing_column(df, ["ip", "ip_address", "client_ip", "user_ip"])).alias("ip"),
        trim(_first_existing_column(df, ["product_id", "productid", "product"])).alias("product_id"),
        trim(_first_existing_column(df, ["viewing_product_id", "viewingProductId"])).alias("viewing_product_id"),
        trim(_first_existing_column(df, ["current_url", "currentUrl", "url"])).alias("current_url"),
        trim(_first_existing_column(df, ["referrer_url", "referrerUrl", "referer_url", "referer"])).alias("referrer_url"),
        input_file_name().alias("source_file"),
        current_timestamp().alias("ingested_at"),
    )

    cleaned.write.mode(args.get("write_mode", "overwrite")).parquet(args["output_uri"])


if __name__ == "__main__":
    main()
