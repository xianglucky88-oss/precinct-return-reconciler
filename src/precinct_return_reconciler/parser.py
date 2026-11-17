"""Input validation and record extraction.

Two input shapes are supported:

* ``schema_version: 2`` — the full event-sourced contract (hierarchy plus a
  stream of announcements).
* legacy shape — the original flat ``returns`` / ``expected_precinct_totals``
  fixture. It is normalised into synthetic announcements so that one engine
  serves both and old callers keep working unchanged.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .models import (
    BATCH,
    CONVERSION,
    KINDS,
    LINE_STATUSES,
    OFFICIAL,
    RENAME,
    SCHEMA_VERSION,
    WITHDRAWAL,
    Announcement,
    Scope,
    VoteLine,
)


def _require(payload: Any, key: str, expected_type: type, context: str) -> Any:
    if not isinstance(payload, dict) or key not in payload:
        raise ValueError(f"{context}: missing required field '{key}'")
    value = payload[key]
    if not isinstance(value, expected_type):
        raise ValueError(
            f"{context}: field '{key}' must be {expected_type.__name__}, "
            f"got {type(value).__name__}"
        )
    return value


def _opt_int(value: Any, context: str) -> Any:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{context}: expected integer, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{context}: totals and votes must be non-negative")
    return value


def _parse_scope(raw: Any, context: str) -> Scope:
    if not isinstance(raw, dict):
        raise ValueError(f"{context}: scope must be an object")
    state = _require(raw, "state", str, context)
    county = raw.get("county")
    precinct = raw.get("precinct")
    ballot_type = raw.get("ballot_type")
    for name, value in (
        ("county", county),
        ("precinct", precinct),
        ("ballot_type", ballot_type),
    ):
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{context}: scope.{name} must be a string")
    if not state:
        raise ValueError(f"{context}: scope.state must be non-empty")
    if precinct is not None and county is None:
        raise ValueError(f"{context}: scope skips county before precinct")
    if ballot_type is not None and precinct is None:
        raise ValueError(f"{context}: scope skips precinct before ballot_type")
    return Scope(state=state, county=county, precinct=precinct, ballot_type=ballot_type)


def _parse_lines(raw: Any, context: str) -> Tuple[VoteLine, ...]:
    if not isinstance(raw, list):
        raise ValueError(f"{context}: lines must be a list")
    lines: List[VoteLine] = []
    for index, entry in enumerate(raw):
        here = f"{context}.lines[{index}]"
        candidate = _require(entry, "candidate", str, here)
        votes = _opt_int(_require(entry, "votes", int, here), here)
        status = entry.get("status", OFFICIAL)
        if status not in LINE_STATUSES:
            raise ValueError(f"{here}: unknown line status '{status}'")
        ballot_type = entry.get("ballot_type")
        if ballot_type is not None and not isinstance(ballot_type, str):
            raise ValueError(f"{here}: ballot_type must be a string")
        lines.append(
            VoteLine(
                candidate=candidate,
                votes=votes,
                status=status,
                ballot_type=ballot_type,
            )
        )
    return tuple(lines)


def parse_announcement(raw: Any, index: int) -> Announcement:
    context = f"announcements[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{context}: must be an object")
    announcement_id = _require(raw, "announcement_id", str, context)
    version = _require(raw, "version", int, context)
    if isinstance(version, bool) or version < 1:
        raise ValueError(f"{context}: version must be a positive integer")
    kind = _require(raw, "kind", str, context)
    if kind not in KINDS:
        raise ValueError(f"{context}: unknown kind '{kind}'")
    scope = _parse_scope(raw.get("scope"), context)

    lines = (
        _parse_lines(raw["lines"], context)
        if "lines" in raw
        else ()
    )
    expected_total = _opt_int(raw.get("expected_total"), context)
    raw_bt = raw.get("expected_ballot_totals", {})
    if not isinstance(raw_bt, dict):
        raise ValueError(f"{context}: expected_ballot_totals must be an object")
    expected_ballot_totals = {
        str(key): _opt_int(value, f"{context}.expected_ballot_totals.{key}")
        for key, value in raw_bt.items()
    }

    supersedes = raw.get("supersedes")
    if supersedes is not None and not isinstance(supersedes, str):
        raise ValueError(f"{context}: supersedes must be a string")
    batch_id = raw.get("batch_id")
    if batch_id is not None and not isinstance(batch_id, str):
        raise ValueError(f"{context}: batch_id must be a string")
    status = raw.get("status")
    if status is not None and status not in LINE_STATUSES:
        raise ValueError(f"{context}: unknown status '{status}'")
    from_candidate = raw.get("from_candidate")
    to_candidate = raw.get("to_candidate")
    if kind == RENAME and (
        not isinstance(from_candidate, str) or not isinstance(to_candidate, str)
    ):
        raise ValueError(f"{context}: rename requires string from_candidate/to_candidate")
    if kind == WITHDRAWAL and lines:
        raise ValueError(f"{context}: withdrawal must not carry vote lines")
    if kind in (BATCH,) and not lines:
        raise ValueError(f"{context}: batch requires at least one vote line")
    if kind == CONVERSION and not lines:
        raise ValueError(f"{context}: conversion requires vote lines")

    return Announcement(
        announcement_id=announcement_id,
        version=version,
        kind=kind,
        scope=scope,
        lines=lines,
        batch_id=batch_id,
        supersedes=supersedes,
        expected_total=expected_total,
        expected_ballot_totals=dict(expected_ballot_totals),
        status=status,
        from_candidate=from_candidate,
        to_candidate=to_candidate,
        published_at=str(raw.get("published_at", "")),
    )


def _parse_hierarchy(payload: dict, context: str) -> List[dict]:
    raw = payload.get("hierarchy", [])
    if not isinstance(raw, list):
        raise ValueError(f"{context}: hierarchy must be a list")
    parsed: List[dict] = []
    for index, entry in enumerate(raw):
        here = f"hierarchy[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{here}: must be an object")
        node = {"scope": _parse_scope(entry.get("scope"), here)}
        node["expected_total"] = _opt_int(entry.get("expected_total"), here)
        raw_bt = entry.get("expected_ballot_totals", {})
        if not isinstance(raw_bt, dict):
            raise ValueError(f"{here}: expected_ballot_totals must be an object")
        node["expected_ballot_totals"] = {
            str(key): _opt_int(value, f"{here}.expected_ballot_totals.{key}")
            for key, value in raw_bt.items()
        }
        parsed.append(node)
    return parsed


def _legacy_to_v2(payload: dict) -> dict:
    """Normalise the original flat fixture into announcement form.

    Rows sharing one (county, precinct, ballot-type) leaf describe a single
    physical batch, so they are merged into one announcement rather than
    allowed to supersede each other.
    """
    rows = payload.get("returns", [])
    if not isinstance(rows, list):
        raise ValueError("returns must be a list")
    state = "STATE"
    counties: Dict[str, str] = {}
    expected: Dict[str, int] = dict(
        payload.get("expected_precinct_totals", {}) or {}
    )
    hierarchy: List[dict] = [{"scope": {"state": state}}]
    grouped: Dict[Tuple[str, str, str], List[dict]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"returns[{index}]: must be an object")
        county = str(row.get("county", "C?"))
        precinct = str(_require(row, "precinct", str, f"returns[{index}]"))
        ballot_type = str(_require(row, "ballot_type", str, f"returns[{index}]"))
        candidate = str(_require(row, "candidate", str, f"returns[{index}]"))
        votes = _opt_int(_require(row, "votes", int, f"returns[{index}]"),
                         f"returns[{index}]")
        if county not in counties:
            counties[county] = county
            hierarchy.append({"scope": {"state": state, "county": county}})
        grouped.setdefault((county, precinct, ballot_type), []).append(
            {"candidate": candidate, "votes": votes}
        )
    announcements: List[dict] = []
    for leaf_index, ((county, precinct, ballot_type), lines) in enumerate(
        sorted(grouped.items())
    ):
        hierarchy.append(
            {
                "scope": {
                    "state": state,
                    "county": county,
                    "precinct": precinct,
                    "ballot_type": ballot_type,
                }
            }
        )
        announcements.append(
            {
                "announcement_id": f"legacy-{leaf_index}",
                "version": 1,
                "kind": BATCH,
                "batch_id": f"legacy-batch-{leaf_index}",
                "scope": {
                    "state": state,
                    "county": county,
                    "precinct": precinct,
                    "ballot_type": ballot_type,
                },
                "lines": lines,
            }
        )
    precincts: Dict[str, str] = {}
    for (county, precinct, _bt) in grouped:
        precincts.setdefault(f"{county}||{precinct}", precinct)
    for key, precinct in precincts.items():
        county = key.split("||", 1)[0]
        entry: Dict[str, Any] = {
            "scope": {"state": state, "county": county, "precinct": precinct}
        }
        if precinct in expected:
            entry["expected_total"] = expected[precinct]
        hierarchy.append(entry)
    return {
        "schema_version": SCHEMA_VERSION,
        "state": state,
        "hierarchy": hierarchy,
        "announcements": announcements,
    }


def load_payload(payload: dict) -> dict:
    """Validate any supported payload and return a normalised v2 dict."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    if "announcements" in payload or "hierarchy" in payload:
        schema = payload.get("schema_version", SCHEMA_VERSION)
        if schema != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {schema!r}")
        state = _require(payload, "state", str, "payload")
        hierarchy = _parse_hierarchy(payload, "payload")
        raw_events = _require(payload, "announcements", list, "payload")
        announcements = [parse_announcement(raw, i) for i, raw in enumerate(raw_events)]
        return {
            "schema_version": SCHEMA_VERSION,
            "state": state,
            "hierarchy": hierarchy,
            "announcements": announcements,
        }
    # Legacy compatibility path.
    return load_payload(_legacy_to_v2(payload))


def load_records(payload: dict) -> List[dict]:
    """Backward-compatible flat record extraction used by the legacy shim."""
    rows = payload.get("returns", [])
    if not isinstance(rows, list):
        raise ValueError("returns must be a list")
    return [dict(row) for row in rows if isinstance(row, dict)]
