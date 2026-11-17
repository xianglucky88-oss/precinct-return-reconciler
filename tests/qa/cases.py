"""Curated audit cases with pinned expectations (known-gap registry).

Each case pins three layers of expectations, all hand-verified against the
reference rules in ``tests.qa.reference_model``:

* ``oracle``  - facts the independent reference model must report.
* ``sut``     - the outcome kind of the real pipeline (ok / raise + type).
* ``verdict`` - the exact differential classification. Cases pinned as
  ``divergent`` or ``sut_error`` document known gaps between the SUT and
  the audit reference model. If the SUT is ever fixed, these tests fail
  and the registry must be updated explicitly.
"""


def row(county, precinct, ballot_type, candidate, votes, revision=0):
    return {
        "county": county,
        "precinct": precinct,
        "ballot_type": ballot_type,
        "candidate": candidate,
        "votes": votes,
        "revision": revision,
    }


def _case(name, payload, oracle, sut, verdict, notes=()):
    return {
        "name": name,
        "payload": payload,
        "expect": {
            "oracle": oracle,
            "sut": sut,
            "verdict": verdict,
            "notes": list(notes),
        },
    }


_CLEAN_SINGLE_ROWS = [
    row("C1", "P1", "early", "A", 40),
    row("C1", "P1", "election_day", "A", 60),
    row("C1", "P1", "early", "B", 20),
]

