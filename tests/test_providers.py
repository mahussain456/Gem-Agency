"""Tests for the Claude / ChatGPT provider layer and the Jev decision adapter.

No network: every SDK call and HTTP request is mocked. What matters is that an
unconnected provider is reported honestly rather than silently skipped, that
failover moves to the next provider instead of killing a run, and that Jev's
typed answers are read correctly — including the case where Jev is absent and
the pipeline must decide for itself.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jev
import playbooks
import providers


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = providers.CREDS_PATH
        providers.CREDS_PATH = Path(self.tmp.name) / "creds.json"

    def tearDown(self):
        providers.CREDS_PATH = self._orig
        self.tmp.cleanup()

    def test_rejects_unknown_provider(self):
        with self.assertRaises(ValueError):
            providers.save_credentials("gemini", "k")

    def test_requires_a_key(self):
        for bad in ("", "   "):
            with self.assertRaises(ValueError):
                providers.save_credentials("claude", bad)

    def test_claude_gets_the_documented_default_model(self):
        providers.save_credentials("claude", "sk-test")
        self.assertEqual(providers._model("claude"), providers.CLAUDE_DEFAULT_MODEL)

    def test_chatgpt_has_no_guessed_model(self):
        """A model id is never invented — the operator picks from the real list."""
        providers.save_credentials("chatgpt", "sk-test")
        self.assertEqual(providers._model("chatgpt"), "")
        self.assertFalse(providers.available("chatgpt"), "unusable until a model is chosen")

    def test_set_model_and_disconnect(self):
        providers.save_credentials("chatgpt", "sk-test")
        providers.set_model("chatgpt", "some-model")
        self.assertEqual(providers._model("chatgpt"), "some-model")
        providers.disconnect("chatgpt")
        self.assertIsNone(providers._key("chatgpt"))

    def test_environment_key_is_a_fallback(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "from-env"}, clear=False):
            self.assertEqual(providers._key("claude"), "from-env")

    def test_stored_key_beats_environment(self):
        providers.save_credentials("claude", "stored-key")
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "from-env"}, clear=False):
            self.assertEqual(providers._key("claude"), "stored-key")


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = providers.CREDS_PATH
        providers.CREDS_PATH = Path(self.tmp.name) / "creds.json"

    def tearDown(self):
        providers.CREDS_PATH = self._orig
        self.tmp.cleanup()

    def test_unconnected_is_reported_not_hidden(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            st = providers.status()
        for name in ("claude", "chatgpt"):
            self.assertFalse(st[name]["connected"])
            self.assertIn("no API key", st[name]["error"])
        self.assertIn("_any", st)

    def test_status_reports_the_model_in_use(self):
        providers.save_credentials("claude", "k")
        with mock.patch.object(providers, "_sdk", return_value=object()):
            st = providers.status()
        self.assertEqual(st["claude"]["model"], providers.CLAUDE_DEFAULT_MODEL)
        self.assertEqual(st["claude"]["key_source"], "stored")


class FailoverTests(unittest.TestCase):
    """A provider running out of credit must not end a run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = providers.CREDS_PATH
        providers.CREDS_PATH = Path(self.tmp.name) / "creds.json"

    def tearDown(self):
        providers.CREDS_PATH = self._orig
        self.tmp.cleanup()

    def test_unknown_provider_rejected(self):
        with self.assertRaises(providers.ProviderError):
            providers.complete("hi", provider="nope")

    def test_no_fallback_means_the_error_surfaces(self):
        with mock.patch.object(providers, "_hermes", side_effect=providers.ProviderError("402 out of credit")):
            with self.assertRaises(providers.ProviderError) as ctx:
                providers.complete("hi", provider="hermes")
        self.assertIn("out of credit", str(ctx.exception))

    def test_fallback_uses_the_next_connected_provider(self):
        good = {"text": "rescued", "provider": "claude", "model": "claude-opus-5",
                "seconds": 1.0, "tokens_in": 5, "tokens_out": 2}
        with mock.patch.object(providers, "_hermes", side_effect=providers.ProviderError("402 out of credit")), \
             mock.patch.object(providers, "available", side_effect=lambda p: p == "claude"), \
             mock.patch.object(providers, "_claude", return_value=good):
            res = providers.complete("hi", provider="hermes", fallback=True)
        self.assertEqual(res["text"], "rescued")
        self.assertEqual(res["provider"], "claude")
        self.assertEqual(res["fallback_from"][0]["provider"], "hermes")
        self.assertIn("402", res["fallback_from"][0]["error"])

    def test_every_provider_failing_reports_all_of_them(self):
        with mock.patch.object(providers, "_hermes", side_effect=providers.ProviderError("gateway down")), \
             mock.patch.object(providers, "available", side_effect=lambda p: p == "claude"), \
             mock.patch.object(providers, "_claude", side_effect=providers.ProviderError("bad key")):
            with self.assertRaises(providers.ProviderError) as ctx:
                providers.complete("hi", provider="hermes", fallback=True)
        msg = str(ctx.exception)
        self.assertIn("gateway down", msg)
        self.assertIn("bad key", msg)


class JevTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = jev.CREDS_PATH
        jev.CREDS_PATH = Path(self.tmp.name) / "jev.json"

    def tearDown(self):
        jev.CREDS_PATH = self._orig
        self.tmp.cleanup()

    def test_unconnected_status_is_honest(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            st = jev.status()
            self.assertFalse(st["ok"])
            self.assertFalse(st["connected"])
            self.assertIn("Not connected", st["error"])

    def test_question_builders_match_the_api_shape(self):
        n = jev.noul("Is it broken?", {"yes": "a", "no": "b"})
        self.assertEqual(n["type"], "noul")
        self.assertEqual(n["criteria"], {"yes": "a", "no": "b"})
        c = jev.choice("Which?", {"a": "first", "b": "second"})
        self.assertEqual(c["type"], "choice")
        s = jev.score("How bad?", ["fine", "poor", "awful"])
        self.assertEqual(s["criteria"], ["fine", "poor", "awful"])

    def test_degenerate_questions_rejected(self):
        with self.assertRaises(ValueError):
            jev.choice("Which?", {"only": "one"})
        with self.assertRaises(ValueError):
            jev.score("How?", ["single"])

    def test_calls_refuse_without_a_key(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(jev.JevError):
                jev.decide("state", {"q": jev.noul("yes?")})

    def test_reads_each_answer_type(self):
        self.assertTrue(jev.yes({"noul": 0.9}))
        self.assertFalse(jev.yes({"noul": 0.2}))
        self.assertAlmostEqual(jev.probability({"noul": 0.42}), 0.42)
        pick, conf = jev.picked({"choice": "b", "confidence": 0.8, "probabilities": {"a": .2, "b": .8}})
        self.assertEqual((pick, conf), ("b", 0.8))
        value, label = jev.level({"score": 2.0, "legend": {"0": "low", "1": "mid", "2": "high"}})
        self.assertEqual((value, label), (2.0, "high"))

    def test_wrong_answer_type_is_loud(self):
        with self.assertRaises(jev.JevError):
            jev.yes({"choice": "a"})
        with self.assertRaises(jev.JevError):
            jev.picked({"noul": 0.5})

    def test_decide_parses_a_real_response(self):
        jev.save_credentials("ts-key")
        payload = json.dumps({"model": "jev-latest",
                              "answers": {"q": {"noul": 0.77}},
                              "usage": {"input_tokens": 10, "output_tokens": 1}}).encode()

        class Resp:
            def read(self): return payload
            def __enter__(self): return self
            def __exit__(self, *a): return False

        with mock.patch("urllib.request.urlopen", return_value=Resp()):
            res = jev.decide("some state", {"q": jev.noul("yes?")})
        self.assertEqual(res["answers"]["q"]["noul"], 0.77)
        self.assertEqual(res["provenance"], "verified")

    def test_missing_answer_is_an_error(self):
        jev.save_credentials("ts-key")
        payload = json.dumps({"model": "jev-latest", "answers": {}}).encode()

        class Resp:
            def read(self): return payload
            def __enter__(self): return self
            def __exit__(self, *a): return False

        with mock.patch("urllib.request.urlopen", return_value=Resp()):
            with self.assertRaises(jev.JevError):
                jev.decide("s", {"q": jev.noul("yes?")})

    def test_question_cap(self):
        jev.save_credentials("ts-key")
        with self.assertRaises(ValueError):
            jev.decide("s", {f"q{i}": jev.noul("?") for i in range(jev.MAX_QUESTIONS + 1)})


class RepairTriageTests(unittest.TestCase):
    """The repair stage must skip clean builds and never skip real defects."""

    def test_clean_build_skips(self):
        skip, why = playbooks._repair_needed({"visual_qa": {"defects": []}})
        self.assertTrue(skip)
        self.assertIn("nothing to repair", why)

    def test_serious_defect_always_repairs(self):
        for sev in ("critical", "high"):
            skip, why = playbooks._repair_needed(
                {"visual_qa": {"defects": [{"severity": sev, "title": "Overflow"}]}})
            self.assertFalse(skip, f"{sev} must not be skipped")
            self.assertIn("serious", why)

    def test_no_measurements_means_run(self):
        self.assertEqual(playbooks._repair_needed({}), (False, ""))

    def test_minor_defects_without_jev_still_repair(self):
        with mock.patch.object(jev, "connected", return_value=False):
            skip, why = playbooks._repair_needed(
                {"visual_qa": {"defects": [{"severity": "low", "title": "No og:title"}]}})
        self.assertFalse(skip)
        self.assertIn("not connected", why)

    def test_jev_can_skip_trivial_defects(self):
        with mock.patch.object(jev, "connected", return_value=True), \
             mock.patch.object(jev, "decide", return_value={
                 "answers": {"worth_rebuilding": {"noul": 0.1}}, "ms": 90}):
            skip, why = playbooks._repair_needed(
                {"visual_qa": {"defects": [{"severity": "low", "title": "No og:title"}]}})
        self.assertTrue(skip)
        self.assertIn("not worth a rebuild", why)

    def test_jev_can_require_the_repair(self):
        with mock.patch.object(jev, "connected", return_value=True), \
             mock.patch.object(jev, "decide", return_value={
                 "answers": {"worth_rebuilding": {"noul": 0.9}}, "ms": 90}):
            skip, why = playbooks._repair_needed(
                {"visual_qa": {"defects": [{"severity": "medium", "title": "Tiny text"}]}})
        self.assertFalse(skip)
        self.assertIn("worth repairing", why)

    def test_triage_failure_never_skips_work(self):
        with mock.patch.object(jev, "connected", return_value=True), \
             mock.patch.object(jev, "decide", side_effect=jev.JevError("rate limited")):
            skip, why = playbooks._repair_needed(
                {"visual_qa": {"defects": [{"severity": "low", "title": "x"}]}})
        self.assertFalse(skip, "a triage error must not cause work to be skipped")
        self.assertIn("triage unavailable", why)


class RunnerWiringTests(unittest.TestCase):
    def test_default_provider_is_the_brain_chain(self):
        import runner
        self.assertEqual(runner.DEFAULT_PROVIDER, providers.BRAIN)

    def test_stages_may_name_a_provider(self):
        import runner
        source = Path(runner.__file__).read_text(encoding="utf-8")
        self.assertIn('stage.get("provider")', source)
        self.assertIn("fallback=True", source)

    def test_skip_if_is_honoured(self):
        import runner
        source = Path(runner.__file__).read_text(encoding="utf-8")
        self.assertIn('stage.get("skip_if")', source)
        self.assertIn('state="skipped"', source)


if __name__ == "__main__":
    unittest.main()


class SignInTests(unittest.TestCase):
    """Anthropic can be authenticated by signing in; OpenAI cannot."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = providers.CREDS_PATH
        providers.CREDS_PATH = Path(self.tmp.name) / "creds.json"
        self.config = Path(self.tmp.name) / "anthropic"

    def tearDown(self):
        providers.CREDS_PATH = self._orig
        self.tmp.cleanup()

    def test_no_profile_means_not_signed_in(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_CONFIG_DIR": str(self.config)}, clear=True):
            self.assertFalse(providers.signed_in("claude"))

    def test_active_config_marker_counts_as_signed_in(self):
        self.config.mkdir(parents=True)
        (self.config / "active_config").write_text("default", encoding="utf-8")
        with mock.patch.dict("os.environ", {"ANTHROPIC_CONFIG_DIR": str(self.config)}, clear=True):
            self.assertTrue(providers.signed_in("claude"))


    def test_stored_credentials_count_as_signed_in(self):
        """The layout `ant auth login` really writes: credentials/<profile>.json."""
        (self.config / "credentials").mkdir(parents=True)
        (self.config / "credentials" / "default.json").write_text("{}", encoding="utf-8")
        with mock.patch.dict("os.environ", {"ANTHROPIC_CONFIG_DIR": str(self.config)}, clear=True):
            self.assertTrue(providers.signed_in("claude"))

    def test_config_without_credentials_is_not_signed_in(self):
        """A profile config is settings, not a token — that is not authentication."""
        (self.config / "configs").mkdir(parents=True)
        (self.config / "configs" / "default.json").write_text("{}", encoding="utf-8")
        with mock.patch.dict("os.environ", {"ANTHROPIC_CONFIG_DIR": str(self.config)}, clear=True):
            self.assertFalse(providers.signed_in("claude"))

    def test_config_dir_agrees_with_the_sdk(self):
        """We must look where the SDK looks.

        Hardcoding the POSIX path meant a real `ant auth login` on Windows —
        which stores under %APPDATA%\\Anthropic — was never detected.
        """
        try:
            from anthropic.lib.credentials._constants import _config_dir
        except Exception:
            self.skipTest("SDK does not expose _config_dir")
        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("ANTHROPIC_CONFIG_DIR", None)
            self.assertEqual(providers._anthropic_config_dir(), _config_dir())

    def test_auth_token_env_counts_as_signed_in(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_AUTH_TOKEN": "oauth-token"}, clear=True):
            self.assertTrue(providers.signed_in("claude"))

    def test_chatgpt_can_never_be_signed_in(self):
        """OpenAI has no consumer sign-in path; claiming otherwise would mislead."""
        self.config.mkdir(parents=True)
        (self.config / "active_config").write_text("default", encoding="utf-8")
        with mock.patch.dict("os.environ", {"ANTHROPIC_CONFIG_DIR": str(self.config),
                                            "ANTHROPIC_AUTH_TOKEN": "x"}, clear=True):
            self.assertFalse(providers.signed_in("chatgpt"))

    def test_signed_in_claude_is_available_without_a_key(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_AUTH_TOKEN": "oauth-token"}, clear=True), \
             mock.patch.object(providers, "_sdk", return_value=object()):
            self.assertTrue(providers.available("claude"))

    def test_status_names_the_auth_method(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_AUTH_TOKEN": "oauth-token"}, clear=True), \
             mock.patch.object(providers, "_sdk", return_value=object()):
            st = providers.status()
        self.assertTrue(st["claude"]["connected"])
        self.assertEqual(st["claude"]["key_source"], "signed in")

    def test_hint_is_accurate_for_both(self):
        h = providers.signin_hint()
        self.assertTrue(h["claude"]["supported"])
        self.assertEqual(h["claude"]["command"], "ant auth login")
        self.assertFalse(h["chatgpt"]["supported"])
        self.assertIn("separate product", h["chatgpt"]["note"])

    def test_claude_error_mentions_both_routes(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_CONFIG_DIR": str(self.config)}, clear=True):
            st = providers.status()
        self.assertIn("ant auth login", st["claude"]["error"])


class ChatModelFilterTests(unittest.TestCase):
    """The first entry is preselected in the picker, so order and filtering matter."""

    IDS = ["gpt-3.5-turbo", "gpt-3.5-turbo-instruct", "chatgpt-image-latest", "gpt-4o",
           "gpt-4o-2024-08-06", "gpt-4o-audio-preview", "gpt-4o-realtime-preview", "gpt-4o-mini-tts",
           "gpt-5.5", "gpt-5.5-2026-04-23", "gpt-6-sol", "o3", "text-embedding-3-small",
           "whisper-1", "dall-e-3", "gpt-4o-search-preview", "gpt-4o-transcribe"]

    def test_only_chat_capable_models_remain(self):
        out = providers.chat_models(self.IDS)
        for bad in ("gpt-3.5-turbo-instruct", "chatgpt-image-latest", "gpt-4o-audio-preview",
                    "gpt-4o-realtime-preview", "gpt-4o-mini-tts", "text-embedding-3-small",
                    "whisper-1", "dall-e-3", "gpt-4o-search-preview", "gpt-4o-transcribe"):
            self.assertNotIn(bad, out)
        for good in ("gpt-3.5-turbo", "gpt-4o", "gpt-5.5", "gpt-6-sol", "o3"):
            self.assertIn(good, out)

    def test_newest_family_first_and_aliases_before_snapshots(self):
        out = providers.chat_models(self.IDS)
        self.assertEqual(out[0], "gpt-6-sol")
        self.assertLess(out.index("gpt-5.5"), out.index("gpt-5.5-2026-04-23"))
        self.assertLess(out.index("gpt-4o"), out.index("gpt-4o-2024-08-06"))
        self.assertEqual(out[-1], "gpt-3.5-turbo")
