"""Product target extraction export services.

This module contains the reusable application layer for Project 5 product target
outputs. MongoDB remains an adapter in ``mongodb.py``; scripts provide CLI
configuration only.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol, TextIO

from glamira_aws.observability import NoOpWorkflowObserver, WorkflowEvent, WorkflowObserver


PRODUCT_TARGET_FIELDS = [
    "product_id",
    "candidate_url",
    "source_event_type",
    "url_source_field",
    "event_count",
    "first_seen_at",
    "last_seen_at",
    "url_rank",
]


@dataclass(frozen=True)
class ProductTargetExportRequest:
    output_path: Path
    output_format: str = "csv"
    include_all_candidates: bool = False


@dataclass(frozen=True)
class ProductTargetExportResult:
    aggregation_engine: str
    output_mode: str
    scanned_records: int | None
    candidate_records: int
    grouped_url_count: int
    target_rows: int
    unique_products: int


class ProductTargetRankingStrategy(Protocol):
    """Strategy for deciding whether a ranked product target should be output."""

    def should_write(self, row: dict[str, Any]) -> bool:
        """Return true when a ranked target row belongs in the output."""


class AllCandidatesStrategy:
    """Write every ranked candidate URL for each product."""

    def should_write(self, row: dict[str, Any]) -> bool:
        return True


class BestCandidatePerProductStrategy:
    """Write only rank 1 for each product."""

    def should_write(self, row: dict[str, Any]) -> bool:
        return int(row.get("url_rank") or 0) == 1


class ProductTargetSink(Protocol):
    """Adapter interface for target output formats."""

    def write(self, row: dict[str, Any]) -> None:
        """Write one target row."""


class CsvProductTargetSink:
    """CSV adapter for product target rows."""

    def __init__(self, handle: TextIO) -> None:
        self._writer = csv.DictWriter(handle, fieldnames=PRODUCT_TARGET_FIELDS, extrasaction="ignore")
        self._writer.writeheader()

    def write(self, row: dict[str, Any]) -> None:
        self._writer.writerow(row)


class JsonlProductTargetSink:
    """JSONL adapter for product target rows."""

    def __init__(self, handle: TextIO) -> None:
        self._handle = handle

    def write(self, row: dict[str, Any]) -> None:
        self._handle.write(json.dumps(row, default=str, ensure_ascii=False) + "\n")


class ProductTargetSinkFactory:
    """Factory Method for target output sinks."""

    def create(self, output_format: str, handle: TextIO) -> ProductTargetSink:
        if output_format == "csv":
            return CsvProductTargetSink(handle)
        if output_format == "jsonl":
            return JsonlProductTargetSink(handle)
        raise ValueError(f"Unsupported product target output format: {output_format}")


class ProductTargetExportService:
    """Facade for writing ranked product targets and collecting run stats."""

    def __init__(
        self,
        sink_factory: ProductTargetSinkFactory | None = None,
        observer: WorkflowObserver | None = None,
    ) -> None:
        self._sink_factory = sink_factory or ProductTargetSinkFactory()
        self._observer = observer or NoOpWorkflowObserver()

    def export_stream(
        self,
        rows: Iterable[dict[str, Any]],
        request: ProductTargetExportRequest,
        scanned_records: int | None = None,
    ) -> ProductTargetExportResult:
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        output_mode = "all_ranked_candidates" if request.include_all_candidates else "one_best_target_per_product"
        strategy: ProductTargetRankingStrategy = (
            AllCandidatesStrategy()
            if request.include_all_candidates
            else BestCandidatePerProductStrategy()
        )

        grouped_url_count = 0
        candidate_records = 0
        target_rows = 0
        unique_products = 0
        last_product_id = None

        self._observer.on_event(
            WorkflowEvent(
                "product_targets_export_started",
                details={
                    "output": str(request.output_path),
                    "output_format": request.output_format,
                    "output_mode": output_mode,
                },
            )
        )

        newline = "" if request.output_format == "csv" else None
        with request.output_path.open("w", newline=newline, encoding="utf-8") as handle:
            sink = self._sink_factory.create(request.output_format, handle)
            for row in rows:
                grouped_url_count += 1
                candidate_records += int(row.get("event_count") or 0)
                product_id = row.get("product_id")
                if product_id != last_product_id:
                    unique_products += 1
                    last_product_id = product_id

                if not strategy.should_write(row):
                    self._observer.on_event(
                        WorkflowEvent(
                            "product_targets_export_progress",
                            count=grouped_url_count,
                            details={"target_rows": target_rows},
                        )
                    )
                    continue

                sink.write(row)
                target_rows += 1
                self._observer.on_event(
                    WorkflowEvent(
                        "product_targets_export_progress",
                        count=grouped_url_count,
                        details={"target_rows": target_rows},
                    )
                )

        result = ProductTargetExportResult(
            aggregation_engine="mongodb",
            output_mode=output_mode,
            scanned_records=scanned_records,
            candidate_records=candidate_records,
            grouped_url_count=grouped_url_count,
            target_rows=target_rows,
            unique_products=unique_products,
        )
        self._observer.on_event(
            WorkflowEvent(
                "product_targets_export_completed",
                count=grouped_url_count,
                details={
                    "target_rows": target_rows,
                    "candidate_records": candidate_records,
                    "unique_products": unique_products,
                },
            )
        )
        return result
