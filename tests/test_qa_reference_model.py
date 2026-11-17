"""Unit and conservation tests for the independent reference model."""

import random
import unittest

from tests.qa import generators, reference_model
from tests.qa.cases import all_cases
from tests.qa.reference_model import (
    aggregate,
    check_conservation,
    evaluate,
    reorder_projection,
    select_filings,
    validate_row,
)


class TestRowValidation(unittest.TestCase):
    def test_valid_row_normalizes_revision_default(self):
        record, errors = validate_row(
            {"county": "C1", "precinct": "P1", "ballot_type": "early",
             "candidate": "A", "votes": 3}, 0)
        self.assertEqual(errors, [])
        self.assertEqual(record["revision"], 0)
        self.assertEqual(record["source_index"], 0)

    def test_zero_votes_are_valid(self):
        record, errors = validate_row(
            {"county": "C1", "precinct": "P1", "ballot_type": "early",
             "candidate": "A", "votes": 0}, 0)
        self.assertEqual(errors, [])
        self.assertEqual(record["votes"], 0)

    def test_negative_votes_rejected(self):
        record, errors = validate_row(
            {"county": "C1", "precinct": "P1", "ballot_type": "early",
             "candidate": "A", "votes": -1}, 4)
        self.assertIsNone(record)
        self.assertEqual([e["reason"] for e in errors], ["negative_votes"])
        self.assertEqual(errors[0]["index"], 4)

    def test_bool_votes_rejected(self):
        _, errors = validate_row(
            {"county": "C1", "precinct": "P1", "ballot_type": "early",
             "candidate": "A", "votes": True}, 0)
        self.assertEqual([e["reason"] for e in errors], ["invalid_field_type"])

    def test_missing_fields_each_reported(self):
        _, errors = validate_row({"county": "C1", "votes": 5}, 0)
        reasons = {(e["reason"], e["detail"]) for e in errors}
        self.assertIn(("missing_field", "precinct"), reasons)
        self.assertIn(("missing_field", "ballot_type"), reasons)
        self.assertIn(("missing_field", "candidate"), reasons)

    def test_non_mapping_row(self):
        _, errors = validate_row(42, 2)
        self.assertEqual(errors[0]["reason"], "row_not_mapping")
        self.assertEqual(errors[0]["index"], 2)

    def test_bad_revision_rejected(self):
        for bad in (-1, "2", 1.5, True):
            _, errors = validate_row(
                {"county": "C1", "precinct": "P1", "ballot_type": "early",
                 "candidate": "A", "votes": 1, "revision": bad}, 0)
            self.assertEqual([e["reason"] for e in errors], ["invalid_field_type"])
            self.assertEqual(errors[0]["detail"], "revision")


class TestRevisionSelection(unittest.TestCase):
    def _rec(self, precinct, candidate, votes, revision, index, county="C1",
             ballot_type="early"):
        return {"county": county, "precinct": precinct, "ballot_type": ballot_type,
                "candidate": candidate, "votes": votes, "revision": revision,
                "source_index": index}

    def test_newer_filing_supersedes_wholesale(self):
        records = [
            self._rec("P1", "A", 40, 1, 0),
            self._rec("P1", "B", 20, 1, 1),
            self._rec("P1", "A", 45, 2, 2),
        ]
        winners, superseded, conflicts, _ = select_filings(records)
        self.assertEqual([w["source_index"] for w in winners], [2])
        self.assertEqual([s["reason"] for s in superseded],
                         ["superseded_by_newer_filing"] * 2)
        self.assertEqual(conflicts, [])

    def test_revision_is_per_precinct_not_global(self):
        records = [
            self._rec("P1", "A", 40, 3, 0),
            self._rec("P2", "A", 10, 1, 1),
            self._rec("P2", "A", 12, 2, 2),
        ]
        winners, superseded, _, _ = select_filings(records)
        self.assertEqual([w["source_index"] for w in winners], [0, 2])
        self.assertEqual([s["index"] for s in superseded], [1])

    def test_exact_duplicate_collapses_with_warning(self):
        records = [self._rec("P1", "A", 40, 0, 0), self._rec("P1", "A", 40, 0, 1)]
        winners, superseded, conflicts, warnings = select_filings(records)
        self.assertEqual(len(winners), 1)
        self.assertEqual([s["reason"] for s in superseded], ["exact_duplicate"])
        self.assertEqual(conflicts, [])
        self.assertEqual([w["code"] for w in warnings], ["exact_duplicate_rows"])

    def test_conflicting_same_revision_invalidates_all(self):
        records = [self._rec("P1", "A", 40, 0, 0), self._rec("P1", "A", 41, 0, 1)]
        winners, superseded, conflicts, warnings = select_filings(records)
        self.assertEqual(winners, [])
        self.assertEqual(superseded, [])
        self.assertEqual(len(conflicts), 2)
        self.assertEqual({c["reason"] for c in conflicts}, {"conflicting_same_revision"})

    def test_candidate_added_in_new_filing_counts(self):
        records = [
            self._rec("P1", "A", 40, 1, 0),
            self._rec("P1", "A", 45, 2, 1),
            self._rec("P1", "C", 7, 2, 2),
        ]
        winners, superseded, _, _ = select_filings(records)
        totals = aggregate(winners)["candidate_totals"]
        self.assertEqual(totals, {"A": 45, "C": 7})
        self.assertEqual(len(superseded), 1)


