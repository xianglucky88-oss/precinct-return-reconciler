"""Differential harness: run the SUT pipeline against the reference oracle.

Outcomes are classified into a small, stable vocabulary so that snapshot
tests, fault-injection tests and the mutation runner can all compare
behaviour deterministically. Divergence verdicts carry provenance that
points back to the original return rows.
"""

import json

try:
    from precinct_return_reconciler import pipeline as _default_sut
except ImportError:  # pragma: no cover - defensive path for direct invocation
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from precinct_return_reconciler import pipeline as _default_sut

from . import reference_model as _default_oracle

PROJECT = "precinct-return-reconciler"

SHARED_KEYS = ("candidate_totals", "ballot_type_totals", "total_votes", "balanced", "difference")


def canonical_json(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)


def run_sut(payload, sut=None):
    """Run the real pipeline, capturing the outcome instead of raising."""
    sut = sut or _default_sut
    try:
        return {"kind": "ok", "output": sut.run(payload)}
    except Exception as exc:  # noqa: BLE001 - classification is the point
        return {"kind": "raise", "error_type": type(exc).__name__, "message": str(exc)}


def _sut_coercible_votes(row):
    """Votes value the SUT would add for this row, or None if it would crash."""
    if not isinstance(row, dict):
        return 0  # the SUT parser silently drops non-mapping rows
    try:
        return int(row["votes"])
    except (KeyError, TypeError, ValueError):
        return None


def _explained_gap(payload, oracle_result):
    """Sum of votes the oracle excluded but the SUT would have counted.

    Returns None when the gap cannot be attributed mechanically (e.g. the
    SUT would have crashed on an uncoercible value).
    """
    rows = payload.get("returns", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return None
    total = 0
    for entry in oracle_result["superseded_records"]:
        total += entry["record"]["votes"]
    for entry in oracle_result["invalid_records"]:
        index = entry["index"]
        row = rows[index] if 0 <= index < len(rows) else None
        value = _sut_coercible_votes(row)
        if value is None:
            return None
        total += value
    return total


def _deltas(sut_mapping, oracle_mapping):
    keys = sorted(set(sut_mapping) | set(oracle_mapping))
    return {
        key: {"sut": sut_mapping.get(key, 0), "oracle": oracle_mapping.get(key, 0)}
        for key in keys
        if sut_mapping.get(key, 0) != oracle_mapping.get(key, 0)
    }


def _classify(payload, sut_outcome, oracle_result):
    reasons = []
    notes = []
    provenance = {}
    invalid = oracle_result["invalid_records"]
    superseded = oracle_result["superseded_records"]
    if invalid:
        provenance["invalid_records"] = invalid
    if superseded:
        provenance["superseded_records"] = superseded

    if sut_outcome["kind"] == "raise":
        reasons.append("sut_raises:" + sut_outcome["error_type"])
        classified = sorted({(e["reason"], str(e["detail"])) for e in invalid})
        for reason, detail in classified:
            reasons.append("sut_cannot_classify:{}:{}".format(reason, detail))
        if oracle_result["contributing_indices"]:
            # The oracle produced partial results; the SUT is all-or-nothing.
            reasons.append("sut_loses_partial_results")
        status = "sut_error"
    else:
        out = sut_outcome["output"]
        if out.get("project") != PROJECT:
            reasons.append("envelope:project")
        if out.get("status") != "ok":
            reasons.append("envelope:status")

        mismatches = [key for key in SHARED_KEYS if out.get(key) != oracle_result[key]]
        reasons.extend("mismatch:" + key for key in mismatches)
        totals_mismatch = "total_votes" in mismatches or "difference" in mismatches

        if totals_mismatch:
            for reason in sorted({e["reason"] for e in invalid}):
                reasons.append("cause:sut_counts_rows_oracle_rejects:" + reason)
            for reason in sorted({e["reason"] for e in superseded}):
                reasons.append("cause:sut_counts_superseded:" + reason)
            gap = out.get("total_votes", 0) - oracle_result["total_votes"]
            explained = _explained_gap(payload, oracle_result)
            if explained is None:
                notes.append("gap_analysis:unattributed")
            elif gap == explained:
                notes.append("gap_analysis:fully_explained_by_excluded_rows")
            else:
                notes.append(
                    "gap_analysis:RESIDUAL:sut_minus_oracle={}:excluded_sum={}".format(
                        gap, explained
                    )
                )
        if "balanced" in mismatches and not totals_mismatch:
            reasons.append("cause:balanced_granularity:sut_global_vs_oracle_per_precinct")
            provenance["precinct_differences"] = oracle_result["precinct_differences"]
        if "candidate_totals" in mismatches:
            provenance["candidate_deltas"] = _deltas(
                out.get("candidate_totals", {}), oracle_result["candidate_totals"]
            )
        if "ballot_type_totals" in mismatches:
            provenance["ballot_type_deltas"] = _deltas(
                out.get("ballot_type_totals", {}), oracle_result["ballot_type_totals"]
            )
        if not mismatches and invalid:
            notes.append("sut_silently_drops_invalid_rows")
        status = "divergent" if reasons else "conformant"

    if oracle_result["warnings"]:
        codes = sorted({w["code"] for w in oracle_result["warnings"]})
        notes.append("oracle_warnings:" + ",".join(codes))

    return {
        "status": status,
        "reasons": sorted(dict.fromkeys(reasons)),
        "notes": sorted(dict.fromkeys(notes)),
        "provenance": provenance,
    }


def compare(payload, sut=None, oracle=None):
    """Run SUT and oracle on the same payload and classify the difference."""
    oracle = oracle or _default_oracle
    oracle_result = oracle.evaluate(payload)
    sut_outcome = run_sut(payload, sut=sut)
    verdict = _classify(payload, sut_outcome, oracle_result)
    return {"sut": sut_outcome, "oracle": oracle_result, "verdict": verdict}
