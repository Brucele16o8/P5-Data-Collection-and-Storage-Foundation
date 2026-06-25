"""Split crawled product JSONL into warehouse fields and raw react_data archive."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from glamira_aws.product_export import ProductExportService
from glamira_aws.product_export import iter_product_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Crawler product_information.jsonl input path.")
    parser.add_argument(
        "--warehouse-output",
        help="Warehouse export output path. Defaults to --output for backward compatibility.",
    )
    parser.add_argument(
        "--react-data-output",
        help="Raw react_data JSONL output path. If omitted, only the warehouse file is written.",
    )
    parser.add_argument(
        "--output",
        help="Deprecated alias for --warehouse-output.",
    )
    parser.add_argument("--warehouse-format", choices=("csv", "jsonl"), default="csv")
    return parser.parse_args()


def export_product_split(
    input_path: Path,
    warehouse_output_path: Path,
    warehouse_format: str,
    react_data_output_path: Path | None = None,
) -> dict[str, int]:
    result = ProductExportService().export_split(
        rows=iter_product_jsonl(input_path),
        warehouse_output_path=warehouse_output_path,
        warehouse_format=warehouse_format,
        react_data_output_path=react_data_output_path,
    )
    return {
        "warehouse_rows": result.warehouse_rows,
        "react_data_rows": result.react_data_rows,
    }


def export_product_warehouse(
    rows: Iterable[dict[str, Any]],
    output_path: Path,
    output_format: str,
) -> int:
    return ProductExportService().export_warehouse(
        rows=rows,
        output_path=output_path,
        output_format=output_format,
    )


def export_product_react_data(rows: Iterable[dict[str, Any]], output_path: Path) -> int:
    return ProductExportService().export_react_data(
        rows=rows,
        output_path=output_path,
    )


def main() -> None:
    args = parse_args()
    warehouse_output = args.warehouse_output or args.output
    if not warehouse_output:
        print("Either --warehouse-output or --output is required.", file=sys.stderr)
        sys.exit(2)

    try:
        result = export_product_split(
            Path(args.input).expanduser(),
            Path(warehouse_output).expanduser(),
            args.warehouse_format,
            Path(args.react_data_output).expanduser() if args.react_data_output else None,
        )
    except Exception as exc:
        print(f"Failed to export product split files: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Wrote {result['warehouse_rows']} product warehouse rows to {warehouse_output}")
    if args.react_data_output:
        print(f"Wrote {result['react_data_rows']} product react_data rows to {args.react_data_output}")


if __name__ == "__main__":
    main()
