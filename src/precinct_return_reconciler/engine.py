"""Domain engine: fold announcements, materialise the hierarchy and verify
conservation.

Design choice (see README "Trade-offs"): the journal is an append-only event
log; every publication version is a *full recomputation* of that fold. Incremental
aggregation is deliberately avoided inside a version so that input permutation
can never change an interpretation. A snapshot cache in :mod:`journal` is the
only incremental optimisation, and it is verified against the recomputed fold.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .models import (
    BATCH,
    BLOCKING_REASONS,
    BALLOT_TYPE_CONSERVATION,
    CANDIDATE_CONSERVATION,
    COMPLETE,
    CONVERSION,
    CROSS_LEVEL_MISMATCH,
    HIERARCHY_GAP,
    INCOMPLETE,
    INGEST_CONFLICT,
    LEAF,
    MISSING_CHILD,
    MISSING_REPORT,
    PARTIAL_WITHDRAWAL,
    PROVISIONAL_PENDING,
    RENAME,
    RENAME_CYCLE,
    REPORTED_ZERO,
    REVISION,
    SUPERSEDED_REFERENCE,
    STATE,
    COUNTY,
    PRECINCT,
    WITHDRAWN,
    WITHDRAWAL,
    Announcement,
    IngestResult,
    Scope,
    VoteLine,
)

LeafKey = Tuple[str, str, str]  # county, precinct, ballot_type
PrecinctKey = Tuple[str, str]
CountyKey = str


# ---------------------------------------------------------------------------
# Fold state
# ---------------------------------------------------------------------------


@dataclass
class _LeafState:
    """The effective announcement governing one leaf, plus lifecycle flags."""

    effective: Optional[Announcement] = None
    withdrawn_by: Optional[Announcement] = None
    superseded_ids: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    # Precinct-scoped revisions mark every fan-out leaf with this.
    fanout: bool = False
    # Conversion whose target had no provisional result to convert.
    orphan_conversion: Optional[Announcement] = None
    # Set when a broader (precinct-scoped) revision superseded this leaf.
    superseded_by_scope: Optional[Announcement] = None


@dataclass
class _Fold:
    events: Dict[str, Announcement] = field(default_factory=dict)
    leaves: Dict[LeafKey, _LeafState] = field(default_factory=dict)
    # Scope -> expected total and ballot-type subtotal declarations.
    expected_totals: Dict[tuple, int] = field(default_factory=dict)
    expected_ballot: Dict[tuple, Dict[str, int]] = field(default_factory=dict)
    rename_hops: List[Tuple[int, str, str]] = field(default_factory=list)
    max_version: int = 0


# ---------------------------------------------------------------------------
# Event store with idempotent ingestion
# ---------------------------------------------------------------------------


class EventStore:
    """Append-only announcement log with idempotent duplicate semantics."""

    def __init__(self) -> None:
        self._fold = _Fold()

    @property
    def fold(self) -> _Fold:
        return self._fold

    def all_announcements(self) -> List[Announcement]:
        return list(self._fold.events.values())

    def versions(self) -> List[int]:
        return sorted({event.version for event in self._fold.events.values()})

    def ingest(self, announcement: Announcement) -> IngestResult:
        existing = self._fold.events.get(announcement.announcement_id)
        if existing is not None:
            if existing.checksum() == announcement.checksum():
                # Exact redelivery: idempotent no-op.
                return IngestResult("duplicate", announcement.announcement_id)
            return IngestResult(
                "conflict",
                announcement.announcement_id,
                (
                    {
                        "code": INGEST_CONFLICT,
                        "message": "same announcement_id reused with different body",
                        "announcement_id": announcement.announcement_id,
                    },
                ),
            )
        self._apply(announcement)
        self._fold.events[announcement.announcement_id] = announcement
        self._fold.max_version = max(self._fold.max_version, announcement.version)
        return IngestResult("accepted", announcement.announcement_id)

    def ingest_many(
        self, announcements: Iterable[Announcement]
    ) -> List[IngestResult]:
        return [self.ingest(event) for event in announcements]

    # -- internal fold application -----------------------------------------

    def _apply(self, event: Announcement) -> None:
        kind = event.kind
        if kind == RENAME:
            self._fold.rename_hops.append(
                (event.version, event.from_candidate, event.to_candidate)
            )
            self._record_expected(event.scope, event)
            return
        self._record_expected(event.scope, event)
        if kind == WITHDRAWAL:
            for key in self._target_leaves(event):
                state = self._fold.leaves.setdefault(key, _LeafState())
                state.withdrawn_by = event
                state.sources.append(event.announcement_id)
            return
        if kind == CONVERSION:
            for key in self._target_leaves(event):
                self._fold.leaves.setdefault(key, _LeafState())
        if event.scope.level == LEAF:
            self._apply_leaf_event(event)
        else:
            self._apply_scope_event(event)

    def _apply_leaf_event(self, event: Announcement) -> None:
        key = event.scope.leaf_key()
        state = self._fold.leaves.setdefault(key, _LeafState())
        if event.kind in (BATCH, REVISION):
            if state.effective is not None:
                state.superseded_ids.append(state.effective.announcement_id)
            state.effective = event
            state.withdrawn_by = None
            state.fanout = False
        elif event.kind == CONVERSION:
            # Provisional -> official: flip matching candidate lines in place.
            if state.effective is None:
                # Late conversion with nothing to convert: keep a trace so the
                # materialiser can flag a dangling reference.
                state.sources.append(event.announcement_id)
                state.orphan_conversion = event
                return
            converted = self._converted_lines(state.effective, event)
            state.effective = Announcement(
                announcement_id=state.effective.announcement_id,
                version=event.version,
                kind=state.effective.kind,
                scope=state.effective.scope,
                lines=converted,
                batch_id=state.effective.batch_id,
                supersedes=state.effective.supersedes,
                expected_total=state.effective.expected_total,
                expected_ballot_totals=state.effective.expected_ballot_totals,
                status=state.effective.status,
                published_at=state.effective.published_at,
            )
        state.sources.append(event.announcement_id)

    def _apply_scope_event(self, event: Announcement) -> None:
        """Precinct (or broader) revision/conversion fans out to leaves."""
        target_keys = self._target_leaves(event)
        if event.kind == REVISION:
            # A revision covering a whole precinct supersedes every leaf under
            # it; lines with an explicit ballot_type only touch that leaf.
            for key in target_keys:
                state = self._fold.leaves.setdefault(key, _LeafState())
                if state.effective is not None:
                    state.superseded_ids.append(state.effective.announcement_id)
                state.effective = None
                state.superseded_by_scope = event
                state.withdrawn_by = None
            if all(line.ballot_type is None for line in event.lines):
                # Aggregate lines: distribute onto a synthetic "_precinct" leaf
                # so totals remain attributable without inventing ballot types.
                synthetic_key = (
                    event.scope.county,
                    event.scope.precinct,
                    "_precinct",
                )
                state = self._fold.leaves.setdefault(synthetic_key, _LeafState())
                if state.effective is not None:
                    state.superseded_ids.append(state.effective.announcement_id)
                state.effective = event
                state.fanout = True
                state.withdrawn_by = None
                state.sources.append(event.announcement_id)
                # The synthetic leaf carries the supersession history for the
                # whole precinct revision.
                state.superseded_ids.extend(
                    sid
                    for key in target_keys
                    for sid in self._fold.leaves[key].superseded_ids
                    if sid not in state.superseded_ids
                )
            else:
                # Per-ballot-type lines replace their respective leaves.
                grouped: Dict[LeafKey, List[VoteLine]] = {}
                for line in event.lines:
                    bt = line.ballot_type
                    if bt is None:
                        continue
                    grouped.setdefault(
                        (event.scope.county, event.scope.precinct, bt), []
                    ).append(line)
                for key, lines in grouped.items():
                    leaf_event = Announcement(
                        announcement_id=f"{event.announcement_id}::{key[2]}",
                        version=event.version,
                        kind=REVISION,
                        scope=Scope(
                            state=event.scope.state,
                            county=key[0],
                            precinct=key[1],
                            ballot_type=key[2],
                        ),
                        lines=tuple(lines),
                        supersedes=event.announcement_id,
                        expected_total=event.expected_total,
                        expected_ballot_totals=event.expected_ballot_totals,
                        published_at=event.published_at,
                    )
                    state = self._fold.leaves.setdefault(key, _LeafState())
                    state.effective = leaf_event
                    state.fanout = True
                    state.sources.append(event.announcement_id)
        elif event.kind == CONVERSION:
            for key in target_keys:
                state = self._fold.leaves.get(key)
                if state is None or state.effective is None:
                    continue
                state.effective = Announcement(
                    announcement_id=state.effective.announcement_id,
                    version=event.version,
                    kind=state.effective.kind,
                    scope=state.effective.scope,
                    lines=self._converted_lines(state.effective, event),
                    batch_id=state.effective.batch_id,
                    supersedes=state.effective.supersedes,
                    expected_total=state.effective.expected_total,
                    expected_ballot_totals=state.effective.expected_ballot_totals,
                    status=state.effective.status,
                    published_at=state.effective.published_at,
                )
                state.sources.append(event.announcement_id)

    def _target_leaves(self, event: Announcement) -> List[LeafKey]:
        if event.scope.level == LEAF:
            return [event.scope.leaf_key()]
        known = [
            key
            for key in self._fold.leaves
            if key[0] == event.scope.county
            and key[1] == event.scope.precinct
        ]
        return sorted(known)

    @staticmethod
    def _converted_lines(
        current: Announcement, conversion: Announcement
    ) -> Tuple[VoteLine, ...]:
        flip = {line.candidate: line.votes for line in conversion.lines}
        result: List[VoteLine] = []
        for line in current.lines:
            if line.candidate in flip and line.status != "official":
                # The conversion either states the new official vote count or,
                # when votes are zero, simply certifies the existing count.
                new_votes = flip[line.candidate] or line.votes
                result.append(
                    VoteLine(
                        candidate=line.candidate,
                        votes=new_votes,
                        status="official",
                        ballot_type=line.ballot_type,
                    )
                )
            else:
                result.append(line)
        return tuple(result)

    def _record_expected(self, scope: Scope, event: Announcement) -> None:
        if event.expected_total is not None:
            self._fold.expected_totals[scope.as_tuple()] = event.expected_total
        if event.expected_ballot_totals:
            bucket = self._fold.expected_ballot.setdefault(scope.as_tuple(), {})
            bucket.update(dict(event.expected_ballot_totals))


# ---------------------------------------------------------------------------
# Materialisation
# ---------------------------------------------------------------------------


def _build_alias(fold: _Fold) -> Tuple[Dict[str, str], List[dict]]:
    """Resolve rename hops (old name -> current canonical name)."""
    hops = sorted(fold.rename_hops)  # by version, then pair
    parent: Dict[str, str] = {}
    for _version, old, new in hops:
        parent[old] = new
    alias: Dict[str, str] = {}
    problems: List[dict] = []
    for start in sorted(parent):
        seen = {start}
        node = start
        while node in parent:
            node = parent[node]
            if node in seen:
                problems.append(
                    {
                        "code": RENAME_CYCLE,
                        "message": f"rename cycle involving {start!r}",
                        "candidate": start,
                    }
                )
                node = start
                break
            seen.add(node)
        alias[start] = node
    return alias, problems


def _reason(code: str, message: str, **extra: Any) -> dict:
    entry = {"code": code, "message": message}
    entry.update(extra)
    return entry


class Reconciler:
    """Stateless recomputation over an :class:`EventStore`."""

    def __init__(self, state: str, hierarchy: List[dict]):
        self.state = state

        def _scope_of(node: dict) -> Scope:
            raw = node["scope"]
            return raw if isinstance(raw, Scope) else Scope(**raw)

        self._declared_scopes = {
            _scope_of(node).as_tuple(): node for node in hierarchy
        }
        # Seed expected declarations from the hierarchy.
        self._hierarchy_expected_total: Dict[tuple, int] = {}
        self._hierarchy_expected_ballot: Dict[tuple, Dict[str, int]] = {}
        for node in hierarchy:
            key = _scope_of(node).as_tuple()
            if node.get("expected_total") is not None:
                self._hierarchy_expected_total[key] = node["expected_total"]
            if node.get("expected_ballot_totals"):
                self._hierarchy_expected_ballot[key] = dict(
                    node["expected_ballot_totals"]
                )

    # -- public API ---------------------------------------------------------

    def materialise(
        self,
        store: EventStore,
        as_of_version: Optional[int] = None,
    ) -> dict:
        fold = self._project(store, as_of_version)
        alias, rename_problems = _build_alias(fold)
        leaves = self._materialise_leaves(fold, alias)
        precincts = self._group_materialise(
            fold, leaves, level=PRECINCT, alias=alias
        )
        counties = self._group_materialise(
            fold, precincts, level=COUNTY, alias=alias
        )
        state_node = self._materialise_state(fold, counties, alias)

        for problem in rename_problems:
            state_node["reasons"].append(problem)
            state_node["status"] = INCOMPLETE

        self._mark_publishable(state_node)
        observed_versions = sorted(
            {event.version for event in fold.events.values()}
        )
        return {
            "state": self.state,
            "as_of_version": as_of_version if as_of_version is not None else (
                max(observed_versions) if observed_versions else 0
            ),
            "observed_versions": observed_versions,
            "publishable": state_node["publishable"],
            "state_node": state_node,
            "counties": counties,
            "precincts": precincts,
            "leaves": leaves,
            "candidate_aliases": dict(sorted(alias.items())),
        }

    # -- version projection (replay) ---------------------------------------

    def _project(
        self, store: EventStore, as_of_version: Optional[int]
    ) -> _Fold:
        """Replay the log up to a version through a fresh fold."""
        projected = EventStore()
        events = sorted(
            store.all_announcements(),
            key=lambda event: (event.version, event.announcement_id),
        )
        for event in events:
            if as_of_version is not None and event.version > as_of_version:
                break
            projected.ingest(event)
        fold = projected.fold
        # Hierarchy expectations are configuration, valid at every version.
        for key, value in self._hierarchy_expected_total.items():
            fold.expected_totals.setdefault(key, value)
        for key, values in self._hierarchy_expected_ballot.items():
            fold.expected_ballot.setdefault(key, values)
        return fold

    # -- leaf materialisation ----------------------------------------------

    def _materialise_leaves(self, fold: _Fold, alias: Dict[str, str]) -> dict:
        declared_leaf_keys = {
            (key[1], key[2], key[3])
            for key in self._declared_scopes
            if key[1] is not None
            and key[2] is not None
            and key[3] is not None
        }
        all_keys = sorted(set(fold.leaves) | {k for k in declared_leaf_keys})
        out: Dict[str, dict] = {}
        for key in all_keys:
            county, precinct, ballot_type = key
            scope = Scope(
                state=self.state,
                county=county,
                precinct=precinct,
                ballot_type=ballot_type,
            )
            state = fold.leaves.get(key, _LeafState())
            node: Dict[str, Any] = {
                "node_id": self._node_id(scope.as_tuple()),
                "level": LEAF,
                "scope": scope.to_dict(),
                "status": INCOMPLETE,
                "candidate_totals": {},
                "ballot_type_totals": {ballot_type: 0},
                "total_votes": 0,
                "provisional_votes": 0,
                "flags": [],
                "reasons": [],
                "provenance": {
                    "effective_announcement": None,
                    "superseded": list(state.superseded_ids),
                    "sources": sorted(set(state.sources)),
                    "withdrawn_by": None,
                },
            }
            declared = key in declared_leaf_keys
            if not declared:
                node["reasons"].append(
                    _reason(
                        HIERARCHY_GAP,
                        "leaf observed in announcements but absent from declared hierarchy",
                        node_id=node["node_id"],
                    )
                )
            if state.withdrawn_by is not None:
                node["status"] = WITHDRAWN
                node["flags"].append("withdrawn")
                node["reasons"].append(
                    _reason(
                        PARTIAL_WITHDRAWAL,
                        "results withdrawn; prior votes are retracted, not zero",
                        node_id=node["node_id"],
                        withdrawn_by=state.withdrawn_by.announcement_id,
                    )
                )
                node["provenance"]["withdrawn_by"] = (
                    state.withdrawn_by.announcement_id
                )
                out[node["node_id"]] = node
                continue
            if state.effective is None:
                if state.orphan_conversion is not None:
                    node["flags"].append("missing_report")
                    node["reasons"].append(
                        _reason(
                            MISSING_REPORT,
                            "no announcement has ever reported this leaf",
                            node_id=node["node_id"],
                        )
                    )
                    node["reasons"].append(
                        _reason(
                            SUPERSEDED_REFERENCE,
                            "conversion arrived with no provisional result to convert",
                            node_id=node["node_id"],
                            announcement_id=state.orphan_conversion.announcement_id,
                        )
                    )
                    out[node["node_id"]] = node
                    continue
                if state.superseded_by_scope is not None:
                    # A broader revision replaced this leaf; its votes live on
                    # the replacement scope, so this is not a missing report.
                    node["status"] = WITHDRAWN
                    node["flags"].append("superseded_by_scope")
                    node["reasons"].append(
                        _reason(
                            PARTIAL_WITHDRAWAL,
                            "leaf superseded by a broader precinct revision",
                            node_id=node["node_id"],
                            superseded_by=state.superseded_by_scope.announcement_id,
                        )
                    )
                    out[node["node_id"]] = node
                    continue
                node["flags"].append("missing_report")
                node["reasons"].append(
                    _reason(
                        MISSING_REPORT,
                        "no announcement has ever reported this leaf",
                        node_id=node["node_id"],
                    )
                )
                out[node["node_id"]] = node
                continue

            event = state.effective
            node["provenance"]["effective_announcement"] = event.announcement_id
            totals: Dict[str, int] = {}
            provisional = 0
            for line in event.lines:
                canonical = alias.get(line.candidate, line.candidate)
                totals[canonical] = totals.get(canonical, 0) + line.votes
                if line.status == "provisional":
                    provisional += line.votes
            node["candidate_totals"] = dict(sorted(totals.items()))
            node["total_votes"] = sum(totals.values())
            node["provisional_votes"] = provisional
            node["ballot_type_totals"] = {ballot_type: node["total_votes"]}
            # An explicit zero announcement is one whose every line reported
            # zero; a leaf carrying provisional votes alongside a zero line is
            # not a "reported zero" overall.
            if event.lines and all(line.votes == 0 for line in event.lines):
                node["flags"].append("reported_zero")
                node["reasons"].append(
                    _reason(
                        REPORTED_ZERO,
                        "announcement explicitly reported zero votes "
                        "(distinct from a missing report)",
                        node_id=node["node_id"],
                    )
                )
            if provisional:
                node["flags"].append("provisional_pending")
                node["reasons"].append(
                    _reason(
                        PROVISIONAL_PENDING,
                        "leaf contains uncertified provisional ballots",
                        node_id=node["node_id"],
                        provisional_votes=provisional,
                    )
                )
            if getattr(state, "orphan_conversion", None) is not None:
                node["reasons"].append(
                    _reason(
                        SUPERSEDED_REFERENCE,
                        "conversion arrived with no provisional result to convert",
                        node_id=node["node_id"],
                        announcement_id=state.orphan_conversion.announcement_id,
                    )
                )
            self._check_conservation(fold, scope, node)
            node["status"] = (
                COMPLETE
                if not any(r["code"] in BLOCKING_REASONS for r in node["reasons"])
                else INCOMPLETE
            )
            out[node["node_id"]] = node
        return out

    def _check_conservation(
        self, fold: _Fold, scope: Scope, node: dict
    ) -> None:
        key = scope.as_tuple()
        total = node["total_votes"]
        expected_total = fold.expected_totals.get(key)
        if expected_total is not None and total != expected_total:
            node["reasons"].append(
                _reason(
                    CANDIDATE_CONSERVATION,
                    "candidate sum does not match declared expected total",
                    node_id=node["node_id"],
                    expected=expected_total,
                    actual=total,
                    difference=total - expected_total,
                )
            )
        expected_bt = fold.expected_ballot.get(key)
        if expected_bt:
            actual_bt = node["ballot_type_totals"]
            for ballot_type, expected_votes in sorted(expected_bt.items()):
                actual_votes = actual_bt.get(ballot_type, 0)
                if actual_votes != expected_votes:
                    node["reasons"].append(
                        _reason(
                            BALLOT_TYPE_CONSERVATION,
                            "ballot-type subtotal does not match declaration",
                            node_id=node["node_id"],
                            ballot_type=ballot_type,
                            expected=expected_votes,
                            actual=actual_votes,
                            difference=actual_votes - expected_votes,
                        )
                    )

    # -- parent materialisation --------------------------------------------

    def _group_materialise(
        self,
        fold: _Fold,
        children: Dict[str, dict],
        level: str,
        alias: Dict[str, str],
    ) -> Dict[str, dict]:
        groups: Dict[tuple, List[dict]] = {}
        for child in children.values():
            scope = child["scope"]
            if level == PRECINCT:
                group_key = (scope["state"], scope["county"], scope["precinct"])
            else:
                group_key = (scope["state"], scope["county"])
            groups.setdefault(group_key, []).append(child)

        # Declared parents with no observed children must still appear.
        for declared in self._declared_scopes:
            if level == PRECINCT and declared[2] is not None and declared[3] is None:
                groups.setdefault(declared[:3], [])
            if level == COUNTY and declared[1] is not None and declared[2] is None:
                groups.setdefault(declared[:2], [])

        out: Dict[str, dict] = {}
        for group_key in sorted(groups):
            kids = groups[group_key]
            if level == PRECINCT:
                scope = Scope(
                    state=group_key[0], county=group_key[1], precinct=group_key[2]
                )
            else:
                scope = Scope(state=group_key[0], county=group_key[1])
            node = self._aggregate(scope, level, kids, fold)
            out[node["node_id"]] = node
        return out

    def _aggregate(
        self, scope: Scope, level: str, kids: List[dict], fold: _Fold
    ) -> dict:
        candidate_totals: Dict[str, int] = {}
        ballot_totals: Dict[str, int] = {}
        provisional = 0
        reasons: List[dict] = []
        flags: List[str] = []
        sources: List[str] = []
        withdrawn_children: List[str] = []
        missing_children: List[str] = []
        incomplete_children: List[str] = []

        for child in kids:
            if "superseded_by_scope" in child["flags"]:
                # Replaced by a broader revision; the replacement scope carries
                # the votes, so this ghost leaf is neither missing nor withdrawn.
                sources.extend(child["provenance"]["sources"])
                continue
            for name, votes in child["candidate_totals"].items():
                candidate_totals[name] = candidate_totals.get(name, 0) + votes
            for name, votes in child["ballot_type_totals"].items():
                ballot_totals[name] = ballot_totals.get(name, 0) + votes
            provisional += child["provisional_votes"]
            sources.extend(child["provenance"]["sources"])
            if child["status"] == WITHDRAWN:
                withdrawn_children.append(child["node_id"])
            if child["status"] == INCOMPLETE:
                incomplete_children.append(child["node_id"])
            if "missing_report" in child["flags"]:
                missing_children.append(child["node_id"])

        total = sum(candidate_totals.values())
        declared_scope = scope.as_tuple() in self._declared_scopes
        if not declared_scope:
            reasons.append(
                _reason(
                    HIERARCHY_GAP,
                    f"{level} observed but absent from declared hierarchy",
                    node_id=self._node_id(scope.as_tuple()),
                )
            )
        if not kids:
            reasons.append(
                _reason(
                    MISSING_CHILD,
                    f"{level} has no child reports at all",
                    node_id=self._node_id(scope.as_tuple()),
                )
            )
        if missing_children:
            reasons.append(
                _reason(
                    MISSING_CHILD,
                    "one or more child scopes have never reported",
                    node_id=self._node_id(scope.as_tuple()),
                    children=sorted(missing_children),
                )
            )
        if withdrawn_children:
            flags.append("partial_withdrawal")
            reasons.append(
                _reason(
                    PARTIAL_WITHDRAWAL,
                    "some child results were withdrawn; totals exclude them",
                    node_id=self._node_id(scope.as_tuple()),
                    children=sorted(withdrawn_children),
                )
            )
        if incomplete_children:
            reasons.append(
                _reason(
                    MISSING_CHILD,
                    "child rollup is not yet stable",
                    node_id=self._node_id(scope.as_tuple()),
                    children=sorted(incomplete_children),
                )
            )

        expected_total = self._resolve_expected_total(fold, scope, kids)
        if expected_total is not None and total != expected_total:
            reasons.append(
                _reason(
                    CROSS_LEVEL_MISMATCH,
                    f"{level} total does not match declared expected total",
                    node_id=self._node_id(scope.as_tuple()),
                    expected=expected_total,
                    actual=total,
                    difference=total - expected_total,
                )
            )
        expected_bt = fold.expected_ballot.get(scope.as_tuple())
        if expected_bt:
            for ballot_type, expected_votes in sorted(expected_bt.items()):
                actual_votes = ballot_totals.get(ballot_type, 0)
                if actual_votes != expected_votes:
                    reasons.append(
                        _reason(
                            BALLOT_TYPE_CONSERVATION,
                            "ballot-type subtotal does not match declaration",
                            node_id=self._node_id(scope.as_tuple()),
                            ballot_type=ballot_type,
                            expected=expected_votes,
                            actual=actual_votes,
                            difference=actual_votes - expected_votes,
                        )
                    )
        if provisional:
            flags.append("provisional_pending")
            reasons.append(
                _reason(
                    PROVISIONAL_PENDING,
                    "provisional ballots remain uncertified below this node",
                    node_id=self._node_id(scope.as_tuple()),
                    provisional_votes=provisional,
                )
            )

        all_withdrawn = bool(kids) and all(
            child["status"] == WITHDRAWN for child in kids
        )
        has_blocking = any(r["code"] in BLOCKING_REASONS for r in reasons)
        if all_withdrawn:
            status = WITHDRAWN
        elif has_blocking:
            status = INCOMPLETE
        else:
            status = COMPLETE

        return {
            "node_id": self._node_id(scope.as_tuple()),
            "level": level,
            "scope": scope.to_dict(),
            "status": status,
            "candidate_totals": dict(sorted(candidate_totals.items())),
            "ballot_type_totals": dict(sorted(ballot_totals.items())),
            "total_votes": total,
            "provisional_votes": provisional,
            "flags": sorted(set(flags)),
            "reasons": reasons,
            "provenance": {
                "sources": sorted(set(sources)),
                "child_count": len(kids),
            },
        }

    def _materialise_state(
        self,
        fold: _Fold,
        counties: Dict[str, dict],
        alias: Dict[str, str],
    ) -> dict:
        scope = Scope(state=self.state)
        node = self._aggregate(scope, STATE, list(counties.values()), fold)
        # Every declared county must be present and stable.
        declared_counties = {
            key[1]
            for key in self._declared_scopes
            if key[1] is not None and key[2] is None
        }
        present = {child["scope"]["county"] for child in counties.values()}
        absent = sorted(declared_counties - present)
        if absent:
            node["reasons"].append(
                _reason(
                    MISSING_CHILD,
                    "declared counties have produced no rollup",
                    node_id=node["node_id"],
                    counties=absent,
                )
            )
            node["status"] = INCOMPLETE
        return node

    # -- helpers ------------------------------------------------------------

    def _resolve_expected_total(
        self, fold: _Fold, scope: Scope, kids: List[dict]
    ) -> Optional[int]:
        """Expected total for a parent.

        A direct declaration on the scope always wins. Otherwise expectations
        declared deeper in the tree (e.g. an announcement carries
        ``expected_total`` at leaf level while a precinct or county is being
        checked) are summed. Only leaf-level descendant declarations are
        counted so a precinct declaration and its leaf declarations can never
        be double counted; if any observed child lacks a benchmark the derived
        figure is treated as unavailable rather than silently partial.
        """
        direct = fold.expected_totals.get(scope.as_tuple())
        if direct is not None:
            return direct
        prefix = tuple(part for part in scope.as_tuple() if part is not None)
        observed_descendant_leaves = [
            full_key
            for key in fold.leaves
            for full_key in [(self.state,) + key]
            if full_key[: len(prefix)] == prefix
        ]
        descendant_leaf_keys = [
            key
            for key in observed_descendant_leaves
            if key in fold.expected_totals
        ]
        if not observed_descendant_leaves:
            return None
        # Every observed leaf under this scope must carry a benchmark,
        # otherwise a partial set of expectations would rig the comparison.
        if len(descendant_leaf_keys) != len(observed_descendant_leaves):
            return None
        return sum(fold.expected_totals[key] for key in descendant_leaf_keys)

    def _mark_publishable(self, state_node: dict) -> None:
        blocking = [
            reason
            for reason in state_node["reasons"]
            if reason["code"] in BLOCKING_REASONS
        ]
        state_node["publishable"] = not blocking and state_node["status"] == COMPLETE
        state_node["blocking_reason_codes"] = sorted(
            {reason["code"] for reason in blocking}
        )

    @staticmethod
    def _node_id(parts: tuple) -> str:
        return "/".join(str(part) for part in parts if part is not None)


# ---------------------------------------------------------------------------
# Legacy calculation shim (preserves the original engine.calculate contract)
# ---------------------------------------------------------------------------


def calculate(rows: List[dict], payload: dict) -> dict:
    """Original flat-sum behaviour, retained for backward compatibility."""
    candidates: Dict[str, int] = {}
    ballot_totals: Dict[str, int] = {}
    for row in rows:
        name = row["candidate"]
        votes = int(row["votes"])
        candidates[name] = candidates.get(name, 0) + votes
        ballot_totals[row["ballot_type"]] = (
            ballot_totals.get(row["ballot_type"], 0) + votes
        )
    actual = sum(candidates.values())
    expected = sum((payload.get("expected_precinct_totals") or {}).values())
    return {
        "candidate_totals": candidates,
        "ballot_type_totals": ballot_totals,
        "total_votes": actual,
        "balanced": actual == expected,
        "difference": actual - expected,
    }
