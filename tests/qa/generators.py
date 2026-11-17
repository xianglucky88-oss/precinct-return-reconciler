"""Deterministic seeded payload generators for property-based testing.

All generators take a ``random.Random`` instance so every corpus is fully
reproducible from its seed. No external property-testing framework is used;
shrinking of counterexamples lives in ``tests.qa.shrinker``.
"""

import copy
import random

COUNTIES = ("C1", "C2", "C3")
PRECINCTS = ("P1", "P2", "P3", "P4")
BALLOT_TYPES = ("early", "election_day", "absentee", "provisional")
CANDIDATES = ("A", "B", "C", "D")

FAULTS = (
    "drop_county",
    "drop_precinct",
    "drop_ballot_type",
    "drop_candidate",
    "drop_votes",
    "negative_votes",
    "votes_float",
    "votes_string_numeric",
    "votes_string_garbage",
    "votes_bool",
    "bad_revision_string",
    "bad_revision_negative",
)


def _row(county, precinct, ballot_type, candidate, votes, revision=0):
    return {
        "county": county,
        "precinct": precinct,
        "ballot_type": ballot_type,
        "candidate": candidate,
        "votes": votes,
        "revision": revision,
    }


def clean_rows(rng, precincts=None, ballot_types=BALLOT_TYPES, candidates=CANDIDATES,
               revision=0, zero_rate=0.1, max_rows=None):
    """Generate internally consistent rows for one filing per precinct."""
    if precincts is None:
        precincts = [("C1", p) for p in PRECINCTS[: rng.randint(1, 3)]]
    rows = []
    for county, precinct in precincts:
        bts = rng.sample(list(ballot_types), rng.randint(1, len(ballot_types)))
        cands = rng.sample(list(candidates), rng.randint(1, len(candidates)))
        for ballot_type in bts:
            for candidate in cands:
                votes = 0 if rng.random() < zero_rate else rng.randint(1, 60)
                rows.append(_row(county, precinct, ballot_type, candidate, votes, revision))
    if max_rows is not None:
        rows = rows[:max_rows]
    return rows


def expected_from_rows(rows):
    """Expected precinct totals computed from the latest filing per precinct."""
    latest = {}
    for row in rows:
        key = row["precinct"]
        if key not in latest or row["revision"] > latest[key][0]:
            latest[key] = (row["revision"], set())
    totals = {}
    for row in rows:
        revision, seen = latest[row["precinct"]]
        if row["revision"] != revision:
            continue
        marker = (row["ballot_type"], row["candidate"])
        if marker in seen:
            continue
        seen.add(marker)
        totals[row["precinct"]] = totals.get(row["precinct"], 0) + row["votes"]
    return totals


def add_revision_filing(rng, rows, conflict=False):
    """Append a newer (or conflicting) filing for one precinct, keeping the old rows."""
    by_precinct = {}
    for row in rows:
        by_precinct.setdefault(row["precinct"], []).append(row)
    if not by_precinct:
        return rows
    precinct = rng.choice(sorted(by_precinct))
    originals = by_precinct[precinct]
    max_revision = max(r["revision"] for r in originals)
    new_revision = max_revision if conflict else max_revision + 1
    for original in originals:
        clone = dict(original)
        clone["revision"] = new_revision
        if rng.random() < 0.6:
            clone["votes"] = rng.randint(1, 60)
        rows.append(clone)
    if conflict and originals:
        # Force a same-revision conflict on the first record key.
        clone = dict(originals[0])
        clone["revision"] = new_revision
        clone["votes"] = originals[0]["votes"] + 1
        rows.append(clone)
    return rows


