#!/usr/bin/env python3
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import twoCqRouteSpecificSymmetricChoiceV1 as subject


class RouteStateGenerationTests(unittest.TestCase):
    def test_route_state_change_invalidates_decision_generation(self):
        first = subject.decision_generation("selected", "contract", "state-a")
        second = subject.decision_generation("selected", "contract", "state-b")
        self.assertNotEqual(first, second)

    def test_generation_is_deterministic(self):
        first = subject.decision_generation("selected", "contract", "state")
        second = subject.decision_generation("selected", "contract", "state")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
