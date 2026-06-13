import unittest

from glamira_aws.ip_locations import is_valid_ip, normalize_ip


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


if __name__ == "__main__":
    unittest.main()
