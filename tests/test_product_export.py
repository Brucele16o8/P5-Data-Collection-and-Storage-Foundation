import csv
import json
import tempfile
import unittest
from pathlib import Path

from glamira_aws.product_export import ProductExportService
from scripts.export_product_warehouse import export_product_split


class RecordingObserver:
    def __init__(self):
        self.events = []

    def on_started(self):
        self.events.append(("started", None))

    def on_progress(self, source_rows):
        self.events.append(("progress", source_rows))

    def on_completed(self, result):
        self.events.append(("completed", result.warehouse_rows, result.react_data_rows))


class ProductExportTest(unittest.TestCase):
    def test_exports_warehouse_csv_without_react_data_and_separate_react_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            input_path = base / "product_information.jsonl"
            warehouse_path = base / "product_information_warehouse.csv"
            react_data_path = base / "product_react_data.jsonl"
            rows = [
                {
                    "requested_product_id": "100",
                    "product_id": "100",
                    "source_url": "https://example.test/100",
                    "original_url": "https://example.test/original-100",
                    "resolved_url": "https://example.test/resolved-100",
                    "country_store": "ie",
                    "crawl_url_strategy": "fallback_domain_product_id_url",
                    "product_name": "Ring Test",
                    "name": "Ring Test",
                    "sku": "RING-TEST",
                    "category": "rings",
                    "category_name": "Rings",
                    "price": "100.00",
                    "currency": "EUR",
                    "store_code": "glie",
                    "active": True,
                    "scraped_at": "2026-06-19T00:00:00+00:00",
                    "status": "ok",
                    "failure_reason": None,
                    "error_message": None,
                    "react_data_basic": {"product_id": 100, "name": "Ring Test"},
                    "react_data": {"product_id": 100, "name": "Ring Test", "options": [{"id": "1"}]},
                },
                {
                    "requested_product_id": "200",
                    "status": "not_found_in_configured_stores",
                    "failure_reason": "not_found_in_configured_stores",
                    "react_data": {},
                },
            ]
            input_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            result = export_product_split(input_path, warehouse_path, "csv", react_data_path)

            self.assertEqual(result, {"warehouse_rows": 2, "react_data_rows": 2})
            with warehouse_path.open(encoding="utf-8") as handle:
                warehouse_rows = list(csv.DictReader(handle))
            self.assertEqual(len(warehouse_rows), 2)
            self.assertNotIn("react_data", warehouse_rows[0])
            self.assertNotIn("react_data_basic", warehouse_rows[0])
            self.assertEqual(warehouse_rows[0]["product_id"], "100")
            self.assertEqual(warehouse_rows[0]["active"], "true")
            self.assertEqual(warehouse_rows[0]["react_data_available"], "true")
            self.assertEqual(warehouse_rows[1]["product_id"], "200")
            self.assertEqual(warehouse_rows[1]["react_data_available"], "false")

            react_rows = [
                json.loads(line)
                for line in react_data_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(react_rows), 2)
            self.assertEqual(react_rows[0]["requested_product_id"], "100")
            self.assertEqual(react_rows[0]["react_data"]["options"], [{"id": "1"}])
            self.assertEqual(react_rows[1]["requested_product_id"], "200")
            self.assertEqual(react_rows[1]["react_data"], {})

    def test_export_service_supports_jsonl_warehouse_projection_and_progress_observer(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "product_information_warehouse.jsonl"
            rows = [
                {
                    "requested_product_id": "100",
                    "product_id": "100",
                    "name": "Ring Test",
                    "react_data": {"product_id": 100},
                },
                {
                    "requested_product_id": "200",
                    "name": "Pendant Test",
                    "react_data": {},
                },
            ]
            observer = RecordingObserver()
            service = ProductExportService(observer=observer, progress_every=1)

            result = service.export_split(
                rows=rows,
                warehouse_output_path=output_path,
                warehouse_format="jsonl",
            )

            self.assertEqual(result.warehouse_rows, 2)
            self.assertEqual(result.react_data_rows, 0)
            output_rows = [
                json.loads(line)
                for line in output_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(output_rows[0]["product_name"], "Ring Test")
            self.assertNotIn("react_data", output_rows[0])
            self.assertTrue(output_rows[0]["react_data_available"])
            self.assertFalse(output_rows[1]["react_data_available"])
            self.assertEqual(
                observer.events,
                [
                    ("started", None),
                    ("progress", 1),
                    ("progress", 2),
                    ("completed", 2, 0),
                ],
            )

    def test_export_service_rejects_unknown_warehouse_format(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "product_information.out"
            service = ProductExportService()

            with self.assertRaisesRegex(ValueError, "Unsupported product export format"):
                service.export_split(
                    rows=[{"requested_product_id": "100"}],
                    warehouse_output_path=output_path,
                    warehouse_format="xml",
                )


if __name__ == "__main__":
    unittest.main()
