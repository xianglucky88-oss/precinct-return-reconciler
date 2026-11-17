"""Durable append-only journal with crash recovery.

Each announcement is written as one checksummed JSON line:

    <sha256-hex> <canonical-json>

A torn write at the tail (crash mid-write) fails checksum verification and is
truncated on reopen; fully committed lines never disappear. Snapshots cache a
materialised version but are always verified against the recomputed fold, so a
corrupt snapshot can never change a result.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .engine import EventStore, Reconciler
from .models import Announcement, Scope, VoteLine, canonical_json, checksum


class JournalCorruption(RuntimeError):
    pass


@dataclass
class RecoveryReport:
    loaded: int
    truncated_tail_lines: int
    snapshot_restored: Optional[int]
    warnings: List[str]


def _announcement_to_json(event: Announcement) -> dict:
    return {
        "announcement_id": event.announcement_id,
        "version": event.version,
        "kind": event.kind,
        "scope": event.scope.to_dict(),
        "lines": [line.to_dict() for line in event.lines],
        "batch_id": event.batch_id,
        "supersedes": event.supersedes,
        "expected_total": event.expected_total,
        "expected_ballot_totals": dict(event.expected_ballot_totals),
        "status": event.status,
        "from_candidate": event.from_candidate,
        "to_candidate": event.to_candidate,
        "published_at": event.published_at,
    }


def _announcement_from_json(raw: dict) -> Announcement:
    scope = Scope(**raw["scope"])
    lines = tuple(
        VoteLine(
            candidate=line["candidate"],
            votes=line["votes"],
            status=line["status"],
            ballot_type=line.get("ballot_type"),
        )
        for line in raw["lines"]
    )
    return Announcement(
        announcement_id=raw["announcement_id"],
        version=raw["version"],
        kind=raw["kind"],
        scope=scope,
        lines=lines,
        batch_id=raw.get("batch_id"),
        supersedes=raw.get("supersedes"),
        expected_total=raw.get("expected_total"),
        expected_ballot_totals=raw.get("expected_ballot_totals", {}),
        status=raw.get("status"),
        from_candidate=raw.get("from_candidate"),
        to_candidate=raw.get("to_candidate"),
        published_at=raw.get("published_at", ""),
    )


class Journal:
    """File-backed :class:`EventStore`."""

    LOG_NAME = "events.log"
    SNAPSHOT_NAME = "snapshot.json"

    def __init__(self, directory: str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.log_path = self.directory / self.LOG_NAME
        self.snapshot_path = self.directory / self.SNAPSHOT_NAME
        self.store = EventStore()
        self.recovery: Optional[RecoveryReport] = None

    # -- durability ---------------------------------------------------------

    def append(self, event: Announcement):
        """Idempotent durable append. Re-opening after a crash replays it."""
        body = canonical_json(_announcement_to_json(event))
        line = f"{checksum(body)} {body}\n"
        # O_APPEND plus a single write gives atomic-per-line semantics on
        # POSIX for writes under PIPE_BUF-sized payloads; fsync guarantees
        # the line survives a power loss once the call returns.
        with open(self.log_path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return self.store.ingest(event)

    def recover(self) -> RecoveryReport:
        truncated = 0
        warnings: List[str] = []
        good_lines: List[str] = []
        if self.log_path.exists():
            raw_lines = self.log_path.read_text(encoding="utf-8").splitlines()
            for index, line in enumerate(raw_lines):
                if not line.strip():
                    continue
                parts = line.split(" ", 1)
                if len(parts) != 2:
                    if index < len(raw_lines) - 1 or line.strip():
                        # Corruption away from the tail is fatal: silently
                        # dropping committed history would be worse.
                        raise JournalCorruption(
                            f"malformed journal line {index + 1}"
                        )
                    truncated += 1
                    continue
                digest, body = parts
                if checksum(body) != digest:
                    if index == len(raw_lines) - 1 or not raw_lines[index + 1:]:
                        truncated += 1
                        warnings.append(
                            f"truncated torn write at journal line {index + 1}"
                        )
                        continue
                    raise JournalCorruption(
                        f"checksum mismatch at journal line {index + 1}"
                    )
                good_lines.append(body)
        loaded = 0
        for body in good_lines:
            event = _announcement_from_json(json.loads(body))
            result = self.store.ingest(event)
            if result.status == "conflict":
                raise JournalCorruption(
                    f"duplicate announcement id with conflicting body: "
                    f"{event.announcement_id}"
                )
            loaded += 1
        if truncated:
            # Persist the repair so the bad tail never resurfaces.
            self.log_path.write_text(
                "".join(f"{checksum(body)} {body}\n" for body in good_lines),
                encoding="utf-8",
            )
        snapshot_version = self._load_snapshot(warnings)
        self.recovery = RecoveryReport(
            loaded=loaded,
            truncated_tail_lines=truncated,
            snapshot_restored=snapshot_version,
            warnings=warnings,
        )
        return self.recovery

    # -- snapshot cache -----------------------------------------------------

    def save_snapshot(self, version: int, materialisation: dict) -> None:
        payload = {"version": version, "materialisation": materialisation}
        # Write-temp-then-rename: snapshots are never observed half-written.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.directory), prefix=".snapshot-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.snapshot_path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def _load_snapshot(self, warnings: List[str]) -> Optional[int]:
        if not self.snapshot_path.exists():
            return None
        try:
            payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            return int(payload["version"])
        except (ValueError, KeyError, json.JSONDecodeError):
            warnings.append("ignoring unreadable snapshot")
            return None

    def snapshot_materialisation(
        self, reconciler: Reconciler, version: int
    ) -> Optional[dict]:
        """Return a cached materialisation only if it still matches a fresh
        recomputation; otherwise it is discarded."""
        if not self.snapshot_path.exists():
            return None
        try:
            payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            if payload.get("version") != version:
                return None
            cached = payload["materialisation"]
        except (ValueError, KeyError, json.JSONDecodeError):
            return None
        fresh = reconciler.materialise(self.store, as_of_version=version)
        if canonical_json(cached) == canonical_json(fresh):
            return cached
        self.snapshot_path.unlink(missing_ok=True)
        return None
