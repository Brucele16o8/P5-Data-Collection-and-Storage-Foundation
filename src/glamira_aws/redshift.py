"""Redshift SQL helpers used by the final loading job."""

from __future__ import annotations


def generate_copy_sql(
    table_name: str,
    s3_uri: str,
    iam_role_arn: str,
    file_format: str = "PARQUET",
    region: str | None = None,
) -> str:
    format_sql = file_format.upper()
    if format_sql not in {"PARQUET", "CSV", "JSON"}:
        raise ValueError("file_format must be PARQUET, CSV, or JSON")

    if format_sql == "CSV":
        options = "CSV IGNOREHEADER 1"
    elif format_sql == "JSON":
        options = "JSON 'auto'"
    else:
        options = "FORMAT AS PARQUET"

    region_sql = f"\nREGION '{region}'" if region else ""
    return (
        f"COPY {table_name}\n"
        f"FROM '{s3_uri}'\n"
        f"IAM_ROLE '{iam_role_arn}'\n"
        f"{options}{region_sql};"
    )
