"""Durable journal: crash mid-write recovery, idempotent replay, snapshots."""
import json
import tempfile
import unittest
from pathlib import Path

from precinct_return_reconciler.engine import Reconciler
from precinct_return_reconciler.journal import Journal, JournalCorruption
from precinct_return_reconciler.parser import parse_announcement

from tests.support import batch, hierarchy_entry


def events():
    return [
        parse_announcement(batch(
            "e1", 1, "C1", "P1", "early", [{"candidate": "A", "votes": 10}]
        ), 0),
        parse_announcement(batch(
            "e2", 2, "C1", "P1", "mail", [{"candidate": "A", "votes": 20}]
        ), 1),
    ]


def reconciler():
    hierarchy = [
        hierarchy_entry(),
        hierarchy_entry(county="C1"),
        hierarchy_entry(county="C1", precinct="P1"),
        hierarchy_entry(county="C1", precinct="P1", ballot_type="early"),
        hierarchy_entry(county="C1", precinct="P1", ballot_type="mail"),
    ]
    return Reconciler("S", hierarchy)


class TestJournalRecovery(unittest.TestCase):
    def test_reopen_replays_committed_events_losslessly(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(directory)
            journal.recover()
            for event in events():
                journal.append(event)

            reopened = Journal(directory)
            report = reopened.recover()
            self.assertEqual(report.loaded, 2)
            self.assertEqual(report.truncated_tail_lines, 0)
            materialisation = reconciler().materialise(reopened.store)
            self.assertEqual(
                materialisation["state_node"]["total_votes"], 30
            )

    def test_torn_tail_write_is_truncated_on_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(directory)
            journal.recover()
            journal.append(events()[0])
            log = Path(directory) / "events.log"
            # Simulate a crash halfway through the second write.
            with open(log, "a", encoding="utf-8") as handle:
                handle.write("deadbeef partial-line-without-newline")

            reopened = Journal(directory)
            report = reopened.recover()
            self.assertEqual(report.loaded, 1)
            self.assertEqual(report.truncated_tail_lines, 1)
            materialisation = reconciler().materialise(reopened.store)
            self.assertEqual(
                materialisation["state_node"]["total_votes"], 10
            )
            # The repaired log no longer contains the torn tail.
            self.assertNotIn("deadbeef", log.read_text())

    def test_reappending_after_recovery_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(directory)
            journal.recover()
            for event in events():
                journal.append(event)
            reopened = Journal(directory)
            reopened.recover()
            results = [reopened.append(event) for event in events()]
            self.assertTrue(all(r.status == "duplicate" for r in results))

    def test_mid_log_corruption_is_fatal_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(directory)
            journal.recover()
            for event in events():
                journal.append(event)
            log = Path(directory) / "events.log"
            lines = log.read_text().splitlines(keepends=True)
            # Corrupt the body of the first (non-tail) line.
            first = lines[0].split(" ", 1)
            lines[0] = first[0] + " " + '{"tampered": true}\n'
            log.write_text("".join(lines))
            with self.assertRaises(JournalCorruption):
                Journal(directory).recover()

    def test_snapshot_is_used_only_when_it_matches_recomputation(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(directory)
            journal.recover()
            for event in events():
                journal.append(event)
            rec = reconciler()
            fresh = rec.materialise(journal.store, as_of_version=2)
            journal.save_snapshot(2, fresh)

            reopened = Journal(directory)
            reopened.recover()
            cached = reopened.snapshot_materialisation(rec, 2)
            self.assertIsNotNone(cached)
            self.assertEqual(
                cached["state_node"]["total_votes"],
                fresh["state_node"]["total_votes"],
            )

            # Tamper with the snapshot; it must be discarded.
            tampered = json.loads(
                Path(directory, "snapshot.json").read_text()
            )
            tampered["materialisation"]["state_node"]["total_votes"] = 9999
            Path(directory, "snapshot.json").write_text(
                json.dumps(tampered, sort_keys=True)
            )
            self.assertIsNone(
                Journal(directory).snapshot_materialisation(rec, 2)
            )


if __name__ == "__main__":
    unittest.main()
