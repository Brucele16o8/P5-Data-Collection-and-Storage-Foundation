"""Create a practical data dictionary from raw event records."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def flatten_record(record: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, value in record.items():
        field = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flattened.update(flatten_record(value, field))
        else:
            flattened[field] = value
    return flattened


def infer_value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def profile_records(records: list[dict[str, Any]], sample_size: int = 3) -> list[dict[str, Any]]:
    total_records = len(records)
    type_counts: dict[str, Counter[str]] = defaultdict(Counter)
    non_null_counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)

    for record in records:
        for field, value in flatten_record(record).items():
            value_type = infer_value_type(value)
            type_counts[field][value_type] += 1
            if value is not None and str(value).strip() != "":
                non_null_counts[field] += 1
                text = str(value)
                if text not in examples[field] and len(examples[field]) < sample_size:
                    examples[field].append(text[:200])

    rows: list[dict[str, Any]] = []
    for field in sorted(type_counts):
        observed_types = type_counts[field]
        primary_type = observed_types.most_common(1)[0][0]
        non_null_count = non_null_counts[field]
        null_count = max(total_records - non_null_count, 0)
        null_pct = round((null_count / total_records) * 100, 2) if total_records else 0.0
        rows.append(
            {
                "field_name": field,
                "data_type": primary_type,
                "observed_types": ",".join(sorted(observed_types)),
                "non_null_count": non_null_count,
                "null_pct": null_pct,
                "example": examples[field][0] if examples[field] else "",
                "sample_values": " | ".join(examples[field]),
            }
        )
    return rows
