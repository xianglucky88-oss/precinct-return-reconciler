"""Immutable domain contracts for the versioned return-reconciliation pipeline.

The system is event-sourced: every fact arrives as an :class:`Announcement`.
Nothing in this module mutates after construction, which makes folds and
replays safe to cache and share.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 2

# Announcement kinds.
BATCH = "batch"                # first arrival of a counted batch
REVISION = "revision"          # corrected re-announcement of a scope
WITHDRAWAL = "withdrawal"      # retraction: old votes are NOT zero votes
CONVERSION = "conversion"      # provisional ballots becoming official
RENAME = "rename"              # candidate display-name change
KINDS = frozenset({BATCH, REVISION, WITHDRAWAL, CONVERSION, RENAME})

# Per-line certification states.
OFFICIAL = "official"
PROVISIONAL = "provisional"
LINE_STATUSES = frozenset({OFFICIAL, PROVISIONAL})

# Hierarchy levels, ordered from coarsest to finest.
STATE = "state"
COUNTY = "county"
PRECINCT = "precinct"
LEAF = "leaf"  # one ballot type inside one precinct
LEVEL_ORDER = (STATE, COUNTY, PRECINCT, LEAF)
LEVEL_RANK = {level: index for index, level in enumerate(LEVEL_ORDER)}

# Node rollup states.
COMPLETE = "complete"
INCOMPLETE = "incomplete"
WITHDRAWN = "withdrawn"

# Reason codes. Blocking codes prevent a state-level publication from being
# certified; informational codes explain interpretation without blocking.
CANDIDATE_CONSERVATION = "CANDIDATE_CONSERVATION"
BALLOT_TYPE_CONSERVATION = "BALLOT_TYPE_CONSERVATION"
CROSS_LEVEL_MISMATCH = "CROSS_LEVEL_MISMATCH"
MISSING_REPORT = "MISSING_REPORT"
MISSING_CHILD = "MISSING_CHILD"
REPORTED_ZERO = "REPORTED_ZERO"
PROVISIONAL_PENDING = "PROVISIONAL_PENDING"
PARTIAL_WITHDRAWAL = "PARTIAL_WITHDRAWAL"
HIERARCHY_GAP = "HIERARCHY_GAP"
INGEST_CONFLICT = "INGEST_CONFLICT"
RENAME_CYCLE = "RENAME_CYCLE"
SUPERSEDED_REFERENCE = "SUPERSEDED_REFERENCE"

BLOCKING_REASONS = frozenset(
    {
        CANDIDATE_CONSERVATION,
        BALLOT_TYPE_CONSERVATION,
        CROSS_LEVEL_MISMATCH,
        MISSING_REPORT,
        MISSING_CHILD,
        HIERARCHY_GAP,
        INGEST_CONFLICT,
        PROVISIONAL_PENDING,
        RENAME_CYCLE,
        SUPERSEDED_REFERENCE,
    }
)
INFORMATIONAL_REASONS = frozenset({REPORTED_ZERO, PARTIAL_WITHDRAWAL})


def canonical_json(obj: Any) -> str:
    """Deterministic JSON encoding used for identity checks and checksums."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def checksum(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VoteLine:
    """One candidate's votes inside an announcement."""

    candidate: str
    votes: int
    status: str = OFFICIAL
    # Set when a precinct-scoped announcement fans out across ballot types.
    ballot_type: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "candidate": self.candidate,
            "votes": self.votes,
            "status": self.status,
            "ballot_type": self.ballot_type,
        }


@dataclass(frozen=True)
class Scope:
    """A hierarchy address. Finer components may be ``None``; a scope may
    never skip a level (a ballot type without a precinct is invalid)."""

    state: str
    county: Optional[str] = None
    precinct: Optional[str] = None
    ballot_type: Optional[str] = None

    @property
    def level(self) -> str:
        if self.ballot_type is not None:
            return LEAF
        if self.precinct is not None:
            return PRECINCT
        if self.county is not None:
            return COUNTY
        return STATE

    def as_tuple(self) -> Tuple[str, Optional[str], Optional[str], Optional[str]]:
        return (self.state, self.county, self.precinct, self.ballot_type)

    def leaf_key(self) -> Tuple[str, str, str]:
        return (self.county, self.precinct, self.ballot_type)  # type: ignore[return-value]

    def path_components(self) -> Tuple[Tuple[str, str], ...]:
        parts = [(STATE, self.state)]
        if self.county is not None:
            parts.append((COUNTY, self.county))
        if self.precinct is not None:
            parts.append((PRECINCT, self.precinct))
        if self.ballot_type is not None:
            parts.append((LEAF, self.ballot_type))
        return tuple(parts)

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "county": self.county,
            "precinct": self.precinct,
            "ballot_type": self.ballot_type,
        }


@dataclass(frozen=True)
class Announcement:
    """A single versioned fact in the event stream."""

    announcement_id: str
    version: int
    kind: str
    scope: Scope
    lines: Tuple[VoteLine, ...] = field(default_factory=tuple)
    batch_id: Optional[str] = None
    supersedes: Optional[str] = None
    expected_total: Optional[int] = None
    expected_ballot_totals: Mapping[str, int] = field(default_factory=dict)
    status: Optional[str] = None
    from_candidate: Optional[str] = None
    to_candidate: Optional[str] = None
    published_at: str = ""

    def identity_body(self) -> dict:
        """Everything that defines the announcement's meaning. Metadata that
        may legitimately differ on redelivery is excluded."""
        return {
            "announcement_id": self.announcement_id,
            "version": self.version,
            "kind": self.kind,
            "scope": self.scope.as_tuple(),
            "lines": [line.to_dict() for line in self.lines],
            "batch_id": self.batch_id,
            "supersedes": self.supersedes,
            "expected_total": self.expected_total,
            "expected_ballot_totals": dict(self.expected_ballot_totals),
            "status": self.status,
            "from_candidate": self.from_candidate,
            "to_candidate": self.to_candidate,
        }

    def checksum(self) -> str:
        return checksum(self.identity_body())


@dataclass(frozen=True)
class IngestResult:
    status: str  # accepted | duplicate | conflict | rejected
    announcement_id: Optional[str]
    reasons: tuple = ()
