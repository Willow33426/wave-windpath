"""응답 캐시 테스트."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.cache import ResponseCache  # noqa: E402


class ResponseCacheTest(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.cache = ResponseCache(60, clock=lambda: self.now)

    def test_reuses_value_within_ttl_and_same_version(self):
        self.cache.put(("suncheon", 12), "v1", {"a": 1})
        self.now += 59
        self.assertEqual(self.cache.get(("suncheon", 12), "v1"), {"a": 1})

    def test_expires_after_ttl(self):
        self.cache.put(("suncheon", 12), "v1", {"a": 1})
        self.now += 60
        self.assertIsNone(self.cache.get(("suncheon", 12), "v1"))

    def test_new_collection_invalidates(self):
        self.cache.put(("suncheon", 12), "v1", {"a": 1})
        self.assertIsNone(self.cache.get(("suncheon", 12), "v2"))

    def test_keys_are_separate(self):
        self.cache.put(("suncheon", 12), "v1", {"a": 1})
        self.assertIsNone(self.cache.get(("suncheon", 6), "v1"))


if __name__ == "__main__":
    unittest.main()
