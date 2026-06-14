import json
import tempfile
import unittest
from pathlib import Path

from scripts.crawl_products import _ensure_append_starts_on_new_line, _read_checkpoint, _write_failed_targets


class CrawlCheckpointTest(unittest.TestCase):
    def test_reads_existing_product_ids_and_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "product_information.jsonl"
            rows = [
                {
                    "requested_product_id": "100",
                    "status": "ok",
                    "active": True,
                    "country_store": "ie",
                },
                {
                    "requested_product_id": "200",
                    "status": "not_found_in_configured_stores",
                    "active": False,
                },
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            checkpoint = _read_checkpoint(str(path))

            self.assertEqual(checkpoint["product_ids"], {"100", "200"})
            self.assertEqual(checkpoint["row_count"], 2)
            self.assertEqual(checkpoint["ok_count"], 1)
            self.assertEqual(checkpoint["active_count"], 1)
            self.assertEqual(checkpoint["status_counts"]["ok"], 1)
            self.assertEqual(checkpoint["country_counts"]["ie"], 1)

    def test_counts_invalid_checkpoint_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "product_information.jsonl"
            path.write_text('{"requested_product_id": "100", "status": "ok"}\nnot-json\n', encoding="utf-8")

            checkpoint = _read_checkpoint(str(path))

            self.assertEqual(checkpoint["product_ids"], {"100"})
            self.assertEqual(checkpoint["row_count"], 1)
            self.assertEqual(checkpoint["invalid_line_count"], 1)

    def test_checkpoint_can_be_limited_to_current_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "product_information.jsonl"
            rows = [
                {"requested_product_id": "100", "status": "ok", "active": True},
                {"requested_product_id": "999", "status": "ok", "active": True},
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            checkpoint = _read_checkpoint(str(path), {"100"})

            self.assertEqual(checkpoint["product_ids"], {"100"})
            self.assertEqual(checkpoint["row_count"], 1)
            self.assertEqual(checkpoint["ok_count"], 1)

    def test_resume_append_starts_on_new_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "product_information.jsonl"
            path.write_text('{"requested_product_id": "100"', encoding="utf-8")

            _ensure_append_starts_on_new_line(str(path))

            self.assertTrue(path.read_text(encoding="utf-8").endswith("\n"))

    def test_failed_target_file_uses_latest_result_per_product(self):
        with tempfile.TemporaryDirectory() as directory:
            product_info_path = Path(directory) / "product_information.jsonl"
            failed_path = Path(directory) / "product_failed_targets.csv"
            rows = [
                {
                    "requested_product_id": "100",
                    "status": "not_found_in_configured_stores",
                    "failure_reason": "not_found_in_configured_stores",
                    "source_url": "https://old.example/100",
                    "attempts": [{"url": "https://old.example/100"}],
                },
                {
                    "requested_product_id": "100",
                    "status": "ok",
                    "active": True,
                    "source_url": "https://new.example/100",
                },
                {
                    "requested_product_id": "200",
                    "status": "not_found_in_configured_stores",
                    "failure_reason": "not_found_in_configured_stores",
                    "source_url": "https://old.example/200",
                    "attempts": [{"url": "https://old.example/200"}],
                },
            ]
            product_info_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            targets = [
                {"product_id": "100", "candidate_url": "https://target.example/100"},
                {"product_id": "200", "candidate_url": "https://target.example/200"},
            ]

            failed_count = _write_failed_targets(str(product_info_path), str(failed_path), targets)

            lines = failed_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(failed_count, 1)
            self.assertEqual(len(lines), 2)
            self.assertIn("200", lines[1])
            self.assertIn("https://target.example/200", lines[1])


if __name__ == "__main__":
    unittest.main()
