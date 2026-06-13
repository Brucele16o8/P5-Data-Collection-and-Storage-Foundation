"""Glue job 01: profile raw Glamira events and create a data dictionary."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from glamira_aws.data_dictionary import profile_records
from glamira_aws.glue_runtime import parse_job_args
from glamira_aws.s3_io import write_csv, write_json


def _read_local_records(input_uri: str) -> list[dict[str, Any]]:
    path = Path(input_uri).expanduser()
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else [payload]
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    raise ValueError("Local profiling supports .jsonl, .json, and .csv files.")


def _profile_with_spark(input_uri: str, input_format: str, output_uri: str) -> None:
    from pyspark.sql import SparkSession
    from pyspark.sql.functions import col

    spark = SparkSession.builder.appName("project05-profile-raw-events").getOrCreate()
    reader = spark.read.option("multiLine", "false")
    fmt = input_format.lower()
    if fmt == "auto":
        if input_uri.endswith(".csv"):
            fmt = "csv"
        elif input_uri.endswith(".parquet"):
            fmt = "parquet"
        else:
            fmt = "json"

    if fmt == "csv":
        df = reader.option("header", "true").csv(input_uri)
    elif fmt == "parquet":
        df = reader.parquet(input_uri)
    else:
        df = reader.json(input_uri)

    total = df.count()
    rows: list[dict[str, Any]] = []
    for field in df.schema.fields:
        name = field.name
        non_null = df.where(col(name).isNotNull()).count()
        sample_rows = df.where(col(name).isNotNull()).select(col(name).cast("string")).limit(3).collect()
        samples = [row[0][:200] for row in sample_rows if row[0] is not None]
        rows.append(
            {
                "field_name": name,
                "data_type": field.dataType.simpleString(),
                "observed_types": field.dataType.simpleString(),
                "non_null_count": non_null,
                "null_pct": round(((total - non_null) / total) * 100, 2) if total else 0.0,
                "example": samples[0] if samples else "",
                "sample_values": " | ".join(samples),
            }
        )

    fieldnames = ["field_name", "data_type", "observed_types", "non_null_count", "null_pct", "example", "sample_values"]
    write_csv(f"{output_uri.rstrip('/')}/data_dictionary.csv", rows, fieldnames)
    write_json(f"{output_uri.rstrip('/')}/data_dictionary.json", rows)

    event_column = "event_type" if "event_type" in df.columns else None
    if event_column:
        event_counts = [row.asDict() for row in df.groupBy(event_column).count().orderBy("count", ascending=False).collect()]
        write_json(f"{output_uri.rstrip('/')}/event_type_counts.json", event_counts)


def main() -> None:
    args = parse_job_args(
        required=["input_uri", "output_uri"],
        optional=["input_format", "local_only"],
    )
    input_format = args.get("input_format", "auto")
    local_only = str(args.get("local_only", "false")).lower() == "true"

    if local_only or not args["input_uri"].startswith("s3://"):
        records = _read_local_records(args["input_uri"])
        rows = profile_records(records)
        fieldnames = ["field_name", "data_type", "observed_types", "non_null_count", "null_pct", "example", "sample_values"]
        write_csv(f"{args['output_uri'].rstrip('/')}/data_dictionary.csv", rows, fieldnames)
        write_json(f"{args['output_uri'].rstrip('/')}/data_dictionary.json", rows)
        return

    _profile_with_spark(args["input_uri"], input_format, args["output_uri"])


if __name__ == "__main__":
    main()
