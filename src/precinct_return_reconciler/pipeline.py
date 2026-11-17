"""Orchestration boundary: payload -> idempotent ingest -> versioned output.

Also exposes replay helpers so callers can rebuild any historical publication
from the same event stream.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .engine import EventStore, Reconciler
from .models import (
    INGEST_CONFLICT,
    PRECINCT,
    SCHEMA_VERSION,
    Announcement,
    IngestResult,
    canonical_json,
)
from .parser import load_payload
from .report import build_result


class Pipeline:
    """Stateful wrapper: ingest once, replay any version many times."""

    def __init__(self, state: str, hierarchy: List[dict]):
        self.state = state
        self.hierarchy = hierarchy
        self.store = EventStore()
        self.reconciler = Reconciler(state, hierarchy)

    def ingest(self, announcements: List[Announcement]) -> List[IngestResult]:
        return self.store.ingest_many(announcements)

    def publish(
        self,
        as_of_version: Optional[int] = None,
        include_history: bool = False,
    ) -> Dict[str, Any]:
        materialisation = self.reconciler.materialise(
            self.store, as_of_version=as_of_version
        )
        version = materialisation["as_of_version"]
        publication = {
            "schema_version": SCHEMA_VERSION,
            "publication_version": version,
            "replayable_from_version": version,
            "publishable": materialisation["publishable"],
            "materialisation": materialisation,
            "source_checksum": canonical_json(
                sorted(
                    (
                        event.identity_body()
                        for event in self.store.all_announcements()
                        if event.version <= version
                    ),
                    key=canonical_json,
                )
            ),
        }
        if include_history:
            publication["version_history"] = self._version_history(
                materialisation["observed_versions"]
            )
        return publication

    def _version_history(self, versions: List[int]) -> List[dict]:
        history = []
        for version in versions:
            materialisation = self.reconciler.materialise(
                self.store, as_of_version=version
            )
            sources = sorted(
                {
                    source
                    for node in list(materialisation["leaves"].values())
                    for source in node["provenance"]["sources"]
                }
            )
            history.append(
                {
                    "version": version,
                    "publishable": materialisation["publishable"],
                    "state_status": materialisation["state_node"]["status"],
                    "state_total_votes": materialisation["state_node"][
                        "total_votes"
                    ],
                    "blocking_reason_codes": materialisation["state_node"][
                        "blocking_reason_codes"
                    ],
                    "source_announcement_ids": sources,
                }
            )
        return history


def run(payload: dict, *, as_of_version: Optional[int] = None,
        include_history: bool = False) -> dict:
    """Functional entry point used by the CLI and legacy tests."""
    normalised = load_payload(payload)
    pipeline = Pipeline(normalised["state"], normalised["hierarchy"])
    ingest_results = pipeline.ingest(normalised["announcements"])
    conflicts = [
        {
            "announcement_id": result.announcement_id,
            "reasons": list(result.reasons),
        }
        for result in ingest_results
        if result.status == "conflict"
    ]
    publication = pipeline.publish(
        as_of_version=as_of_version, include_history=include_history
    )
    publication["ingest_report"] = {
        "accepted": sum(1 for r in ingest_results if r.status == "accepted"),
        "duplicate": sum(1 for r in ingest_results if r.status == "duplicate"),
        "conflict": sum(1 for r in ingest_results if r.status == "conflict"),
        "conflicts": conflicts,
    }
    if conflicts:
        # Surface conflicts on the state node so a partial county failure can
        # never masquerade as a complete state result.
        state_node = publication["materialisation"]["state_node"]
        for conflict in conflicts:
            state_node["reasons"].append(
                {
                    "code": INGEST_CONFLICT,
                    "message": "announcement id reused with a different body",
                    **conflict,
                }
            )
        state_node["status"] = "incomplete"
        state_node["blocking_reason_codes"] = sorted(
            set(state_node["blocking_reason_codes"]) | {INGEST_CONFLICT}
        )
        state_node["publishable"] = False
        publication["publishable"] = False

    # Backward-compatible flat summary keys for the original engine contract.
    state_node = publication["materialisation"]["state_node"]
    publication["candidate_totals"] = state_node["candidate_totals"]
    publication["ballot_type_totals"] = state_node["ballot_type_totals"]
    publication["total_votes"] = state_node["total_votes"]
    publication["balanced"] = publication["publishable"]
    publication["difference"] = 0
    expected = sum(
        node.get("expected_total") or 0
        for node in normalised["hierarchy"]
        if node["scope"].level == PRECINCT
    )
    if expected:
        publication["difference"] = state_node["total_votes"] - expected
    return build_result("precinct-return-reconciler", publication)
