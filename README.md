# Precinct Return Reconciler

Versioned election-night return reconciliation across the
**state / county / precinct / ballot-type (leaf)** hierarchy.

The original program added candidate votes and compared them to one expected
total. It could not express hierarchy, versioning, retractions or the public
distinction between *zero votes*, *never reported*, *provisional* and
*withdrawn* results. This implementation replaces it with an event-sourced
pipeline that ingests announcements, folds them into a checksummed journal,
and recomputes every publication version from that journal.

## Concepts

### Announcements (the only input fact)

Every input row is an announcement with a stable `announcement_id`, a
monotonic integer `version`, a hierarchy `scope` and one of five kinds:

| kind         | meaning                                                            |
|--------------|--------------------------------------------------------------------|
| `batch`      | first arrival of counted ballots for a leaf                        |
| `revision`   | corrected re-announcement; may target a leaf or a whole precinct   |
| `withdrawal` | retraction — the old numbers are retracted, **never** treated as 0 |
| `conversion` | provisional ballots certified as official                          |
| `rename`     | candidate display-name change, resolved into a canonical alias     |

### Four result interpretations that are never conflated

| situation        | flag                | blocking? | totals treatment            |
|------------------|---------------------|-----------|-----------------------------|
| explicit zero    | `reported_zero`     | no        | contributes 0, node complete|
| never reported   | `missing_report`    | yes       | node incomplete             |
| provisional      | `provisional_pending` | yes     | counted but uncertified     |
| withdrawn        | `withdrawn`         | partial   | excluded; old votes kept in provenance |

### Rollup readiness

A county cannot publish before every precinct under it is stable, and a
precinct cannot publish before every declared leaf reports. A failing county
keeps its own numbers but forces the **state** node to `incomplete` with a
blocking reason — partial county failure can never masquerade as a complete
state result.

### Reason codes

Blocking: `CANDIDATE_CONSERVATION`, `BALLOT_TYPE_CONSERVATION`,
`CROSS_LEVEL_MISMATCH`, `MISSING_REPORT`, `MISSING_CHILD`, `HIERARCHY_GAP`,
`INGEST_CONFLICT`, `PROVISIONAL_PENDING`, `RENAME_CYCLE`,
`SUPERSEDED_REFERENCE`.
Informational: `REPORTED_ZERO`, `PARTIAL_WITHDRAWAL`.

Every reason carries the offending node id, expected/actual values and the
signed difference so an imbalance is explainable without re-reading the log.

## Architecture and the incremental vs. full-recompute trade-off

```
payload ──▶ parser ──▶ EventStore (in-memory fold)  ──▶ Reconciler ──▶ report envelope
                          │                                   ▲
                          └──▶ Journal (checksummed log) ─────┘  replay any version
                                  + verified snapshots
```

Three strategies were considered:

1. **Event increments applied directly to aggregate counters.** Fastest, but
   counters cannot answer "what did version 3 say?", cannot distinguish a
   retraction from a zero adjustment without tombstone plumbing, and a single
   wrong increment silently corrupts every later publication. Rejected as the
   primary mechanism.
2. **Hierarchy materialisation maintained incrementally.** Parent totals update
   as children change, but out-of-order delivery and partial withdrawals
   require compensating logic that is easy to get wrong and order-dependent.
3. **Event-sourced journal + full per-version recompute (chosen).** A fold of
   the log up to version *V* rebuilds leaf state, aliases and every aggregate
   from scratch. Cost is O(events × versions) for full history; correctness is
   independent of arrival order, replay of any announcement version is exact,
   and a crash only loses the un-fsynced tail line. The only incremental
   optimisation is an optional **snapshot cache that is discarded unless it
   byte-matches a fresh recomputation**, so it can never change an answer.

### Handled edge cases

- **Out-of-order batches / input permutation** — events are folded ordered by
  `(version, announcement_id)`; shuffled inputs produce byte-identical output
  (asserted by `test_versioning_replay` and the combinatorial test).
- **Hierarchy gaps** — announcements for undeclared scopes materialise with
  `HIERARCHY_GAP` and block publication; declared-but-silent scopes still
  appear as incomplete nodes.
- **Candidate renames** — hops form an old→canonical alias map; cycles are
  detected (`RENAME_CYCLE`); replay before a rename keeps the old name.
- **Provisional → official** — conversion rewrites matching lines in place and
  clears the pending flag; a conversion with nothing to convert is a
  `SUPERSEDED_REFERENCE`.
- **Mid-write crash recovery** — each journal line is
  `<sha256> <canonical-json>`; a torn tail is truncated on reopen, mid-log
  corruption is fatal, snapshots are atomic via temp-file + rename.
- **Idempotent redelivery** — identical `announcement_id` + body is a no-op;
  the same id with a different body is an `INGEST_CONFLICT` that blocks the
  state publication.

## Layout

- `models.py` — immutable contracts, reason vocabulary, canonical checksums
- `parser.py` — strict v2 validation, legacy flat-fixture adaptation
- `engine.py` — `EventStore` fold, `Reconciler` materialisation, conservation
- `journal.py` — durable append-only log, crash recovery, verified snapshots
- `pipeline.py` — orchestration, replay, version history, ingest report
- `report.py` — stable, key-sorted output envelope
- `cli.py` — deterministic JSON CLI

## Run

```bash
# legacy fixture still works (balanced flat totals preserved)
PYTHONPATH=src python -m precinct_return_reconciler.cli

# full versioned scenario
PYTHONPATH=src python -m precinct_return_reconciler.cli fixtures/returns_v2.json

# replay an arbitrary historical publication version
PYTHONPATH=src python -m precinct_return_reconciler.cli fixtures/returns_v2.json --version 3

# durable journal with crash recovery and full version history
PYTHONPATH=src python -m precinct_return_reconciler.cli fixtures/returns_v2.json --journal .run --history

# tests
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Output contract

Each publication contains `publication_version`, `replayable_from_version`,
`publishable`, a `source_checksum` over every announcement folded into it,
the full `materialisation` (state/county/precinct/leaf nodes with
`candidate_totals`, `ballot_type_totals`, `status`, `flags`, `reasons` and
`provenance`), and an `ingest_report` of accepted/duplicate/conflict counts.
Every leaf provenance retains its effective announcement, superseded ids,
source ids and any withdrawal id, so every published number remains traceable
to its source announcement.
