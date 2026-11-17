"""Deterministic JSON CLI.

Examples
--------
PYTHONPATH=src python -m precinct_return_reconciler.cli fixtures/input.json
PYTHONPATH=src python -m precinct_return_reconciler.cli returns.json --version 3
PYTHONPATH=src python -m precinct_return_reconciler.cli returns.json --journal .run
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .journal import Journal
from .pipeline import run
from .report import build_result

DEFAULT_FIXTURE = Path(__file__).parents[2] / "fixtures" / "input.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Versioned election-night return reconciliation across "
            "state / county / precinct / ballot-type scopes."
        )
    )
    parser.add_argument("input", nargs="?", default=str(DEFAULT_FIXTURE))
    parser.add_argument(
        "--version",
        type=int,
        default=None,
        help="replay the log as of this announcement version",
    )
    parser.add_argument(
        "--journal",
        default=None,
        help="directory for the durable append-only event journal",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="include every historical publication version",
    )
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))

    if args.journal:
        # Durable path: persist through the checksummed journal, recover any
        # torn tail, then publish from the replayed store.
        from .parser import load_payload
        from .pipeline import Pipeline

        normalised = load_payload(payload)
        journal = Journal(args.journal)
        report = journal.recover()
        for event in normalised["announcements"]:
            journal.append(event)
        pipeline = Pipeline(normalised["state"], normalised["hierarchy"])
        pipeline.store = journal.store
        publication = pipeline.publish(
            as_of_version=args.version, include_history=args.history
        )
        publication["recovery"] = {
            "loaded": report.loaded,
            "truncated_tail_lines": report.truncated_tail_lines,
            "warnings": report.warnings,
        }
        result = build_result("precinct-return-reconciler", publication)
    else:
        result = run(
            payload,
            as_of_version=args.version,
            include_history=args.history,
        )

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
