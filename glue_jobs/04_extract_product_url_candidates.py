"""Glue job 04: extract and rank product URL candidates from clean events."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from glamira_aws.events import PRODUCT_CURRENT_URL_EVENTS, RECOMMEND_CLICK_EVENT
from glamira_aws.glue_runtime import parse_job_args


def main() -> None:
    args = parse_job_args(
        required=["input_uri", "output_uri"],
        optional=["input_format", "write_mode"],
    )

    from pyspark.sql import SparkSession, Window
    from pyspark.sql.functions import col, coalesce, count, lit, max as spark_max, min as spark_min, row_number, trim

    spark = SparkSession.builder.appName("project05-extract-product-url-candidates").getOrCreate()
    fmt = args.get("input_format", "parquet").lower()
    if fmt == "csv":
        df = spark.read.option("header", "true").csv(args["input_uri"])
    elif fmt == "json":
        df = spark.read.json(args["input_uri"])
    else:
        df = spark.read.parquet(args["input_uri"])

    def optional_col(name: str):
        return col(name) if name in df.columns else lit(None)

    event_col = coalesce(optional_col("event_type"), optional_col("collection"))
    event_time_col = coalesce(optional_col("event_time"), optional_col("time_stamp"))
    working = df.withColumn("_event_type", event_col).withColumn("_event_time", event_time_col)

    current_url_candidates = (
        working.where(col("_event_type").isin(list(PRODUCT_CURRENT_URL_EVENTS)))
        .select(
            trim(coalesce(optional_col("product_id"), optional_col("viewing_product_id"))).alias("product_id"),
            trim(optional_col("current_url")).alias("candidate_url"),
            col("_event_type").alias("source_event_type"),
            lit("current_url").alias("url_source_field"),
            col("_event_time").alias("event_time"),
        )
    )

    recommend_candidates = (
        working.where(col("_event_type") == RECOMMEND_CLICK_EVENT)
        .select(
            trim(optional_col("viewing_product_id")).alias("product_id"),
            trim(optional_col("referrer_url")).alias("candidate_url"),
            col("_event_type").alias("source_event_type"),
            lit("referrer_url").alias("url_source_field"),
            col("_event_time").alias("event_time"),
        )
    )

    candidates = current_url_candidates.unionByName(recommend_candidates).where(
        col("product_id").isNotNull() & col("candidate_url").isNotNull()
    )

    grouped = candidates.groupBy("product_id", "candidate_url", "source_event_type", "url_source_field").agg(
        count(lit(1)).alias("event_count"),
        spark_min("event_time").alias("first_seen_at"),
        spark_max("event_time").alias("last_seen_at"),
    )

    window = Window.partitionBy("product_id").orderBy(col("event_count").desc(), col("last_seen_at").desc_nulls_last())
    ranked = grouped.withColumn("url_rank", row_number().over(window))
    ranked.write.mode(args.get("write_mode", "overwrite")).parquet(args["output_uri"])


if __name__ == "__main__":
    main()
