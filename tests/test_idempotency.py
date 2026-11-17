"""Idempotent redelivery and conflicting re-use of announcement ids."""
import unittest

from precinct_return_reconciler.engine import EventStore
from precinct_return_reconciler.models import canonical_json
from precinct_return_reconciler.parser import parse_announcement
from precinct_return_reconciler.pipeline import run

from tests.support import batch, hierarchy_entry, payload


def one_batch_payload():
    hierarchy = [
        hierarchy_entry(),
        hierarchy_entry(county="C1"),
        hierarchy_entry(county="C1", precinct="P1"),
        hierarchy_entry(county="C1", precinct="P1", ballot_type="early"),
    ]
    return payload(hierarchy, [
        batch("e1", 1, "C1", "P1", "early", [
            {"candidate": "A", "votes": 10}
        ]),
    ])


class TestIdempotency(unittest.TestCase):
    def test_identical_redelivery_is_a_duplicate_noop(self):
        p = one_batch_payload()
        duplicated = payload(
            p["hierarchy"], p["announcements"] * 3
        )
        result = run(duplicated)
        self.assertEqual(result["ingest_report"]["accepted"], 1)
        self.assertEqual(result["ingest_report"]["duplicate"], 2)
        self.assertEqual(result["ingest_report"]["conflict"], 0)
        self.assertEqual(
            result["materialisation"]["state_node"]["total_votes"], 10
        )

    def test_same_announcement_id_with_different_body_is_blocking_conflict(self):
        p = one_batch_payload()
        evil = dict(p["announcements"][0])
        evil = {**evil, "lines": [{"candidate": "A", "votes": 999}]}
        conflicting = payload(
            p["hierarchy"], p["announcements"] + [evil]
        )
        result = run(conflicting)
        self.assertEqual(result["ingest_report"]["conflict"], 1)
        self.assertFalse(result["publishable"])
        codes = result["materialisation"]["state_node"]["blocking_reason_codes"]
        self.assertIn("INGEST_CONFLICT", codes)

    def test_event_store_ingest_returns_explicit_statuses(self):
        store = EventStore()
        event = parse_announcement(one_batch_payload()["announcements"][0], 0)
        first = store.ingest(event)
        second = store.ingest(event)
        self.assertEqual(first.status, "accepted")
        self.assertEqual(second.status, "duplicate")

        changed = parse_announcement(
            {**one_batch_payload()["announcements"][0],
             "lines": [{"candidate": "A", "votes": 2}]},
            0,
        )
        conflict = store.ingest(changed)
        self.assertEqual(conflict.status, "conflict")

    def test_repeated_full_runs_are_byte_identical(self):
        p = one_batch_payload()
        first = canonical_json(run(p))
        second = canonical_json(run(p))
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
