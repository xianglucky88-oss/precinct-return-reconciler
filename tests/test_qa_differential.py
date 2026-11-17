"""Differential tests: curated cases against the known-gap registry,
differential snapshots, combined partial-success cases, and provenance
trace-back from divergence reasons to the original return rows."""

import unittest

from tests.qa import checks, harness
from tests.qa.cases import COMBINED_CASES, all_cases, get_case
from tests.qa.snapshots import check_snapshot


class TestKnownGapRegistry(unittest.TestCase):
    def test_every_curated_case_matches_pinned_expectations(self):
        for case in all_cases():
            with self.subTest(case=case["name"]):
                failures = checks.case_failures(case)
                if failures:
                    hint = ""
                    if case["expect"]["verdict"]["status"] != "conformant":
                        hint = (
                            "\nThis case is pinned as a known gap. If the SUT was "
                            "fixed, update the registry in tests/qa/cases.py."
                        )
                    self.fail("\n".join(failures) + hint)

    def test_registry_covers_required_case_categories(self):
        names = {case["name"] for case in all_cases()}
        required = {
            "duplicate_revision", "missing_ballot_type",
            "candidate_added_in_revision", "zero_votes", "negative_votes",
            "non_dict_rows", "expected_mismatch", "reordered_clean_single",
            "combined_mixed", "combined_mixed_no_crash",
        }
        self.assertTrue(required <= names, required - names)


class TestDifferentialSnapshots(unittest.TestCase):
    def test_case_snapshots_are_stable(self):
        for case in all_cases():
            with self.subTest(case=case["name"]):
                result = harness.compare(case["payload"])
                check_snapshot(case["name"], {
                    "case": case["name"],
                    "sut": result["sut"],
                    "oracle": result["oracle"],
                    "verdict": result["verdict"],
                })


class TestCombinedPartialSuccess(unittest.TestCase):
    def test_combined_cases_show_partial_success_and_inconsistency(self):
        for name in COMBINED_CASES:
            with self.subTest(case=name):
                result = harness.compare(get_case(name)["payload"])
                balanced = result["oracle"]["precinct_balanced"]
                # Partial success and inconsistency at the same time.
                self.assertIn(True, balanced.values(), balanced)
                self.assertIn(False, balanced.values(), balanced)
                self.assertFalse(result["oracle"]["balanced"])
                # Inconsistencies are classified, not silently absorbed.
                self.assertNotEqual(result["verdict"]["status"], "conformant")
                self.assertTrue(
                    result["oracle"]["invalid_records"]
                    or result["oracle"]["superseded_records"]
                )

    def test_combined_case_attributes_gap_to_original_rows(self):
        result = harness.compare(get_case("combined_mixed_no_crash")["payload"])
        self.assertIn("gap_analysis:fully_explained_by_excluded_rows",
                      result["verdict"]["notes"])
        provenance = result["verdict"]["provenance"]
        self.assertIn("superseded_records", provenance)
        self.assertIn("invalid_records", provenance)


class TestProvenanceTraceback(unittest.TestCase):
    def test_divergence_provenance_points_to_original_rows(self):
        for case in all_cases():
            result = harness.compare(case["payload"])
            provenance = result["verdict"]["provenance"]
            rows = case["payload"].get("returns", [])
            with self.subTest(case=case["name"]):
                for entry in provenance.get("invalid_records", []):
                    index = entry["index"]
                    self.assertIsInstance(index, int)
                    self.assertTrue(0 <= index < len(rows))
                    # The recorded row snapshot matches the original return.
                    self.assertEqual(entry["row"], rows[index])
                for entry in provenance.get("superseded_records", []):
                    index = entry["index"]
                    self.assertTrue(0 <= index < len(rows))
                    original = rows[index]
                    record = entry["record"]
                    for field in ("county", "precinct", "ballot_type",
                                  "candidate", "votes"):
                        self.assertEqual(record[field], original[field])

    def test_granularity_divergence_carries_precinct_differences(self):
        result = harness.compare(get_case("expected_offsetting")["payload"])
        self.assertEqual(result["verdict"]["status"], "divergent")
        self.assertIn(
            "cause:balanced_granularity:sut_global_vs_oracle_per_precinct",
            result["verdict"]["reasons"])
        differences = result["verdict"]["provenance"]["precinct_differences"]
        self.assertEqual(differences, {"P1": -5, "P2": 5})
        # The SUT's global check nets the two precincts out.
        self.assertTrue(result["sut"]["output"]["balanced"])
        self.assertFalse(result["oracle"]["balanced"])


class TestReportEncapsulation(unittest.TestCase):
    def test_envelope_on_all_successful_cases(self):
        for case in all_cases():
            result = harness.compare(case["payload"])
            if result["sut"]["kind"] != "ok":
                continue
            with self.subTest(case=case["name"]):
                output = result["sut"]["output"]
                self.assertEqual(output["project"], harness.PROJECT)
                self.assertEqual(output["status"], "ok")
                for key in ("candidate_totals", "ballot_type_totals",
                            "total_votes", "balanced", "difference"):
                    self.assertIn(key, output)

    def test_envelope_output_is_canonical_json_stable(self):
        for case in all_cases():
            first = harness.run_sut(case["payload"])
            second = harness.run_sut(case["payload"])
            with self.subTest(case=case["name"]):
                self.assertEqual(harness.canonical_json(first),
                                 harness.canonical_json(second))


if __name__ == "__main__":
    unittest.main()
