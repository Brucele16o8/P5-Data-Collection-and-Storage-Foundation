"""Small URI helpers for reading and writing local paths or S3 objects."""

from __future__ import annotations

import csv
import io
import json
import tempfile
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


def is_s3_uri(uri: str) -> bool:
    return uri.startswith("s3://")


def parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Not a valid S3 URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def _s3_client():
    import boto3

    return boto3.client("s3")


def ensure_parent(uri: str) -> None:
    if is_s3_uri(uri):
        return
    Path(uri).expanduser().parent.mkdir(parents=True, exist_ok=True)


def read_text(uri: str) -> str:
    if is_s3_uri(uri):
        bucket, key = parse_s3_uri(uri)
        obj = _s3_client().get_object(Bucket=bucket, Key=key)
        return obj["Body"].read().decode("utf-8")
    return Path(uri).expanduser().read_text(encoding="utf-8")


def write_text(uri: str, content: str, content_type: str = "text/plain") -> None:
    if is_s3_uri(uri):
        bucket, key = parse_s3_uri(uri)
        _s3_client().put_object(
            Bucket=bucket,
            Key=key,
            Body=content.encode("utf-8"),
            ContentType=content_type,
        )
        return
    ensure_parent(uri)
    Path(uri).expanduser().write_text(content, encoding="utf-8")


def write_json(uri: str, payload: Any) -> None:
    write_text(uri, json.dumps(payload, indent=2, default=str), "application/json")


def write_csv(uri: str, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    write_text(uri, buffer.getvalue(), "text/csv")


def read_csv(uri: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(read_text(uri))))


def list_s3_objects(prefix_uri: str, suffixes: tuple[str, ...] = ()) -> list[str]:
    if not is_s3_uri(prefix_uri):
        base = Path(prefix_uri).expanduser()
        if base.is_file():
            return [str(base)]
        paths = [path for path in base.rglob("*") if path.is_file()]
        if suffixes:
            paths = [path for path in paths if path.name.endswith(suffixes)]
        return [str(path) for path in paths]

    bucket, prefix = parse_s3_uri(prefix_uri)
    client = _s3_client()
    paginator = client.get_paginator("list_objects_v2")
    uris: list[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            if suffixes and not key.endswith(suffixes):
                continue
            uris.append(f"s3://{bucket}/{key}")
    return uris


def download_to_tmp(uri: str, suffix: str = "") -> str:
    if not is_s3_uri(uri):
        return str(Path(uri).expanduser())

    bucket, key = parse_s3_uri(uri)
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    handle.close()
    _s3_client().download_file(bucket, key, handle.name)
    return handle.name
