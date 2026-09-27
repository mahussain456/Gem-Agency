"""Tests for the execution engine: parser, audit scoring, playbooks, JSON extraction.

No network and no gateway calls — those are exercised by real runs, not by the
unit suite. What is tested here is the logic that decides what a run does with
what it gets back.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import audit
import llm
import playbooks

SAMPLE = """<!DOCTYPE html><html lang="en"><head>
<title>Real Document Title</title>
<meta name="description" content="A description that is long enough to be considered reasonable for testing purposes only.">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="canonical" href="https://example.test/">
<meta property="og:title" content="OG Title">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"FAQPage",
 "mainEntity":[{"@type":"Question","name":"How fast?","acceptedAnswer":{"@type":"Answer","text":"Same day."}}]}</script>
</head><body>
<h1>The Only H1</h1>
<h2>How fast can you come out?</h2>
<p>Text body here with enough words to register as content on the page.</p>
<img src="a.png" alt="described image" width="10" height="10">
<svg viewBox="0 0 10 10"><title>SVG accessible name that must not become the page title</title></svg>
<a href="/one">1</a><a href="/two">2</a><a href="/three">3</a><a href="https://other.test/x">ext</a>
</body></html>"""


def _runner_tools() -> set[str]:
    """Tool names runner._run_tool actually dispatches on."""
    import re as _re
    import runner
    source = Path(runner.__file__).read_text(encoding="utf-8")
    return set(_re.findall(r'if tool == "([a-z_]+)":', source))

class ParserTests(unittest.TestCase):
    def setUp(self):
        self.p = audit.PageParser()
        self.p.feed(SAMPLE)

    def test_document_title_wins_over_svg_title(self):
        self.assertEqual(self.p.title, "Real Document Title")

    def test_core_fields(self):
        self.assertEqual(self.p.lang, "en")
        self.assertTrue(self.p.has_viewport)
        self.assertEqual(self.p.canonical, "https://example.test/")
        self.assertEqual(self.p.og.get("og:title"), "OG Title")
        self.assertIn("description", self.p.meta)

    def test_headings_and_images(self):
        self.assertEqual([t for lvl, t in self.p.headings if lvl == 1], ["The Only H1"])
        self.assertEqual(len(self.p.images), 1)
        self.assertEqual(self.p.images[0]["alt"], "described image")

    def test_schema_types_flattened(self):
        types = audit.schema_types(self.p.jsonld)
        self.assertIn("FAQPage", types)
        self.assertIn("Question", types)  # nested via mainEntity


class ScoringTests(unittest.TestCase):
    def test_health_score_penalties(self):
        self.assertEqual(audit.health_score({"good": 10}), 100)
        self.assertEqual(audit.health_score({"critical": 1}), 75)
        self.assertEqual(audit.health_score({"high": 2, "medium": 1}), 76)
        self.assertEqual(audit.health_score({"critical": 10}), 0)  # floored

    def test_severity_ordering(self):
        order = sorted(["low", "critical", "medium", "high"], key=lambda s: audit.SEV_ORDER[s])
        self.assertEqual(order, ["critical", "high", "medium", "low"])


class LocalAuditTests(unittest.TestCase):
    def test_local_build_audit(self):
        import runner
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "index.html"
            f.write_text(SAMPLE, encoding="utf-8")
            rep = runner._audit_local_html(f)
        self.assertEqual(rep["provenance"], "verified")
        self.assertEqual(rep["page"]["title"], "Real Document Title")
        self.assertIn("FAQPage", rep["page"]["schema_types"])
        titles = [f["title"] for f in rep["findings"]]
        self.assertTrue(any("question-shaped" in t for t in titles))
        self.assertGreaterEqual(rep["score"], 90)


class JSONExtractionTests(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(llm.extract_json('{"a": 1}'), {"a": 1})

    def test_fenced(self):
        self.assertEqual(llm.extract_json('```json\n{"a": 2}\n```'), {"a": 2})

    def test_prefaced(self):
        self.assertEqual(llm.extract_json('Sure, here you go:\n{"a": 3}\nHope that helps.'), {"a": 3})

    def test_array(self):
        self.assertEqual(llm.extract_json("[1, 2, 3]"), [1, 2, 3])

    def test_failure_is_loud(self):
        with self.assertRaises(llm.LLMError):
            llm.extract_json("no json at all here")


class PlaybookTests(unittest.TestCase):
    def test_both_playbooks_registered(self):
        ids = {p["id"] for p in playbooks.summaries()}
        self.assertEqual(ids, {"website_build", "seo_campaign"})

    def test_unknown_playbook_raises(self):
        with self.assertRaises(KeyError):
            playbooks.get("nope")

    def test_every_stage_is_well_formed(self):
        for pb in playbooks.PLAYBOOKS.values():
            seen = set()
            for s in pb["stages"]:
                self.assertNotIn(s["id"], seen, f"duplicate stage id in {pb['id']}")
                seen.add(s["id"])
                self.assertIn(s["kind"], {"llm", "json", "markdown", "tool", "gate"})
                if s["kind"] in {"llm", "json", "markdown"}:
                    self.assertTrue(callable(s["prompt"]))
                    self.assertTrue(s.get("agent"), f"{s['id']} has no agent")
                if s["kind"] == "tool":
                    # Derive the valid tools from the runner itself, so adding a
                    # tool does not silently make this assertion stale.
                    self.assertIn(s["tool"], _runner_tools(),
                                  f"{s['tool']} has no dispatch branch in runner.py")

    def test_prompts_render_with_empty_context(self):
        """A stage must not crash when an earlier artifact is missing."""
        ctx = {"name": "Test", "brief": "An idea", "url": "https://example.test"}
        for pb in playbooks.PLAYBOOKS.values():
            for s in pb["stages"]:
                if callable(s.get("prompt")):
                    text = s["prompt"](ctx)
                    self.assertIsInstance(text, str)
                    self.assertGreater(len(text), 50)

    def test_prompts_forbid_invented_metrics(self):
        """The honesty rule must be present in every generative prompt."""
        ctx = {"name": "T", "brief": "b", "url": "u"}
        for pb in playbooks.PLAYBOOKS.values():
            for s in pb["stages"]:
                if callable(s.get("prompt")):
                    self.assertIn("Never invent metrics", s["prompt"](ctx))

    def test_seo_keyword_stage_bans_volume_estimates(self):
        ctx = {"name": "T", "brief": "b", "url": "u"}
        stage = next(s for s in playbooks.SEO_CAMPAIGN["stages"] if s["id"] == "keywords")
        text = stage["prompt"](ctx).lower()
        self.assertIn("no search volumes", text)

    def test_outreach_stage_bans_spam_tactics(self):
        ctx = {"name": "T", "brief": "b", "url": "u"}
        stage = next(s for s in playbooks.SEO_CAMPAIGN["stages"] if s["id"] == "backlinks")
        text = stage["prompt"](ctx).lower()
        for banned in ("pbn", "paid links", "spam"):
            self.assertIn(banned, text)


if __name__ == "__main__":
    unittest.main()
