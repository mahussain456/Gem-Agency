"""Two design samples per website, picking one, redoing work, and talking to the team."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency
import playbooks
import providers
import runner

DIRECTIONS = {"directions": [
    {"key": "a", "name": "Harbour Morning", "idea": "calm, airy, serif"},
    {"key": "b", "name": "Night Shift", "idea": "dark, dense, sans"}], "how_they_differ": "light vs dark"}
PAGE = "<!doctype html><html><head><title>x</title></head><body><h1>Hi</h1></body></html>"


class _Db(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig, self._ws = agency.AGENCY_DB, runner.WORKSPACE
        agency.AGENCY_DB = Path(self._tmp.name) / "design-test.db"
        runner.WORKSPACE = Path(self._tmp.name) / "workspace"
        agency._initialized = False
        agency.init_db()
        self.project = agency.project_create({"name": "Bright Smile Dental", "brief": "A dentist in Austin"})
        self.patches = [mock.patch.object(runner, "ensure_worker"), mock.patch.object(runner._jobs, "put")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        agency.AGENCY_DB, runner.WORKSPACE = self._orig, self._ws
        agency._initialized = False
        self._tmp.cleanup()

    def run_at_design_gate(self):
        """A website run with directions, two samples and the pick gate waiting."""
        run = runner.start_run(self.project["id"], "website_build")
        rid = run["id"]
        for sid in ("positioning", "research", "sitemap", "copy", "design_directions", "design_samples"):
            runner._set_stage(rid, sid, state="done")
        runner._store_artifact(rid, self.project["id"], {"id": "design_directions", "title": "d"}, json.dumps(DIRECTIONS), "json")
        with mock.patch.object(providers, "complete", return_value={"text": PAGE, "provider": "chatgpt", "seconds": 1}), \
                mock.patch.object(runner.browser, "capture_set", return_value=[]):
            content, _, _ = runner._design_samples(runner._context(rid, self.project), self.project, rid)
        runner._store_artifact(rid, self.project["id"], {"id": "design_samples", "title": "s"}, content, "json")
        appr = agency.approval_create({"title": "Pick a design", "kind": "design", "blocking": True,
                                       "project_id": self.project["id"], "source_agent": "pipeline",
                                       "payload": {"run_id": rid, "stage_id": "design_pick"}})
        runner._set_stage(rid, "design_pick", state="awaiting_approval")
        runner._set_run(rid, state="awaiting_approval")
        return rid, appr["id"]


class DesignPipelineTests(_Db):
    def test_designer_and_prototyper_join_the_build(self):
        ids = [s["id"] for s in playbooks.get("website_build")["stages"]]
        self.assertLess(ids.index("copy"), ids.index("design_directions"))
        self.assertEqual(ids[ids.index("design_directions"):ids.index("design_directions") + 3],
                         ["design_directions", "design_samples", "design_pick"])
        self.assertLess(ids.index("design_pick"), ids.index("build"))
        prompt = playbooks.get("website_build")["stages"][ids.index("design_directions")]["prompt"]({})
        self.assertIn("Sofia Marino", prompt)
        self.assertIn("eyebrow", prompt)                     # the craft bar travels with the prompt

    def test_two_samples_are_built_in_one_round(self):
        rid, _ = self.run_at_design_gate()
        state = runner.design_state(self.project["id"])
        self.assertEqual([o["key"] for o in state["options"]], ["a", "b"])
        self.assertEqual(state["options"][0]["name"], "Harbour Morning")
        self.assertEqual(state["round"], 1)
        self.assertTrue(state["pending"])
        self.assertNotIn("file", state["options"][0])        # paths stay on the server
        self.assertTrue(runner.design_file(self.project["id"], state["options"][1]["file_name"]).is_file())

    def test_one_failed_sample_fails_the_step_loudly(self):
        run = runner.start_run(self.project["id"], "website_build")
        runner._store_artifact(run["id"], self.project["id"], {"id": "design_directions", "title": "d"},
                               json.dumps(DIRECTIONS), "json")
        replies = iter([{"text": PAGE, "provider": "chatgpt"}, {"text": "sorry, no", "provider": "chatgpt"}])
        with mock.patch.object(providers, "complete", side_effect=lambda *a, **k: next(replies)), \
                mock.patch.object(runner.browser, "capture_set", return_value=[]):
            with self.assertRaises(RuntimeError):
                runner._design_samples(runner._context(run["id"], self.project), self.project, run["id"])

    def test_a_bare_approve_cannot_skip_the_choice(self):
        _, appr = self.run_at_design_gate()
        with self.assertRaises(ValueError):
            agency.approval_decide(appr, "approved")

    def test_picking_records_the_choice_and_feeds_the_build(self):
        rid, appr = self.run_at_design_gate()
        out = runner.pick_design(appr, "b")
        self.assertEqual(out["choice"], "b")
        state = runner.design_state(self.project["id"])
        self.assertEqual(state["chosen"], "b")
        self.assertIsNone(state["pending"])
        chosen = playbooks._chosen_design(runner._context(rid, self.project))
        self.assertIn("Night Shift", chosen)
        self.assertIn("<h1>Hi</h1>", chosen)
        with self.assertRaises(ValueError):
            runner.pick_design(appr, "c")

    def test_design_files_stay_in_the_project_folder(self):
        for bad in ("../agency.db", "index.html", "design-r1-c.html", "design-r1-a.html/../../x"):
            with self.assertRaises(KeyError):
                runner.design_file(self.project["id"], bad)

    def test_redo_with_feedback(self):
        rid, appr = self.run_at_design_gate()
        out = runner.redo_from(rid, "design_directions", "Warmer, and show real smiles in the illustration")
        self.assertEqual(out["redo"][:3], ["design_directions", "design_samples", "design_pick"])
        run = runner.get_run(rid)
        self.assertEqual(run["state"], "queued")
        states = {s["stage_id"]: s["state"] for s in run["stages"]}
        self.assertEqual(states["design_directions"], "pending")
        self.assertEqual(states["copy"], "done")             # earlier work is kept
        with agency._conn() as c:
            self.assertEqual(c.execute("SELECT status FROM approvals WHERE id=?", (appr,)).fetchone()["status"], "withdrawn")
        ctx = runner._context(rid, self.project)
        self.assertIn("Warmer", ctx["feedback_design_directions"])
        self.assertIn("Warmer", playbooks.get("website_build")["stages"][4]["prompt"](ctx))

    def test_redo_never_interrupts_a_working_run(self):
        rid, _ = self.run_at_design_gate()
        runner._set_run(rid, state="running")
        with self.assertRaises(ValueError):
            runner.redo_from(rid, "design_directions")

    def test_runs_from_before_the_design_step_skip_it(self):
        run = runner.start_run(self.project["id"], "website_build")
        with agency._conn() as c:
            c.execute("DELETE FROM stage_runs WHERE run_id=? AND stage_id LIKE 'design_%'", (run["id"],))
            c.execute("UPDATE stage_runs SET state='done' WHERE run_id=?", (run["id"],))
        with mock.patch.object(runner, "_run_llm") as llm, mock.patch.object(runner, "_run_tool") as tool:
            runner._execute_run(run["id"])
        llm.assert_not_called(); tool.assert_not_called()
        self.assertEqual(runner.get_run(run["id"])["state"], "completed")


class TeamChatTests(_Db):
    def reply(self, text):
        return mock.patch.object(providers, "complete", return_value={"text": text, "provider": "chatgpt", "seconds": 2})

    def test_chat_in_character_with_the_project(self):
        self.run_at_design_gate()
        with self.reply("Happy to. Design B leans darker; I can warm it up.") as model:
            out = agency.agent_chat("lumen", self.project["id"], "What do you think of the two designs?")
        system = model.call_args.kwargs["system"]
        self.assertIn("Sofia Marino", system)
        self.assertIn("Bright Smile Dental", system)
        self.assertIn("Harbour Morning", system)
        self.assertIn("design_directions", system)          # her own step
        self.assertIsNone(out["message"]["action"])
        thread = agency.agent_thread("lumen", self.project["id"])
        self.assertEqual([m["role"] for m in thread["messages"]], ["user", "agent"])

    def test_a_redo_is_proposed_then_confirmed(self):
        rid, _ = self.run_at_design_gate()
        text = 'I will push both further.\nACTION: {"do": "redo", "stage": "design_directions", "feedback": "bolder, warmer"}'
        with self.reply(text):
            out = agency.agent_chat("lumen", self.project["id"], "Both are too safe, redo them bolder")
        msg = out["message"]
        self.assertNotIn("ACTION", msg["text"])
        self.assertEqual((msg["action"]["stage"], msg["action"]["state"]), ("design_directions", "proposed"))
        self.assertGreater(msg["action"]["steps"], 3)
        done = agency.agent_confirm(msg["id"])
        self.assertEqual(done["run"]["redo"][0], "design_directions")
        self.assertIn("Sofia Marino started", done["event"]["text"])
        with self.assertRaises(ValueError):
            agency.agent_confirm(msg["id"])                  # one click, once
        self.assertEqual(runner.get_run(rid)["state"], "queued")

    def test_a_redo_of_someone_elses_work_is_dropped(self):
        self.run_at_design_gate()
        with self.reply('Sure.\nACTION: {"do": "redo", "stage": "copy", "feedback": "x"}'):
            out = agency.agent_chat("lumen", self.project["id"], "rewrite the copy")
        self.assertIsNone(out["message"]["action"])           # the copy is Priya's

    def test_unknown_person_and_empty_message(self):
        with self.assertRaises(KeyError):
            agency.agent_thread("nobody", "")
        with self.assertRaises(ValueError):
            agency.agent_chat("lumen", "", "   ")

    def test_routes(self):
        for r in ("/api/agency/agents/chat", "/api/agency/agents/confirm", "/api/agency/design/pick", "/api/agency/runs/redo"):
            self.assertIn(r, agency.POST_ROUTES)


if __name__ == "__main__":
    unittest.main()
