"""Fault-injection tests: systematic perturbations of a valid base payload
must yield stable oracle classifications and characterized SUT outcomes."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.qa import harness, reference_model

REPO_ROOT = Path(__file__).resolve().parents[1]

BASE_PAYLOAD = {
    "returns": [
        {"county": "C1", "precinct": "P1", "ballot_type": "early",
         "candidate": "A", "votes": 40},
        {"county": "C1", "precinct": "P1", "ballot_type": "election_day",
         "candidate": "A", "votes": 60},
        {"county": "C1", "precinct": "P1", "ballot_type": "early",
         "candidate": "B", "votes": 20},
    ],
    "expected_precinct_totals": {"P1": 120},
}


def _with_row(row):
    payload = copy.deepcopy(BASE_PAYLOAD)
    payload["returns"].append(row)
    return payload


def _mutate_first_row(mutator):
    payload = copy.deepcopy(BASE_PAYLOAD)
    mutator(payload["returns"][0])
    return payload


# (fault name, payload factory, expected oracle reasons, expected SUT outcome)
FAULT_MATRIX = [
    ("drop_county", _mutate_first_row(lambda r: r.pop("county")),
     ["missing_field:county"], ("ok", None)),
    ("drop_precinct", _mutate_first_row(lambda r: r.pop("precinct")),
     ["missing_field:precinct"], ("ok", None)),
    ("drop_ballot_type", _mutate_first_row(lambda r: r.pop("ballot_type")),
     ["missing_field:ballot_type"], ("raise", "KeyError")),
    ("drop_candidate", _mutate_first_row(lambda r: r.pop("candidate")),
     ["missing_field:candidate"], ("raise", "KeyError")),
    ("drop_votes", _mutate_first_row(lambda r: r.pop("votes")),
     ["missing_field:votes"], ("raise", "KeyError")),
    ("negative_votes", _mutate_first_row(lambda r: r.update(votes=-5)),
     ["negative_votes:-5"], ("ok", None)),
    ("zero_votes", _mutate_first_row(lambda r: r.update(votes=0)),
     [], ("ok", None)),
    ("votes_float", _mutate_first_row(lambda r: r.update(votes=2.5)),
     ["invalid_field_type:votes"], ("ok", None)),
    ("votes_bool", _mutate_first_row(lambda r: r.update(votes=True)),
     ["invalid_field_type:votes"], ("ok", None)),
    ("votes_string_numeric", _mutate_first_row(lambda r: r.update(votes="40")),
     ["invalid_field_type:votes"], ("ok", None)),
    ("votes_string_garbage", _mutate_first_row(lambda r: r.update(votes="abc")),
     ["invalid_field_type:votes"], ("raise", "ValueError")),
    ("revision_string", _mutate_first_row(lambda r: r.update(revision="2")),
     ["invalid_field_type:revision"], ("ok", None)),
    ("revision_negative", _mutate_first_row(lambda r: r.update(revision=-1)),
     ["invalid_field_type:revision"], ("ok", None)),
    ("non_dict_row_int", _with_row(7), ["row_not_mapping:int"], ("ok", None)),
    ("non_dict_row_str", _with_row("corrupt"), ["row_not_mapping:str"], ("ok", None)),
    ("non_dict_row_none", _with_row(None), ["row_not_mapping:NoneType"], ("ok", None)),
    ("non_dict_row_list", _with_row(["x"]), ["row_not_mapping:list"], ("ok", None)),
]


class TestFaultMatrix(unittest.TestCase):
    def test_each_fault_is_classified_stably(self):
        for name, payload, reasons, (sut_kind, sut_error) in FAULT_MATRIX:
            with self.subTest(fault=name):
                result = harness.compare(payload)
                oracle_reasons = sorted(
                    "{}:{}".format(e["reason"], e["detail"])
                    for e in result["oracle"]["invalid_records"]
                )
                self.assertEqual(oracle_reasons, sorted(reasons))
                self.assertEqual(result["sut"]["kind"], sut_kind)
                if sut_error:
                    self.assertEqual(result["sut"]["error_type"], sut_error)

    def test_classification_is_repeatable(self):
        for name, payload, _, _ in FAULT_MATRIX:
            with self.subTest(fault=name):
                first = harness.compare(payload)
                second = harness.compare(payload)
                self.assertEqual(harness.canonical_json(first),
                                 harness.canonical_json(second))

    def test_classification_invariant_under_row_reorder(self):
        import random

        rng = random.Random(2024)
        for name, payload, reasons, _ in FAULT_MATRIX:
            with self.subTest(fault=name):
                baseline = reference_model.reorder_projection(
                    reference_model.evaluate(payload))
                for _ in range(3):
                    shuffled = copy.deepcopy(payload)
                    rng.shuffle(shuffled["returns"])
                    self.assertEqual(
                        reference_model.reorder_projection(
                            reference_model.evaluate(shuffled)),
                        baseline)


class TestFaultStorm(unittest.TestCase):
    def test_all_faults_combined_still_classify(self):
        payload = copy.deepcopy(BASE_PAYLOAD)
        for _, fault_payload, _, _ in FAULT_MATRIX:
            payload["returns"].extend(fault_payload["returns"][len(BASE_PAYLOAD["returns"]):])
        # Also inject the first-row mutations.
        storm = copy.deepcopy(payload)
        storm["returns"][0].update(votes=-5)
        storm["returns"][1].pop("ballot_type")
        result = harness.compare(storm)
        # The oracle still produces a structured, conserving result.
        self.assertEqual(reference_model.check_conservation(result["oracle"]), [])
        self.assertTrue(result["oracle"]["invalid_records"])
        # The SUT cannot classify any of it: it crashes all-or-nothing.
        self.assertEqual(result["sut"]["kind"], "raise")
        self.assertEqual(result["verdict"]["status"], "sut_error")
        self.assertIn("sut_loses_partial_results", result["verdict"]["reasons"])
        cannot = sorted(r for r in result["verdict"]["reasons"]
                        if r.startswith("sut_cannot_classify:"))
        self.assertEqual(cannot, sorted(set(cannot)))  # stable, de-duplicated
        self.assertTrue(cannot)


class TestStructuralFaults(unittest.TestCase):
    def test_returns_not_a_list(self):
        result = harness.compare({"returns": {"P1": []}})
        self.assertEqual(result["sut"]["kind"], "raise")
        self.assertEqual(result["sut"]["error_type"], "ValueError")
        self.assertEqual(result["oracle"]["input_errors"],
                         [{"reason": "returns_not_list", "detail": "dict"}])

    def test_payload_not_a_mapping(self):
        result = harness.compare([1, 2, 3])
        self.assertEqual(result["sut"]["kind"], "raise")
        self.assertEqual(result["oracle"]["input_errors"],
                         [{"reason": "payload_not_mapping", "detail": "list"}])

    def test_expected_totals_not_a_mapping(self):
        result = harness.compare({"returns": [], "expected_precinct_totals": "x"})
        self.assertEqual(result["sut"]["kind"], "raise")
        codes = [w["code"] for w in result["oracle"]["warnings"]]
        self.assertEqual(codes, ["expected_totals_not_mapping"])


class TestCliDeterminism(unittest.TestCase):
    def _run_cli(self, payload):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"
            path.write_text(json.dumps(payload))
            env = dict(os.environ)
            env["PYTHONPATH"] = str(REPO_ROOT / "src")
            completed = subprocess.run(
                [sys.executable, "-m", "precinct_return_reconciler.cli", str(path)],
                capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
            )
            return completed

    def test_cli_output_byte_identical_across_runs_and_reorders(self):
        first = self._run_cli(BASE_PAYLOAD)
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self._run_cli(BASE_PAYLOAD)
        self.assertEqual(first.stdout, second.stdout)
        reordered = copy.deepcopy(BASE_PAYLOAD)
        reordered["returns"] = list(reversed(reordered["returns"]))
        third = self._run_cli(reordered)
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertEqual(first.stdout, third.stdout)

    def test_cli_report_keys_sorted(self):
        completed = self._run_cli(BASE_PAYLOAD)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(list(report), sorted(report))
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["project"], "precinct-return-reconciler")


if __name__ == "__main__":
    unittest.main()