class TestConservation(unittest.TestCase):
    def test_curated_cases_all_conserve(self):
        for case in all_cases():
            with self.subTest(case=case["name"]):
                self.assertEqual(check_conservation(evaluate(case["payload"])), [])

    def test_generated_corpus_all_conserve(self):
        for index, payload in enumerate(generators.mixed_corpus(150, seed=991)):
            with self.subTest(index=index):
                self.assertEqual(check_conservation(evaluate(payload)), [])

    def test_record_level_accounting(self):
        # Every input record is invalid, superseded, or contributing - once.
        payload = {
            "returns": [
                {"county": "C1", "precinct": "P1", "ballot_type": "early",
                 "candidate": "A", "votes": 10, "revision": 1},
                {"county": "C1", "precinct": "P1", "ballot_type": "early",
                 "candidate": "A", "votes": 12, "revision": 2},
                {"county": "C1", "precinct": "P1", "ballot_type": "early",
                 "candidate": "B", "votes": -3},
                "junk",
                {"county": "C1", "precinct": "P1", "ballot_type": "early",
                 "candidate": "C", "votes": 0},
            ],
            "expected_precinct_totals": {"P1": 12},
        }
        result = evaluate(payload)
        self.assertEqual(check_conservation(result), [])
        accounted = (
            {e["index"] for e in result["invalid_records"]}
            | {e["index"] for e in result["superseded_records"]}
            | set(result["contributing_indices"])
        )
        self.assertEqual(accounted, set(range(5)))
        self.assertEqual(result["total_votes"], 12)

    def test_precinct_subtotal_equals_ballot_type_detail(self):
        for payload in generators.mixed_corpus(60, seed=77):
            result = evaluate(payload)
            for precinct, subtotal in result["precinct_totals"].items():
                detail = result["precinct_ballot_type_totals"][precinct]
                self.assertEqual(subtotal, sum(detail.values()), precinct)

    def test_county_totals_never_double_count_old_revisions(self):
        payload = {
            "returns": [
                {"county": "C1", "precinct": "P1", "ballot_type": "early",
                 "candidate": "A", "votes": 100, "revision": 1},
                {"county": "C1", "precinct": "P1", "ballot_type": "early",
                 "candidate": "A", "votes": 105, "revision": 2},
            ],
            "expected_precinct_totals": {},
        }
        result = evaluate(payload)
        self.assertEqual(result["county_totals"], {"C1": 105})
        self.assertEqual(result["total_votes"], 105)


class TestOrderAndDeterminism(unittest.TestCase):
    def test_reorder_invariance_on_generated_payloads(self):
        rng = random.Random(4242)
        for index, payload in enumerate(generators.mixed_corpus(60, seed=31337)):
            with self.subTest(index=index):
                baseline = reorder_projection(evaluate(payload))
                for _ in range(3):
                    shuffled = generators.shuffled(payload, rng)
                    self.assertEqual(reorder_projection(evaluate(shuffled)), baseline)

    def test_evaluation_is_deterministic(self):
        for payload in generators.mixed_corpus(25, seed=5150):
            first = evaluate(payload)
            second = evaluate(payload)
            self.assertEqual(first, second)

    def test_report_keys_sorted(self):
        payload = {
            "returns": [
                {"county": "C2", "precinct": "P2", "ballot_type": "early",
                 "candidate": "Z", "votes": 1},
                {"county": "C1", "precinct": "P1", "ballot_type": "absentee",
                 "candidate": "A", "votes": 2},
                {"county": "C1", "precinct": "P1", "ballot_type": "early",
                 "candidate": "M", "votes": 3},
            ],
            "expected_precinct_totals": {},
        }
        result = evaluate(payload)
        self.assertEqual(list(result["candidate_totals"]), ["A", "M", "Z"])
        self.assertEqual(list(result["ballot_type_totals"]), ["absentee", "early"])
        self.assertEqual(list(result["county_totals"]), ["C1", "C2"])
        self.assertEqual(list(result["precinct_totals"]), ["P1", "P2"])

    def test_input_does_not_get_mutated(self):
        payload = generators.gen_payload(random.Random(8))
        snapshot = repr(payload)
        evaluate(payload)
        self.assertEqual(repr(payload), snapshot)


class TestExpectedTotals(unittest.TestCase):
    def test_invalid_entries_dropped_with_warning(self):
        payload = {
            "returns": [{"county": "C1", "precinct": "P1", "ballot_type": "early",
                         "candidate": "A", "votes": 10}],
            "expected_precinct_totals": {"P1": 10, "P2": "90", "P3": -1},
        }
        result = evaluate(payload)
        self.assertEqual(result["expected_precinct_totals"], {"P1": 10})
        codes = [w["code"] for w in result["warnings"]]
        self.assertEqual(codes.count("invalid_expected_total_entry"), 2)
        self.assertTrue(result["balanced"])

    def test_non_mapping_expected_totals(self):
        payload = {"returns": [], "expected_precinct_totals": [1, 2]}
        result = evaluate(payload)
        self.assertEqual(result["expected_precinct_totals"], {})
        self.assertEqual([w["code"] for w in result["warnings"]],
                         ["expected_totals_not_mapping"])

    def test_unknown_and_missing_precinct_warnings(self):
        payload = {
            "returns": [{"county": "C1", "precinct": "P1", "ballot_type": "early",
                         "candidate": "A", "votes": 10}],
            "expected_precinct_totals": {"P1": 10, "P9": 5},
        }
        result = evaluate(payload)
        codes = {w["code"] for w in result["warnings"]}
        self.assertIn("expected_for_unknown_precinct", codes)
        self.assertEqual(result["precinct_differences"]["P9"], -5)
        self.assertFalse(result["balanced"])


if __name__ == "__main__":
    unittest.main()
