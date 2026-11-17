"""Independent hierarchical aggregation reference model (audit oracle).

This module is the election audit team's ground-truth specification for
hierarchical aggregation. It is written independently from
``src/precinct_return_reconciler`` and is used only by the QA suite as a
differential oracle. It never raises on payload content: every anomaly is
classified into the result so behaviour can be compared deterministically.

Hierarchy and rules
-------------------
R1  A row must be a mapping with non-empty string ``county`` / ``precinct``
    / ``ballot_type`` / ``candidate`` and an integer ``votes`` (bool is not
    accepted as an int).
R2  ``votes`` may be zero; negative votes invalidate the row.
R3  ``revision`` is optional, defaults to 0, and must be a non-negative int.
R4  Within a ``(county, precinct)`` filing group only records carrying the
    maximal revision belong to the current filing. Older revisions are
    superseded and must not contribute to any subtotal (a county must never
    double-count an old precinct revision).
R5  Within the current filing, records sharing the same
    ``(county, precinct, ballot_type, candidate)`` key must be unique.
    Identical duplicates are collapsed with a warning; conflicting
    duplicates invalidate all conflicting rows.
R6  Only contributing records feed any aggregate. Aggregates must conserve:
    precinct subtotal == sum of its ballot-type detail; county == sum of
    its precincts; grand total == sum over any hierarchical dimension.
R7  ``expected_precinct_totals`` must map precinct -> non-negative int;
    invalid entries are dropped with a warning.
R8  Output is deterministic: sorted keys, stable classification order, and
    invariant under input row reordering (see ``reorder_projection``).
"""

import copy
import json

REQUIRED_STRING_FIELDS = ("county", "precinct", "ballot_type", "candidate")

SCHEMA = "qa-reference/v1"


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _safe_row(row):
    """Return a JSON-serializable snapshot of a raw input row."""
    try:
        json.dumps(row)
        return copy.deepcopy(row)
    except (TypeError, ValueError):
        return {"__unserializable__": type(row).__name__}


def _public_record(rec):
    """Normalized record without the positional source index."""
    return {
        "county": rec["county"],
        "precinct": rec["precinct"],
        "ballot_type": rec["ballot_type"],
        "candidate": rec["candidate"],
        "votes": rec["votes"],
        "revision": rec["revision"],
    }


def validate_row(row, index):
    """R1-R3 row validation. Returns (record, errors); exactly one is empty."""
    if not isinstance(row, dict):
        return None, [{
            "index": index,
            "reason": "row_not_mapping",
            "detail": type(row).__name__,
            "row": _safe_row(row),
        }]
    errors = []

    def err(reason, detail):
        errors.append({
            "index": index,
            "reason": reason,
            "detail": detail,
            "row": _safe_row(row),
        })

    for field in REQUIRED_STRING_FIELDS:
        if field not in row:
            err("missing_field", field)
        elif not isinstance(row[field], str) or row[field] == "":
            err("invalid_field_type", field)
    if "votes" not in row:
        err("missing_field", "votes")
    elif not _is_int(row["votes"]):
        err("invalid_field_type", "votes")
    elif row["votes"] < 0:
        err("negative_votes", row["votes"])
    revision = row.get("revision", 0)
    if not _is_int(revision) or revision < 0:
        err("invalid_field_type", "revision")
    if errors:
        return None, errors
    record = {
        "county": row["county"],
        "precinct": row["precinct"],
        "ballot_type": row["ballot_type"],
        "candidate": row["candidate"],
        "votes": row["votes"],
        "revision": revision,
        "source_index": index,
        "_raw": _safe_row(row),
    }
    return record, []


