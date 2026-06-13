"""Glue job 06: submit Redshift COPY statements for processed S3 data."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from glamira_aws.glue_runtime import parse_job_args
from glamira_aws.redshift import generate_copy_sql
from glamira_aws.s3_io import write_text


def main() -> None:
    args = parse_job_args(
        required=["table_name", "s3_uri", "iam_role_arn"],
        optional=[
            "file_format",
            "region",
            "database",
            "workgroup_name",
            "cluster_identifier",
            "secret_arn",
            "sql_output_uri",
            "execute",
        ],
    )

    sql = generate_copy_sql(
        table_name=args["table_name"],
        s3_uri=args["s3_uri"],
        iam_role_arn=args["iam_role_arn"],
        file_format=args.get("file_format", "PARQUET"),
        region=args.get("region"),
    )

    if args.get("sql_output_uri"):
        write_text(args["sql_output_uri"], sql, "text/sql")
    print(sql)

    if str(args.get("execute", "false")).lower() != "true":
        return

    import boto3

    client = boto3.client("redshift-data", region_name=args.get("region"))
    request = {
        "Database": args["database"],
        "Sql": sql,
    }
    if args.get("workgroup_name"):
        request["WorkgroupName"] = args["workgroup_name"]
    if args.get("cluster_identifier"):
        request["ClusterIdentifier"] = args["cluster_identifier"]
    if args.get("secret_arn"):
        request["SecretArn"] = args["secret_arn"]

    response = client.execute_statement(**request)
    print(f"Submitted Redshift statement: {response['Id']}")


if __name__ == "__main__":
    main()
