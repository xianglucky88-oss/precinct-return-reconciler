"""Strict input validation and legacy-schema adaptation."""
import json
import unittest
from pathlib import Path

from precinct_return_reconciler.parser import load_payload, load_records
from precinct_return_reconciler.engine import calculate
from precinct_return_reconciler.pipeline import run
from tests.support import hierarchy_entry, payload


class TestValidation(unittest.TestCase):
    def test_scope_cannot_skip_hierarchy_levels(self):
        bad = payload(
            [hierarchy_entry()],
            [
                {
                    "announcement_id": "x",
                    "version": 1,
                    "kind": "batch",
                    "scope": {"state": "S", "precinct": "P1"},
                    "lines": [{"candidate": "A", "votes": 1}],
                }
            ],
        )
        with self.assertRaises(ValueError):
            load_payload(bad)

    def test_unknown_kind_and_line_status_are_rejected(self):
        good_scope = {"state": "S", "county": "C", "precinct": "P",
                      "ballot_type": "b"}
        with self.assertRaises(ValueError):
            load_payload(payload(
                [hierarchy_entry()],
                [{"announcement_id": "x", "version": 1, "kind": "mystery",
                  "scope": good_scope,
                  "lines": [{"candidate": "A", "votes": 1}]}],
            ))
        with self.assertRaises(ValueError):
            load_payload(payload(
                [hierarchy_entry()],
                [{"announcement_id": "x", "version": 1, "kind": "batch",
                  "scope": good_scope,
                  "lines": [{"candidate": "A", "votes": 1,
                             "status": "unheard-of"}]}],
            ))

    def test_negative_votes_and_non_integer_versions_are_rejected(self):
        good_scope = {"state": "S", "county": "C", "precinct": "P",
                      "ballot_type": "b"}
        with self.assertRaises(ValueError):
            load_payload(payload(
                [hierarchy_entry()],
                [{"announcement_id": "x", "version": 1, "kind": "batch",
                  "scope": good_scope,
                  "lines": [{"candidate": "A", "votes": -1}]}],
            ))
        with self.assertRaises(ValueError):
            load_payload(payload(
                [hierarchy_entry()],
                [{"announcement_id": "x", "version": 0, "kind": "batch",
                  "scope": good_scope,
                  "lines": [{"candidate": "A", "votes": 1}]}],
            ))

    def test_withdrawal_cannot_carry_vote_lines(self):
        with self.assertRaises(ValueError):
            load_payload(payload(
                [hierarchy_entry()],
                [{
                    "announcement_id": "w",
                    "version": 1,
                    "kind": "withdrawal",
                    "scope": {"state": "S", "county": "C", "precinct": "P",
                              "ballot_type": "b"},
                    "lines": [{"candidate": "A", "votes": 1}],
                }],
            ))

    def test_rename_requires_both_candidate_names(self):
        with self.assertRaises(ValueError):
            load_payload(payload(
                [hierarchy_entry()],
                [{
                    "announcement_id": "r",
                    "version": 1,
                    "kind": "rename",
                    "scope": {"state": "S"},
                }],
            ))


class TestLegacyCompatibility(unittest.TestCase):
    def test_original_fixture_still_balances_through_new_pipeline(self):
        raw = json.loads(
            (Path(__file__).parents[1] / "fixtures" / "input.json").read_text()
        )
        rows = load_records(raw)
        flat = calculate(rows, raw)
        self.assertTrue(flat["balanced"])
        self.assertEqual(flat["total_votes"], 120)

        result = run(raw)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["balanced"])
        self.assertEqual(result["total_votes"], 120)
        self.assertEqual(result["candidate_totals"], {"A": 100, "B": 20})
        self.assertTrue(result["publishable"])

    def test_legacy_ballot_rows_merge_into_single_leaf_batches(self):
        raw = {
            "returns": [
                {"county": "C", "precinct": "P", "ballot_type": "b",
                 "candidate": "A", "votes": 3},
                {"county": "C", "precinct": "P", "ballot_type": "b",
                 "candidate": "B", "votes": 2},
            ],
            "expected_precinct_totals": {"P": 5},
        }
        result = run(raw)
        self.assertTrue(result["balanced"])
        leaf = result["materialisation"]["leaves"]["STATE/C/P/b"]
        self.assertEqual(leaf["candidate_totals"], {"A": 3, "B": 2})


if __name__ == "__main__":
    unittest.main()
