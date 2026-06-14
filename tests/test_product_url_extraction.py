import unittest

from glamira_aws.events import aggregate_product_url_candidates, build_product_url_candidate


class ProductUrlExtractionTest(unittest.TestCase):
    def test_current_url_event_uses_product_id_first(self):
        record = {
            "event_type": "view_product_detail",
            "product_id": "P100",
            "viewing_product_id": "P999",
            "current_url": "https://example.com/p100",
        }

        candidate = build_product_url_candidate(record)

        self.assertEqual(candidate["product_id"], "P100")
        self.assertEqual(candidate["candidate_url"], "https://example.com/p100")
        self.assertEqual(candidate["url_source_field"], "current_url")

    def test_current_url_event_falls_back_to_viewing_product_id(self):
        record = {
            "event_type": "add_to_cart_action",
            "viewing_product_id": "P200",
            "current_url": "https://example.com/p200",
        }

        candidate = build_product_url_candidate(record)

        self.assertEqual(candidate["product_id"], "P200")

    def test_recommend_clicked_uses_referrer_url(self):
        record = {
            "event_type": "product_view_all_recommend_clicked",
            "viewing_product_id": "P300",
            "referrer_url": "https://example.com/p300",
            "current_url": "https://example.com/recommendations",
        }

        candidate = build_product_url_candidate(record)

        self.assertEqual(candidate["product_id"], "P300")
        self.assertEqual(candidate["candidate_url"], "https://example.com/p300")
        self.assertEqual(candidate["url_source_field"], "referrer_url")

    def test_countly_schema_uses_collection_and_time_stamp(self):
        record = {
            "collection": "view_product_detail",
            "product_id": "P400",
            "current_url": "https://example.com/p400",
            "time_stamp": 1718060000,
        }

        candidate = build_product_url_candidate(record)

        self.assertEqual(candidate["product_id"], "P400")
        self.assertEqual(candidate["source_event_type"], "view_product_detail")
        self.assertEqual(candidate["event_time"], "1718060000")

    def test_aggregate_ranks_highest_event_count_first(self):
        records = [
            {"event_type": "view_product_detail", "product_id": "P1", "current_url": "https://example.com/a"},
            {"event_type": "view_product_detail", "product_id": "P1", "current_url": "https://example.com/a"},
            {"event_type": "view_product_detail", "product_id": "P1", "current_url": "https://example.com/b"},
        ]

        candidates = aggregate_product_url_candidates(records)

        self.assertEqual(candidates[0]["candidate_url"], "https://example.com/a")
        self.assertEqual(candidates[0]["event_count"], 2)
        self.assertEqual(candidates[0]["url_rank"], 1)

    def test_aggregate_can_return_one_best_target_per_product(self):
        records = [
            {"event_type": "view_product_detail", "product_id": "P1", "current_url": "https://example.com/a"},
            {"event_type": "view_product_detail", "product_id": "P1", "current_url": "https://example.com/a"},
            {"event_type": "view_product_detail", "product_id": "P1", "current_url": "https://example.com/b"},
            {"event_type": "view_product_detail", "product_id": "P2", "current_url": "https://example.com/c"},
        ]

        candidates = aggregate_product_url_candidates(records, include_all_candidates=False)

        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["product_id"], "P1")
        self.assertEqual(candidates[0]["candidate_url"], "https://example.com/a")
        self.assertEqual(candidates[0]["url_rank"], 1)
        self.assertEqual(candidates[1]["product_id"], "P2")


if __name__ == "__main__":
    unittest.main()
