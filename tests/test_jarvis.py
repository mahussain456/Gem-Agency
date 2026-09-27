"""Tests for Jarvis, the voice assistant's server side.

No network: overview, runs and providers are mocked. What matters is that the
model is grounded in real rows, that it can only ever ask to *navigate* (a
hallucinated or injected action is dropped, never executed or read aloud),
and that failures surface instead of becoming a confident wrong answer.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency
import jarvis
import providers
import runner

OVERVIEW = {
    "generated_at": "2026-09-26T21:00:00+00:00",
    "counts": {"clients": 1, "projects": 2, "approvals_pending": 1, "approvals_blocking": 1,
               "tasks_open": 0, "keywords": 4},
    "mrr_cents": 250000, "mrr_provenance": "declared",
    "projects": [
        {"name": "Northgate Plumbing", "status": "active", "health": "on_track",
         "stage": 11, "stage_total": 11},
        {"name": "ZZ E2E Test - Ember & Oak", "status": "active", "health": "on_track"},
    ],
    "clients": [{"name": "GlobalSync", "status": "active"}],
    "approvals": [{"title": "Approve homepage copy", "blocking": 1}],
    "attention": [{"severity": "medium", "title": "Search Console authentication failed"}],
    "integrations": {"gsc": {"state": "error"}, "hermes_gateway": {"state": "configured"}},
    "agents": [{"name": "Scout", "status": "running"}, {"name": "Scribe", "status": "idle"}],
    "activity": [{"action": "run_failed", "actor": "operator", "ts": "2026-09-14T21:54:20"}],
}
RUNS = [{"project_name": "GlobalSync Audit", "playbook": "seo_campaign", "state": "failed",
         "error": "Billing or credits exhausted"}]


class ParseActionTests(unittest.TestCase):
    def test_whitelisted_route_becomes_an_action_and_leaves_the_text(self):
        text, acts = jarvis.parse_actions("Two approvals are waiting. [[open:approvals]]")
        self.assertEqual(text, "Two approvals are waiting.")
        self.assertEqual(acts, [{"type": "open", "route": "approvals", "label": "Approvals"}])

    def test_unknown_route_is_dropped_and_not_read_aloud(self):
        text, acts = jarvis.parse_actions("Done. [[open:payments]]")
        self.assertEqual(acts, [])
        self.assertNotIn("payments", text)
        self.assertNotIn("[[", text)

    def test_only_one_action_is_honoured(self):
        _, acts = jarvis.parse_actions("x [[open:clients]] [[open:models]]")
        self.assertEqual([a["route"] for a in acts], ["clients"])

    def test_overview_route_is_the_empty_key(self):
        _, acts = jarvis.parse_actions("Here you go. [[open:]]")
        self.assertEqual(acts[0]["route"], "")

    def test_markdown_is_stripped_for_speech(self):
        text, _ = jarvis.parse_actions("**Three** sites are `live`.")
        self.assertEqual(text, "Three sites are live.")

    def test_only_navigation_verbs_parse(self):
        # an action vocabulary beyond "open" must not exist at all
        _, acts = jarvis.parse_actions("[[approve:all]] [[start:builder]] [[delete:clients]]")
        self.assertEqual(acts, [])


class DigestTests(unittest.TestCase):
    def test_digest_is_built_from_real_rows(self):
        with mock.patch.object(agency, "overview", return_value=OVERVIEW), \
             mock.patch.object(runner, "runs_list", return_value=RUNS):
            d = jarvis.digest()
        for needle in ("1 clients", "2 websites/projects", "Northgate Plumbing",
                       "Approve homepage copy (blocking)", "gsc=error",
                       "Agents working now: 1", "Scout", "GlobalSync Audit",
                       "credits exhausted", "typed in, not billed"):
            self.assertIn(needle, d)

    def test_test_fixtures_are_hidden_from_the_model(self):
        with mock.patch.object(agency, "overview", return_value=OVERVIEW), \
             mock.patch.object(runner, "runs_list", return_value=[]):
            d = jarvis.digest()
        self.assertNotIn("Ember & Oak", d)
        self.assertIn("Pipeline runs: none yet.", d)

    def test_broken_overview_says_unavailable_rather_than_empty(self):
        with mock.patch.object(agency, "overview", side_effect=RuntimeError("db locked")):
            d = jarvis.digest()
        self.assertIn("unavailable", d)
        self.assertIn("db locked", d)

    def test_broken_runs_are_reported_not_hidden(self):
        with mock.patch.object(agency, "overview", return_value=OVERVIEW), \
             mock.patch.object(runner, "runs_list", side_effect=RuntimeError("boom")):
            d = jarvis.digest()
        self.assertIn("Pipeline runs: unavailable", d)


class AskTests(unittest.TestCase):
    def setUp(self):
        p1 = mock.patch.object(agency, "overview", return_value=OVERVIEW)
        p2 = mock.patch.object(runner, "runs_list", return_value=RUNS)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def test_rejects_empty_and_overlong_questions(self):
        with self.assertRaises(ValueError):
            jarvis.ask("   ")
        with self.assertRaises(ValueError):
            jarvis.ask("x" * (jarvis.MAX_QUESTION + 1))

    def test_asks_the_preferred_provider_with_failover(self):
        fake = {"text": "One approval is blocking. [[open:approvals]]",
                "provider": "claude", "model": "claude-opus-5", "seconds": 1.2}
        with mock.patch.object(providers, "complete", return_value=fake) as comp:
            out = jarvis.ask("what is blocking us?",
                             [{"role": "user", "text": "hello"}, {"role": "jarvis", "text": "Hi."}])
        args, kw = comp.call_args
        self.assertEqual(kw["provider"], providers.BRAIN)
        self.assertTrue(kw["fallback"])
        self.assertIn("AGENCY STATE", args[0])
        self.assertIn("Operator: hello", args[0])
        self.assertIn("Jarvis: Hi.", args[0])
        self.assertTrue(args[0].rstrip().endswith("Jarvis:"))
        self.assertIn("approvals", kw["system"])
        self.assertEqual(out["reply"], "One approval is blocking.")
        self.assertEqual(out["actions"][0]["route"], "approvals")
        self.assertFalse(out["fell_back"])

    def test_reports_when_it_had_to_fall_back(self):
        fake = {"text": "Fine.", "provider": "chatgpt", "model": "m",
                "fallback_from": [{"provider": "claude", "error": "down"}]}
        with mock.patch.object(providers, "complete", return_value=fake):
            out = jarvis.ask("status?")
        self.assertTrue(out["fell_back"])
        self.assertEqual(out["provider"], "chatgpt")

    def test_provider_failure_propagates_instead_of_a_made_up_answer(self):
        with mock.patch.object(providers, "complete",
                               side_effect=providers.ProviderError("every provider failed")):
            with self.assertRaises(providers.ProviderError):
                jarvis.ask("how are rankings?")

    def test_history_is_capped(self):
        hist = [{"role": "user", "text": f"turn {i}"} for i in range(20)]
        with mock.patch.object(providers, "complete", return_value={"text": "ok"}) as comp:
            jarvis.ask("next", hist)
        prompt = comp.call_args[0][0]
        self.assertNotIn("turn 13", prompt)
        self.assertIn("turn 19", prompt)


class BrainRoutingTests(unittest.TestCase):
    def test_jarvis_thinks_with_the_brain_chain(self):
        with mock.patch.object(agency, "overview", return_value=OVERVIEW), \
             mock.patch.object(runner, "runs_list", return_value=[]), \
             mock.patch.object(providers, "complete", return_value={"text": "ok"}) as comp:
            jarvis.ask("anything")
        self.assertEqual(comp.call_args.kwargs["provider"], providers.BRAIN)
        self.assertTrue(comp.call_args.kwargs["fallback"])


class WiringTests(unittest.TestCase):
    def test_endpoint_is_registered_as_an_authenticated_post(self):
        self.assertIn("/api/agency/voice/ask", agency.POST_ROUTES)

    def test_voice_routes_exist_in_the_frontend_route_table(self):
        routes_js = (Path(__file__).resolve().parent.parent / "app/q/js/routes.js").read_text("utf-8")
        for key in jarvis.ROUTES:
            if key:
                self.assertRegex(routes_js, rf"\b{key}\b", f"route '{key}' missing from routes.js")


if __name__ == "__main__":
    unittest.main()


class GatewayEnvelopeTests(unittest.TestCase):
    """The gateway returns 200 with an upstream error as the 'answer'.
    Found through Jarvis: a revoked key came back as the reply text."""

    def test_bare_status_line_is_an_error_not_an_answer(self):
        import llm
        for text in ("HTTP 401: User not found.", "  HTTP 403 - forbidden", "http 503: upstream unavailable"):
            self.assertIsNotNone(llm._provider_error(text), text)

    def test_prose_mentioning_a_status_code_is_still_content(self):
        import llm
        for text in ("Fix the HTTP 404: pages returned by /blog.", "Your site returns HTTP 500 on checkout.",
                     "Status 401 appears when the token expires."):
            self.assertIsNone(llm._provider_error(text), text)
