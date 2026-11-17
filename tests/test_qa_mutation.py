"""Mutation testing: every curated mutant (hierarchical keys, revision
selection, quantity summation, report encapsulation) must be killed by the
QA harness."""

import unittest

from tests.qa import checks, mutations


class TestDetectionBaseline(unittest.TestCase):
    def test_unmutated_detection_is_clean(self):
        failures = checks.detection_failures()
        self.assertEqual(failures, [])


class TestMutantDefinitions(unittest.TestCase):
    def test_every_mutant_find_string_is_unique_in_its_target(self):
        for mutant in mutations.MUTANTS:
            with self.subTest(mutant=mutant.ident):
                if mutant.target == "sut":
                    path = mutations.SUT_PACKAGE / mutant.relpath
                else:
                    path = mutations.ORACLE_FILE
                text = path.read_text()
                self.assertEqual(
                    text.count(mutant.find), 1,
                    "find string for {} must occur exactly once".format(mutant.ident))

    def test_mutants_cover_required_concerns(self):
        concerns = {m.concern for m in mutations.MUTANTS}
        self.assertIn("hierarchical keys", concerns)
        self.assertIn("revision selection", concerns)
        self.assertIn("quantity summation", concerns)
        self.assertIn("report encapsulation", concerns)

    def test_mutants_cover_both_targets(self):
        targets = {m.target for m in mutations.MUTANTS}
        self.assertEqual(targets, {"sut", "oracle"})


class TestMutationKillRate(unittest.TestCase):
    def test_all_mutants_are_killed(self):
        survivors = []
        for mutant in mutations.MUTANTS:
            with self.subTest(mutant=mutant.ident):
                outcome = mutations.run_mutant(mutant)
                if not outcome["killed"]:
                    survivors.append(mutant.ident)
                self.assertTrue(
                    outcome["killed"],
                    "mutant {} survived - the QA harness does not detect "
                    "this {} defect".format(mutant.ident, mutant.concern))
        self.assertEqual(survivors, [])

    def test_killed_mutants_fail_for_the_right_concern(self):
        # Spot-check: revision-selection mutants must be detected through
        # cases that actually exercise filings.
        for ident in ("oracle-revision-min", "oracle-keep-old-filings"):
            mutant = next(m for m in mutations.MUTANTS if m.ident == ident)
            with self.subTest(mutant=ident):
                outcome = mutations.run_mutant(mutant)
                self.assertTrue(outcome["killed"])
                self.assertTrue(
                    any("duplicate_revision" in f or "combined_mixed" in f
                        for f in outcome["failures"]),
                    outcome["failures"])


if __name__ == "__main__":
    unittest.main()
