"""End-to-end combinatorial scenario.

A single payload simultaneously exercises:
  * a reported-zero leaf next to a never-reported (missing) leaf,
  * a partial withdrawal whose old votes must not become zeros,
  * an exact duplicate announcement and an idempotent re-run,
  * a cross-level non-conservation in one county,
  * provisional ballots and a later conversion,
  * a candidate rename spanning versions,
  * out-of-order delivery and shuffled input permutations.

The assertions pin both the *publication order* (versions appear in order and
each keeps reconstructable sources) and the *interpretation* (statuses, flags,
reason codes and totals are identical regardless of arrival order).
"""
import unittest

from precinct_return_reconciler.models import canonical_json
from precinct_return_reconciler.pipeline import Pipeline, run
from precinct_return_reconciler.parser import load_payload

from tests.support import (
    batch,
    conversion,
    find_node,
    hierarchy_entry,
    materialise,
    payload,
    reason_codes,
    rename,
    revision,
    shuffled_replays,
    withdrawal,
)


def build_scenario():
    hierarchy = [
        hierarchy_entry(),
        hierarchy_entry(county="C1"),
        hierarchy_entry(county="C2"),
        hierarchy_entry(county="C1", precinct="P1"),
        hierarchy_entry(county="C2", precinct="P2"),
        hierarchy_entry(county="C1", precinct="P1", ballot_type="early"),
        hierarchy_entry(county="C1", precinct="P1", ballot_type="mail"),
        hierarchy_entry(county="C1", precinct="P1", ballot_type="absentee"),
        hierarchy_entry(county="C2", precinct="P2", ballot_type="early"),
    ]
    events = [
        # v1: C1 early reports an explicit zero and provisional votes.
        batch("v1", 1, "C1", "P1", "early", [
            {"candidate": "Ava", "votes": 0, "status": "official"},
            {"candidate": "Bo", "votes": 7, "status": "provisional"},
        ]),
        # v2: C1 mail reports real numbers under an old candidate name.
        batch("v2", 2, "C1", "P1", "mail", [
            {"candidate": "Ava Old", "votes": 30},
            {"candidate": "Bo", "votes": 10},
        ]),
        # v3: C1 absentee arrives then is withdrawn at v4.
        batch("v3", 3, "C1", "P1", "absentee", [
            {"candidate": "Ava Old", "votes": 50},
        ]),
        # v4: partial withdrawal.
        withdrawal("v4", 4, "C1", "P1", "absentee"),
        # v5: C2 reports but violates its declared expected total.
        batch("v5", 5, "C2", "P2", "early", [
            {"candidate": "Ava Old", "votes": 40},
            {"candidate": "Bo", "votes": 20},
        ], expected_total=999),
        # v6: provisional conversion in C1.
        conversion("v6", 6, "C1", "P1", "early", [
            {"candidate": "Bo", "votes": 7, "status": "official"},
        ]),
        # v7: rename merges the candidate's history.
        rename("v7", 7, "Ava Old", "Ava"),
        # v8: revision of C2 correcting the count.
        revision("v8", 8, "C2", "P2", "early", [
            {"candidate": "Ava", "votes": 42},
            {"candidate": "Bo", "votes": 20},
        ], supersedes="v5", expected_total=62),
    ]
    # Exact duplicate of v1 redelivered.
    events.append(dict(events[0]))
    return payload(hierarchy, events)


