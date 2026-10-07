"""Laya, the local decision engine, behind the dashboard's typed decisions.

No model runs here: the HTTP layer is mocked with the documented
/v1/systemone payloads, and the process layer is not started.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jev
import laya_engine
import playbooks


def reply(answers, model="english"):
    body = json.dumps({"answers": answers, "routing": {"model": model}, "usage": {"input_tokens": 40}}).encode()
    resp = mock.MagicMock()
    resp.__enter__.return_value.read.return_value = body
    return resp


QUESTION = {"q": jev.noul("Is it broken?")}


class EngineOrderTests(unittest.TestCase):
    def test_laya_answers_first_and_says_so(self):
        seen = []

        def urlopen(req, timeout=None):
            seen.append((req.full_url, req.headers.get("Authorization"), timeout))
            return reply({"q": {"noul": 0.9}})
        with mock.patch.object(laya_engine, "installed", return_value=True), \
             mock.patch.object(laya_engine, "ensure", return_value=True), \
             mock.patch.object(laya_engine, "token", return_value="tok"), \
             mock.patch.object(jev, "cloud_connected", return_value=True), \
             mock.patch.object(jev.urllib.request, "urlopen", side_effect=urlopen):
            r = jev.decide("state", QUESTION, timeout=20)
        self.assertEqual((r["engine"], r["engine_label"], r["model"]), ("laya", "Laya (local)", "english"))
        self.assertEqual(seen[0][0], laya_engine.URL + "/v1/systemone")
        self.assertEqual(seen[0][1], "Bearer tok")
        self.assertGreaterEqual(seen[0][2], 180)       # the first decision also loads a checkpoint

    def test_jev_cloud_takes_over_when_laya_fails(self):
        calls = []

        def urlopen(req, timeout=None):
            calls.append(req.full_url)
            if "127.0.0.1" in req.full_url:
                raise jev.urllib.error.URLError("refused")
            return reply({"q": {"noul": 0.2}})
        with mock.patch.object(laya_engine, "installed", return_value=True), \
             mock.patch.object(laya_engine, "ensure", return_value=True), \
             mock.patch.object(jev, "_key", return_value="sk"), \
             mock.patch.object(jev.urllib.request, "urlopen", side_effect=urlopen):
            r = jev.decide("state", QUESTION)
        self.assertEqual(r["engine"], "jev")
        self.assertEqual(calls, [laya_engine.URL + "/v1/systemone", jev.API_URL])

    def test_no_engine_is_a_clear_error(self):
        with mock.patch.object(laya_engine, "installed", return_value=False), \
             mock.patch.object(jev, "cloud_connected", return_value=False):
            self.assertFalse(jev.connected())
            with self.assertRaisesRegex(jev.JevError, "install Laya"):
                jev.decide("state", QUESTION)

    def test_a_laya_that_will_not_start_is_reported(self):
        with mock.patch.object(laya_engine, "installed", return_value=True), \
             mock.patch.object(laya_engine, "ensure", return_value=False), \
             mock.patch.object(jev, "cloud_connected", return_value=False):
            with self.assertRaisesRegex(jev.JevError, "could not start"):
                jev.decide("state", QUESTION)


class QuestionShapeTests(unittest.TestCase):
    def test_yes_no_criteria_use_the_protocols_keys(self):
        """Jev silently drops other keys and Laya refuses them (found 2026-10-07)."""
        self.assertEqual(jev.noul("?", {"yes": "a", "no": "b"})["criteria"], {"true": "a", "false": "b"})
        self.assertEqual(jev.noul("?", {"True": "a"})["criteria"], {"true": "a"})
        with self.assertRaises(ValueError):
            jev.noul("?", {"maybe": "c"})


class RebuildGateTests(unittest.TestCase):
    def gate(self, p):
        res = {"answers": {"worth_rebuilding": {"noul": p}}, "engine_label": "Laya (local)", "ms": 200}
        ctx = {"visual_qa": {"defects": [{"severity": "medium", "title": "Contact form does nothing"}]}}
        with mock.patch.object(jev, "connected", return_value=True), \
             mock.patch.object(jev, "decide", return_value=res):
            return playbooks._repair_needed(ctx)

    def test_only_a_confident_cosmetic_verdict_skips_the_repair(self):
        self.assertFalse(self.gate(0.23)[0])           # measured on a broken contact form: repair
        self.assertFalse(self.gate(0.31)[0])
        skip, why = self.gate(0.07)
        self.assertTrue(skip)
        self.assertIn("Laya (local)", why)

    def test_serious_defects_never_ask(self):
        with mock.patch.object(jev, "decide") as d:
            skip, _ = playbooks._repair_needed({"visual_qa": {"defects": [{"severity": "high", "title": "x"}]}})
        self.assertFalse(skip)
        d.assert_not_called()


class ManagerTests(unittest.TestCase):
    def test_environment_is_allowlisted_and_local(self):
        fake = {"PATH": "C:\\bin", "OPENAI_API_KEY": "sk-secret", "CLAUDE_CODE_MESSAGING_TOKEN": "t"}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(laya_engine, "STATE_DIR", Path(tmp)), \
             mock.patch.object(laya_engine, "TOKEN_FILE", Path(tmp) / "token"), \
             mock.patch.dict(laya_engine.os.environ, fake, clear=True):
            env = laya_engine._env()
            token = laya_engine.token()
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("CLAUDE_CODE_MESSAGING_TOKEN", env)
        self.assertEqual(env["LAYA_HOST"], "127.0.0.1")
        self.assertEqual(env["LAYA_API_KEY"], token)
        self.assertGreater(len(token), 30)
        self.assertEqual(env["HF_HUB_DISABLE_TELEMETRY"], "1")
        self.assertEqual(env["PYTHONPATH"], str(laya_engine.PKGS))

    def test_ensure_does_not_start_what_is_not_installed(self):
        with mock.patch.object(laya_engine, "health", return_value=None), \
             mock.patch.object(laya_engine, "installed", return_value=False), \
             mock.patch.object(laya_engine, "_spawn") as spawn:
            self.assertFalse(laya_engine.ensure(timeout=1))
        spawn.assert_not_called()

    def test_routes(self):
        import agency
        for path in ("/api/agency/laya/install", "/api/agency/laya/start", "/api/agency/laya/stop"):
            self.assertIn(path, agency.POST_ROUTES)
