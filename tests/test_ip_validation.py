import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from glamira_aws.ip_locations import is_valid_ip, normalize_ip
from scripts.process_ip_locations import (
    iter_distinct_ips_scan,
    iter_distinct_product_event_ips_scan,
    read_product_target_ids,
)


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents
        self.closed = False

    def __iter__(self):
        return iter(self.documents)

    def close(self):
        self.closed = True


class FakeCollection:
    def __init__(self, documents):
        self.documents = documents
        self.cursor = FakeCursor(documents)

    def count_documents(self, query):
        return len(self.documents)

    def find(self, query, projection=None, no_cursor_timeout=False):
        return self.cursor


class IpValidationTest(unittest.TestCase):
    def test_public_ip_is_valid(self):
        self.assertTrue(is_valid_ip("8.8.8.8"))

    def test_private_ip_is_invalid_by_default(self):
        self.assertFalse(is_valid_ip("192.168.1.10"))

    def test_private_ip_can_be_allowed(self):
        self.assertTrue(is_valid_ip("192.168.1.10", allow_private=True))

    def test_invalid_ip_is_false(self):
        self.assertFalse(is_valid_ip("not-an-ip"))

    def test_forwarded_for_header_uses_first_ip(self):
        self.assertEqual(normalize_ip("8.8.8.8, 10.0.0.1"), "8.8.8.8")

    def test_scan_discovery_yields_distinct_normalized_ips(self):
        collection = FakeCollection(
            [
                {"ip": "8.8.8.8"},
                {"ip": "8.8.8.8, 10.0.0.1"},
                {"ip": "1.1.1.1"},
            ]
        )

        stats = {}

        ips = list(iter_distinct_ips_scan(collection, "ip", progress_every=10, stats=stats))

        self.assertEqual(ips, ["8.8.8.8", "1.1.1.1"])
        self.assertEqual(stats["mongodb_ip_record_count"], 3)
        self.assertEqual(stats["mongodb_ip_scanned_count"], 3)
        self.assertTrue(collection.cursor.closed)

    def test_reads_product_target_ids_from_csv(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "product_targets.csv"
            path.write_text("product_id,candidate_url\nP1,https://example.com/p1\nP2,https://example.com/p2\n")

            product_ids = read_product_target_ids(path)

        self.assertEqual(product_ids, {"P1", "P2"})

    def test_product_event_scan_filters_to_target_products_and_distinct_ips(self):
        collection = FakeCollection(
            [
                {
                    "ip": "8.8.8.8",
                    "collection": "view_product_detail",
                    "product_id": "P1",
                    "current_url": "https://example.com/p1",
                },
                {
                    "ip": "8.8.8.8, 10.0.0.1",
                    "collection": "view_product_detail",
                    "product_id": "P1",
                    "current_url": "https://example.com/p1",
                },
                {
                    "ip": "1.1.1.1",
                    "collection": "view_product_detail",
                    "product_id": "P2",
                    "current_url": "https://example.com/p2",
                },
                {
                    "ip": "9.9.9.9",
                    "collection": "view_home",
                    "product_id": "P1",
                    "current_url": "https://example.com/p1",
                },
            ]
        )
        stats = {}

        ips = list(
            iter_distinct_product_event_ips_scan(
                collection,
                "ip",
                {"P1"},
                progress_every=10,
                stats=stats,
            )
        )

        self.assertEqual(ips, ["8.8.8.8"])
        self.assertEqual(stats["product_event_scanned_count"], 4)
        self.assertEqual(stats["product_event_candidate_count"], 3)
        self.assertEqual(stats["product_event_matched_count"], 2)
        self.assertTrue(collection.cursor.closed)


if __name__ == "__main__":
    unittest.main()
