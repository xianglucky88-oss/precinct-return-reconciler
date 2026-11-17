"""Property-based tests: seeded generators check conservation, reorder
invariance, SUT conformance on clean inputs, and report encapsulation."""

import json
import random
import unittest

from precinct_return_reconciler.parser import load_records
from precinct_return_reconciler.report import build_result

from tests.qa import generators, harness, reference_model


class TestOracleProperties(unittest.TestCase):
    def test_conservation_holds_over_generated_corpus(self):
        for index, payload in enumerate(generators.mixed_corpus(200, seed=20261117)):
            with self.subTest(index=index):
                violations = reference_model.check_conservation(
                    reference_model.evaluate(payload))
                self.assertEqual(violations, [])

    def test_oracle_invariant_under_input_reordering(self):
        rng = random.Random(117)
        for index, payload in enumerate(generators.mixed_corpus(40, seed=881)):
            with self.subTest(index=index):
                baseline = reference_model.reorder_projection(
                    reference_model.evaluate(payload))
                for _ in range(3):
                    shuffled = generators.shuffled(payload, rng)
                    self.assertEqual(
                        reference_model.reorder_projection(
                            reference_model.evaluate(shuffled)),
                        baseline)

    def test_total_is_sum_of_contributing_votes(self):
        for payload in generators.mixed_corpus(80, seed=606):
            result = reference_model.evaluate(payload)
            rows = payload["returns"]
            contributing_sum = sum(
                rows[i]["votes"] for i in result["contributing_indices"]
            )
            self.assertEqual(result["total_votes"], contributing_sum)

    def test_adding_valid_record_increases_total_by_its_votes(self):
        rng = random.Random(99)
        for index, payload in enumerate(generators.mixed_corpus(40, seed=414)):
            with self.subTest(index=index):
                before = reference_model.evaluate(payload)["total_votes"]
                extra = {
                    "county": "C9", "precinct": "PX", "ballot_type": "early",
                    "candidate": "Q", "votes": rng.randint(0, 50),
                }
                extended = dict(payload)
                extended["returns"] = list(payload["returns"]) + [extra]
                after = reference_model.evaluate(extended)["total_votes"]
                self.assertEqual(after, before + extra["votes"])

    def test_adding_zero_vote_record_never_changes_totals(self):
        for index, payload in enumerate(generators.mixed_corpus(40, seed=515)):
            with self.subTest(index=index):
                before = reference_model.evaluate(payload)
                extra = {
                    "county": "C9", "precinct": "PZ", "ballot_type": "early",
                    "candidate": "Z", "votes": 0,
                }
                extended = dict(payload)
                extended["returns"] = list(payload["returns"]) + [extra]
                after = reference_model.evaluate(extended)
                self.assertEqual(before["total_votes"], after["total_votes"])
                self.assertEqual(before["candidate_totals"].keys(),
                                 after["candidate_totals"].keys() - {"Z"})


