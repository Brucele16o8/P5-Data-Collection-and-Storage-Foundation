import csv
import json
import tempfile
import unittest
from pathlib import Path

from glamira_aws.product_targets import ProductTargetExportRequest, ProductTargetExportService


class RecordingWorkflowObserver:
    def __init__(self):
        self.events = []

    def on_event(self, event):
        self.events.append((event.name, event.count, dict(event.details)))


class ProductTargetExportServiceTest(unittest.TestCase):
    def test_exports_one_best_target_per_product_to_csv(self):
        rows = [
            {
                "product_id": "P1",
                "candidate_url": "https://example.test/p1-a",
                "event_count": 10,
                "url_rank": 1,
            },
            {
                "product_id": "P1",
                "candidate_url": "https://example.test/p1-b",
                "event_count": 4,
                "url_rank": 2,
            },
            {
                "product_id": "P2",
                "candidate_url": "https://example.test/p2",
                "event_count": 1,
                "url_rank": 1,
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "product_targets.csv"
            observer = RecordingWorkflowObserver()

            result = ProductTargetExportService(observer=observer).export_stream(
                rows,
                ProductTargetExportRequest(output_path=output_path, output_format="csv"),
                scanned_records=7,
            )

            with output_path.open(encoding="utf-8") as handle:
                output_rows = list(csv.DictReader(handle))

        self.assertEqual(result.scanned_records, 7)
        self.assertEqual(result.grouped_url_count, 3)
        self.assertEqual(result.candidate_records, 15)
        self.assertEqual(result.target_rows, 2)
        self.assertEqual(result.unique_products, 2)
        self.assertEqual([row["product_id"] for row in output_rows], ["P1", "P2"])
        self.assertEqual(observer.events[0][0], "product_targets_export_started")
        self.assertEqual(observer.events[-1][0], "product_targets_export_completed")

    def test_exports_all_candidates_to_jsonl(self):
        rows = [
            {"product_id": "P1", "candidate_url": "https://example.test/p1-a", "event_count": 2, "url_rank": 1},
            {"product_id": "P1", "candidate_url": "https://example.test/p1-b", "event_count": 1, "url_rank": 2},
        ]
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "product_targets.jsonl"

            result = ProductTargetExportService().export_stream(
                rows,
                ProductTargetExportRequest(
                    output_path=output_path,
                    output_format="jsonl",
                    include_all_candidates=True,
                ),
            )

            output_rows = [
                json.loads(line)
                for line in output_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(result.output_mode, "all_ranked_candidates")
        self.assertEqual(result.target_rows, 2)
        self.assertEqual([row["url_rank"] for row in output_rows], [1, 2])

    def test_rejects_unknown_output_format(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "product_targets.xml"

            with self.assertRaisesRegex(ValueError, "Unsupported product target output format"):
                ProductTargetExportService().export_stream(
                    [{"product_id": "P1", "url_rank": 1}],
                    ProductTargetExportRequest(output_path=output_path, output_format="xml"),
                )


if __name__ == "__main__":
    unittest.main()
