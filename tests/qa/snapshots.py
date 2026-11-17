"""Snapshot utilities for differential snapshot testing.

Snapshots are canonical JSON (sorted keys) stored under
``tests/qa/snapshots_data``. Regenerate intentionally with:

    QA_REGEN_SNAPSHOTS=1 PYTHONPATH=src python -m unittest discover -s tests
"""

import difflib
import json
import os
from pathlib import Path

SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots_data"

REGEN = os.environ.get("QA_REGEN_SNAPSHOTS") == "1"


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def check_snapshot(name, obj):
    """Compare ``obj`` against the stored snapshot; raise AssertionError on
    mismatch. In regen mode, (re)write the snapshot instead."""
    path = SNAPSHOT_DIR / (name + ".json")
    text = canonical(obj)
    if REGEN:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return
    if not path.exists():
        raise AssertionError(
            "missing snapshot {} - regenerate with QA_REGEN_SNAPSHOTS=1".format(path)
        )
    expected = path.read_text()
    if expected != text:
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                text.splitlines(keepends=True),
                fromfile="snapshot/" + name,
                tofile="current",
            )
        )
        raise AssertionError("snapshot mismatch for {}:\n{}".format(name, diff))