def select_filings(records):
    """R4-R5 revision selection. Returns (winners, superseded, conflicts, warnings)."""
    winners = []
    superseded = []
    conflicts = []
    warnings = []
    by_precinct = {}
    for rec in records:
        by_precinct.setdefault((rec["county"], rec["precinct"]), []).append(rec)
    for pkey in sorted(by_precinct):
        group = by_precinct[pkey]
        filing_revision = max(r["revision"] for r in group)
        current = []
        for rec in group:
            if rec["revision"] < filing_revision:
                superseded.append({
                    "index": rec["source_index"],
                    "reason": "superseded_by_newer_filing",
                    "record": _public_record(rec),
                    "winning_revision": filing_revision,
                })
            else:
                current.append(rec)
        by_key = {}
        for rec in current:
            rkey = (rec["county"], rec["precinct"], rec["ballot_type"], rec["candidate"])
            by_key.setdefault(rkey, []).append(rec)
        for rkey in sorted(by_key):
            rows = sorted(by_key[rkey], key=lambda r: r["source_index"])
            if len(rows) == 1:
                winners.append(rows[0])
                continue
            indices = [r["source_index"] for r in rows]
            if len({r["votes"] for r in rows}) == 1:
                winners.append(rows[0])
                for rec in rows[1:]:
                    superseded.append({
                        "index": rec["source_index"],
                        "reason": "exact_duplicate",
                        "record": _public_record(rec),
                        "kept_index": rows[0]["source_index"],
                    })
                warnings.append({
                    "code": "exact_duplicate_rows",
                    "key": list(rkey),
                    "indices": indices,
                })
            else:
                for rec in rows:
                    conflicts.append({
                        "index": rec["source_index"],
                        "reason": "conflicting_same_revision",
                        "detail": rec["votes"],
                        "row": rec.get("_raw", _public_record(rec)),
                    })
                warnings.append({
                    "code": "conflicting_same_revision",
                    "key": list(rkey),
                    "indices": indices,
                })
    winners.sort(key=lambda r: r["source_index"])
    return winners, superseded, conflicts, warnings


def aggregate(winners):
    """R6 hierarchical aggregation over contributing records only."""
    candidate_totals = {}
    ballot_type_totals = {}
    precinct_totals = {}
    county_totals = {}
    precinct_ballot = {}
    county_precincts = {}
    record_refs = {}
    for rec in winners:
        votes = rec["votes"]
        candidate_totals[rec["candidate"]] = candidate_totals.get(rec["candidate"], 0) + votes
        ballot_type_totals[rec["ballot_type"]] = ballot_type_totals.get(rec["ballot_type"], 0) + votes
        precinct_totals[rec["precinct"]] = precinct_totals.get(rec["precinct"], 0) + votes
        county_totals[rec["county"]] = county_totals.get(rec["county"], 0) + votes
        bucket = precinct_ballot.setdefault(rec["precinct"], {})
        bucket[rec["ballot_type"]] = bucket.get(rec["ballot_type"], 0) + votes
        county_precincts.setdefault(rec["county"], set()).add(rec["precinct"])
        record_refs.setdefault(rec["precinct"], []).append(rec["source_index"])
    return {
        "candidate_totals": dict(sorted(candidate_totals.items())),
        "ballot_type_totals": dict(sorted(ballot_type_totals.items())),
        "precinct_totals": dict(sorted(precinct_totals.items())),
        "county_totals": dict(sorted(county_totals.items())),
        "precinct_ballot_type_totals": {
            p: dict(sorted(bucket.items())) for p, bucket in sorted(precinct_ballot.items())
        },
        "county_precincts": {c: sorted(ps) for c, ps in sorted(county_precincts.items())},
        "record_refs": {p: sorted(ix) for p, ix in sorted(record_refs.items())},
    }


def validate_expected(payload):
    """R7 expected-total validation. Returns (expected, warnings)."""
    raw = payload.get("expected_precinct_totals", {})
    warnings = []
    if not isinstance(raw, dict):
        warnings.append({
            "code": "expected_totals_not_mapping",
            "detail": type(raw).__name__,
        })
        return {}, warnings
    expected = {}
    for key in sorted(raw, key=str):
        value = raw[key]
        if not isinstance(key, str) or not _is_int(value) or value < 0:
            warnings.append({
                "code": "invalid_expected_total_entry",
                "precinct": key if isinstance(key, str) else str(key),
                "value": value if _is_int(value) or isinstance(value, str) else str(value),
            })
            continue
        expected[key] = value
    return expected, warnings


