"""Version progression, arbitrary-version replay and input-permutation
stability."""
import unittest

from tests.support import (
    batch,
    canonical_json,
    find_node,
    hierarchy_entry,
    materialise,
    payload,
    pipeline_for,
    revision,
    shuffled_replays,
)


def build_payload():
    hierarchy = [
        hierarchy_entry(),
        hierarchy_entry(county="C1"),
        hierarchy_entry(county="C1", precinct="P1"),
        hierarchy_entry(county="C1", precinct="P1", ballot_type="early"),
        hierarchy_entry(county="C1", precinct="P1", ballot_type="mail"),
    ]
    events = [
        batch("e1", 1, "C1", "P1", "early", [
            {"candidate": "A", "votes": 10},
            {"candidate": "B", "votes": 5},
        ]),
        batch("e2", 2, "C1", "P1", "mail", [
            {"candidate": "A", "votes": 7},
            {"candidate": "B", "votes": 3},
        ]),
        revision("e3", 3, "C1", "P1", "mail", [
            {"candidate": "A", "votes": 9},
            {"candidate": "B", "votes": 3},
        ], supersedes="e2"),
    ]
    return payload(hierarchy, events)


class TestVersioningReplay(unittest.TestCase):
    def test_each_version_replays_to_a_distinct_stable_snapshot(self):
        p = build_payload()
        v1 = materialise(p, as_of_version=1)
        v2 = materialise(p, as_of_version=2)
        v3 = materialise(p, as_of_version=3)

        self.assertEqual(v1["as_of_version"], 1)
        leaf = find_node(v1["leaves"], "S", "C1", "P1", "mail")
        self.assertEqual(leaf["flags"], ["missing_report"])
        self.assertEqual(leaf["total_votes"], 0)
        self.assertFalse(v1["publishable"])

        leaf_v2 = find_node(v2["leaves"], "S", "C1", "P1", "mail")
        self.assertEqual(leaf_v2["candidate_totals"], {"A": 7, "B": 3})
        self.assertEqual(leaf_v2["provenance"]["effective_announcement"], "e2")

        leaf_v3 = find_node(v3["leaves"], "S", "C1", "P1", "mail")
        self.assertEqual(leaf_v3["candidate_totals"], {"A": 9, "B": 3})
        self.assertIn("e2", leaf_v3["provenance"]["superseded"])
        self.assertEqual(leaf_v3["provenance"]["effective_announcement"], "e3")

    def test_replay_is_order_independent_and_deterministic(self):
        p = build_payload()
        baseline = canonical_json(materialise(p))
        permutations = shuffled_replays(p)
        for variant in permutations:
            self.assertEqual(variant, baseline)

    def test_out_of_order_arrival_converges_to_same_publication(self):
        p = build_payload()
        reversed_payload = payload(
            p["hierarchy"], list(reversed(p["announcements"]))
        )
        self.assertEqual(
            canonical_json(materialise(p)),
            canonical_json(materialise(reversed_payload)),
        )

    def test_version_history_lists_every_publication(self):
        pipe = pipeline_for(build_payload())
        publication = pipe.publish(include_history=True)
        history = publication["version_history"]
        self.assertEqual([row["version"] for row in history], [1, 2, 3])
        self.assertEqual(
            history[-1]["source_announcement_ids"], ["e1", "e2", "e3"]
        )
        # Every historical version remains reconstructable from the same log.
        for row in history:
            rebuilt = pipe.publish(as_of_version=row["version"])
            self.assertEqual(
                rebuilt["materialisation"]["state_node"]["total_votes"],
                row["state_total_votes"],
            )

    def test_observed_versions_sorted_even_when_input_is_not(self):
        p = build_payload()
        messy = payload(p["hierarchy"], list(reversed(p["announcements"])))
        result = materialise(messy)
        self.assertEqual(result["observed_versions"], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
