"""Hierarchy readiness: parents must not publish before children stabilise,
hierarchy gaps are flagged, and a failing county can never make the state
look complete."""
import unittest

from tests.support import (
    batch,
    find_node,
    hierarchy_entry,
    materialise,
    payload,
    reason_codes,
)


def two_county_hierarchy():
    return [
        hierarchy_entry(),
        hierarchy_entry(county="C1"),
        hierarchy_entry(county="C2"),
        hierarchy_entry(county="C1", precinct="P1"),
        hierarchy_entry(county="C2", precinct="P2"),
        hierarchy_entry(county="C1", precinct="P1", ballot_type="early"),
        hierarchy_entry(county="C2", precinct="P2", ballot_type="early"),
    ]


class TestHierarchyReadiness(unittest.TestCase):
    def test_county_waits_for_precinct_and_leaf_stability(self):
        events = [
            # C1 fully reported; C2 silent.
            batch("e", 1, "C1", "P1", "early", [
                {"candidate": "A", "votes": 25}
            ]),
        ]
        result = materialise(payload(two_county_hierarchy(), events))
        c1 = find_node(result["counties"], "S", "C1")
        c2 = find_node(result["counties"], "S", "C2")
        self.assertEqual(c1["status"], "complete")
        self.assertEqual(c2["status"], "incomplete")
        self.assertIn("MISSING_CHILD", reason_codes(c2))
        self.assertFalse(result["publishable"])
        state = result["state_node"]
        self.assertEqual(state["status"], "incomplete")
        self.assertIn("MISSING_CHILD", reason_codes(state))

    def test_partial_county_failure_blocks_state_but_keeps_good_county(self):
        events = [
            batch("ok", 1, "C1", "P1", "early", [
                {"candidate": "A", "votes": 25}
            ]),
            # C2 reports, but its declared expected total fails conservation.
            batch("bad", 2, "C2", "P2", "early", [
                {"candidate": "A", "votes": 30}
            ], expected_total=99),
        ]
        hierarchy = two_county_hierarchy()
        # Attach the expectation at leaf level.
        hierarchy[-1] = hierarchy_entry(
            county="C2", precinct="P2", ballot_type="early", expected_total=99
        )
        result = materialise(payload(hierarchy, events))
        c1 = find_node(result["counties"], "S", "C1")
        c2 = find_node(result["counties"], "S", "C2")
        self.assertEqual(c1["status"], "complete")
        self.assertEqual(c2["status"], "incomplete")
        self.assertFalse(result["publishable"])
        # The good county's numbers are still present and untouched.
        self.assertEqual(c1["candidate_totals"], {"A": 25})
        self.assertEqual(c2["candidate_totals"], {"A": 30})

    def test_undeclared_scope_creates_hierarchy_gap_reason(self):
        # Only C1/P1 is declared; an event arrives for unknown C3/P9.
        hierarchy = [
            hierarchy_entry(),
            hierarchy_entry(county="C1"),
            hierarchy_entry(county="C1", precinct="P1"),
            hierarchy_entry(county="C1", precinct="P1", ballot_type="early"),
        ]
        events = [
            batch("known", 1, "C1", "P1", "early", [
                {"candidate": "A", "votes": 5}
            ]),
            batch("rogue", 2, "C3", "P9", "early", [
                {"candidate": "A", "votes": 7}
            ]),
        ]
        result = materialise(payload(hierarchy, events))
        rogue_leaf = find_node(result["leaves"], "S", "C3", "P9", "early")
        self.assertIn("HIERARCHY_GAP", reason_codes(rogue_leaf))
        rogue_county = find_node(result["counties"], "S", "C3")
        self.assertIn("HIERARCHY_GAP", reason_codes(rogue_county))
        self.assertFalse(result["publishable"])

    def test_declared_county_with_zero_events_still_materialises(self):
        result = materialise(payload(two_county_hierarchy(), []))
        self.assertIn("S/C1", result["counties"])
        self.assertIn("S/C2", result["counties"])
        self.assertFalse(result["publishable"])
        self.assertEqual(result["observed_versions"], [])
        self.assertEqual(result["as_of_version"], 0)


if __name__ == "__main__":
    unittest.main()
