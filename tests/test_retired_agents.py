"""Antigravity and ChatGPT were retired (2026-10-08): never used in four months,
and ChatGPT is a model, not a team member. Their rows and history stay."""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency

RETIRED = {"antigravity", "chatgpt"}


class RetiredAgentTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = agency.AGENCY_DB
        agency.AGENCY_DB = Path(self._tmp.name) / "retired-test.db"
        agency._initialized = False

    def tearDown(self):
        agency.AGENCY_DB = self._orig
        agency._initialized = False
        self._tmp.cleanup()

    def test_a_fresh_install_has_nine_agents(self):
        agency.init_db()
        ids = {a["id"] for a in agency.agents_list(include_retired=True)}
        self.assertEqual(len(ids), 9)
        self.assertFalse(ids & RETIRED)

    def test_an_existing_install_retires_them_and_keeps_the_rows(self):
        # a database from before the retirement: migrations 1-4 and all eleven agents
        conn = sqlite3.connect(agency.AGENCY_DB)
        conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        for version, sql in agency.MIGRATIONS:
            if version < 5:
                conn.executescript(sql)
                conn.execute("INSERT INTO schema_migrations VALUES (?, 'then')", (version,))
        for agent_id in [a[0] for a in agency.DEFAULT_AGENTS] + sorted(RETIRED):
            conn.execute("INSERT INTO agents (id, name, role, purpose, gateway_target, status) VALUES (?,?,?,?,?,'idle')",
                         (agent_id, agent_id.title(), "", "", "@" + agent_id))
        conn.commit(); conn.close()
        agency.init_db()
        all_rows = {a["id"]: a["status"] for a in agency.agents_list(include_retired=True)}
        self.assertEqual({k: all_rows[k] for k in RETIRED}, {"antigravity": "retired", "chatgpt": "retired"})
        team = {a["id"] for a in agency.agents_list()}
        self.assertEqual(len(team), 9)
        self.assertFalse(team & RETIRED)
        office = agency.office_state()
        self.assertEqual(office["counts"]["free"], 9)
        self.assertFalse({a["id"] for a in office["agents"]} & RETIRED)


class NowhereToSendWorkTests(unittest.TestCase):
    def test_no_target_list_offers_them(self):
        import jarvis
        import llm
        import server
        self.assertFalse({"@antigravity", "@chatgpt"} & set(jarvis.AGENT_TARGETS))
        self.assertFalse({"@antigravity", "@chatgpt"} & set(server.BRIDGE_TARGETS))
        self.assertFalse(RETIRED & set(llm.AGENTS))
        root = Path(agency.__file__).resolve().parent
        for f in ("app/q/js/pages/ai.js", "app/q/js/jarvis/intents.js", "app/q/js/pages/office.js"):
            text = (root / f).read_text(encoding="utf-8")
            self.assertNotIn('"@antigravity"', text, f)
            self.assertNotIn('room: "labs"', text, f)
