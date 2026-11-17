"""Shared pinned-expectation checks.

Used by the differential tests and by the mutation runner's detection
routine. Every failure is a deterministic, human-readable string.
"""

from . import cases as _cases
from . import generators, harness, reference_model


def oracle_fact_failures(expected, oracle_result, prefix):
    """Compare pinned oracle facts against an actual oracle result."""
    failures = []

    def check(key, actual):
        if key in expected and expected[key] != actual:
            failures.append("{}:{}:expected={!r}:actual={!r}".format(
                prefix, key, expected[key], actual))

    check("total_votes", oracle_result["total_votes"])
    check("balanced", oracle_result["balanced"])
    check("precinct_balanced", oracle_result["precinct_balanced"])
    if "invalid" in expected:
        actual = sorted(
            "{}:{}".format(e["reason"], e["detail"])
            for e in oracle_result["invalid_records"]
        )
        check("invalid", actual)
    if "superseded" in expected:
        actual = sorted(e["reason"] for e in oracle_result["superseded_records"])
        check("superseded", actual)
    if "warnings" in expected:
        actual = sorted(w["code"] for w in oracle_result["warnings"])
        check("warnings", actual)
    if "input_errors" in expected:
        actual = sorted(e["reason"] for e in oracle_result["input_errors"])
        check("input_errors", actual)
    return failures


def case_failures(case, sut=None, oracle=None):
    """Run one curated case and check every pinned expectation layer."""
    result = harness.compare(case["payload"], sut=sut, oracle=oracle)
    expect = case["expect"]
    prefix = "case:" + case["name"]
    failures = []

    # Layer 1: SUT outcome kind.
    sut_expect = expect["sut"]
    sut_actual = result["sut"]
    if sut_actual["kind"] != sut_expect["kind"]:
        failures.append("{}:sut.kind:expected={}:actual={}".format(
            prefix, sut_expect["kind"], sut_actual["kind"]))
    elif sut_expect["kind"] == "raise" and sut_actual.get("error_type") != sut_expect.get("error_type"):
        failures.append("{}:sut.error:expected={}:actual={}".format(
            prefix, sut_expect.get("error_type"), sut_actual.get("error_type")))

    # Layer 2: oracle facts.
    failures.extend(oracle_fact_failures(expect["oracle"], result["oracle"], prefix))

    # Layer 3: differential verdict.
    verdict_expect = expect["verdict"]
    verdict_actual = result["verdict"]
    if verdict_actual["status"] != verdict_expect["status"]:
        failures.append("{}:verdict.status:expected={}:actual={}".format(
            prefix, verdict_expect["status"], verdict_actual["status"]))
    if verdict_actual["reasons"] != verdict_expect["reasons"]:
        failures.append("{}:verdict.reasons:expected={!r}:actual={!r}".format(
            prefix, verdict_expect["reasons"], verdict_actual["reasons"]))
    for note in expect.get("notes", []):
        if note not in verdict_actual["notes"]:
            failures.append("{}:verdict.notes:missing={}".format(prefix, note))

    # Layer 4: the oracle result must always satisfy its own conservation
    # invariants, and a successful SUT result must keep the report envelope.
    for violation in reference_model.check_conservation(result["oracle"]):
        failures.append("{}:conservation:{}".format(prefix, violation))
    if sut_actual["kind"] == "ok":
        output = sut_actual["output"]
        if output.get("project") != harness.PROJECT:
            failures.append("{}:envelope:project={!r}".format(prefix, output.get("project")))
        if output.get("status") != "ok":
            failures.append("{}:envelope:status={!r}".format(prefix, output.get("status")))
    return failures


def generated_failures(sut=None, oracle=None, seed=20261117):
    """Property checks over the generated corpus (mutation detection set)."""
    import random

    failures = []
    oracle = oracle or reference_model

    for index, payload in enumerate(generators.mixed_corpus(10, seed)):
        result = oracle.evaluate(payload)
        for violation in reference_model.check_conservation(result):
            failures.append("generated:mixed:{}:conservation:{}".format(index, violation))
        shuffled = generators.shuffled(payload, random.Random(seed + index))
        if oracle.reorder_projection(oracle.evaluate(shuffled)) != oracle.reorder_projection(result):
            failures.append("generated:mixed:{}:reorder_invariance".format(index))

    for index, payload in enumerate(generators.clean_corpus(8, seed + 1, single_precinct=True)):
        verdict = harness.compare(payload, sut=sut, oracle=oracle)["verdict"]
        if verdict["status"] != "conformant":
            failures.append("generated:clean:{}:expected_conformant:got={}:{}".format(
                index, verdict["status"], verdict["reasons"]))
    return failures


def detection_failures(sut=None, oracle=None):
    """Full detection routine: curated cases + generated corpus."""
    failures = []
    for case in _cases.all_cases():
        failures.extend(case_failures(case, sut=sut, oracle=oracle))
    failures.extend(generated_failures(sut=sut, oracle=oracle))
    return failures
