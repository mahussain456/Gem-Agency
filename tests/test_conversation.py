"""Jarvis in conversation: natural language in, speech and safe actions out.

No network: the provider stream is scripted. What matters is that the
ACTIONS line is never spoken (even when the marker arrives split across
tokens), only vocabulary actions against real records get through, nothing
in the vocabulary can spend money, and a provider that just failed stops
costing the operator a round trip on every sentence.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency
import jarvis
import providers

IDS = {("project", "p1"), ("client", "c1")}


class ActionVocabularyTests(unittest.TestCase):
    def test_valid_actions_survive(self):
        acts = jarvis.clean_actions([
            {"do": "open", "page": "approvals"},
            {"do": "open_record", "kind": "project", "id": "p1"},
            {"do": "draft_website", "brief": "A bold site for a plumber in Austin"},
        ], IDS)
        self.assertEqual([a["do"] for a in acts], ["open", "open_record", "draft_website"])

    def test_invented_records_pages_and_verbs_are_dropped(self):
        acts = jarvis.clean_actions([
            {"do": "open_record", "kind": "project", "id": "made-up"},
            {"do": "open", "page": "billing"},
            {"do": "approve", "id": "a1"},
            {"do": "start_build", "brief": "x" * 40},
            {"do": "delete_client", "id": "c1"},
            {"do": "draft_agent_task", "agent": "@hacker", "text": "hi"},
            {"do": "set_accent", "accent": "red"},
            "open approvals",
        ], IDS)
        self.assertEqual(acts, [])

    def test_at_most_three(self):
        acts = jarvis.clean_actions([{"do": "scroll", "to": "down"}] * 6, IDS)
        self.assertEqual(len(acts), jarvis.MAX_ACTIONS)

    def test_nothing_in_the_vocabulary_spends_or_writes(self):
        # drafts load a page for a human; the rest is navigation and display
        spending = {"start", "build", "run", "send", "approve", "delete", "buy", "publish", "generate"}
        for line in jarvis.CHAT_SYSTEM.splitlines():
            if '{"do":"' in line:
                verb = line.split('{"do":"')[1].split('"')[0]
                self.assertFalse(any(verb.startswith(w) for w in spending), verb)

    def test_browser_task_url_must_be_http(self):
        acts = jarvis.clean_actions([{"do": "draft_browser_task", "task": "check rankings", "url": "javascript:alert(1)"}], IDS)
        self.assertEqual(acts[0]["url"], "")


def scripted(*pieces, meta=None):
    def fake_stream(messages, **kw):
        fake_stream.kw = kw
        fake_stream.messages = messages
        yield "meta", meta or {"provider": "chatgpt", "label": "ChatGPT (OpenAI)", "model": "m", "fell_back_from": []}
        for p in pieces:
            yield "token", p
    return fake_stream


class ChatStreamTests(unittest.TestCase):
    def setUp(self):
        ov = {"projects": [{"id": "p1", "name": "Northgate Plumbing", "status": "active"}],
              "clients": [{"id": "c1", "name": "GlobalSync"}]}
        for p in (mock.patch.object(jarvis, "_overview_or_none", return_value=ov),
                  mock.patch.object(jarvis, "digest", return_value="AGENCY STATE: test")):
            p.start()
            self.addCleanup(p.stop)

    def run_turn(self, *pieces, text="open the approvals please", history=None, page="#models"):
        fs = scripted(*pieces)
        with mock.patch.object(providers, "stream", side_effect=fs):
            events = list(jarvis.chat_events(text, history or [], page))
        return events, fs

    def test_actions_line_is_never_spoken_even_when_split(self):
        events, _ = self.run_turn("Sure, opening approvals now. ", "ACT", "IONS: [{\"do\":\"op", "en\",\"page\":\"approvals\"}]")
        spoken = "".join(d for k, d in events if k == "token")
        self.assertEqual(spoken.strip(), "Sure, opening approvals now.")
        self.assertNotIn("ACT", spoken)
        self.assertIn(("actions", [{"do": "open", "page": "approvals"}]), events)
        self.assertEqual(events[-1][0], "done")

    def test_plain_conversation_streams_everything(self):
        events, _ = self.run_turn("Honestly? ", "Lead with reviews, ", "then speed.")
        self.assertEqual("".join(d for k, d in events if k == "token"), "Honestly? Lead with reviews, then speed.")
        self.assertFalse(any(k == "actions" for k, _ in events))

    def test_bad_actions_json_is_ignored_not_spoken(self):
        events, _ = self.run_turn("Done. ACTIONS: [{not json")
        self.assertEqual("".join(d for k, d in events if k == "token").strip(), "Done.")
        self.assertFalse(any(k == "actions" for k, _ in events))

    def test_context_history_page_and_speed(self):
        _, fs = self.run_turn("ok", history=[{"role": "user", "text": "hi"}, {"role": "jarvis", "text": "Hello."}],
                              page="#project/p1")
        system = fs.messages[0]["content"]
        self.assertIn("AGENCY STATE: test", system)
        self.assertIn("project p1 = Northgate Plumbing", system)
        self.assertIn("Websites (project/p1)", system)
        self.assertEqual([m["role"] for m in fs.messages[1:]], ["user", "assistant", "user"])
        self.assertEqual(fs.kw["effort"], "low")

    def test_seo_questions_stay_on_the_cloud_models(self):
        _, fs = self.run_turn("fine", text="which keywords should Northgate rank for?")
        self.assertEqual(fs.kw["provider"], providers.CLOUD)
        _, fs = self.run_turn("fine", text="tell me a joke")
        self.assertEqual(fs.kw["provider"], providers.BRAIN)

    def test_empty_input_is_refused(self):
        with self.assertRaises(ValueError):
            list(jarvis.chat_events("   ", [], ""))


class HealthyFirstTests(unittest.TestCase):
    def test_a_failing_provider_moves_to_the_back(self):
        with mock.patch.dict(providers._HEALTH, {}, clear=True), \
             mock.patch.object(providers, "brain_order", return_value=["claude", "chatgpt", "ollama"]):
            providers.record("claude", "credit balance too low")
            self.assertEqual(providers.chain_for("brain", True), ["chatgpt", "ollama", "claude"])
            self.assertEqual(providers.chain_for("cloud", True), ["chatgpt", "claude"])
            providers.record("claude")
            self.assertEqual(providers.chain_for("brain", True)[0], "claude")

    def test_voice_effort_reaches_claude(self):
        with mock.patch.object(providers, "available", return_value=True), \
             mock.patch.object(providers, "_stream_claude", side_effect=lambda m, t, e: iter(["hi"])) as sc, \
             mock.patch.dict(providers._HEALTH, {}, clear=True), \
             mock.patch.object(providers, "brain_order", return_value=["claude"]):
            list(providers.stream([{"role": "user", "content": "x"}], effort="low"))
        self.assertEqual(sc.call_args.args[2], "low")


class EndpointTests(unittest.TestCase):
    def test_streaming_endpoint_is_authenticated(self):
        src = Path(agency.__file__).read_text(encoding="utf-8")
        i = src.index("def handle_post(")
        nxt = src.find("\ndef ", i + 10)
        body = src[i:nxt if nxt > 0 else len(src)]
        self.assertLess(body.index("_auth_ok(handler)"), body.index('"/api/agency/voice/chat"'))

    def test_stream_errors_become_an_error_event(self):
        sent = []
        h = mock.Mock()
        h.wfile.write = lambda b: sent.append(b.decode())
        with mock.patch.object(jarvis, "chat_events", side_effect=providers.ProviderError("every provider failed")):
            agency._voice_chat_sse(h, {"text": "hi"})
        self.assertTrue(any('"type": "error"' in s and "every provider failed" in s for s in sent))


if __name__ == "__main__":
    unittest.main()
