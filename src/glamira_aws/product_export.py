"""Product export projections and streaming writers.

This module keeps product export rules independent from local scripts and
future warehouse loaders. It uses a few small GoF-style boundaries:

- Strategy: projection classes define different output shapes.
- Factory Method: ``ProductSinkFactory`` creates output sinks by format.
- Adapter: sink classes adapt CSV/JSONL libraries to one writer protocol.
- Facade: ``ProductExportService`` runs the complete export use case.
- Observer: optional callbacks report progress without polluting transforms.
- Iterator: JSONL input is streamed instead of loaded into memory.
"""

from __future__ import annotations

import csv
import json
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol, TextIO


WAREHOUSE_PRODUCT_FIELDS = [
    "requested_product_id",
    "product_id",
    "source_url",
    "original_url",
    "resolved_url",
    "country_store",
    "crawl_url_strategy",
    "product_name",
    "name",
    "sku",
    "category",
    "category_name",
    "price",
    "currency",
    "store_code",
    "active",
    "scraped_at",
    "status",
    "failure_reason",
    "error_message",
    "react_data_available",
]

REACT_DATA_FIELDS = [
    "requested_product_id",
    "product_id",
    "scraped_at",
    "status",
    "react_data",
]


@dataclass(frozen=True)
class ExportResult:
    """Summary returned by a product export run."""

    warehouse_rows: int = 0
    react_data_rows: int = 0


class ProductProjection(Protocol):
    """Strategy interface for turning crawler rows into an output shape."""

    @property
    def fields(self) -> list[str]:
        """Ordered field list for columnar outputs."""

    def project(self, row: dict[str, Any]) -> dict[str, Any]:
        """Return one projected row."""


class WarehouseProductProjection:
    """Projection used by Project 7 staging/mart models.

    The large ``react_data`` payload is intentionally excluded. It remains
    available in a separate raw archive projection.
    """

    @property
    def fields(self) -> list[str]:
        return WAREHOUSE_PRODUCT_FIELDS

    def project(self, row: dict[str, Any]) -> dict[str, Any]:
        react_data = row.get("react_data")
        return {
            "requested_product_id": row.get("requested_product_id"),
            "product_id": row.get("product_id") or row.get("requested_product_id"),
            "source_url": row.get("source_url"),
            "original_url": row.get("original_url"),
            "resolved_url": row.get("resolved_url"),
            "country_store": row.get("country_store"),
            "crawl_url_strategy": row.get("crawl_url_strategy"),
            "product_name": row.get("product_name") or row.get("name"),
            "name": row.get("name"),
            "sku": row.get("sku"),
            "category": row.get("category"),
            "category_name": row.get("category_name"),
            "price": row.get("price"),
            "currency": row.get("currency"),
            "store_code": row.get("store_code"),
            "active": row.get("active"),
            "scraped_at": row.get("scraped_at"),
            "status": row.get("status"),
            "failure_reason": row.get("failure_reason"),
            "error_message": row.get("error_message"),
            "react_data_available": bool(isinstance(react_data, dict) and react_data),
        }


class ReactDataProjection:
    """Raw archive projection for replay/debugging of crawled product data."""

    @property
    def fields(self) -> list[str]:
        return REACT_DATA_FIELDS

    def project(self, row: dict[str, Any]) -> dict[str, Any]:
        react_data = row.get("react_data")
        return {
            "requested_product_id": row.get("requested_product_id"),
            "product_id": row.get("product_id") or row.get("requested_product_id"),
            "scraped_at": row.get("scraped_at"),
            "status": row.get("status"),
            "react_data": react_data if isinstance(react_data, dict) else {},
        }


def csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    return value


