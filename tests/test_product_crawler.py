import unittest

from glamira_aws.product_crawler import (
    build_product_url_attempts,
    extract_product_info_from_html,
    extract_react_data,
)


class ProductCrawlerTest(unittest.TestCase):
    def test_extracts_react_data_object(self):
        html = """
        <html><script>
        var react_data = {"product_id": 113650, "name": "Women's Bangle Seema", "sku": "Tabetha-Bracelet", "store_code": "glau"};
        </script></html>
        """

        react_data = extract_react_data(html)

        self.assertEqual(react_data["product_id"], 113650)
        self.assertEqual(react_data["store_code"], "glau")

    def test_product_is_active_when_react_data_matches(self):
        html = """
        <script>
        var react_data = {"product_id": 113650, "name": "Women's Bangle Seema", "sku": "Tabetha-Bracelet", "store_code": "glau"};
        </script>
        """

        product = extract_product_info_from_html("113650", "https://www.glamira.com.au/catalog/product/view/id/113650", html)

        self.assertTrue(product.active)
        self.assertEqual(product.status, "ok")
        self.assertEqual(product.name, "Women's Bangle Seema")
        self.assertEqual(product.sku, "Tabetha-Bracelet")
        self.assertEqual(product.store_code, "glau")

    def test_attempts_original_then_source_domain_then_fallbacks(self):
        attempts = build_product_url_attempts(
            "100004",
            "https://www.glamira.com.au/old-product.html",
            ["www.glamira.com.au", "www.glamira.com"],
        )

        self.assertEqual(attempts[0]["url"], "https://www.glamira.com.au/old-product.html")
        self.assertEqual(attempts[1]["url"], "https://www.glamira.com.au/catalog/product/view/id/100004")
        self.assertEqual(attempts[2]["url"], "https://www.glamira.com/catalog/product/view/id/100004")

    def test_non_english_source_domain_is_tried_after_english_fallbacks(self):
        attempts = build_product_url_attempts(
            "100004",
            "https://www.glamira.de/old-product.html",
            ["www.glamira.com.au", "www.glamira.com", "www.glamira.de"],
        )

        self.assertEqual(attempts[0]["url"], "https://www.glamira.de/old-product.html")
        self.assertEqual(attempts[1]["url"], "https://www.glamira.com.au/catalog/product/view/id/100004")
        self.assertEqual(attempts[2]["url"], "https://www.glamira.com/catalog/product/view/id/100004")
        self.assertEqual(attempts[3]["url"], "https://www.glamira.de/catalog/product/view/id/100004")


if __name__ == "__main__":
    unittest.main()