CASES = [
    _case(
        "clean_single",
        {"returns": list(_CLEAN_SINGLE_ROWS), "expected_precinct_totals": {"P1": 120}},
        oracle={"total_votes": 120, "balanced": True, "invalid": [],
                "superseded": [], "warnings": [], "precinct_balanced": {"P1": True}},
        sut={"kind": "ok"},
        verdict={"status": "conformant", "reasons": []},
    ),
    _case(
        "clean_multi",
        {
            "returns": [
                row("C1", "P1", "early", "A", 10),
                row("C1", "P1", "election_day", "A", 5),
                row("C1", "P2", "early", "B", 7),
                row("C2", "P3", "absentee", "A", 1),
                row("C2", "P3", "early", "B", 2),
                row("C2", "P3", "election_day", "C", 3),
            ],
            "expected_precinct_totals": {"P1": 15, "P2": 7, "P3": 6},
        },
        oracle={"total_votes": 28, "balanced": True, "invalid": [],
                "superseded": [], "warnings": [],
                "precinct_balanced": {"P1": True, "P2": True, "P3": True}},
        sut={"kind": "ok"},
        verdict={"status": "conformant", "reasons": []},
    ),
    _case(
        "reordered_clean_single",
        {"returns": list(reversed(_CLEAN_SINGLE_ROWS)),
         "expected_precinct_totals": {"P1": 120}},
        oracle={"total_votes": 120, "balanced": True, "invalid": [],
                "superseded": [], "warnings": [], "precinct_balanced": {"P1": True}},
        sut={"kind": "ok"},
        verdict={"status": "conformant", "reasons": []},
    ),
    _case(
        "duplicate_revision",
        {
            "returns": [
                row("C1", "P1", "early", "A", 40, revision=1),
                row("C1", "P1", "election_day", "A", 60, revision=1),
                row("C1", "P1", "early", "B", 20, revision=1),
                row("C1", "P1", "early", "A", 45, revision=2),
                row("C1", "P1", "election_day", "A", 55, revision=2),
                row("C1", "P1", "early", "B", 25, revision=2),
            ],
            "expected_precinct_totals": {"P1": 125},
        },
        oracle={"total_votes": 125, "balanced": True, "invalid": [],
                "superseded": ["superseded_by_newer_filing"] * 3, "warnings": [],
                "precinct_balanced": {"P1": True}},
        sut={"kind": "ok"},
        verdict={"status": "divergent", "reasons": [
            "cause:sut_counts_superseded:superseded_by_newer_filing",
            "mismatch:balanced", "mismatch:ballot_type_totals",
            "mismatch:candidate_totals", "mismatch:difference", "mismatch:total_votes",
        ]},
        notes=["gap_analysis:fully_explained_by_excluded_rows"],
    ),
    _case(
        "conflicting_same_revision",
        {
            "returns": [
                row("C1", "P1", "early", "A", 40),
                row("C1", "P1", "early", "A", 45),
                row("C1", "P1", "election_day", "A", 60),
            ],
            "expected_precinct_totals": {"P1": 105},
        },
        oracle={"total_votes": 60, "balanced": False,
                "invalid": ["conflicting_same_revision:40", "conflicting_same_revision:45"],
                "superseded": [], "warnings": ["conflicting_same_revision"],
                "precinct_balanced": {"P1": False}},
        sut={"kind": "ok"},
        verdict={"status": "divergent", "reasons": [
            "cause:sut_counts_rows_oracle_rejects:conflicting_same_revision",
            "mismatch:ballot_type_totals", "mismatch:candidate_totals",
            "mismatch:difference", "mismatch:total_votes",
        ]},
        notes=["gap_analysis:fully_explained_by_excluded_rows"],
    ),
    _case(
        "exact_duplicate",
        {
            "returns": [
                row("C1", "P1", "early", "A", 40),
                row("C1", "P1", "early", "A", 40),
                row("C1", "P1", "election_day", "A", 60),
            ],
            "expected_precinct_totals": {"P1": 100},
        },
        oracle={"total_votes": 100, "balanced": True, "invalid": [],
                "superseded": ["exact_duplicate"], "warnings": ["exact_duplicate_rows"],
                "precinct_balanced": {"P1": True}},
        sut={"kind": "ok"},
        verdict={"status": "divergent", "reasons": [
            "cause:sut_counts_superseded:exact_duplicate",
            "mismatch:balanced", "mismatch:ballot_type_totals",
            "mismatch:candidate_totals", "mismatch:difference", "mismatch:total_votes",
        ]},
        notes=["gap_analysis:fully_explained_by_excluded_rows"],
    ),
    _case(
        "missing_ballot_type",
        {
            "returns": [
                row("C1", "P1", "early", "A", 40),
                row("C1", "P1", "election_day", "A", 60),
                {"county": "C1", "precinct": "P1", "candidate": "B", "votes": 20},
            ],
            "expected_precinct_totals": {"P1": 120},
        },
        oracle={"total_votes": 100, "balanced": False,
                "invalid": ["missing_field:ballot_type"], "superseded": [],
                "warnings": [], "precinct_balanced": {"P1": False}},
        sut={"kind": "raise", "error_type": "KeyError"},
        verdict={"status": "sut_error", "reasons": [
            "sut_cannot_classify:missing_field:ballot_type",
            "sut_loses_partial_results", "sut_raises:KeyError",
        ]},
    ),
    _case(
        "missing_votes",
        {
            "returns": [
                row("C1", "P1", "early", "A", 40),
                {"county": "C1", "precinct": "P1", "ballot_type": "early",
                 "candidate": "B"},
            ],
            "expected_precinct_totals": {"P1": 40},
        },
        oracle={"total_votes": 40, "balanced": True,
                "invalid": ["missing_field:votes"], "superseded": [],
                "warnings": [], "precinct_balanced": {"P1": True}},
        sut={"kind": "raise", "error_type": "KeyError"},
        verdict={"status": "sut_error", "reasons": [
            "sut_cannot_classify:missing_field:votes",
            "sut_loses_partial_results", "sut_raises:KeyError",
        ]},
    ),
    _case(
        "votes_string_garbage",
        {
            "returns": [
                row("C1", "P1", "early", "A", 40),
                row("C1", "P1", "early", "B", "abc"),
            ],
            "expected_precinct_totals": {"P1": 40},
        },
        oracle={"total_votes": 40, "balanced": True,
                "invalid": ["invalid_field_type:votes"], "superseded": [],
                "warnings": [], "precinct_balanced": {"P1": True}},
        sut={"kind": "raise", "error_type": "ValueError"},
        verdict={"status": "sut_error", "reasons": [
            "sut_cannot_classify:invalid_field_type:votes",
            "sut_loses_partial_results", "sut_raises:ValueError",
        ]},
    ),
    _case(
        "votes_float",
        {
            "returns": [
                row("C1", "P1", "early", "A", 40),
                row("C1", "P1", "early", "B", 1.5),
            ],
            "expected_precinct_totals": {"P1": 40},
        },
        oracle={"total_votes": 40, "balanced": True,
                "invalid": ["invalid_field_type:votes"], "superseded": [],
                "warnings": [], "precinct_balanced": {"P1": True}},
        sut={"kind": "ok"},
        verdict={"status": "divergent", "reasons": [
            "cause:sut_counts_rows_oracle_rejects:invalid_field_type",
            "mismatch:balanced", "mismatch:ballot_type_totals",
            "mismatch:candidate_totals", "mismatch:difference", "mismatch:total_votes",
        ]},
        notes=["gap_analysis:fully_explained_by_excluded_rows"],
    ),
    _case(
        "votes_bool",
        {
            "returns": [
                row("C1", "P1", "early", "A", 40),
                row("C1", "P1", "early", "B", True),
            ],
            "expected_precinct_totals": {"P1": 40},
        },
        oracle={"total_votes": 40, "balanced": True,
                "invalid": ["invalid_field_type:votes"], "superseded": [],
                "warnings": [], "precinct_balanced": {"P1": True}},
        sut={"kind": "ok"},
        verdict={"status": "divergent", "reasons": [
            "cause:sut_counts_rows_oracle_rejects:invalid_field_type",
            "mismatch:balanced", "mismatch:ballot_type_totals",
            "mismatch:candidate_totals", "mismatch:difference", "mismatch:total_votes",
        ]},
        notes=["gap_analysis:fully_explained_by_excluded_rows"],
    ),
    _case(
        "votes_string_numeric",
        {
            "returns": [
                row("C1", "P1", "early", "A", 40),
                row("C1", "P1", "early", "B", "7"),
            ],
            "expected_precinct_totals": {"P1": 40},
        },
        oracle={"total_votes": 40, "balanced": True,
                "invalid": ["invalid_field_type:votes"], "superseded": [],
                "warnings": [], "precinct_balanced": {"P1": True}},
        sut={"kind": "ok"},
        verdict={"status": "divergent", "reasons": [
            "cause:sut_counts_rows_oracle_rejects:invalid_field_type",
            "mismatch:balanced", "mismatch:ballot_type_totals",
            "mismatch:candidate_totals", "mismatch:difference", "mismatch:total_votes",
        ]},
        notes=["gap_analysis:fully_explained_by_excluded_rows"],
    ),
    _case(
        "non_dict_rows",
        {
            "returns": [
                row("C1", "P1", "early", "A", 40),
                42,
                "junk",
                None,
                row("C1", "P1", "election_day", "A", 60),
            ],
            "expected_precinct_totals": {"P1": 100},
        },
        oracle={"total_votes": 100, "balanced": True,
                "invalid": ["row_not_mapping:NoneType", "row_not_mapping:int",
                            "row_not_mapping:str"],
                "superseded": [], "warnings": [], "precinct_balanced": {"P1": True}},
        sut={"kind": "ok"},
        verdict={"status": "conformant", "reasons": []},
        notes=["sut_silently_drops_invalid_rows"],
    ),
    _case(
        "negative_votes",
        {
            "returns": [
                row("C1", "P1", "early", "A", 40),
                row("C1", "P1", "early", "B", -15),
                row("C1", "P1", "election_day", "A", 60),
            ],
            "expected_precinct_totals": {"P1": 100},
        },
        oracle={"total_votes": 100, "balanced": True,
                "invalid": ["negative_votes:-15"], "superseded": [],
                "warnings": [], "precinct_balanced": {"P1": True}},
        sut={"kind": "ok"},
        verdict={"status": "divergent", "reasons": [
            "cause:sut_counts_rows_oracle_rejects:negative_votes",
            "mismatch:balanced", "mismatch:ballot_type_totals",
            "mismatch:candidate_totals", "mismatch:difference", "mismatch:total_votes",
        ]},
        notes=["gap_analysis:fully_explained_by_excluded_rows"],
    ),
    _case(
        "zero_votes",
        {
            "returns": [
                row("C1", "P1", "early", "A", 0),
                row("C1", "P1", "election_day", "A", 60),
                row("C1", "P1", "early", "B", 0),
            ],
            "expected_precinct_totals": {"P1": 60},
        },
        oracle={"total_votes": 60, "balanced": True, "invalid": [],
                "superseded": [], "warnings": [], "precinct_balanced": {"P1": True}},
        sut={"kind": "ok"},
        verdict={"status": "conformant", "reasons": []},
    ),
    _case(
        "candidate_added_in_revision",
        {
            "returns": [
                row("C1", "P1", "early", "A", 40, revision=1),
                row("C1", "P1", "election_day", "A", 60, revision=1),
                row("C1", "P1", "early", "A", 45, revision=2),
                row("C1", "P1", "election_day", "A", 55, revision=2),
                row("C1", "P1", "early", "C", 7, revision=2),
            ],
            "expected_precinct_totals": {"P1": 107},
        },
        oracle={"total_votes": 107, "balanced": True, "invalid": [],
                "superseded": ["superseded_by_newer_filing"] * 2, "warnings": [],
                "precinct_balanced": {"P1": True}},
        sut={"kind": "ok"},
        verdict={"status": "divergent", "reasons": [
            "cause:sut_counts_superseded:superseded_by_newer_filing",
            "mismatch:balanced", "mismatch:ballot_type_totals",
            "mismatch:candidate_totals", "mismatch:difference", "mismatch:total_votes",
        ]},
        notes=["gap_analysis:fully_explained_by_excluded_rows"],
    ),
    _case(
        "candidate_only_in_one_precinct",
        {
            "returns": [
                row("C1", "P1", "early", "A", 10),
                row("C1", "P2", "early", "Z", 5),
            ],
            "expected_precinct_totals": {"P1": 10, "P2": 5},
        },
        oracle={"total_votes": 15, "balanced": True, "invalid": [],
                "superseded": [], "warnings": [],
                "precinct_balanced": {"P1": True, "P2": True}},
        sut={"kind": "ok"},
        verdict={"status": "conformant", "reasons": []},
    ),
    _case(
        "expected_mismatch",
        {"returns": list(_CLEAN_SINGLE_ROWS), "expected_precinct_totals": {"P1": 127}},
        oracle={"total_votes": 120, "balanced": False, "invalid": [],
                "superseded": [], "warnings": [], "precinct_balanced": {"P1": False}},
        sut={"kind": "ok"},
        verdict={"status": "conformant", "reasons": []},
    ),
    _case(
        "expected_offsetting",
        {
            "returns": [
                row("C1", "P1", "early", "A", 40),
                row("C1", "P1", "election_day", "A", 25),
                row("C1", "P2", "early", "B", 55),
            ],
            "expected_precinct_totals": {"P1": 70, "P2": 50},
        },
        oracle={"total_votes": 120, "balanced": False, "invalid": [],
                "superseded": [], "warnings": [],
                "precinct_balanced": {"P1": False, "P2": False}},
        sut={"kind": "ok"},
        verdict={"status": "divergent", "reasons": [
            "cause:balanced_granularity:sut_global_vs_oracle_per_precinct",
            "mismatch:balanced",
        ]},
    ),
    _case(
        "expected_unknown_precinct",
        {"returns": list(_CLEAN_SINGLE_ROWS),
         "expected_precinct_totals": {"P1": 120, "P9": 30}},
        oracle={"total_votes": 120, "balanced": False, "invalid": [],
                "superseded": [], "warnings": ["expected_for_unknown_precinct"],
                "precinct_balanced": {"P1": True, "P9": False}},
        sut={"kind": "ok"},
        verdict={"status": "conformant", "reasons": []},
        notes=["oracle_warnings:expected_for_unknown_precinct"],
    ),
    _case(
        "expected_missing_precinct",
        {
            "returns": [
                row("C1", "P1", "early", "A", 10),
                row("C1", "P2", "early", "B", 20),
            ],
            "expected_precinct_totals": {"P1": 10},
        },
        oracle={"total_votes": 30, "balanced": False, "invalid": [],
                "superseded": [], "warnings": ["precinct_missing_expected_total"],
                "precinct_balanced": {"P1": True, "P2": False}},
        sut={"kind": "ok"},
        verdict={"status": "conformant", "reasons": []},
        notes=["oracle_warnings:precinct_missing_expected_total"],
    ),
    _case(
        "returns_not_list",
        {"returns": {"P1": []}, "expected_precinct_totals": {}},
        oracle={"total_votes": 0, "balanced": True, "invalid": [],
                "superseded": [], "warnings": [], "precinct_balanced": {},
                "input_errors": ["returns_not_list"]},
        sut={"kind": "raise", "error_type": "ValueError"},
        verdict={"status": "sut_error", "reasons": ["sut_raises:ValueError"]},
    ),
    _case(
        "returns_missing",
        {},
        oracle={"total_votes": 0, "balanced": True, "invalid": [],
                "superseded": [], "warnings": [], "precinct_balanced": {}},
        sut={"kind": "ok"},
        verdict={"status": "conformant", "reasons": []},
    ),
    _case(
        "expected_totals_wrong_type",
        {"returns": [row("C1", "P1", "early", "A", 40)],
         "expected_precinct_totals": [40]},
        oracle={"total_votes": 40, "balanced": False, "invalid": [],
                "superseded": [],
                "warnings": ["expected_totals_not_mapping",
                             "precinct_missing_expected_total"],
                "precinct_balanced": {"P1": False}},
        sut={"kind": "raise", "error_type": "AttributeError"},
        verdict={"status": "sut_error", "reasons": [
            "sut_loses_partial_results", "sut_raises:AttributeError",
        ]},
    ),
    _case(
        "expected_invalid_entry",
        {"returns": list(_CLEAN_SINGLE_ROWS),
         "expected_precinct_totals": {"P1": 120, "P2": "90", "P3": -4}},
        oracle={"total_votes": 120, "balanced": True, "invalid": [],
                "superseded": [],
                "warnings": ["invalid_expected_total_entry"] * 2,
                "precinct_balanced": {"P1": True}},
        sut={"kind": "raise", "error_type": "TypeError"},
        verdict={"status": "sut_error", "reasons": [
            "sut_loses_partial_results", "sut_raises:TypeError",
        ]},
    ),
    _case(
        # Partial success and inconsistency in one payload: P1, P2 and P4
        # reconcile against expectations while P3 does not; the payload also
        # carries a superseded filing, a negative-vote row, a structurally
        # invalid row and a non-mapping row. The SUT cannot produce partial
        # results at all (it crashes on the missing ballot_type).
        "combined_mixed",
        {
            "returns": [
                row("C1", "P1", "early", "A", 40, revision=2),
                row("C1", "P1", "election_day", "A", 60, revision=2),
                row("C1", "P1", "early", "B", 20, revision=2),
                row("C1", "P2", "early", "A", 10, revision=1),
                row("C1", "P2", "early", "A", 12, revision=3),
                row("C1", "P2", "election_day", "B", 5, revision=3),
                row("C2", "P3", "early", "A", 50),
                row("C2", "P3", "early", "B", -9),
                row("C2", "P3", "election_day", "A", 30),
                row("C2", "P4", "election_day", "A", 0),
                {"county": "C2", "precinct": "P4", "candidate": "C", "votes": 3},
                "corrupted",
            ],
            "expected_precinct_totals": {"P1": 120, "P2": 17, "P3": 88, "P4": 0},
        },
        oracle={"total_votes": 217, "balanced": False,
                "invalid": ["missing_field:ballot_type", "negative_votes:-9",
                            "row_not_mapping:str"],
                "superseded": ["superseded_by_newer_filing"],
                "warnings": [],
                "precinct_balanced": {"P1": True, "P2": True, "P3": False,
                                      "P4": True}},
        sut={"kind": "raise", "error_type": "KeyError"},
        verdict={"status": "sut_error", "reasons": [
            "sut_cannot_classify:missing_field:ballot_type",
            "sut_cannot_classify:negative_votes:-9",
            "sut_cannot_classify:row_not_mapping:str",
            "sut_loses_partial_results", "sut_raises:KeyError",
        ]},
    ),
    _case(
        # Same mixture of partial success (P1, P2 reconcile) and
        # inconsistency (P3 unbalanced, superseded filing, negative votes)
        # but without crash-triggering rows, so the SUT returns a result
        # whose totals silently absorb the old revision and the negatives.
        "combined_mixed_no_crash",
        {
            "returns": [
                row("C1", "P1", "early", "A", 40),
                row("C1", "P1", "election_day", "A", 60),
                row("C1", "P1", "early", "B", 20),
                row("C1", "P2", "early", "A", 10, revision=1),
                row("C1", "P2", "early", "A", 12, revision=2),
                row("C1", "P2", "election_day", "B", 5, revision=2),
                row("C2", "P3", "early", "A", 50),
                row("C2", "P3", "early", "B", -9),
                row("C2", "P3", "election_day", "A", 30),
            ],
            "expected_precinct_totals": {"P1": 120, "P2": 17, "P3": 88},
        },
        oracle={"total_votes": 217, "balanced": False,
                "invalid": ["negative_votes:-9"],
                "superseded": ["superseded_by_newer_filing"],
                "warnings": [],
                "precinct_balanced": {"P1": True, "P2": True, "P3": False}},
        sut={"kind": "ok"},
        verdict={"status": "divergent", "reasons": [
            "cause:sut_counts_rows_oracle_rejects:negative_votes",
            "cause:sut_counts_superseded:superseded_by_newer_filing",
            "mismatch:ballot_type_totals", "mismatch:candidate_totals",
            "mismatch:difference", "mismatch:total_votes",
        ]},
        notes=["gap_analysis:fully_explained_by_excluded_rows"],
    ),
    _case(
        # Candidates deliberately arrive in reverse alphabetical order so
        # that report-ordering mutations (unsorted maps) are detectable.
        "ordering_probe",
        {
            "returns": [
                row("C1", "P1", "early", "C", 5),
                row("C1", "P1", "early", "A", 10),
                row("C1", "P1", "election_day", "B", 7),
            ],
            "expected_precinct_totals": {"P1": 22},
        },
        oracle={"total_votes": 22, "balanced": True, "invalid": [],
                "superseded": [], "warnings": [], "precinct_balanced": {"P1": True}},
        sut={"kind": "ok"},
        verdict={"status": "conformant", "reasons": []},
    ),
]

CASES_BY_NAME = {case["name"]: case for case in CASES}

# Cases combined_mixed and combined_mixed_no_crash must simultaneously show
# partial success and inconsistency; this list is asserted by the tests.
COMBINED_CASES = ("combined_mixed", "combined_mixed_no_crash")


def all_cases():
    return list(CASES)


def get_case(name):
    return CASES_BY_NAME[name]
