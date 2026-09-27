"""Tests for the client report: it must tell a client the truth.

Built after the old report marked a 31-day-overdue project and two stopped
runs "On track", said Search Console was disconnected when it was connected,
and would have listed a provider billing error as a deliverable.
"""

import json
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency

AUDIT = {
    "url": "https://www.acme.test", "final_url": "https://www.acme.test", "score": 90,
    "findings": [
        {"severity": "good", "area": "technical", "title": "HTTPS", "fix": ""},
        {"severity": "good", "area": "technical", "title": "Robots", "fix": ""},
        {"severity": "medium", "area": "on-page", "title": "Title is 70 characters", "fix": "Trim to about 55."},
        {"severity": "low", "area": "performance", "title": "2 images without sizes", "fix": "Set width and height."},
    ],
}


def _now(days=0):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        agency.AGENCY_DB = Path(cls._tmp.name) / "report-test.db"
        agency._initialized = False
        agency.init_db()
        cls.client = agency.client_create({"name": "Acme"})
        cid = cls.client["id"]
        past_due = (datetime.now(timezone.utc) - timedelta(days=31)).date().isoformat()

        cls.overdue = agency.project_create({"name": "Redesign", "client_id": cid, "due_date": past_due})
        cls.delivered = agency.project_create({"name": "Audit", "client_id": cid})
        cls.paused = agency.project_create({"name": "Follow-up", "client_id": cid})
        cls.stopped = agency.project_create({"name": "Experiment", "client_id": cid})

        with agency._conn() as conn:
            def run(project, state, stage_states, titles=None):
                rid = str(uuid.uuid4())
                conn.execute("INSERT INTO pipeline_runs (id, project_id, playbook, state, created_at, updated_at) "
                             "VALUES (?,?,?,?,?,?)", (rid, project["id"], "seo_campaign", state, _now(-40), _now(-41)))
                for i, st in enumerate(stage_states):
                    conn.execute("INSERT INTO stage_runs (id, run_id, stage_id, title, state, position) VALUES (?,?,?,?,?,?)",
                                 (str(uuid.uuid4()), rid, f"s{i}", (titles or {}).get(i, f"Step {i}"), st, i))
                return rid

            r = run(cls.delivered, "completed", ["done"] * 3)
            for stage_id, title, kind, content in [
                ("audit", "Technical audit (real fetch)", "json", json.dumps(AUDIT)),
                ("keywords", "Keyword & intent map", "json", "{}"),
                # stored before the provider-error guard existed: must never reach a client
                ("fix_plan", "Technical fix plan", "markdown",
                 "Billing or credits exhausted: HTTP 402: This request requires more credits"),
            ]:
                conn.execute("INSERT INTO artifacts (id, run_id, project_id, stage_id, title, kind, content, created_at) "
                             "VALUES (?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), r, cls.delivered["id"], stage_id, title,
                                                          kind, content, _now(-41)))
            run(cls.paused, "failed", ["done", "done", "failed", "pending"], {2: "Keyword & intent map (JSON)"})
            run(cls.stopped, "cancelled", ["done", "pending"])
            conn.execute("INSERT INTO backlink_prospects (id, client_id, domain, status, created_at, updated_at) "
                         "VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), cid, "linker.test", "approved", _now(), _now()))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def report(self, gsc_state="connected"):
        with mock.patch.object(agency, "_gsc_integration_state", return_value={"state": gsc_state}):
            return agency.client_report(self.client["id"])

    def by_name(self, rep):
        return {p["name"]: p for p in rep["projects"]}

    def test_statuses_come_from_runs_and_dates_not_the_health_flag(self):
        p = self.by_name(self.report())
        self.assertEqual(p["Redesign"]["state"], "overdue")
        self.assertEqual(p["Redesign"]["label"], "31 days overdue")
        self.assertIn("not yet started", p["Redesign"]["detail"])
        self.assertEqual((p["Audit"]["state"], p["Audit"]["done"], p["Audit"]["total"]), ("delivered", 3, 3))
        self.assertEqual((p["Follow-up"]["state"], p["Follow-up"]["done"]), ("paused", 2))
        self.assertIn("keyword & intent map", p["Follow-up"]["detail"])
        self.assertNotIn("(JSON)", p["Follow-up"]["detail"])
        self.assertEqual(p["Experiment"]["state"], "stopped")
        # every project was created with health=on_track; none may be reported that way
        self.assertNotIn("On track", {x["label"] for x in p.values()})

    def test_most_urgent_projects_come_first(self):
        states = [p["state"] for p in self.report()["projects"]]
        self.assertEqual(states[:2], ["overdue", "paused"])

    def test_verified_audit_and_clean_deliverables(self):
        rep = self.report()
        site = rep["site_health"][0]
        self.assertEqual((site["score"], site["counts"]), (90, {"high": 0, "medium": 1, "low": 1}))
        self.assertEqual(site["issues"][0]["severity"], "medium")
        self.assertEqual({a["label"]: (a["good"], a["total"]) for a in site["areas"]}["Technical"], (2, 2))
        titles = [d["title"] for d in rep["deliverables"]]
        self.assertIn("Technical audit", titles)          # parenthetical operator detail stripped
        self.assertNotIn("Technical fix plan", titles)    # the billing error is not a deliverable

    def test_search_console_note_matches_reality(self):
        connected = self.report("connected")["seo"]
        self.assertEqual(connected["gsc_state"], "connected")
        self.assertIn("not linked", connected["gsc_note"])
        self.assertNotIn("not connected", connected["gsc_note"])
        self.assertIn("not connected", self.report("not_connected")["seo"]["gsc_note"])

    def test_summary_and_next_steps_follow_from_the_facts(self):
        rep = self.report()
        s = rep["summary"]
        self.assertEqual(s["headline"], "Your site is healthy.")
        self.assertEqual(s["subline"], "Two projects need a push.")
        self.assertIn("90 out of 100", s["text"])
        self.assertIn("31 days past its due date", s["text"])
        steps = " ".join(rep["next_steps"])
        for expected in ["new date for Redesign", "Ship the 2 fixes", "1 approved link prospect",
                         "Resume Follow-up", "Link the site in Google Search Console"]:
            self.assertIn(expected, steps)

    def test_html_escapes_names_and_carries_the_facts(self):
        agency.client_update(self.client["id"], {"name": "Acme <script>"})
        try:
            html = agency._report_html(self.report())
        finally:
            agency.client_update(self.client["id"], {"name": "Acme"})
        self.assertNotIn("<script>", html)
        self.assertIn("Acme &lt;script&gt;", html)
        for fact in ["31 days overdue", "90<small>/100</small>", "Trim to about 55.", "Delivered to date",
                     "@media print", "Next 30 days"]:
            self.assertIn(fact, html)
        self.assertNotIn("credits exhausted", html)

    def test_empty_client_still_renders(self):
        empty = agency.client_create({"name": "New Co"})
        with mock.patch.object(agency, "_gsc_integration_state", return_value={"state": "not_connected"}):
            rep = agency.client_report(empty["id"])
        self.assertEqual((rep["projects"], rep["site_health"], rep["deliverables"]), ([], [], []))
        self.assertEqual(rep["summary"]["subline"], "Everything is on track.")
        html = agency._report_html(rep)
        self.assertIn("No projects are active", html)
        self.assertIn("No audit run yet", html)


if __name__ == "__main__":
    unittest.main()
