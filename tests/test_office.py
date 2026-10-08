"""The office view: who is working, waiting or free, from live records only."""

import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency


class OfficeStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = agency.AGENCY_DB
        agency.AGENCY_DB = Path(self._tmp.name) / "office-test.db"
        agency._initialized = False
        agency.init_db()
        self.project = agency.project_create({"name": "Northgate Plumbing"})["id"]

    def tearDown(self):
        agency.AGENCY_DB = self._orig
        agency._initialized = False
        self._tmp.cleanup()

    def run_(self, state, project=None):
        rid = uuid.uuid4().hex
        with agency._conn() as c:
            c.execute("INSERT INTO pipeline_runs (id, project_id, playbook, state, stage_index, error, created_at, updated_at) "
                      "VALUES (?,?,?,?,0,'',?,?)", (rid, project or self.project, "website_build", state, agency.now_iso(), agency.now_iso()))
        return rid

    def stage(self, run, agent, title, state, finished=""):
        with agency._conn() as c:
            c.execute("INSERT INTO stage_runs (id, run_id, stage_id, title, kind, agent, state, detail, error, tokens_in, tokens_out, "
                      "seconds, position, started_at, finished_at) VALUES (?,?,?,?,?,?,?,'','',0,0,0,0,?,?)",
                      (uuid.uuid4().hex, run, title.lower().replace(" ", "_"), title, "llm", agent, state, agency.now_iso(), finished))

    def office(self):
        o = agency.office_state()
        return o, {a["id"]: a for a in o["agents"]}

    def test_everyone_is_free_when_nothing_runs(self):
        o, by = self.office()
        self.assertTrue(by)
        self.assertEqual(o["counts"]["working"], 0)
        self.assertTrue(all(a["state"] == "free" for a in by.values()))

    def test_a_live_stage_puts_its_agent_at_work(self):
        self.stage(self.run_("running"), "scout", "Research: competitor SERPs", "running")
        o, by = self.office()
        self.assertEqual(by["scout"]["state"], "working")
        self.assertEqual(by["scout"]["task"], "Research: competitor SERPs")
        self.assertEqual(by["scout"]["project"], "Northgate Plumbing")
        self.assertEqual(o["counts"]["working"], 1)

    def test_a_stage_left_running_in_an_ended_run_is_ignored(self):
        """Found in the real database: a 'running' stage inside a run cancelled weeks ago."""
        self.stage(self.run_("cancelled"), "scribe", "Content plan & briefs", "running")
        o, by = self.office()
        self.assertEqual(by["scribe"]["state"], "free")
        self.assertFalse(any("Content plan" in f["text"] for f in o["feed"]))

    def test_a_run_paused_on_approval_shows_its_agent_waiting(self):
        self.stage(self.run_("awaiting_approval"), "reach", "Approve outreach programme", "awaiting_approval")
        o, by = self.office()
        self.assertEqual(by["reach"]["state"], "waiting")
        self.assertEqual(o["counts"]["waiting"], 1)

    def test_a_pending_approval_marks_who_raised_it(self):
        with agency._conn() as c:
            c.execute("INSERT INTO approvals (id, project_id, title, detail, kind, status, blocking, source_agent, payload, created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, self.project, "Publish the homepage copy", "", "content",
                                                     "pending", 1, "scribe", "{}", agency.now_iso()))
        _, by = self.office()
        self.assertEqual((by["scribe"]["state"], by["scribe"]["task"]), ("waiting", "Publish the homepage copy"))

    def test_automated_stages_show_at_the_orchestrators_desk(self):
        self.stage(self.run_("running"), "audit_url", "Technical audit (real fetch)", "running")
        _, by = self.office()
        self.assertEqual(by["orchestrator"]["state"], "working")
        self.assertEqual(by["orchestrator"]["task"], "Running: Technical audit (real fetch)")

    def test_feed_is_real_history_without_test_projects(self):
        self.stage(self.run_("completed"), "rank", "Technical fix plan", "done", finished=agency.now_iso())
        test = agency.project_create({"name": "ZZ E2E Test - Ember & Oak"})["id"]
        self.stage(self.run_("failed", project=test), "scout", "Positioning", "failed", finished=agency.now_iso())
        o, _ = self.office()
        texts = [f["text"] for f in o["feed"]]
        self.assertIn("Rank finished: Technical fix plan", texts)
        self.assertFalse(any("Positioning" in t for t in texts))
        self.assertTrue(agency._TEST_PROJECT.search("My test site"))
        self.assertFalse(agency._TEST_PROJECT.search("Contest Co"))   # a word boundary, not a substring


class FrontendWiringTests(unittest.TestCase):
    def test_office_is_routed_and_bundled_locally(self):
        root = Path(agency.__file__).resolve().parent
        routes = (root / "app/q/js/routes.js").read_text(encoding="utf-8")
        nav = (root / "app/q/js/nav.js").read_text(encoding="utf-8")
        page = (root / "app/q/js/pages/office.js").read_text(encoding="utf-8")
        self.assertIn("office,", routes)
        self.assertIn('h: "office"', nav)
        self.assertIn('import("/app/q/vendor/three.module.min.js")', page)   # no CDN: works offline
        self.assertTrue((root / "app/q/vendor/three.module.min.js").is_file())
        self.assertTrue((root / "app/q/vendor/three.LICENSE").is_file())
