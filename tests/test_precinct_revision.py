"""Precinct-scoped revisions that supersede whole groups of leaves."""
import unittest

from tests.support import (
    announcement,
    batch,
    find_node,
    hierarchy_entry,
    materialise,
    payload,
    scope,
)


def hierarchy():
    return [
        hierarchy_entry(),
        hierarchy_entry(county="C1"),
        hierarchy_entry(county="C1", precinct="P1"),
        hierarchy_entry(county="C1", precinct="P1", ballot_type="early"),
        hierarchy_entry(county="C1", precinct="P1", ballot_type="mail"),
    ]


class TestPrecinctScopedRevision(unittest.TestCase):
    def test_per_ballot_revision_replaces_each_named_leaf_only(self):
        events = [
            batch("b1", 1, "C1", "P1", "early", [
                {"candidate": "A", "votes": 10}
            ]),
            batch("b2", 2, "C1", "P1", "mail", [
                {"candidate": "A", "votes": 20}
            ]),
            announcement(
                "r", 3, "revision",
                scope("S", "C1", "P1"),
                lines=[
                    {"candidate": "A", "votes": 11, "ballot_type": "early"},
                    {"candidate": "A", "votes": 22, "ballot_type": "mail"},
                ],
                supersedes="b1;b2",
            ),
        ]
        result = materialise(payload(hierarchy(), events))
        early = find_node(result["leaves"], "S", "C1", "P1", "early")
        mail = find_node(result["leaves"], "S", "C1", "P1", "mail")
        self.assertEqual(early["total_votes"], 11)
        self.assertEqual(mail["total_votes"], 22)
        self.assertTrue(early["provenance"]["effective_announcement"].startswith("r::"))
        self.assertIn("b1", early["provenance"]["superseded"])
        self.assertIn("b2", mail["provenance"]["superseded"])
        self.assertEqual(
            find_node(result["precincts"], "S", "C1", "P1")["total_votes"], 33
        )

    def test_aggregate_precinct_revision_uses_synthetic_attribution_leaf(self):
        events = [
            batch("b1", 1, "C1", "P1", "early", [
                {"candidate": "A", "votes": 10}
            ]),
            batch("b2", 2, "C1", "P1", "mail", [
                {"candidate": "A", "votes": 20}
            ]),
            announcement(
                "r", 3, "revision",
                scope("S", "C1", "P1"),
                lines=[{"candidate": "A", "votes": 25}],
                supersedes="b1;b2",
            ),
        ]
        result = materialise(payload(hierarchy(), events))
        # Old leaves were superseded; the aggregate lands on one synthetic leaf.
        synthetic = find_node(result["leaves"], "S", "C1", "P1", "_precinct")
        self.assertEqual(synthetic["candidate_totals"], {"A": 25})
        self.assertTrue(synthetic["provenance"]["superseded"])
        precinct = find_node(result["precincts"], "S", "C1", "P1")
        self.assertEqual(precinct["total_votes"], 25)


if __name__ == "__main__":
    unittest.main()
