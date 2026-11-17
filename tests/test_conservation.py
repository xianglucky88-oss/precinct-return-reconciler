"""Conservation failures must produce precise, attributable reason codes:
candidate conservation, ballot-type conservation and cross-level mismatch."""
import unittest

from tests.support import (
    batch,
    find_node,
    hierarchy_entry,
    materialise,
    payload,
    reason_codes,
)


class TestConservation(unittest.TestCase):
    def test_candidate_sum_vs_expected_total_is_reported_with_difference(self):
        hierarchy = [
            hierarchy_entry(),
            hierarchy_entry(county="C1"),
            hierarchy_entry(county="C1", precinct="P1"),
            hierarchy_entry(
                county="C1",
                precinct="P1",
                ballot_type="early",
                expected_total=100,
            ),
        ]
        events = [
            batch("e", 1, "C1", "P1", "early", [
                {"candidate": "A", "votes": 70},
                {"candidate": "B", "votes": 20},
            ]),
        ]
        result = materialise(payload(hierarchy, events))
        precinct = find_node(result["precincts"], "S", "C1", "P1")
        leaf = find_node(result["leaves"], "S", "C1", "P1", "early")
        self.assertIn("CANDIDATE_CONSERVATION", reason_codes(leaf))
        self.assertIn("CROSS_LEVEL_MISMATCH", reason_codes(precinct))
        mismatch = next(
            r for r in precinct["reasons"] if r["code"] == "CROSS_LEVEL_MISMATCH"
        )
        self.assertEqual(mismatch["expected"], 100)
        self.assertEqual(mismatch["actual"], 90)
        self.assertEqual(mismatch["difference"], -10)
        self.assertFalse(result["publishable"])

    def test_ballot_type_conservation_uses_ballot_subtotal_declarations(self):
        hierarchy = [
            hierarchy_entry(),
            hierarchy_entry(county="C1"),
            hierarchy_entry(county="C1", precinct="P1"),
            hierarchy_entry(
                county="C1",
                precinct="P1",
                ballot_type="early",
                expected_ballot_totals={"early": 50},
            ),
        ]
        events = [
            batch("e", 1, "C1", "P1", "early", [
                {"candidate": "A", "votes": 48},
            ]),
        ]
        result = materialise(payload(hierarchy, events))
        leaf = find_node(result["leaves"], "S", "C1", "P1", "early")
        codes = reason_codes(leaf)
        self.assertIn("BALLOT_TYPE_CONSERVATION", codes)
        detail = next(
            r for r in leaf["reasons"]
            if r["code"] == "BALLOT_TYPE_CONSERVATION"
        )
        self.assertEqual(detail["ballot_type"], "early")
        self.assertEqual(detail["expected"], 50)
        self.assertEqual(detail["actual"], 48)

    def test_balanced_returns_have_no_blocking_reasons(self):
        hierarchy = [
            hierarchy_entry(),
            hierarchy_entry(county="C1"),
            hierarchy_entry(county="C1", precinct="P1", expected_total=30),
            hierarchy_entry(county="C1", precinct="P1", ballot_type="early"),
        ]
        events = [
            batch("e", 1, "C1", "P1", "early", [
                {"candidate": "A", "votes": 18},
                {"candidate": "B", "votes": 12},
            ], expected_total=30),
        ]
        result = materialise(payload(hierarchy, events))
        self.assertTrue(result["publishable"])
        self.assertEqual(result["state_node"]["reasons"], [])

    def test_precinct_level_ballot_declaration_rolls_up_across_leaves(self):
        hierarchy = [
            hierarchy_entry(),
            hierarchy_entry(county="C1"),
            hierarchy_entry(
                county="C1",
                precinct="P1",
                expected_ballot_totals={"early": 40, "mail": 60},
            ),
            hierarchy_entry(county="C1", precinct="P1", ballot_type="early"),
            hierarchy_entry(county="C1", precinct="P1", ballot_type="mail"),
        ]
        events = [
            batch("e1", 1, "C1", "P1", "early", [{"candidate": "A", "votes": 40}]),
            batch("e2", 2, "C1", "P1", "mail", [{"candidate": "A", "votes": 55}]),
        ]
        result = materialise(payload(hierarchy, events))
        precinct = find_node(result["precincts"], "S", "C1", "P1")
        detail = next(
            r for r in precinct["reasons"]
            if r["code"] == "BALLOT_TYPE_CONSERVATION"
        )
        self.assertEqual(detail["ballot_type"], "mail")
        self.assertEqual(detail["difference"], -5)


if __name__ == "__main__":
    unittest.main()
