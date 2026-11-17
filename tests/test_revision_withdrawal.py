"""Revisions (leaf- and ballot-scoped), partial withdrawal and the rule
that withdrawn numbers never re-enter the totals as zero."""
import unittest

from tests.support import (
    batch,
    find_node,
    hierarchy_entry,
    materialise,
    payload,
    revision,
    withdrawal,
)


def hierarchy():
    return [
        hierarchy_entry(),
        hierarchy_entry(county="C1"),
        hierarchy_entry(county="C1", precinct="P1"),
        hierarchy_entry(county="C1", precinct="P1", ballot_type="early"),
        hierarchy_entry(county="C1", precinct="P1", ballot_type="mail"),
    ]


class TestRevisionWithdrawal(unittest.TestCase):
    def test_leaf_revision_replaces_only_that_ballot_type(self):
        events = [
            batch("b1", 1, "C1", "P1", "early", [
                {"candidate": "A", "votes": 10}
            ]),
            batch("b2", 2, "C1", "P1", "mail", [
                {"candidate": "A", "votes": 20}
            ]),
            revision("r1", 3, "C1", "P1", "mail", [
                {"candidate": "A", "votes": 24}
            ], supersedes="b2"),
        ]
        result = materialise(payload(hierarchy(), events))
        early = find_node(result["leaves"], "S", "C1", "P1", "early")
        mail = find_node(result["leaves"], "S", "C1", "P1", "mail")
        self.assertEqual(early["total_votes"], 10)
        self.assertEqual(mail["total_votes"], 24)
        self.assertEqual(mail["provenance"]["superseded"], ["b2"])
        self.assertEqual(mail["provenance"]["effective_announcement"], "r1")

    def test_withdrawn_leaf_is_excluded_from_parent_totals(self):
        events = [
            batch("b1", 1, "C1", "P1", "early", [
                {"candidate": "A", "votes": 10}
            ]),
            batch("b2", 2, "C1", "P1", "mail", [
                {"candidate": "A", "votes": 20}
            ]),
            withdrawal("w", 3, "C1", "P1", "mail"),
        ]
        result = materialise(payload(hierarchy(), events))
        precinct = find_node(result["precincts"], "S", "C1", "P1")
        self.assertEqual(precinct["total_votes"], 10)
        self.assertIn("partial_withdrawal", precinct["flags"])
        mail = find_node(result["leaves"], "S", "C1", "P1", "mail")
        self.assertEqual(mail["status"], "withdrawn")
        # Provenance retains both the original batch and the withdrawal.
        self.assertEqual(
            sorted(mail["provenance"]["sources"]), ["b2", "w"]
        )

    def test_revision_after_withdrawal_reinstates_the_leaf(self):
        events = [
            batch("b1", 1, "C1", "P1", "early", [
                {"candidate": "A", "votes": 10}
            ]),
            withdrawal("w", 2, "C1", "P1", "early"),
            revision("r", 3, "C1", "P1", "early", [
                {"candidate": "A", "votes": 11}
            ], supersedes="b1"),
        ]
        result = materialise(payload(hierarchy(), events))
        leaf = find_node(result["leaves"], "S", "C1", "P1", "early")
        self.assertEqual(leaf["status"], "complete")
        self.assertEqual(leaf["total_votes"], 11)
        self.assertIsNone(leaf["provenance"]["withdrawn_by"])

    def test_replay_before_withdrawal_shows_the_original_numbers(self):
        events = [
            batch("b", 1, "C1", "P1", "early", [
                {"candidate": "A", "votes": 88}
            ]),
            withdrawal("w", 2, "C1", "P1", "early"),
        ]
        p = payload(hierarchy(), events)
        before = materialise(p, as_of_version=1)
        after = materialise(p, as_of_version=2)
        leaf_before = find_node(before["leaves"], "S", "C1", "P1", "early")
        leaf_after = find_node(after["leaves"], "S", "C1", "P1", "early")
        self.assertEqual(leaf_before["total_votes"], 88)
        self.assertEqual(leaf_before["status"], "complete")
        self.assertEqual(leaf_after["status"], "withdrawn")
        self.assertEqual(leaf_after["total_votes"], 0)
        self.assertNotIn("reported_zero", leaf_after["flags"])

    def test_duplicate_withdrawal_announcement_is_idempotent(self):
        events = [
            batch("b", 1, "C1", "P1", "early", [
                {"candidate": "A", "votes": 10}
            ]),
        ]
        w = withdrawal("w", 2, "C1", "P1", "early")
        p = payload(hierarchy(), events + [w, dict(w)])
        from precinct_return_reconciler.pipeline import run
        result = run(p)
        self.assertEqual(result["ingest_report"]["duplicate"], 1)
        leaf = find_node(
            result["materialisation"]["leaves"], "S", "C1", "P1", "early"
        )
        self.assertEqual(leaf["status"], "withdrawn")


if __name__ == "__main__":
    unittest.main()
