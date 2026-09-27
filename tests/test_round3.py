"""Tests for backlink CRM gates, competitors, search, attention, runs, report."""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency


class Round3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        agency.AGENCY_DB = Path(cls._tmp.name) / "agency-test.db"
        agency._initialized = False
        agency.init_db()
        cls.client = agency.client_create({"name": "R3 Client"})

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_backlink_sent_requires_approved_approval(self):
        b = agency.backlink_create({"domain": "site.com", "client_id": self.client["id"]})
        with self.assertRaises(ValueError):
            agency.backlink_update(b["id"], {"status": "sent"})
        # draft required before approval can be requested
        with self.assertRaises(ValueError):
            agency.backlink_request_approval(b["id"])
        agency.backlink_update(b["id"], {"draft": "personalized pitch"})
        apr = agency.backlink_request_approval(b["id"])
        # still can't send while approval pending
        with self.assertRaises(ValueError):
            agency.backlink_update(b["id"], {"status": "sent"})
        agency.approval_decide(apr["id"], "approved")
        sent = agency.backlink_update(b["id"], {"status": "sent"})
        self.assertEqual(sent["status"], "sent")
        agency.backlink_delete(b["id"])

    def test_rejected_outreach_reverts_prospect(self):
        b = agency.backlink_create({"domain": "no.com", "client_id": self.client["id"]})
        agency.backlink_update(b["id"], {"draft": "pitch"})
        apr = agency.backlink_request_approval(b["id"])
        agency.approval_decide(apr["id"], "rejected")
        row = [x for x in agency.backlinks_list(self.client["id"]) if x["id"] == b["id"]][0]
        self.assertEqual(row["status"], "qualified")
        with self.assertRaises(ValueError):
            agency.backlink_update(b["id"], {"status": "sent"})
        agency.backlink_delete(b["id"])

    def test_run_lifecycle_and_fanout(self):
        agency.bridge_run_started("@scout", "do research")
        agency.bridge_run_finished("@scout", "completed")
        agency.bridge_run_started("@all", "broadcast")
        agency.bridge_run_finished("@all", "failed")
        runs = agency.runs_list()
        states = {(r["agent_name"], r["state"]) for r in runs}
        self.assertIn(("Scout", "completed"), states)
        self.assertIn((None, "failed"), states)
        # closing again is a no-op, not an error
        agency.bridge_run_finished("@scout", "completed")

    def test_search_and_attention(self):
        agency.competitor_create({"domain": "searchable-rival.com", "client_id": self.client["id"]})
        hits = agency.search("searchable-rival")
        self.assertEqual(hits[0]["kind"], "competitor")
        self.assertEqual(hits[0]["client_id"], self.client["id"])
        self.assertEqual(agency.search(""), [])
        a = agency.approval_create({"title": "Att check", "blocking": True})
        titles = [i["title"] for i in agency.attention()]
        self.assertTrue(any("Att check" in t for t in titles))
        agency.approval_decide(a["id"], "rejected")

    def test_report_json_and_html(self):
        # The Search Console note depends on the live integration state; pin it
        # so this test does not change meaning on a machine where GSC is connected.
        from unittest import mock
        with mock.patch.object(agency, "_gsc_integration_state", return_value={"state": "not_connected"}):
            rep = agency.client_report(self.client["id"])
        self.assertEqual(rep["client"]["name"], "R3 Client")
        self.assertIn("not connected", rep["seo"]["gsc_note"])
        html = agency._report_html(rep)
        self.assertIn("R3 Client", html)
        self.assertIn("verified", html)  # data policy present
        with self.assertRaises(KeyError):
            agency.client_report("missing")


class AttentionPipelineTests(unittest.TestCase):
    """Regression: a halted pipeline must reach the operator's attention list."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        agency.AGENCY_DB = Path(cls._tmp.name) / "attention-test.db"
        agency._initialized = False
        agency.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_failed_run_surfaces_in_attention(self):
        client = agency.client_create({"name": "Att Co"})
        project = agency.project_create({"name": "Att Project", "client_id": client["id"]})
        with agency._conn() as conn:
            conn.execute(
                "INSERT INTO pipeline_runs (id, project_id, playbook, state, error, created_at, updated_at) "
                "VALUES ('run-fail', ?, 'seo_campaign', 'failed', 'stage audit failed - no URL', ?, ?)",
                (project["id"], agency.now_iso(), agency.now_iso()))
        items = agency.attention()
        failed = [i for i in items if i["kind"] == "pipeline_failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["severity"], "high")
        self.assertIn("Att Project", failed[0]["title"])
        self.assertEqual(failed[0]["id"], "run-fail")

    def test_completed_run_does_not_surface(self):
        with agency._conn() as conn:
            conn.execute(
                "INSERT INTO pipeline_runs (id, project_id, playbook, state, created_at, updated_at) "
                "VALUES ('run-ok', NULL, 'seo_campaign', 'completed', ?, ?)",
                (agency.now_iso(), agency.now_iso()))
        ids = [i["id"] for i in agency.attention()]
        self.assertNotIn("run-ok", ids)


class TimestampWindowTests(unittest.TestCase):
    """Windows must not widen because of a timestamp format mismatch.

    now_iso() writes '2026-09-09T08:30:46.4+00:00'; SQLite's datetime('now')
    yields '2026-09-09 08:30:46'. Compared as strings, 'T' sorts after ' ', so
    a row from the cutoff date counted as inside the window whatever its time.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        agency.AGENCY_DB = Path(cls._tmp.name) / "cutoff.db"
        agency._initialized = False
        agency.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_cutoff_is_in_the_stored_format(self):
        cutoff = agency._cutoff_iso(3)
        self.assertIn("T", cutoff, "cutoff must match the format the columns store")
        self.assertIn("+00:00", cutoff)

    def test_early_on_the_cutoff_day_is_excluded(self):
        cutoff = agency._cutoff_iso(3)
        midnight_that_day = cutoff[:10] + "T00:00:00.000000+00:00"
        self.assertLess(midnight_that_day, cutoff,
                        "a timestamp before the cutoff time must sort before the cutoff")

    def test_failed_run_outside_the_window_does_not_surface(self):
        old = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
        with agency._conn() as conn:
            conn.execute(
                "INSERT INTO pipeline_runs (id, project_id, playbook, state, error, created_at, updated_at) "
                "VALUES ('old-run', NULL, 'seo_campaign', 'failed', 'stale failure', ?, ?)", (old, old))
        self.assertNotIn("old-run", [i["id"] for i in agency.attention()])

    def test_failed_run_inside_the_window_does_surface(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        with agency._conn() as conn:
            conn.execute(
                "INSERT INTO pipeline_runs (id, project_id, playbook, state, error, created_at, updated_at) "
                "VALUES ('fresh-run', NULL, 'seo_campaign', 'failed', 'fresh failure', ?, ?)", (recent, recent))
        items = {i["id"]: i for i in agency.attention()}
        self.assertIn("fresh-run", items)
        self.assertEqual(items["fresh-run"]["severity"], "high")


if __name__ == "__main__":
    unittest.main()
