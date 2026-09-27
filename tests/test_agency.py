"""Unit tests for the agency module (stdlib unittest, isolated temp DB)."""

import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency


class AgencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        agency.AGENCY_DB = Path(cls._tmp.name) / "agency-test.db"
        agency._initialized = False
        agency.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_migrations_idempotent(self):
        agency._initialized = False
        agency.init_db()  # second run must not raise or duplicate
        h = agency.health()
        self.assertTrue(h["ok"])
        self.assertEqual(h["schema_version"], agency.MIGRATIONS[-1][0])

    def test_agents_seeded(self):
        agents = agency.agents_list()
        # Assert against the seed list itself; hardcoding the count made this
        # test go stale the moment an agent was added.
        self.assertEqual(len(agents), len(agency.DEFAULT_AGENTS))
        targets = [a["gateway_target"] for a in agents]
        self.assertIn("@orchestrator", targets)
        for agent_id, _name, _role, target in agency.DEFAULT_AGENTS:
            self.assertIn(target, targets, f"{agent_id} was not seeded")

    def test_editing_a_projects_url_is_saved(self):
        # it used to be dropped silently while the save reported success
        proj = agency.project_create({"name": "URL edit", "type": "landing_page"})
        out = agency.project_update(proj["id"], {"url": "https://northgate.example"})
        self.assertEqual(out["url"], "https://northgate.example")
        with self.assertRaises(ValueError):
            agency.project_update(proj["id"], {"url": "javascript:alert(1)"})

    def test_marking_live_needs_a_real_status_and_an_address(self):
        proj = agency.project_create({"name": "Go live", "type": "landing_page"})
        with self.assertRaises(ValueError):
            agency.project_update(proj["id"], {"status": "launched!"})
        with self.assertRaises(ValueError):
            agency.project_update(proj["id"], {"status": "live"})          # no URL anywhere yet
        out = agency.project_update(proj["id"], {"status": "live", "url": "https://go-live.example"})
        self.assertEqual((out["status"], out["url"]), ("live", "https://go-live.example"))

    def test_client_project_task_roundtrip(self):
        c = agency.client_create({"name": "RT Client", "mrr_cents": 5000})
        p = agency.project_create({"name": "RT Project", "client_id": c["id"], "type": "landing_page"})
        t = agency.agency_task_create({"title": "RT Task", "project_id": p["id"]})
        self.assertEqual(agency.projects_list(c["id"])[0]["name"], "RT Project")
        updated = agency.agency_task_update(t["id"], {"status": "completed"})
        self.assertEqual(updated["status"], "completed")
        agency.client_delete(c["id"])
        self.assertEqual(agency.projects_list(c["id"]), [])  # cascade

    def test_project_type_validated(self):
        with self.assertRaises(ValueError):
            agency.project_create({"name": "Bad", "type": "not_a_type"})

    def test_approval_flow(self):
        a = agency.approval_create({"title": "Deploy X", "kind": "deploy", "blocking": True})
        decided = agency.approval_decide(a["id"], "approved")
        self.assertEqual(decided["status"], "approved")
        with self.assertRaises(KeyError):
            agency.approval_decide(a["id"], "rejected")  # already decided
        with self.assertRaises(ValueError):
            agency.approval_decide(str(uuid.uuid4()), "maybe")

    def test_keyword_provenance_required(self):
        with self.assertRaises(ValueError):
            agency.keyword_create({"keyword": "x"})
        with self.assertRaises(ValueError):
            agency.keyword_create({"keyword": "x", "provenance": "guessed"})
        k = agency.keyword_create({"keyword": "x", "provenance": "verified", "position": 3})
        self.assertEqual(k["provenance"], "verified")
        agency.keyword_delete(k["id"])

    def test_default_board_protected(self):
        boards = agency.boards_list()
        default = [b for b in boards if b["kind"] == "default"][0]
        with self.assertRaises(ValueError):
            agency.board_delete(default["id"])

    def test_activity_logged(self):
        c = agency.client_create({"name": "Log Client"})
        actions = [(e["action"], e["entity_type"]) for e in agency.activity_list(10)]
        self.assertIn(("create", "client"), actions)
        agency.client_delete(c["id"])


if __name__ == "__main__":
    unittest.main()
