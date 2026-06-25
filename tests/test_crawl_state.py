import json
import tempfile
import unittest
from pathlib import Path

from glamira_aws.crawl_state import (
    ensure_append_starts_on_new_line,
    read_crawl_checkpoint,
    write_failed_targets,
)


class CrawlStateTest(unittest.TestCase):
    def test_read_crawl_checkpoint_summarizes_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "product_information.jsonl"
            rows = [
                {"requested_product_id": "100", "status": "ok", "active": True, "country_store": "ie"},
                {"requested_product_id": "200", "status": "crawl_failed", "active": False},
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\nnot-json\n", encoding="utf-8")

            checkpoint = read_crawl_checkpoint(str(path))

        self.assertEqual(checkpoint["product_ids"], {"100", "200"})
        self.assertEqual(checkpoint["row_count"], 2)
        self.assertEqual(checkpoint["invalid_line_count"], 1)
        self.assertEqual(checkpoint["status_counts"]["ok"], 1)
        self.assertEqual(checkpoint["country_counts"]["ie"], 1)

    def test_ensure_append_starts_on_new_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "product_information.jsonl"
            path.write_text('{"requested_product_id": "100"}', encoding="utf-8")

            ensure_append_starts_on_new_line(str(path))

            self.assertTrue(path.read_text(encoding="utf-8").endswith("\n"))

    def test_write_failed_targets_uses_latest_row_per_product(self):
        with tempfile.TemporaryDirectory() as directory:
            product_info_path = Path(directory) / "product_information.jsonl"
            failed_path = Path(directory) / "product_failed_targets.csv"
            rows = [
                {"requested_product_id": "100", "status": "crawl_failed", "source_url": "https://old/100"},
                {"requested_product_id": "100", "status": "ok", "source_url": "https://new/100"},
                {
                    "requested_product_id": "200",
                    "status": "not_found_in_configured_stores",
                    "failure_reason": "not_found_in_configured_stores",
                    "attempts": [{"url": "https://old/200"}],
                },
            ]
            product_info_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            targets = [
                {"product_id": "100", "candidate_url": "https://target/100"},
                {"product_id": "200", "candidate_url": "https://target/200"},
            ]

            failed_count = write_failed_targets(str(product_info_path), str(failed_path), targets)

            lines = failed_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(failed_count, 1)
            self.assertEqual(len(lines), 2)
            self.assertIn("200", lines[1])
            self.assertIn("https://target/200", lines[1])


if __name__ == "__main__":
    unittest.main()
