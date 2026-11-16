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