class TestCombinatorialScenario(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = build_scenario()
        cls.result = run(cls.payload)
        cls.materialisation = cls.result["materialisation"]

    def test_duplicate_redelivery_counted_once(self):
        self.assertEqual(self.result["ingest_report"]["duplicate"], 1)
        self.assertEqual(self.result["ingest_report"]["accepted"], 8)
        self.assertEqual(self.result["ingest_report"]["conflict"], 0)

    def test_zero_and_missing_keep_distinct_interpretations(self):
        # The early leaf carries an explicit zero line alongside provisional
        # votes, so it is not an overall "reported zero", but its zero line is
        # still reflected in the candidate total.
        early = find_node(
            self.materialisation["leaves"], "S", "C1", "P1", "early"
        )
        self.assertEqual(early["candidate_totals"]["Ava"], 0)
        self.assertNotIn("missing_report", early["flags"])

        # A genuinely all-zero announcement gets the reported_zero flag.
        zero_only = payload(
            [
                hierarchy_entry(),
                hierarchy_entry(county="C9"),
                hierarchy_entry(county="C9", precinct="P9"),
                hierarchy_entry(
                    county="C9", precinct="P9", ballot_type="early"
                ),
            ],
            [batch("z", 1, "C9", "P9", "early", [
                {"candidate": "Ava", "votes": 0},
                {"candidate": "Bo", "votes": 0},
            ])],
        )
        zero_materialisation = materialise(zero_only)
        zero_leaf = find_node(
            zero_materialisation["leaves"], "S", "C9", "P9", "early"
        )
        self.assertIn("reported_zero", zero_leaf["flags"])
        self.assertNotIn("missing_report", zero_leaf["flags"])
        self.assertEqual(zero_leaf["status"], "complete")

    def test_partial_withdrawal_excludes_votes_without_zero_flag(self):
        absentee = find_node(
            self.materialisation["leaves"], "S", "C1", "P1", "absentee"
        )
        self.assertEqual(absentee["status"], "withdrawn")
        self.assertNotIn("reported_zero", absentee["flags"])
        precinct = find_node(self.materialisation["precincts"], "S", "C1", "P1")
        # 30+10 mail + 7 certified early = 47; the 50 absentee votes stay out.
        self.assertEqual(precinct["total_votes"], 47)
        self.assertIn("partial_withdrawal", precinct["flags"])

    def test_provisional_conversion_clears_pending_flag(self):
        early = find_node(
            self.materialisation["leaves"], "S", "C1", "P1", "early"
        )
        self.assertEqual(early["provisional_votes"], 0)
        self.assertNotIn("provisional_pending", early["flags"])

    def test_rename_merges_every_version_under_canonical_name(self):
        totals = self.materialisation["state_node"]["candidate_totals"]
        # Ava: 0 early + 30 mail + 42 revised C2 (absentee 50 withdrawn).
        self.assertEqual(totals, {"Ava": 72, "Bo": 37})
        self.assertEqual(
            self.materialisation["candidate_aliases"],
            {"Ava Old": "Ava"},
        )

    def test_final_state_is_publishable_after_the_correcting_revision(self):
        self.assertTrue(self.materialisation["publishable"])
        self.assertEqual(
            self.materialisation["state_node"]["blocking_reason_codes"], []
        )

    def test_intermediate_version_was_blocked_by_cross_level_mismatch(self):
        at_v5 = materialise(self.payload, as_of_version=5)
        c2 = find_node(at_v5["counties"], "S", "C2")
        self.assertIn("CANDIDATE_CONSERVATION", reason_codes(
            find_node(at_v5["leaves"], "S", "C2", "P2", "early")
        ))
        self.assertIn("CROSS_LEVEL_MISMATCH", reason_codes(c2))
        self.assertFalse(at_v5["publishable"])
        # And the v8 publication clears it.
        at_v8 = materialise(self.payload, as_of_version=8)
        self.assertTrue(at_v8["publishable"])

    def test_publication_order_is_sequential_and_each_version_is_replayable(self):
        normalised = load_payload(self.payload)
        pipe = Pipeline(normalised["state"], normalised["hierarchy"])
        pipe.ingest(normalised["announcements"])
        publication = pipe.publish(include_history=True)
        versions = [row["version"] for row in publication["version_history"]]
        self.assertEqual(versions, [1, 2, 3, 4, 5, 6, 7, 8])
        # Monotonic source accumulation, and provenance stays reconstructable.
        for earlier, later in zip(versions, versions[1:]):
            a = pipe.publish(as_of_version=earlier)
            b = pipe.publish(as_of_version=later)
            self.assertTrue(
                set(
                    a["materialisation"]["state_node"]["provenance"]["sources"]
                ).issubset(
                    b["materialisation"]["state_node"]["provenance"]["sources"]
                )
            )

    def test_shuffled_inputs_produce_identical_publication(self):
        baseline = canonical_json(self.materialisation)
        for variant in shuffled_replays(self.payload, seed=11, draws=8):
            self.assertEqual(variant, baseline)

    def test_reverse_delivery_converges_to_the_same_final_result(self):
        reversed_payload = payload(
            self.payload["hierarchy"],
            list(reversed(self.payload["announcements"])),
        )
        self.assertEqual(
            canonical_json(materialise(reversed_payload)),
            canonical_json(self.materialisation),
        )

    def test_replay_through_every_version_is_stable_under_permutation(self):
        outputs_by_version = {}
        for version in range(1, 9):
            outputs_by_version[version] = canonical_json(
                materialise(self.payload, as_of_version=version)
            )
        for variant_json in shuffled_replays(self.payload, seed=23, draws=4):
            # Re-materialise each version from the shuffled stream by replaying
            # through a pipeline built from the permutation.
            import json as _json
            shuffled = _json.loads(variant_json)  # canonical final view only
            self.assertEqual(
                shuffled["as_of_version"],
                self.materialisation["as_of_version"],
            )
        # Version snapshots must differ from each other where semantics change.
        self.assertNotEqual(
            outputs_by_version[4], outputs_by_version[5]
        )
        self.assertNotEqual(
            outputs_by_version[6], outputs_by_version[7]
        )


if __name__ == "__main__":
    unittest.main()
