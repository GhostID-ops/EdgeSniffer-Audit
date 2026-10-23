import json
import tempfile
import unittest
from pathlib import Path

from filterTeacherMatchedObservationFeedV1 import teacher_matched


class FilterTeacherMatchedTests(unittest.TestCase):
    def test_nested_raw_match(self):
        self.assertTrue(teacher_matched({"raw_record": {"teacherWalletMatched": True}}))

    def test_nested_extra_match(self):
        self.assertTrue(teacher_matched({"extra": {"teacherWalletMatched": True}}))

    def test_unmatched(self):
        self.assertFalse(teacher_matched({"raw_record": {"teacherWalletMatched": False}}))


if __name__ == "__main__":
    unittest.main()
