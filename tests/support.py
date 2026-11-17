"""Shared builders for the reconciliation test-suite."""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from precinct_return_reconciler.models import canonical_json
from precinct_return_reconciler.pipeline import Pipeline, run
from precinct_return_reconciler.parser import load_payload


def scope(state="S", county=None, precinct=None, ballot_type=None) -> dict:
    raw = {"state": state}
    if county is not None:
        raw["county"] = county
    if precinct is not None:
        raw["precinct"] = precinct
    if ballot_type is not None:
        raw["ballot_type"] = ballot_type
    return raw


def announcement(
    announcement_id: str,
    version: int,
    kind: str,
    scope: dict,
    lines: Optional[List[dict]] = None,
    **extra: Any,
) -> dict:
    event: Dict[str, Any] = {
        "announcement_id": announcement_id,
        "version": version,
        "kind": kind,
        "scope": scope,
    }
    if lines is not None:
        event["lines"] = lines
    event.update(extra)
    return event


def batch(announcement_id, version, county, precinct, ballot_type, lines,
          state="S", **extra):
    return announcement(
        announcement_id,
        version,
        "batch",
        scope(state, county, precinct, ballot_type),
        lines=lines,
        batch_id=f"batch-{announcement_id}",
        **extra,
    )


def revision(announcement_id, version, county, precinct, ballot_type, lines,
             supersedes, state="S", **extra):
    return announcement(
        announcement_id,
        version,
        "revision",
        scope(state, county, precinct, ballot_type),
        lines=lines,
        supersedes=supersedes,
        **extra,
    )


def withdrawal(announcement_id, version, county, precinct, ballot_type,
               state="S", **extra):
    return announcement(
        announcement_id,
        version,
        "withdrawal",
        scope(state, county, precinct, ballot_type),
        **extra,
    )


def conversion(announcement_id, version, county, precinct, ballot_type, lines,
               state="S", **extra):
    return announcement(
        announcement_id,
        version,
        "conversion",
        scope(state, county, precinct, ballot_type),
        lines=lines,
        **extra,
    )


def rename(announcement_id, version, old, new, state="S", **extra):
    return announcement(
        announcement_id,
        version,
        "rename",
        scope(state),
        from_candidate=old,
        to_candidate=new,
        **extra,
    )


def hierarchy_entry(county=None, precinct=None, ballot_type=None,
                    expected_total=None, expected_ballot_totals=None,
                    state="S"):
    entry: Dict[str, Any] = {"scope": scope(state, county, precinct, ballot_type)}
    if expected_total is not None:
        entry["expected_total"] = expected_total
    if expected_ballot_totals is not None:
        entry["expected_ballot_totals"] = expected_ballot_totals
    return entry


def payload(hierarchy, announcements, state="S"):
    return {
        "schema_version": 2,
        "state": state,
        "hierarchy": hierarchy,
        "announcements": announcements,
    }


def pipeline_for(p: dict) -> Pipeline:
    normalised = load_payload(p)
    pipe = Pipeline(normalised["state"], normalised["hierarchy"])
    pipe.ingest(normalised["announcements"])
    return pipe


def materialise(p: dict, as_of_version=None):
    return run(p, as_of_version=as_of_version)["materialisation"]


def find_node(nodes: Dict[str, dict], *parts) -> dict:
    node_id = "/".join(parts)
    return nodes[node_id]


def reason_codes(node: dict) -> List[str]:
    return [reason["code"] for reason in node["reasons"]]


def shuffled_replays(p: dict, seed: int = 7, draws: int = 6) -> List[str]:
    """Return canonical materialisation strings for several permutations."""
    rng = random.Random(seed)
    outputs = []
    for _ in range(draws):
        clone = {
            **p,
            "hierarchy": list(p["hierarchy"]),
            "announcements": list(p["announcements"]),
        }
        rng.shuffle(clone["announcements"])
        rng.shuffle(clone["hierarchy"])
        outputs.append(canonical_json(materialise(clone)))
    return outputs


def assert_same_nodes(testcase, nodes_a, nodes_b):
    testcase.assertEqual(sorted(nodes_a), sorted(nodes_b))
    for node_id in nodes_a:
        testcase.assertEqual(
            canonical_json(nodes_a[node_id]),
            canonical_json(nodes_b[node_id]),
            f"node {node_id} differs across permutations",
        )