def inject_fault(rng, rows, fault):
    if not rows:
        return rows
    row = dict(rng.choice(rows))
    if fault == "drop_county":
        row.pop("county", None)
    elif fault == "drop_precinct":
        row.pop("precinct", None)
    elif fault == "drop_ballot_type":
        row.pop("ballot_type", None)
    elif fault == "drop_candidate":
        row.pop("candidate", None)
    elif fault == "drop_votes":
        row.pop("votes", None)
    elif fault == "negative_votes":
        row["votes"] = -rng.randint(1, 30)
    elif fault == "votes_float":
        row["votes"] = rng.randint(1, 40) + 0.5
    elif fault == "votes_string_numeric":
        row["votes"] = str(rng.randint(1, 40))
    elif fault == "votes_string_garbage":
        row["votes"] = "not-a-number"
    elif fault == "votes_bool":
        row["votes"] = rng.choice([True, False])
    elif fault == "bad_revision_string":
        row["revision"] = "2"
    elif fault == "bad_revision_negative":
        row["revision"] = -1
    else:  # pragma: no cover - guarded by callers
        raise ValueError("unknown fault: " + fault)
    rows.append(row)
    return rows


def _usable_for_expected(row):
    if not isinstance(row, dict):
        return False
    if not all(isinstance(row.get(k), str) and row.get(k)
               for k in ("precinct", "ballot_type", "candidate")):
        return False
    votes = row.get("votes")
    if not isinstance(votes, int) or isinstance(votes, bool) or votes < 0:
        return False
    revision = row.get("revision", 0)
    return isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0


def gen_payload(rng, revision_rate=0.35, conflict_rate=0.1, fault_rate=0.3,
                expected_mode="mixed", shuffle=True):
    """Generate a payload mixing clean structure, revisions and faults."""
    rows = clean_rows(rng)
    if rng.random() < conflict_rate:
        rows = add_revision_filing(rng, rows, conflict=True)
    elif rng.random() < revision_rate:
        rows = add_revision_filing(rng, rows, conflict=False)
    if rng.random() < fault_rate:
        for _ in range(rng.randint(1, 2)):
            rows = inject_fault(rng, rows, rng.choice(FAULTS))
    if rng.random() < 0.15:
        rows.append(rng.choice([None, 42, "corrupt", ["not", "a", "dict"]]))

    expected = expected_from_rows([r for r in rows if _usable_for_expected(r)])
    mode = expected_mode if expected_mode != "mixed" else rng.choice(
        ["exact", "perturbed", "partial", "extra", "absent"])
    if mode == "exact":
        pass
    elif mode == "perturbed" and expected:
        key = rng.choice(sorted(expected))
        expected[key] += rng.randint(1, 9)
    elif mode == "partial" and expected:
        expected.pop(rng.choice(sorted(expected)), None)
    elif mode == "extra":
        expected["P9"] = rng.randint(1, 40)
    elif mode == "absent":
        expected = {}

    payload = {"returns": rows, "expected_precinct_totals": expected}
    if shuffle:
        rng.shuffle(payload["returns"])
    return payload


def gen_clean_payload(rng, single_precinct=False, balanced=True, shuffle=True):
    """Payload with no faults and a single filing per precinct."""
    if single_precinct:
        precincts = [(rng.choice(COUNTIES), rng.choice(PRECINCTS))]
    else:
        precincts = [
            (rng.choice(COUNTIES), p) for p in rng.sample(PRECINCTS, rng.randint(1, 4))
        ]
    rows = clean_rows(rng, precincts=precincts)
    expected = expected_from_rows(rows)
    if not balanced and expected:
        key = rng.choice(sorted(expected))
        expected[key] += rng.randint(1, 7)
    payload = {"returns": rows, "expected_precinct_totals": expected}
    if shuffle:
        rng.shuffle(payload["returns"])
    return payload


def shuffled(payload, rng):
    """Deep-copied payload with rows permuted."""
    clone = copy.deepcopy(payload)
    if isinstance(clone.get("returns"), list):
        rng.shuffle(clone["returns"])
    return clone


def mixed_corpus(count, seed):
    rng = random.Random(seed)
    return [gen_payload(rng) for _ in range(count)]


def clean_corpus(count, seed, single_precinct=False, balanced=True):
    rng = random.Random(seed)
    return [gen_clean_payload(rng, single_precinct=single_precinct, balanced=balanced)
            for _ in range(count)]
