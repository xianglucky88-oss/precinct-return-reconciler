"""Mutation testing for the QA suite itself.

A curated set of mutants is applied to copies of the SUT modules
(parser / engine / pipeline / report) and to the reference oracle. Each
mutant targets one of the audited concerns: hierarchical keys, revision
selection, quantity summation, or report encapsulation. A mutant is
"killed" when the detection routine (curated cases + generated corpus)
reports any failure; import-time crashes also count as killed.

The suite asserts a 100% kill rate, i.e. the QA harness is sensitive to
exactly the bug classes the audit team cares about.
"""

import contextlib
import importlib.util
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import checks

REPO_ROOT = Path(__file__).resolve().parents[2]
SUT_PACKAGE = REPO_ROOT / "src" / "precinct_return_reconciler"
ORACLE_FILE = Path(__file__).resolve().parent / "reference_model.py"


@dataclass(frozen=True)
class Mutant:
    ident: str
    target: str  # "sut" or "oracle"
    relpath: str
    find: str
    replace: str
    concern: str


MUTANTS = [
    # --- hierarchical keys -------------------------------------------------
    Mutant("engine-candidate-key", "sut", "engine.py",
           'candidates[row["candidate"]]=',
           'candidates[row["ballot_type"]]=',
           "hierarchical keys"),
    Mutant("oracle-key-drops-ballot-type", "oracle", "reference_model.py",
           'rkey = (rec["county"], rec["precinct"], rec["ballot_type"], rec["candidate"])',
           'rkey = (rec["county"], rec["precinct"], rec["candidate"])',
           "hierarchical keys"),
    # --- revision selection -------------------------------------------------
    Mutant("oracle-revision-min", "oracle", "reference_model.py",
           "filing_revision = max(r[\"revision\"] for r in group)",
           "filing_revision = min(r[\"revision\"] for r in group)",
           "revision selection"),
    Mutant("oracle-keep-old-filings", "oracle", "reference_model.py",
           'if rec["revision"] < filing_revision:',
           'if rec["revision"] < -1:',
           "revision selection"),
    Mutant("oracle-conflicts-kept", "oracle", "reference_model.py",
           "                for rec in rows:\n                    conflicts.append({",
           "                for rec in rows:\n                    winners.append(rec)\n                    conflicts.append({",
           "revision selection"),
    # --- quantity summation -------------------------------------------------
    Mutant("engine-sum-sign", "sut", "engine.py",
           'candidates.get(row["candidate"],0)+int(row["votes"])',
           'candidates.get(row["candidate"],0)-int(row["votes"])',
           "quantity summation"),
    Mutant("engine-expected-dropped", "sut", "engine.py",
           'expected=sum(payload.get("expected_precinct_totals",{}).values())',
           'expected=0',
           "quantity summation"),
    Mutant("engine-difference-flip", "sut", "engine.py",
           '"difference":actual-expected',
           '"difference":expected-actual',
           "quantity summation"),
    Mutant("oracle-candidate-sum-subtract", "oracle", "reference_model.py",
           'candidate_totals[rec["candidate"]] = candidate_totals.get(rec["candidate"], 0) + votes',
           'candidate_totals[rec["candidate"]] = candidate_totals.get(rec["candidate"], 0) - votes',
           "quantity summation"),
    Mutant("oracle-expected-zeroed", "oracle", "reference_model.py",
           "        expected[key] = value",
           "        expected[key] = 0",
           "quantity summation"),
    # --- validation / classification ----------------------------------------
    Mutant("parser-filter-removed", "sut", "parser.py",
           "[dict(row) for row in rows if isinstance(row, dict)]",
           "[dict(row) for row in rows]",
           "input validation"),
    Mutant("parser-typecheck-flip", "sut", "parser.py",
           "if not isinstance(rows, list):",
           "if isinstance(rows, list):",
           "input validation"),
    Mutant("engine-balanced-flip", "sut", "engine.py",
           '"balanced":actual==expected',
           '"balanced":actual!=expected',
           "balance classification"),
    Mutant("oracle-negative-accepted", "oracle", "reference_model.py",
           'elif row["votes"] < 0:',
           "elif False:",
           "input validation"),
    Mutant("oracle-balanced-any", "oracle", "reference_model.py",
           'result["balanced"] = all(d == 0 for d in differences.values())',
           'result["balanced"] = any(d == 0 for d in differences.values())',
           "balance classification"),
    Mutant("oracle-unsorted-candidates", "oracle", "reference_model.py",
           '"candidate_totals": dict(sorted(candidate_totals.items())),',
           '"candidate_totals": dict(candidate_totals.items()),',
           "report ordering"),
    # --- report encapsulation -----------------------------------------------
    Mutant("report-status-constant", "sut", "report.py",
           '"status": "ok"',
           '"status": "error"',
           "report encapsulation"),
    Mutant("report-data-dropped", "sut", "report.py",
           '{"project": result.project, "status": "ok", **result.data}',
           '{"project": result.project, "status": "ok"}',
           "report encapsulation"),
    Mutant("pipeline-project-name", "sut", "pipeline.py",
           "build_result('precinct-return-reconciler'",
           "build_result('mutated-project'",
           "report encapsulation"),
]


class InvalidMutant(Exception):
    pass


def apply_mutation(path, mutant):
    text = path.read_text()
    count = text.count(mutant.find)
    if count != 1:
        raise InvalidMutant(
            "{}: find string occurs {} times in {}".format(mutant.ident, count, path)
        )
    path.write_text(text.replace(mutant.find, mutant.replace))


def _purge_sut_modules():
    for name in [m for m in sys.modules
                 if m == "precinct_return_reconciler"
                 or m.startswith("precinct_return_reconciler.")]:
        del sys.modules[name]


@contextlib.contextmanager
def mutated_sut(mutant):
    """Yield the pipeline module of a mutated copy of the SUT package."""
    with tempfile.TemporaryDirectory() as tmp:
        destination = Path(tmp) / "precinct_return_reconciler"
        shutil.copytree(SUT_PACKAGE, destination)
        apply_mutation(destination / mutant.relpath, mutant)
        sys.path.insert(0, tmp)
        _purge_sut_modules()
        try:
            import precinct_return_reconciler.pipeline as mutated_pipeline
            yield mutated_pipeline
        finally:
            sys.path.remove(tmp)
            _purge_sut_modules()


def load_mutated_oracle(mutant):
    """Import a mutated copy of the reference model as a fresh module."""
    with tempfile.TemporaryDirectory() as tmp:
        destination = Path(tmp) / "reference_model.py"
        shutil.copy(ORACLE_FILE, destination)
        apply_mutation(destination, mutant)
        spec = importlib.util.spec_from_file_location(
            "qa_reference_model_mutated_" + mutant.ident.replace("-", "_"), destination
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def run_mutant(mutant):
    """Return {"killed": bool, "failures": [...], "error": str|None}."""
    try:
        if mutant.target == "sut":
            with mutated_sut(mutant) as mutated_pipeline:
                failures = checks.detection_failures(sut=mutated_pipeline)
        else:
            module = load_mutated_oracle(mutant)
            failures = checks.detection_failures(oracle=module)
        return {"killed": bool(failures), "failures": failures, "error": None}
    except Exception as exc:  # noqa: BLE001 - a crashing mutant is a killed mutant
        return {"killed": True, "failures": [], "error": "{}: {}".format(
            mutant.ident, type(exc).__name__)}


def run_all(mutants=None):
    return {m.ident: run_mutant(m) for m in (mutants or MUTANTS)}