class TestSutProperties(unittest.TestCase):
    def test_sut_output_invariant_under_input_reordering(self):
        rng = random.Random(313)
        checked = 0
        for payload in generators.mixed_corpus(60, seed=2024):
            outcome = harness.run_sut(payload)
            if outcome["kind"] != "ok":
                continue  # crash behaviour is covered by fault-injection tests
            checked += 1
            baseline = harness.canonical_json(outcome["output"])
            for _ in range(3):
                shuffled = generators.shuffled(payload, rng)
                rerun = harness.run_sut(shuffled)
                self.assertEqual(rerun["kind"], "ok")
                self.assertEqual(harness.canonical_json(rerun["output"]), baseline)
        self.assertGreater(checked, 20)

    def test_sut_conforms_on_clean_single_precinct_payloads(self):
        for index, payload in enumerate(
                generators.clean_corpus(100, seed=777, single_precinct=True)):
            with self.subTest(index=index):
                verdict = harness.compare(payload)["verdict"]
                self.assertEqual(verdict["status"], "conformant", verdict["reasons"])

    def test_sut_balanced_implies_oracle_balanced_on_clean_payloads(self):
        # The SUT balances globally; the oracle balances per precinct. The
        # oracle is strictly stricter, so SUT-balanced must imply
        # oracle-balanced on clean inputs.
        for index, payload in enumerate(
                generators.clean_corpus(80, seed=888, balanced=False)):
            with self.subTest(index=index):
                result = harness.compare(payload)
                if result["sut"]["kind"] != "ok":
                    continue
                sut_balanced = result["sut"]["output"]["balanced"]
                oracle_balanced = result["oracle"]["balanced"]
                if sut_balanced:
                    self.assertTrue(
                        oracle_balanced,
                        "SUT claims balanced but oracle found per-precinct "
                        "differences: {}".format(
                            result["oracle"]["precinct_differences"]))

    def test_sut_totals_match_oracle_on_fault_free_generated_payloads(self):
        for index, payload in enumerate(
                generators.clean_corpus(60, seed=999, balanced=False)):
            with self.subTest(index=index):
                result = harness.compare(payload)
                self.assertEqual(result["sut"]["kind"], "ok")
                out = result["sut"]["output"]
                oracle = result["oracle"]
                self.assertEqual(out["total_votes"], oracle["total_votes"])
                self.assertEqual(out["candidate_totals"], oracle["candidate_totals"])
                self.assertEqual(out["ballot_type_totals"],
                                 oracle["ballot_type_totals"])
                self.assertEqual(out["difference"], oracle["difference"])


class TestParserProperties(unittest.TestCase):
    def test_parser_output_contains_only_mappings(self):
        for payload in generators.mixed_corpus(60, seed=1212):
            rows = payload.get("returns", [])
            if not isinstance(rows, list):
                continue
            for record in load_records(payload):
                self.assertIsInstance(record, dict)

    def test_parser_is_idempotent_on_its_own_output(self):
        for payload in generators.mixed_corpus(60, seed=1313):
            rows = payload.get("returns", [])
            if not isinstance(rows, list):
                continue
            once = load_records(payload)
            twice = load_records({"returns": once})
            self.assertEqual(once, twice)

    def test_parser_never_invents_rows(self):
        for payload in generators.mixed_corpus(60, seed=1414):
            rows = payload.get("returns", [])
            if not isinstance(rows, list):
                continue
            self.assertLessEqual(len(load_records(payload)), len(rows))

    def test_parser_rejects_non_list_returns(self):
        with self.assertRaises(ValueError):
            load_records({"returns": {"P1": []}})


class TestReportEncapsulationProperties(unittest.TestCase):
    def test_envelope_shape_and_status(self):
        data = {"candidate_totals": {"A": 1}, "total_votes": 1}
        result = build_result("proj", data)
        self.assertEqual(result["project"], "proj")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["candidate_totals"], {"A": 1})
        self.assertEqual(result["total_votes"], 1)

    def test_envelope_does_not_mutate_data(self):
        data = {"x": {"y": [1, 2]}}
        build_result("proj", data)
        self.assertEqual(data, {"x": {"y": [1, 2]}})

    def test_envelope_serialization_is_order_stable(self):
        first = build_result("proj", {"a": 1, "b": 2, "c": 3})
        second = build_result("proj", {"c": 3, "b": 2, "a": 1})
        dump = lambda o: json.dumps(o, sort_keys=True)
        self.assertEqual(dump(first), dump(second))

    def test_envelope_characterization_reserved_key_collision(self):
        # Characterization of current behaviour (known encapsulation gap):
        # data keys named "project" or "status" override the envelope.
        result = build_result("real-project", {"status": "broken", "project": "x"})
        self.assertEqual(result["status"], "broken")
        self.assertEqual(result["project"], "x")


if __name__ == "__main__":
    unittest.main()
