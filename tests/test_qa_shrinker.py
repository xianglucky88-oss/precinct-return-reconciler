"""Counterexample shrinking tests: failing payloads must reduce to minimal,
still-failing, deterministically-reproducible counterexamples."""

import unittest

from tests.qa import generators, harness
from tests.qa.shrinker import is_row_removal_minimal, shrink


def _diverges_with(reason_prefix):
    def predicate(payload):
        verdict = harness.compare(payload)["verdict"]
        return any(r.startswith(reason_prefix) for r in verdict["reasons"])
    return predicate


def _sut_crashes(payload):
    return harness.run_sut(payload)["kind"] == "raise"


def _padding_rows(count, precinct_offset=0):
    return [
        {"county": "C9", "precinct": "PX{}".format(precinct_offset + i),
         "ballot_type": "early", "candidate": "Q", "votes": i + 1}
        for i in range(count)
    ]


class TestShrinkRevisionDoubleCount(unittest.TestCase):
    def _bloated_payload(self):
        rows = _padding_rows(8)
        rows.extend([
            {"county": "C1", "precinct": "P1", "ballot_type": "early",
             "candidate": "A", "votes": 40, "revision": 1},
            {"county": "C1", "precinct": "P1", "ballot_type": "early",
             "candidate": "A", "votes": 45, "revision": 2},
        ])
        return {"returns": rows,
                "expected_precinct_totals": {"P1": 45, "PX0": 1}}

    def test_shrinks_to_minimal_revision_pair(self):
        payload = self._bloated_payload()
        predicate = _diverges_with("cause:sut_counts_superseded:")
        self.assertTrue(predicate(payload))
        shrunk = shrink(payload, predicate)
        self.assertTrue(predicate(shrunk))
        rows = shrunk["returns"]
        self.assertLessEqual(len(rows), 2)
        revisions = sorted(r.get("revision", 0) for r in rows)
        self.assertEqual(len(revisions), 2)
        self.assertLess(revisions[0], revisions[1])
        # Same precinct and record key: a minimal duplicate-filing pair.
        self.assertEqual(rows[0]["precinct"], rows[1]["precinct"])
        self.assertEqual(rows[0]["candidate"], rows[1]["candidate"])

    def test_shrunk_case_is_row_removal_minimal(self):
        payload = self._bloated_payload()
        predicate = _diverges_with("cause:sut_counts_superseded:")
        shrunk = shrink(payload, predicate)
        self.assertTrue(is_row_removal_minimal(shrunk, predicate))

    def test_shrinking_is_deterministic(self):
        payload = self._bloated_payload()
        predicate = _diverges_with("cause:sut_counts_superseded:")
        self.assertEqual(shrink(payload, predicate), shrink(payload, predicate))


class TestShrinkCrash(unittest.TestCase):
    def test_missing_ballot_type_shrinks_to_single_row(self):
        rows = _padding_rows(6)
        rows.append({"county": "C1", "precinct": "P1", "candidate": "B",
                     "votes": 20})
        payload = {"returns": rows, "expected_precinct_totals": {}}
        self.assertTrue(_sut_crashes(payload))
        shrunk = shrink(payload, _sut_crashes)
        self.assertTrue(_sut_crashes(shrunk))
        self.assertEqual(len(shrunk["returns"]), 1)
        self.assertNotIn("ballot_type", shrunk["returns"][0])
        self.assertTrue(is_row_removal_minimal(shrunk, _sut_crashes))


class TestShrinkNegativeVotes(unittest.TestCase):
    def test_negative_vote_divergence_shrinks(self):
        rows = _padding_rows(5)
        rows.append({"county": "C1", "precinct": "P1", "ballot_type": "early",
                     "candidate": "B", "votes": -15})
        payload = {"returns": rows,
                   "expected_precinct_totals": {"P1": -15}}
        predicate = _diverges_with("cause:sut_counts_rows_oracle_rejects:negative_votes")
        self.assertTrue(predicate(payload))
        shrunk = shrink(payload, predicate)
        self.assertTrue(predicate(shrunk))
        self.assertEqual(len(shrunk["returns"]), 1)
        self.assertLess(shrunk["returns"][0]["votes"], 0)


class TestShrinkFoundCounterexample(unittest.TestCase):
    def test_generated_divergent_case_shrinks_below_original(self):
        # Find a divergent generated payload, then shrink it.
        divergent = None
        for payload in generators.mixed_corpus(200, seed=60321):
            verdict = harness.compare(payload)["verdict"]
            if verdict["status"] == "divergent":
                divergent = payload
                break
        self.assertIsNotNone(divergent, "generator should find divergent cases")

        def predicate(payload):
            return harness.compare(payload)["verdict"]["status"] == "divergent"

        shrunk = shrink(divergent, predicate)
        self.assertTrue(predicate(shrunk))
        self.assertLessEqual(len(shrunk["returns"]), len(divergent["returns"]))
        self.assertTrue(is_row_removal_minimal(shrunk, predicate))
        # The shrunk counterexample still classifies deterministically.
        again = harness.compare(shrunk)["verdict"]
        self.assertEqual(again, harness.compare(shrunk)["verdict"])


if __name__ == "__main__":
    unittest.main()
