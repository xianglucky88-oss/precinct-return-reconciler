"""The four public interpretations must never be conflated:
zero votes, missing report, provisional pending, withdrawn result."""
import unittest

from tests.support import (
    batch,
    find_node,
    hierarchy_entry,
    materialise,
    payload,
    reason_codes,
    withdrawal,
)


def build_hierarchy():
    return [
        hierarchy_entry(),
        hierarchy_entry(county="C1"),
        hierarchy_entry(county="C1", precinct="P1"),
        hierarchy_entry(county="C1", precinct="P1", ballot_type="early"),
        hierarchy_entry(county="C1", precinct="P1", ballot_type="mail"),
        hierarchy_entry(county="C1", precinct="P1", ballot_type="provisional"),
        hierarchy_entry(county="C1", precinct="P1", ballot_type="absentee"),
    ]


class TestStatusSemantics(unittest.TestCase):
    def _leaf(self, result, ballot_type):
        return find_node(
            result["leaves"], "S", "C1", "P1", ballot_type
        )

    def test_reported_zero_is_distinct_from_missing_report(self):
        events = [
            batch("z", 1, "C1", "P1", "early", [
                {"candidate": "A", "votes": 0},
                {"candidate": "B", "votes": 0},
            ]),
        ]
        result = materialise(payload(build_hierarchy(), events))
        zero_leaf = self._leaf(result, "early")
        missing_leaf = self._leaf(result, "mail")

        self.assertEqual(zero_leaf["total_votes"], 0)
        self.assertIn("reported_zero", zero_leaf["flags"])
        self.assertNotIn("missing_report", zero_leaf["flags"])
        # Zero is informational, not a blocking condition on its own.
        self.assertEqual(zero_leaf["status"], "complete")

        self.assertEqual(missing_leaf["total_votes"], 0)
        self.assertIn("missing_report", missing_leaf["flags"])
        self.assertNotIn("reported_zero", missing_leaf["flags"])
        self.assertEqual(missing_leaf["status"], "incomplete")
        self.assertIn("MISSING_REPORT", reason_codes(missing_leaf))

    def test_withdrawal_does_not_turn_old_votes_into_zero(self):
        events = [
            batch("w0", 1, "C1", "P1", "absentee", [
                {"candidate": "A", "votes": 55},
            ]),
            withdrawal("w1", 2, "C1", "P1", "absentee"),
        ]
        result = materialise(payload(build_hierarchy(), events))
        node = self._leaf(result, "absentee")

        self.assertEqual(node["status"], "withdrawn")
        self.assertIn("withdrawn", node["flags"])
        self.assertNotIn("reported_zero", node["flags"])
        self.assertEqual(node["total_votes"], 0)
        self.assertEqual(node["candidate_totals"], {})
        self.assertEqual(
            node["provenance"]["withdrawn_by"], "w1"
        )
        self.assertIn("w0", node["provenance"]["sources"])
        # The retracted numbers remain auditable via superseded/source history.
        self.assertIn("PARTIAL_WITHDRAWAL", reason_codes(node))

    def test_provisional_totals_are_flagged_until_certified(self):
        events = [
            batch("p", 1, "C1", "P1", "provisional", [
                {"candidate": "A", "votes": 12, "status": "provisional"},
                {"candidate": "B", "votes": 4, "status": "official"},
            ]),
        ]
        result = materialise(payload(build_hierarchy(), events))
        node = self._leaf(result, "provisional")
        self.assertEqual(node["total_votes"], 16)
        self.assertEqual(node["provisional_votes"], 12)
        self.assertIn("provisional_pending", node["flags"])
        self.assertEqual(node["status"], "incomplete")

    def test_all_four_states_coexist_without_cross_contamination(self):
        events = [
            batch("z", 1, "C1", "P1", "early", [
                {"candidate": "A", "votes": 0}
            ]),
            batch("p", 2, "C1", "P1", "provisional", [
                {"candidate": "A", "votes": 9, "status": "provisional"}
            ]),
            batch("w0", 3, "C1", "P1", "absentee", [
                {"candidate": "A", "votes": 20}
            ]),
            withdrawal("w1", 4, "C1", "P1", "absentee"),
        ]
        result = materialise(payload(build_hierarchy(), events))
        by_flag = {
            ballot_type: self._leaf(result, ballot_type)["flags"]
            for ballot_type in ("early", "mail", "provisional", "absentee")
        }
        self.assertIn("reported_zero", by_flag["early"])
        self.assertEqual(by_flag["mail"], ["missing_report"])
        self.assertIn("provisional_pending", by_flag["provisional"])
        self.assertIn("withdrawn", by_flag["absentee"])
        self.assertNotIn("reported_zero", by_flag["absentee"])


if __name__ == "__main__":
    unittest.main()
