"""Deterministic counterexample shrinker.

Given a payload and a predicate (typically "the differential verdict is
divergent with reason R"), greedily reduce the payload while the predicate
keeps holding. Fully deterministic: fixed strategy order, first improvement
wins, no randomness.
"""

import copy


def _without_rows(payload, start, length):
    clone = copy.deepcopy(payload)
    rows = clone.get("returns", [])
    if isinstance(rows, list):
        del rows[start:start + length]
    return clone


def _simplify_row_variants(row):
    """Cheaper variants of a single row, cheapest first."""
    variants = []
    if not isinstance(row, dict):
        return variants
    if "revision" in row and row["revision"] != 0:
        clone = dict(row)
        clone["revision"] = 0
        variants.append(clone)
        clone = dict(row)
        del clone["revision"]
        variants.append(clone)
    if isinstance(row.get("votes"), int) and row["votes"] != 0:
        clone = dict(row)
        clone["votes"] = 0
        variants.append(clone)
    for field in ("county", "precinct", "ballot_type", "candidate"):
        if isinstance(row.get(field), str) and row[field] != "x":
            clone = dict(row)
            clone[field] = "x"
            variants.append(clone)
    return variants


def _expected_variants(payload):
    """Cheaper variants of the expected-totals mapping."""
    expected = payload.get("expected_precinct_totals")
    if not isinstance(expected, dict) or not expected:
        return []
    variants = []
    for key in sorted(expected, key=str):
        clone = copy.deepcopy(payload)
        del clone["expected_precinct_totals"][key]
        variants.append(clone)
        value = expected[key]
        if isinstance(value, int) and value > 0:
            clone = copy.deepcopy(payload)
            clone["expected_precinct_totals"][key] = value // 2
            variants.append(clone)
    return variants


def shrink(payload, predicate, max_rounds=60):
    """Reduce ``payload`` while ``predicate`` holds. Returns the 1-minimal
    payload found (removing any single remaining row breaks the predicate,
    unless the predicate stopped depending on rows at all)."""
    current = copy.deepcopy(payload)
    if not predicate(current):
        raise ValueError("predicate must hold for the initial payload")

    for _ in range(max_rounds):
        improved = False
        rows = current.get("returns", [])
        n_rows = len(rows) if isinstance(rows, list) else 0

        # Strategy 1: remove chunks of rows (large chunks first).
        chunk = max(1, n_rows // 2)
        while chunk >= 1 and not improved:
            start = 0
            while start < n_rows:
                candidate = _without_rows(current, start, chunk)
                if predicate(candidate):
                    current = candidate
                    improved = True
                    break
                start += chunk
            chunk //= 2

        # Strategy 2: simplify individual rows.
        if not improved:
            rows = current.get("returns", [])
            if isinstance(rows, list):
                for index in range(len(rows)):
                    for variant in _simplify_row_variants(rows[index]):
                        candidate = copy.deepcopy(current)
                        candidate["returns"][index] = variant
                        if predicate(candidate):
                            current = candidate
                            improved = True
                            break
                    if improved:
                        break

        # Strategy 3: shrink the expected-totals mapping.
        if not improved:
            for candidate in _expected_variants(current):
                if predicate(candidate):
                    current = candidate
                    improved = True
                    break

        if not improved:
            return current
    return current


def is_row_removal_minimal(payload, predicate):
    """True when removing any single row makes the predicate fail."""
    rows = payload.get("returns", [])
    if not isinstance(rows, list):
        return True
    for index in range(len(rows)):
        candidate = _without_rows(payload, index, 1)
        if predicate(candidate):
            return False
    return True
