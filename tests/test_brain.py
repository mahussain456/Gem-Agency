"""The brain: Claude and ChatGPT think for the whole dashboard, local Ollama catches.

No network: providers, Ollama's HTTP API and the gateway are all mocked.
What matters is that the chain runs in the operator's order, skips what is
not connected, falls back instead of failing, never splices two answers
together mid-stream, and that the "local" fallback really is local — an
Ollama cloud model must never stand in for it.
"""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency
import providers

TAGS = [
    {"name": "llama3.1:latest", "details": {"parameter_size": "8.0B", "context_length": 131072},
     "capabilities": ["completion", "tools"]},
    {"name": "Llama2:latest", "details": {"parameter_size": "7B", "context_length": 4096},
     "capabilities": ["completion"]},
    {"name": "nomic-embed-text:latest", "details": {}, "capabilities": ["embedding"]},
    {"name": "kimi-k2.6:cloud", "remote_host": "https://ollama.com:443", "remote_model": "kimi-k2.6",
     "details": {}, "capabilities": ["completion"]},
]


class Isolated(unittest.TestCase):
    """Each test gets its own credentials file and a warm, fake Ollama."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patches = [
            mock.patch.object(providers, "CREDS_PATH", Path(self.tmp.name) / "creds.json"),
            mock.patch.object(providers, "ollama_tags", return_value=(TAGS, "")),
            # failures are remembered process-wide; each test starts with none
            mock.patch.dict(providers._HEALTH, {}, clear=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class ChainOrderTests(Isolated):
    def test_default_brain_is_claude_then_chatgpt_then_local_ollama(self):
        self.assertEqual(providers.brain_order(), ["claude", "chatgpt", "ollama"])

    def test_hermes_is_not_in_the_default_chain(self):
        self.assertNotIn("hermes", providers.brain_order())

    def test_operator_order_is_saved_and_deduplicated(self):
        providers.set_brain_order(["chatgpt", "claude", "chatgpt", "ollama"])
        self.assertEqual(providers.brain_order(), ["chatgpt", "claude", "ollama"])

    def test_rejects_unknown_and_empty_orders(self):
        with self.assertRaises(ValueError):
            providers.set_brain_order(["claude", "gemini"])
        with self.assertRaises(ValueError):
            providers.set_brain_order([])
        with self.assertRaises(ValueError):
            providers.set_brain_order("claude")

    def test_chain_for_a_named_provider_puts_it_first(self):
        self.assertEqual(providers.chain_for("ollama", True), ["ollama", "claude", "chatgpt"])
        self.assertEqual(providers.chain_for("chatgpt", False), ["chatgpt"])
        self.assertEqual(providers.chain_for(None, True), ["claude", "chatgpt", "ollama"])
        with self.assertRaises(providers.ProviderError):
            providers.chain_for("gemini", True)


def fake_calls(fail=()):
    """Patch the three direct providers; names in `fail` raise."""
    def make(name):
        def call(*a, **k):
            if name in fail:
                raise providers.ProviderError(f"{name} is down")
            return {"text": f"from {name}", "provider": name, "model": f"{name}-model"}
        return call
    return [mock.patch.object(providers, "_claude", side_effect=make("claude")),
            mock.patch.object(providers, "_chatgpt", side_effect=make("chatgpt")),
            mock.patch.object(providers, "_ollama", side_effect=make("ollama"))]


class CompleteTests(Isolated):
    def run_with(self, live, fail=(), **kw):
        ps = fake_calls(fail) + [mock.patch.object(providers, "available", side_effect=lambda p: p in live)]
        for p in ps:
            p.start()
        try:
            return providers.complete("q", **kw)
        finally:
            for p in ps:
                p.stop()

    def test_brain_answers_with_the_first_connected_provider(self):
        self.assertEqual(self.run_with({"claude", "chatgpt", "ollama"})["provider"], "claude")
        self.assertEqual(self.run_with({"chatgpt", "ollama"})["provider"], "chatgpt")

    def test_falls_through_to_local_ollama_when_both_clouds_fail(self):
        out = self.run_with({"claude", "chatgpt", "ollama"}, fail={"claude", "chatgpt"})
        self.assertEqual(out["provider"], "ollama")
        self.assertEqual([a["provider"] for a in out["fallback_from"]], ["claude", "chatgpt"])

    def test_nothing_connected_says_so_plainly(self):
        with self.assertRaises(providers.ProviderError) as ctx:
            self.run_with(set())
        self.assertIn("no thinking engine is connected", str(ctx.exception))

    def test_every_failure_is_reported(self):
        with self.assertRaises(providers.ProviderError) as ctx:
            self.run_with({"claude", "ollama"}, fail={"claude", "ollama"})
        self.assertIn("claude is down", str(ctx.exception))
        self.assertIn("ollama is down", str(ctx.exception))

    def test_a_named_provider_is_tried_even_if_it_looks_disconnected(self):
        # so its own error is what the operator sees, not a silent skip
        out = self.run_with({"chatgpt"}, fail={"claude"}, provider="claude", fallback=True)
        self.assertEqual(out["provider"], "chatgpt")
        self.assertEqual(out["fallback_from"][0]["provider"], "claude")

    def test_json_mode_reaches_ollama(self):
        ps = fake_calls() + [mock.patch.object(providers, "available", return_value=True)]
        for p in ps:
            p.start()
        try:
            providers.complete("q", provider="ollama", json_mode=True)
            self.assertTrue(providers._ollama.call_args.kwargs["json_mode"])
        finally:
            for p in ps:
                p.stop()


class OllamaLocalOnlyTests(Isolated):
    def test_cloud_models_are_hidden_from_the_local_list(self):
        local, cloud = providers.ollama_local_models()
        names = [t["name"] for t in local]
        self.assertEqual(cloud, ["kimi-k2.6:cloud"])
        self.assertNotIn("kimi-k2.6:cloud", names)
        self.assertNotIn("nomic-embed-text:latest", names)   # embedding-only cannot chat
        self.assertIn("llama3.1:latest", names)

    def test_a_cloud_model_cannot_be_chosen_as_the_fallback(self):
        with self.assertRaises(ValueError) as ctx:
            providers.set_ollama(model="kimi-k2.6:cloud")
        self.assertIn("ollama.com", str(ctx.exception))

    def test_a_model_that_is_not_installed_is_refused(self):
        with self.assertRaises(ValueError):
            providers.set_ollama(model="mistral:7b")

    def test_unselected_or_missing_model_is_not_available(self):
        self.assertFalse(providers.available("ollama"))
        providers.set_ollama(model="llama3.1:latest")
        self.assertTrue(providers.available("ollama"))

    def test_unreachable_ollama_reports_where_it_looked(self):
        with mock.patch.object(providers, "ollama_tags",
                               return_value=(None, "Ollama is not reachable at http://127.0.0.1:11434 (refused)")):
            st = providers._ollama_status()
        self.assertFalse(st["connected"])
        self.assertFalse(st["reachable"])
        self.assertIn("127.0.0.1:11434", st["error"])

    def test_context_is_capped_for_an_8gb_gpu(self):
        self.assertEqual(providers._ollama_ctx("llama3.1:latest"), providers.OLLAMA_NUM_CTX)
        self.assertEqual(providers._ollama_ctx("Llama2:latest"), 4096)

    def test_chat_request_shape(self):
        providers.set_ollama(model="llama3.1:latest")
        with mock.patch.object(providers, "_ollama_json",
                               return_value={"message": {"content": "{}"}, "eval_count": 3}) as call:
            out = providers._ollama("prompt", "sys", 30, "llama3.1:latest", json_mode=True)
        body = call.call_args.args[1]
        self.assertEqual(body["format"], "json")
        self.assertFalse(body["stream"])
        self.assertEqual(body["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual(out["provider"], "ollama")
        self.assertEqual(out["tokens_out"], 3)

    def test_images_are_passed_as_base64(self):
        msgs = providers._to_ollama_messages([{"role": "user", "content": [
            {"type": "text", "text": "what is this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}]}])
        self.assertEqual(msgs[0], {"role": "user", "content": "what is this", "images": ["QUJD"]})


class StreamTests(Isolated):
    def stream_with(self, gens, live=("claude", "chatgpt", "ollama"), **kw):
        ps = [mock.patch.object(providers, "available", side_effect=lambda p: p in live),
              mock.patch.object(providers, "_stream_claude", side_effect=lambda m, t, *a: gens["claude"]()),
              mock.patch.object(providers, "_stream_chatgpt", side_effect=lambda m, t: gens["chatgpt"]()),
              mock.patch.object(providers, "_stream_ollama", side_effect=lambda m, t: gens["ollama"]())]
        for p in ps:
            p.start()
        try:
            return list(providers.stream([{"role": "user", "content": "hi"}], **kw))
        finally:
            for p in ps:
                p.stop()

    @staticmethod
    def ok(*pieces):
        def g():
            yield from pieces
        return g

    @staticmethod
    def boom(after=()):
        def g():
            yield from after
            raise providers.ProviderError("upstream 529")
        return g

    def test_meta_first_then_tokens(self):
        out = self.stream_with({"claude": self.ok("Hel", "lo"), "chatgpt": self.ok("x"), "ollama": self.ok("y")})
        self.assertEqual(out[0][0], "meta")
        self.assertEqual(out[0][1]["provider"], "claude")
        self.assertEqual("".join(d for k, d in out if k == "token"), "Hello")

    def test_falls_back_before_the_first_token(self):
        out = self.stream_with({"claude": self.boom(), "chatgpt": self.ok("fine"), "ollama": self.ok("y")})
        self.assertEqual(out[0][1]["provider"], "chatgpt")
        self.assertEqual(out[0][1]["fell_back_from"][0]["provider"], "claude")

    def test_never_splices_a_second_answer_after_the_first_started(self):
        with self.assertRaises(providers.ProviderError) as ctx:
            self.stream_with({"claude": self.boom(after=("half an ans",)),
                              "chatgpt": self.ok("other answer"), "ollama": self.ok("y")})
        self.assertIn("stopped mid-answer", str(ctx.exception))

    def test_gateway_is_skipped_without_a_hermes_stream(self):
        providers.set_brain_order(["hermes", "ollama"])
        out = self.stream_with({"claude": self.ok("c"), "chatgpt": self.ok("g"), "ollama": self.ok("local")},
                               live=("hermes", "ollama"))
        self.assertEqual(out[0][1]["provider"], "ollama")


class ClaudeMessageTests(unittest.TestCase):
    def test_system_is_separated_and_turns_alternate(self):
        system, msgs = providers._claude_messages([
            {"role": "system", "content": "You are Scout."},
            {"role": "assistant", "content": "orphan reply"},          # cannot lead
            {"role": "user", "content": "one"},
            {"role": "user", "content": "two"},                         # merged
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": [{"type": "text", "text": "look"},
                                         {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}}]},
        ])
        self.assertEqual(system, "You are Scout.")
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user"])
        self.assertEqual([b["text"] for b in msgs[0]["content"]], ["one", "two"])
        self.assertEqual(msgs[2]["content"][1]["source"],
                         {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"})


class PipelineRoutingTests(unittest.TestCase):
    def test_json_stages_on_the_brain_never_fall_through_to_the_gateway(self):
        import llm
        import runner
        with mock.patch.object(llm, "complete_json", side_effect=AssertionError("went to the gateway")), \
             mock.patch.object(providers, "complete", return_value={"text": '{"ok": true}', "provider": "claude"}) as comp:
            res = runner._complete_json_via(providers.BRAIN, "make json", "scout")
        self.assertEqual(res["data"], {"ok": True})
        self.assertTrue(comp.call_args.kwargs["json_mode"])
        self.assertEqual(comp.call_args.kwargs["provider"], providers.BRAIN)

    def test_an_explicit_hermes_stage_still_uses_the_gateway(self):
        import llm
        import runner
        with mock.patch.object(llm, "complete_json", return_value={"data": {}}) as cj:
            runner._complete_json_via("hermes", "p", "scout")
        cj.assert_called_once()


class BrainHealthTests(unittest.TestCase):
    def state(self, order, live):
        st = {"_brain": {"order": order, "live": live}}
        for p in live:
            st[p] = {"model": p + "-m"}
        return agency._brain_integration_state(st)

    def test_first_choice_answering_is_configured(self):
        s = self.state(["claude", "chatgpt", "ollama"], ["claude", "chatgpt", "ollama"])
        self.assertEqual((s["state"], s["active"], s["model"]), ("configured", "claude", "claude-m"))

    def test_running_on_a_fallback_is_degraded(self):
        s = self.state(["claude", "chatgpt", "ollama"], ["ollama"])
        self.assertEqual((s["state"], s["active"]), ("degraded", "ollama"))

    def test_nothing_live_is_an_error_that_names_the_impact(self):
        s = self.state(["claude", "chatgpt", "ollama"], [])
        self.assertEqual(s["state"], "error")
        self.assertIn("Pipelines", s["error"])

    def test_endpoints_are_registered(self):
        for path in ("/api/agency/models/brain", "/api/agency/models/ollama"):
            self.assertIn(path, agency.POST_ROUTES)


class GatewayEnvelopeStreamTests(unittest.TestCase):
    """The gateway streams its upstream's refusal as if it were the answer."""

    def sse(self, *pieces):
        lines = [f'data: {json.dumps({"choices": [{"delta": {"content": p}}]})}\n'.encode() for p in pieces]
        return io.BytesIO(b"".join(lines) + b"data: [DONE]\n")

    def run_gateway(self, *pieces):
        import server
        resp = self.sse(*pieces)
        resp.__enter__ = lambda *a: resp
        resp.__exit__ = lambda *a: False
        with mock.patch.object(server, "urlopen", return_value=resp), \
             mock.patch.object(server, "api_server_key", return_value="k"):
            return "".join(server._gateway_tokens("@scout", [{"role": "user", "content": "hi"}]))

    def test_refusal_envelope_raises_instead_of_streaming(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.run_gateway("HTTP 401: ", "User not found.")
        self.assertIn("refused", str(ctx.exception))

    def test_a_real_answer_streams_through(self):
        text = "Here are three competitors worth watching. " * 5
        self.assertEqual(self.run_gateway(*[text[i:i + 17] for i in range(0, len(text), 17)]), text)


if __name__ == "__main__":
    unittest.main()


class SearchWorkPolicyTests(Isolated):
    """SEO, AEO and GEO run on Claude or ChatGPT only -- never Ollama or Hermes."""

    def test_cloud_chain_is_claude_and_chatgpt_in_operator_order(self):
        self.assertEqual(providers.chain_for("cloud", True), ["claude", "chatgpt"])
        providers.set_brain_order(["ollama", "chatgpt", "claude"])
        self.assertEqual(providers.chain_for("cloud", True), ["chatgpt", "claude"])

    def test_removing_both_clouds_from_the_brain_does_not_open_search_to_ollama(self):
        providers.set_brain_order(["ollama"])
        self.assertEqual(providers.chain_for("cloud", True), ["claude", "chatgpt"])

    def test_search_work_never_reaches_ollama(self):
        ps = fake_calls(fail={"claude", "chatgpt"}) + [mock.patch.object(providers, "available", return_value=True)]
        for p in ps:
            p.start()
        try:
            with self.assertRaises(providers.ProviderError) as ctx:
                providers.complete("keyword map", provider="cloud", fallback=True)
            providers._ollama.assert_not_called()
        finally:
            for p in ps:
                p.stop()
        self.assertIn("only on Claude or ChatGPT", str(ctx.exception))

    def test_policy_recognises_search_work(self):
        cloud = ["write our keyword map", "improve rankings for plumber austin", "add JSON-LD schema",
                 "optimise for AI Overviews", "find backlink prospects", "fix the meta descriptions"]
        brain = ["draft a welcome email", "the geography of Texas", "summarise this week"]
        for t in cloud:
            self.assertEqual(providers.policy_for(t), "cloud", t)
        for t in brain:
            self.assertEqual(providers.policy_for(t), "brain", t)
        self.assertEqual(providers.policy_for("anything", "@rank"), "cloud")

    def test_every_search_stage_is_tagged(self):
        import playbooks
        for pid, ids in playbooks.SEARCH_STAGES.items():
            stages = {s["id"]: s for s in playbooks.PLAYBOOKS[pid]["stages"]}
            for sid in ids:
                self.assertEqual(stages[sid]["provider"], "cloud", f"{pid}.{sid}")
        # and every stage whose name says search is in the list
        for pid, book in playbooks.PLAYBOOKS.items():
            for s in book["stages"]:
                if s["kind"] in ("llm", "json", "markdown") and any(
                        w in s["id"] for w in ("seo", "aeo", "geo", "keyword", "schema", "backlink")):
                    self.assertEqual(s.get("provider"), "cloud", f"{pid}.{s['id']} is search work but not locked")


class FailureTrackingTests(Isolated):
    def test_a_failing_first_choice_makes_the_brain_degraded_not_answering(self):
        providers.save_credentials("claude", "k")
        providers.save_credentials("chatgpt", "k", "gpt-x")
        providers.record("claude", "Claude API 400: Your credit balance is too low")
        with mock.patch.object(providers, "signed_in", return_value=False), \
             mock.patch.object(providers, "_sdk", return_value=object()):
            st = providers.status()
        b = st["_brain"]
        self.assertEqual(b["active"], "chatgpt")
        self.assertIn("claude", b["failing"])
        self.assertNotEqual(b["active"], "claude")
        providers.record("claude")
        self.assertEqual(providers.failing("claude"), "")

    def test_readable_api_messages(self):
        e = Exception("Error code: 400 - {...}")
        e.body = {"type": "error", "error": {"type": "invalid_request_error", "message": "Your credit balance is too low"}}
        self.assertEqual(providers.api_message(e), "Your credit balance is too low")