def _empty_result():
    return {
        "schema": SCHEMA,
        "input": {"record_count": 0},
        "input_errors": [],
        "candidate_totals": {},
        "ballot_type_totals": {},
        "precinct_totals": {},
        "county_totals": {},
        "precinct_ballot_type_totals": {},
        "county_precincts": {},
        "record_refs": {},
        "contributing_indices": [],
        "total_votes": 0,
        "expected_precinct_totals": {},
        "total_expected": 0,
        "precinct_differences": {},
        "precinct_balanced": {},
        "difference": 0,
        "balanced": True,
        "invalid_records": [],
        "superseded_records": [],
        "warnings": [],
    }


def evaluate(payload):
    """Evaluate a payload against the reference rules. Never raises."""
    result = _empty_result()
    if not isinstance(payload, dict):
        result["input_errors"].append({
            "reason": "payload_not_mapping",
            "detail": type(payload).__name__,
        })
        return result
    rows = payload.get("returns", [])
    if not isinstance(rows, list):
        result["input_errors"].append({
            "reason": "returns_not_list",
            "detail": type(rows).__name__,
        })
        return result

    result["input"]["record_count"] = len(rows)
    records = []
    invalid = []
    for index, row in enumerate(rows):
        rec, errors = validate_row(row, index)
        if rec is not None:
            records.append(rec)
        invalid.extend(errors)

    winners, superseded, conflicts, warnings = select_filings(records)
    invalid.extend(conflicts)

    agg = aggregate(winners)
    result.update(agg)
    result["contributing_indices"] = sorted(rec["source_index"] for rec in winners)
    result["total_votes"] = sum(agg["candidate_totals"].values())

    expected, exp_warnings = validate_expected(payload)
    warnings.extend(exp_warnings)
    result["expected_precinct_totals"] = expected
    result["total_expected"] = sum(expected.values())

    for precinct in sorted(expected):
        if precinct not in agg["precinct_totals"]:
            warnings.append({"code": "expected_for_unknown_precinct", "precinct": precinct})
    for precinct in sorted(agg["precinct_totals"]):
        if precinct not in expected:
            warnings.append({"code": "precinct_missing_expected_total", "precinct": precinct})

    all_precincts = sorted(set(agg["precinct_totals"]) | set(expected))
    differences = {
        p: agg["precinct_totals"].get(p, 0) - expected.get(p, 0) for p in all_precincts
    }
    result["precinct_differences"] = differences
    result["precinct_balanced"] = {p: d == 0 for p, d in differences.items()}
    result["difference"] = result["total_votes"] - result["total_expected"]
    result["balanced"] = all(d == 0 for d in differences.values())

    result["invalid_records"] = sorted(
        invalid, key=lambda e: (e["index"], e["reason"], str(e["detail"]))
    )
    result["superseded_records"] = sorted(superseded, key=lambda e: e["index"])
    result["warnings"] = sorted(warnings, key=_canon)
    return result


def reorder_projection(result):
    """Projection of a result that must be invariant under input row reordering.

    Source indices legitimately change when rows are permuted, so they are
    replaced by content-based multisets here.
    """
    return {
        "input_errors": result["input_errors"],
        "candidate_totals": result["candidate_totals"],
        "ballot_type_totals": result["ballot_type_totals"],
        "precinct_totals": result["precinct_totals"],
        "county_totals": result["county_totals"],
        "precinct_ballot_type_totals": result["precinct_ballot_type_totals"],
        "county_precincts": result["county_precincts"],
        "total_votes": result["total_votes"],
        "expected_precinct_totals": result["expected_precinct_totals"],
        "total_expected": result["total_expected"],
        "precinct_differences": result["precinct_differences"],
        "precinct_balanced": result["precinct_balanced"],
        "difference": result["difference"],
        "balanced": result["balanced"],
        "invalid_records": sorted(
            (
                {"reason": e["reason"], "detail": e["detail"], "row": e["row"]}
                for e in result["invalid_records"]
            ),
            key=_canon,
        ),
        "superseded_records": sorted(
            (
                {"reason": e["reason"], "record": e["record"]}
                for e in result["superseded_records"]
            ),
            key=_canon,
        ),
        "warnings": sorted(
            (
                {k: v for k, v in w.items() if k != "indices"}
                for w in result["warnings"]
            ),
            key=_canon,
        ),
        "contributing_count": len(result["contributing_indices"]),
    }


