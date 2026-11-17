# Precinct Return Reconciler

Reconcile election returns across precinct, ballot-type, and revision hierarchies.

## Modules

- `src/precinct_return_reconciler/models.py`: immutable result contract.
- `src/precinct_return_reconciler/parser.py`: input validation and record extraction.
- `src/precinct_return_reconciler/engine.py`: domain calculation only.
- `src/precinct_return_reconciler/report.py`: stable output envelope.
- `src/precinct_return_reconciler/pipeline.py`: orchestration boundary.
- `src/precinct_return_reconciler/cli.py`: deterministic JSON CLI.

## Run

```bash
PYTHONPATH=src python -m precinct_return_reconciler.cli
PYTHONPATH=src python -m unittest discover -s tests -v
```

The fixture is local and deterministic; no network or credentials are required.

## QA / audit harness

`tests/qa/` contains an independent hierarchical aggregation reference model
used only by the tests (the production modules are not modified):

- `reference_model.py`: audit oracle. Defines county -> precinct ->
  ballot_type -> candidate aggregation with precinct filing revision
  selection, row classification (missing fields, negative votes, non-mapping
  rows, conflicting duplicates), per-precinct expected-total reconciliation,
  and conservation invariants (`check_conservation`). Never raises on input.
- `harness.py`: differential comparator. Runs the real pipeline against the
  oracle and classifies the outcome (`conformant` / `divergent` /
  `sut_error`) with reasons and provenance pointing back to original rows.
- `cases.py`: curated payloads with pinned expectations (known-gap registry).
- `generators.py` / `shrinker.py`: seeded property-test generators and a
  deterministic counterexample shrinker.
- `mutations.py`: mutation testing of hierarchical keys, revision selection,
  quantity summation and report encapsulation (100% kill rate required).
- `snapshots_data/`: differential snapshots. Regenerate intentionally with
  `QA_REGEN_SNAPSHOTS=1 PYTHONPATH=src python -m unittest discover -s tests`.

Test modules: `test_qa_reference_model.py` (oracle rules + conservation),
`test_qa_properties.py` (property tests incl. input-reorder invariance),
`test_qa_differential.py` (registry, snapshots, combined partial-success
cases, provenance trace-back), `test_qa_fault_injection.py` (fault matrix,
classification stability, CLI determinism), `test_qa_shrinker.py`,
`test_qa_mutation.py`.
