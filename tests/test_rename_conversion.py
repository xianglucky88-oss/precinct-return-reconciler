"""Candidate display-name changes and provisional -> official conversion."""
import unittest

from tests.support import (
    batch,
    conversion,
    find_node,
    hierarchy_entry,
    materialise,
    payload,
    rename,
)


def hierarchy():
    return [
        hierarchy_entry(),
        hierarchy_entry(county="C1"),
        hierarchy_entry(county="C1", precinct="P1"),
        hierarchy_entry(county="C1", precinct="P1", ballot_type="early"),
        hierarchy_entry(county="C1", precinct="P1", ballot_type="mail"),
    ]


class TestRenameAndConversion(unittest.TestCase):
    def test_rename_merges_history_under_canonical_name(self):
        events = [
            batch("b1", 1, "C1", "P1", "early", [
                {"candidate": "Old Name", "votes": 12}
            ]),
            batch("b2", 2, "C1", "P1", "mail", [
                {"candidate": "New Name", "votes": 8}
            ]),
            rename("rn", 3, "Old Name", "New Name"),
        ]
        result = materialise(payload(hierarchy(), events))
        totals = result["state_node"]["candidate_totals"]
        self.assertEqual(totals, {"New Name": 20})
        self.assertNotIn("Old Name", totals)
        self.assertEqual(
            result["candidate_aliases"], {"Old Name": "New Name"}
        )
        # Original names survive on the source announcement for provenance.
        leaf = find_node(result["leaves"], "S", "C1", "P1", "early")
        self.assertEqual(
            leaf["provenance"]["effective_announcement"], "b1"
        )

    def test_replay_before_rename_keeps_the_old_name(self):
        events = [
            batch("b", 1, "C1", "P1", "early", [
                {"candidate": "Old Name", "votes": 12}
            ]),
            rename("rn", 2, "Old Name", "New Name"),
        ]
        p = payload(hierarchy(), events)
        before = materialise(p, as_of_version=1)
        after = materialise(p, as_of_version=2)
        self.assertEqual(
            before["state_node"]["candidate_totals"], {"Old Name": 12}
        )
        self.assertEqual(
            after["state_node"]["candidate_totals"], {"New Name": 12}
        )

    def test_rename_chain_resolves_to_final_name(self):
        events = [
            batch("b", 1, "C1", "P1", "early", [
                {"candidate": "A", "votes": 3}
            ]),
            rename("r1", 2, "A", "B"),
            rename("r2", 3, "B", "C"),
        ]
        result = materialise(payload(hierarchy(), events))
        self.assertEqual(
            result["state_node"]["candidate_totals"], {"C": 3}
        )
        self.assertEqual(result["candidate_aliases"], {"A": "C", "B": "C"})

    def test_conversion_certifies_provisional_lines(self):
        events = [
            batch("b", 1, "C1", "P1", "early", [
                {"candidate": "A", "votes": 15, "status": "provisional"},
                {"candidate": "B", "votes": 5, "status": "official"},
            ]),
            conversion("c", 2, "C1", "P1", "early", [
                {"candidate": "A", "votes": 15, "status": "official"},
            ]),
        ]
        result = materialise(payload(hierarchy(), events))
        leaf = find_node(result["leaves"], "S", "C1", "P1", "early")
        self.assertEqual(leaf["provisional_votes"], 0)
        self.assertEqual(leaf["flags"], [])
        self.assertEqual(leaf["status"], "complete")
        self.assertEqual(leaf["total_votes"], 20)

    def test_conversion_can_update_count_when_certifying(self):
        events = [
            batch("b", 1, "C1", "P1", "early", [
                {"candidate": "A", "votes": 15, "status": "provisional"},
            ]),
            conversion("c", 2, "C1", "P1", "early", [
                {"candidate": "A", "votes": 17, "status": "official"},
            ]),
        ]
        result = materialise(payload(hierarchy(), events))
        leaf = find_node(result["leaves"], "S", "C1", "P1", "early")
        self.assertEqual(leaf["candidate_totals"], {"A": 17})
        self.assertEqual(leaf["provisional_votes"], 0)

    def test_replay_before_conversion_shows_provisional_pending(self):
        events = [
            batch("b", 1, "C1", "P1", "early", [
                {"candidate": "A", "votes": 9, "status": "provisional"},
            ]),
            conversion("c", 2, "C1", "P1", "early", [
                {"candidate": "A", "votes": 9, "status": "official"},
            ]),
        ]
        p = payload(hierarchy(), events)
        before = materialise(p, as_of_version=1)
        leaf = find_node(before["leaves"], "S", "C1", "P1", "early")
        self.assertEqual(leaf["status"], "incomplete")
        self.assertEqual(leaf["provisional_votes"], 9)
        self.assertFalse(before["publishable"])

    def test_rename_cycle_blocks_publication(self):
        events = [
            batch("b", 1, "C1", "P1", "early", [
                {"candidate": "A", "votes": 3}
            ]),
            rename("r1", 2, "A", "B"),
            rename("r2", 3, "B", "A"),
        ]
        result = materialise(payload(hierarchy(), events))
        self.assertIn("RENAME_CYCLE", result["state_node"]["blocking_reason_codes"])
        self.assertFalse(result["publishable"])


if __name__ == "__main__":
    unittest.main()