def check_conservation(result):
    """Return a deterministic list of conservation violations (empty == sound)."""
    violations = []

    def check(condition, code):
        if not condition:
            violations.append(code)

    # C1: every report mapping is sorted by key (stable report ordering).
    for key in (
        "candidate_totals",
        "ballot_type_totals",
        "precinct_totals",
        "county_totals",
        "expected_precinct_totals",
        "precinct_differences",
        "precinct_balanced",
        "county_precincts",
        "record_refs",
    ):
        check(list(result[key]) == sorted(result[key]), "C1:unsorted_mapping:" + key)
    check(
        list(result["precinct_ballot_type_totals"])
        == sorted(result["precinct_ballot_type_totals"]),
        "C1:unsorted_mapping:precinct_ballot_type_totals",
    )
    for precinct, bucket in result["precinct_ballot_type_totals"].items():
        check(list(bucket) == sorted(bucket), "C1:unsorted_nested:" + precinct)

    if result["input_errors"]:
        check(result["total_votes"] == 0, "C0:input_error_with_totals")
        return violations

    # C2: grand total is conserved across every hierarchical dimension.
    check(result["total_votes"] == sum(result["candidate_totals"].values()),
          "C2:total!=candidates")
    check(result["total_votes"] == sum(result["ballot_type_totals"].values()),
          "C2:total!=ballot_types")
    check(result["total_votes"] == sum(result["precinct_totals"].values()),
          "C2:total!=precincts")
    check(result["total_votes"] == sum(result["county_totals"].values()),
          "C2:total!=counties")

    # C3: each precinct subtotal equals the sum of its ballot-type detail.
    for precinct, subtotal in result["precinct_totals"].items():
        detail = result["precinct_ballot_type_totals"].get(precinct, {})
        check(subtotal == sum(detail.values()), "C3:precinct!=ballot_detail:" + precinct)

    # C4: each county total equals the sum of its precincts (no double count).
    for county, precincts in result["county_precincts"].items():
        subtotal = sum(result["precinct_totals"].get(p, 0) for p in precincts)
        check(result["county_totals"].get(county) == subtotal,
              "C4:county!=precincts:" + county)

    # C5: expected-total bookkeeping.
    check(result["total_expected"] == sum(result["expected_precinct_totals"].values()),
          "C5:total_expected")
    check(result["difference"] == result["total_votes"] - result["total_expected"],
          "C5:difference")
    union = sorted(set(result["precinct_totals"]) | set(result["expected_precinct_totals"]))
    check(sorted(result["precinct_differences"]) == union, "C5:difference_keys")
    for precinct, diff in result["precinct_differences"].items():
        check(
            diff == result["precinct_totals"].get(precinct, 0)
            - result["expected_precinct_totals"].get(precinct, 0),
            "C5:difference_value:" + precinct,
        )
    check(result["balanced"] == all(d == 0 for d in result["precinct_differences"].values()),
          "C5:balanced_flag")
    for precinct, flag in result["precinct_balanced"].items():
        check(flag == (result["precinct_differences"].get(precinct) == 0),
              "C5:precinct_balanced:" + precinct)

    # C6: record-level conservation. Every input record is accounted for
    # exactly once: invalid, superseded, or contributing.
    count = result["input"]["record_count"]
    invalid_idx = {e["index"] for e in result["invalid_records"]}
    superseded_idx = {e["index"] for e in result["superseded_records"]}
    contributing_idx = set(result["contributing_indices"])
    check(not (invalid_idx & superseded_idx), "C6:invalid_and_superseded_overlap")
    check(not (invalid_idx & contributing_idx), "C6:invalid_and_contributing_overlap")
    check(not (superseded_idx & contributing_idx), "C6:superseded_and_contributing_overlap")
    check(invalid_idx | superseded_idx | contributing_idx == set(range(count)),
          "C6:record_accounting")

    # C7: provenance completeness - record refs cover exactly the contributors.
    refs = sorted(i for indices in result["record_refs"].values() for i in indices)
    check(refs == result["contributing_indices"], "C7:record_refs")

    return violations
