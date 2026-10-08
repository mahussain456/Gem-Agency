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


class WhisperFlowTests(_Db):
    """Whisper on this PC: plain speech skips the model; corrections do not."""

    def whisper(self, text=None, error=None):
        import whisper_engine
        if error:
            return mock.patch.object(whisper_engine, "transcribe", side_effect=RuntimeError(error))
        return mock.patch.object(whisper_engine, "transcribe", return_value={"text": text, "seconds": 0.6, "audio_seconds": 8})

    def test_plain_speech_needs_no_model(self):
        with self.whisper("Um, we should, uh, ship it on Monday."), mock.patch.object(jarvis, "_quick") as model:
            out = jarvis.dictate("dictate", b"\0" * 32000)
        model.assert_not_called()
        self.assertEqual(out["text"], "We should ship it on Monday.")
        self.assertEqual((out["provider"], out["ears"]), ("local", "whisper"))

    def test_a_change_of_mind_goes_to_the_model(self):
        with self.whisper("Launch on Thursday, no wait, Friday."),                 mock.patch.object(jarvis, "_quick", return_value={"text": "Launch on Friday.", "provider": "chatgpt"}) as model:
            out = jarvis.dictate("dictate", b"\0" * 32000)
        model.assert_called_once()
        self.assertEqual(out["text"], "Launch on Friday.")
        self.assertEqual(out["ears"], "whisper")

    def test_an_edit_always_uses_the_model(self):
        with self.whisper("Make it shorter."),                 mock.patch.object(jarvis, "_quick", return_value={"text": "Short.", "provider": "chatgpt"}) as model:
            out = jarvis.dictate("edit", b"\0" * 32000, selection="A long sentence here.")
        model.assert_called_once()
        self.assertEqual(out["text"], "Short.")

    def test_whisper_down_uses_the_browsers_words_and_says_so(self):
        with self.whisper(error="no model"),                 mock.patch.object(jarvis, "_quick", return_value={"text": "Ship it.", "provider": "chatgpt"}):
            out = jarvis.dictate("dictate", b"\0" * 32000, heard="ship it")
        self.assertEqual((out["text"], out["ears"]), ("Ship it.", "browser"))
        self.assertIn("Whisper unavailable", out["note"])

    def test_nothing_heard(self):
        with self.whisper(""):
            with self.assertRaises(ValueError):
                jarvis.dictate("dictate", b"\0" * 32000)

    def test_command_mode_drops_the_full_stop(self):
        with self.whisper("Um, open approvals."):
            self.assertEqual(jarvis.dictate("command", b"\0" * 32000)["text"], "Open approvals")

    def test_fillers_and_markers(self):
        self.assertEqual(jarvis.strip_fillers("Hmm. Okay, call Omar."), "Okay, call Omar.")
        self.assertEqual(jarvis.strip_fillers("The umbrella is uh here."), "The umbrella is here.")
        self.assertTrue(jarvis.needs_model("Thursday, no wait, Friday"))
        self.assertTrue(jarvis.needs_model("first the logo, second the menu"))
        self.assertFalse(jarvis.needs_model("Call the client about the homepage."))

    def test_silence_is_not_sent_to_whisper(self):
        import whisper_engine
        self.assertEqual(whisper_engine.transcribe(b"\0" * 64000)["text"], "")
        self.assertEqual(whisper_engine.transcribe(b"\0" * 100)["text"], "")

    def test_routes(self):
        self.assertIn("/api/agency/voice/dictate", agency.POST_ROUTES)
        with self.assertRaises(ValueError):
            agency._voice_dictate({"mode": "dictate", "audio": "not base64!!"})


if __name__ == "__main__":
    unittest.main()
