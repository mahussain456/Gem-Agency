"""Flow (talk instead of type) and the team's real names."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency
import jarvis
import providers


class _Db(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = agency.AGENCY_DB
        agency.AGENCY_DB = Path(self._tmp.name) / "flow-test.db"
        agency._initialized = False
        agency.init_db()

    def tearDown(self):
        agency.AGENCY_DB = self._orig
        agency._initialized = False
        self._tmp.cleanup()


class TeamNameTests(_Db):
    def test_new_install_has_people(self):
        names = {a["id"]: a["name"] for a in agency.agents_list()}
        self.assertEqual(names["scout"], "Maya Collins")
        self.assertEqual(names["orchestrator"], "Daniel Reyes")
        self.assertNotIn("Scribe", names.values())

    def test_existing_install_is_renamed_but_custom_names_are_kept(self):
        mig = dict(agency.MIGRATIONS)[7]
        with agency._conn() as c:
            c.execute("UPDATE agents SET name='Scout', role='Research and discovery' WHERE id='scout'")
            c.execute("UPDATE agents SET name='My Writer' WHERE id='scribe'")
            c.executescript(mig)
            rows = {r["id"]: (r["name"], r["role"]) for r in c.execute("SELECT id, name, role FROM agents")}
        self.assertEqual(rows["scout"], ("Maya Collins", "Research lead"))
        self.assertEqual(rows["scribe"][0], "My Writer")

    def test_jarvis_knows_first_names(self):
        team = jarvis._team()
        self.assertIn("@scout = Maya Collins (Research lead)", team)
        self.assertIn("@all", team)


class FlowTests(_Db):
    def complete(self, text="Let's launch on Friday.", provider="chatgpt"):
        return mock.patch.object(jarvis, "_quick", return_value={"text": text, "provider": provider, "seconds": 1.2})

    def test_dictation_is_cleaned_by_the_brain(self):
        with self.complete() as c:
            out = jarvis.flow("dictate", "um so let's launch on thursday no wait friday", field="Brief")
        self.assertEqual(out["text"], "Let's launch on Friday.")
        self.assertTrue(out["polished"])
        self.assertEqual(out["provider"], "chatgpt")
        system = c.call_args.args[0]
        self.assertIn("Maya Collins", system)          # names spelled right
        self.assertIn("Brief", system)                 # where the text is going
        self.assertIn("self-corrections", system)

    def test_quotes_and_preamble_are_stripped(self):
        with self.complete('Here is the cleaned text: "Call Omar tomorrow."'):
            self.assertEqual(jarvis.flow("dictate", "call omar tomorrow")["text"], "Call Omar tomorrow.")

    def test_no_model_means_as_heard_and_says_so(self):
        with mock.patch.object(jarvis, "_quick", side_effect=providers.ProviderError("every provider failed")):
            out = jarvis.flow("dictate", "um we should uh ship it")
        self.assertFalse(out["polished"])
        self.assertEqual(out["text"], "We should ship it.")
        self.assertIn("no model answered", out["note"])

    def test_edit_needs_a_selection_and_a_model(self):
        with self.assertRaises(ValueError):
            jarvis.flow("edit", "make it shorter", selection="  ")
        with self.complete("Short."):
            self.assertEqual(jarvis.flow("edit", "make it shorter", selection="A long sentence here.")["text"], "Short.")
        with mock.patch.object(jarvis, "_quick", side_effect=RuntimeError("down")):
            with self.assertRaises(RuntimeError):
                jarvis.flow("edit", "make it shorter", selection="A long sentence.")

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            jarvis.flow("shout", "hello")
        with self.assertRaises(ValueError):
            jarvis.flow("dictate", "   ")

    def test_tidy(self):
        self.assertEqual(jarvis.tidy("uh hello there"), "Hello there.")
        self.assertEqual(jarvis.tidy("is it live?"), "Is it live?")
        self.assertEqual(jarvis.tidy("um"), "")

    def test_route_is_registered(self):
        self.assertIn("/api/agency/voice/flow", agency.POST_ROUTES)


if __name__ == "__main__":
    unittest.main()