def product_warehouse_row(row: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible helper for callers that only need this projection."""

    return WarehouseProductProjection().project(row)


def product_react_data_row(row: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible helper for callers that only need this projection."""

    return ReactDataProjection().project(row)


class ProductSink(Protocol):
    """Adapter interface for export destinations."""

    def write(self, row: dict[str, Any]) -> None:
        """Write one source row."""


class CsvProjectionSink:
    """CSV writer adapter for projected rows."""

    def __init__(self, handle: TextIO, projection: ProductProjection) -> None:
        self._projection = projection
        self._writer = csv.DictWriter(
            handle,
            fieldnames=projection.fields,
            extrasaction="ignore",
        )
        self._writer.writeheader()

    def write(self, row: dict[str, Any]) -> None:
        projected = self._projection.project(row)
        self._writer.writerow({field: csv_value(value) for field, value in projected.items()})


class JsonlProjectionSink:
    """JSONL writer adapter for projected rows."""

    def __init__(self, handle: TextIO, projection: ProductProjection) -> None:
        self._handle = handle
        self._projection = projection

    def write(self, row: dict[str, Any]) -> None:
        projected = self._projection.project(row)
        self._handle.write(json.dumps(projected, default=str, ensure_ascii=False) + "\n")


class ProductSinkFactory:
    """Factory Method for creating writer adapters from an output format."""

    def create(
        self,
        *,
        output_format: str,
        handle: TextIO,
        projection: ProductProjection,
    ) -> ProductSink:
        if output_format == "csv":
            return CsvProjectionSink(handle, projection)
        if output_format == "jsonl":
            return JsonlProjectionSink(handle, projection)
        raise ValueError(f"Unsupported product export format: {output_format}")


class ProductExportObserver(Protocol):
    """Observer hooks for progress/log/audit integrations."""

    def on_started(self) -> None:
        """Called before export begins."""

    def on_progress(self, source_rows: int) -> None:
        """Called periodically while rows are streamed."""

    def on_completed(self, result: ExportResult) -> None:
        """Called after a successful export."""


class NoOpProductExportObserver:
    """Default observer for callers that do not need progress or audit events."""

    def on_started(self) -> None:
        return

    def on_progress(self, source_rows: int) -> None:
        return

    def on_completed(self, result: ExportResult) -> None:
        return


def iter_product_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    """Stream product JSONL records from disk.

    This iterator keeps Project 5 exports scalable for larger crawler outputs by
    avoiding a full in-memory list of product records.
    """

    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number} in {path}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Expected JSON object on line {line_number} in {path}")
            yield row


class ProductExportService:
    """Facade for splitting crawled products into analysis and raw outputs."""

    def __init__(
        self,
        sink_factory: ProductSinkFactory | None = None,
        observer: ProductExportObserver | None = None,
        progress_every: int = 10_000,
    ) -> None:
        self._sink_factory = sink_factory or ProductSinkFactory()
        self._observer = observer or NoOpProductExportObserver()
        self._progress_every = progress_every

    def export_split(
        self,
        *,
        rows: Iterable[dict[str, Any]],
        warehouse_output_path: Path,
        warehouse_format: str,
        react_data_output_path: Path | None = None,
    ) -> ExportResult:
        warehouse_output_path.parent.mkdir(parents=True, exist_ok=True)
        if react_data_output_path is not None:
            react_data_output_path.parent.mkdir(parents=True, exist_ok=True)

        self._observer.on_started()
        source_count = 0
        warehouse_count = 0
        react_data_count = 0

        with ExitStack() as stack:
            warehouse_handle = stack.enter_context(
                warehouse_output_path.open(
                    "w",
                    newline="" if warehouse_format == "csv" else None,
                    encoding="utf-8",
                )
            )
            warehouse_sink = self._sink_factory.create(
                output_format=warehouse_format,
                handle=warehouse_handle,
                projection=WarehouseProductProjection(),
            )

            react_data_sink = None
            if react_data_output_path is not None:
                react_data_handle = stack.enter_context(react_data_output_path.open("w", encoding="utf-8"))
                react_data_sink = self._sink_factory.create(
                    output_format="jsonl",
                    handle=react_data_handle,
                    projection=ReactDataProjection(),
                )

            for row in rows:
                source_count += 1
                warehouse_sink.write(row)
                warehouse_count += 1

                if react_data_sink is not None:
                    react_data_sink.write(row)
                    react_data_count += 1

                if source_count % self._progress_every == 0:
                    self._observer.on_progress(source_count)

        result = ExportResult(
            warehouse_rows=warehouse_count,
            react_data_rows=react_data_count,
        )
        self._observer.on_completed(result)
        return result

    def export_warehouse(
        self,
        *,
        rows: Iterable[dict[str, Any]],
        output_path: Path,
        output_format: str,
    ) -> int:
        return self.export_split(
            rows=rows,
            warehouse_output_path=output_path,
            warehouse_format=output_format,
        ).warehouse_rows

    def export_react_data(
        self,
        *,
        rows: Iterable[dict[str, Any]],
        output_path: Path,
    ) -> int:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with output_path.open("w", encoding="utf-8") as handle:
            sink = self._sink_factory.create(
                output_format="jsonl",
                handle=handle,
                projection=ReactDataProjection(),
            )
            for row in rows:
                sink.write(row)
                count += 1
        return count
