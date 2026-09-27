"""Tests for the DataForSEO adapter and the measure_keywords stage.

No live API calls: every request is mocked. What matters here is that the
envelope is parsed correctly, that cost caps hold, and above all that a
missing or broken connection produces an honest "unmeasured" result instead of
a fabricated number.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency
import dataforseo
import playbooks
import runner


def envelope(result, cost=0.01, status=20000, task_status=20000):
    return {"status_code": status, "status_message": "Ok.", "cost": cost,
            "tasks": [{"status_code": task_status, "status_message": "Ok.", "result": result}]}


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = dataforseo.CREDS_PATH
        dataforseo.CREDS_PATH = Path(self.tmp.name) / "creds.json"

    def tearDown(self):
        dataforseo.CREDS_PATH = self._orig
        self.tmp.cleanup()

    def test_unconfigured_status_is_honest(self):
        st = dataforseo.status()
        self.assertFalse(st["ok"])
        self.assertFalse(st["configured"])
        self.assertIn("Not connected", st["error"])

    def test_requires_both_parts(self):
        for login, password in (("", "p"), ("l", ""), ("", "")):
            with self.assertRaises(ValueError):
                dataforseo.save_credentials(login, password)

    def test_roundtrip_and_disconnect(self):
        dataforseo.save_credentials("me@example.com", "secret")
        self.assertEqual(dataforseo.credentials()["login"], "me@example.com")
        dataforseo.disconnect()
        self.assertEqual(dataforseo.credentials(), {})

    def test_calls_refuse_without_credentials(self):
        with self.assertRaises(dataforseo.DataForSEOError):
            dataforseo.search_volume(["x"])


class ParsingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = dataforseo.CREDS_PATH
        dataforseo.CREDS_PATH = Path(self.tmp.name) / "creds.json"
        dataforseo.save_credentials("me@example.com", "secret")

    def tearDown(self):
        dataforseo.CREDS_PATH = self._orig
        self.tmp.cleanup()

    def test_search_volume_shape(self):
        rows = [{"keyword": "emergency plumber", "search_volume": 2400, "cpc": 6.5, "competition": 0.8}]
        with mock.patch.object(dataforseo, "_post", return_value={"result": rows, "cost": 0.02, "ms": 120}):
            out = dataforseo.search_volume(["emergency plumber"])
        kw = out["keywords"][0]
        self.assertEqual(kw["volume"], 2400)
        self.assertEqual(kw["provenance"], "imported")
        self.assertEqual(out["cost_usd"], 0.02)

    def test_serp_position_found(self):
        items = [
            {"type": "featured_snippet"},
            {"type": "organic", "domain": "other.com", "rank_absolute": 1, "url": "https://other.com/a"},
            {"type": "organic", "domain": "www.mysite.com", "rank_absolute": 4, "url": "https://mysite.com/p"},
        ]
        with mock.patch.object(dataforseo, "_post",
                               return_value={"result": [{"items": items}], "cost": 0.003, "ms": 900}):
            out = dataforseo.serp_position("plumber manchester", "mysite.com")
        self.assertTrue(out["found"])
        self.assertEqual(out["position"], 4)
        self.assertEqual(out["landing_page"], "https://mysite.com/p")
        self.assertIn("featured_snippet", out["serp_features"])
        # A live SERP really was fetched, so this is verified, not imported.
        self.assertEqual(out["provenance"], "verified")

    def test_serp_position_absent_is_not_zero(self):
        items = [{"type": "organic", "domain": "other.com", "rank_absolute": 1, "url": "x"}]
        with mock.patch.object(dataforseo, "_post",
                               return_value={"result": [{"items": items}], "cost": 0.003, "ms": 100}):
            out = dataforseo.serp_position("term", "mysite.com")
        self.assertFalse(out["found"])
        self.assertIsNone(out["position"], "absence must be None, never a fake rank")
        self.assertIn("Not found", out["note"])

    def test_backlinks_summary(self):
        with mock.patch.object(dataforseo, "_post", return_value={
                "result": [{"backlinks": 812, "referring_domains": 96, "rank": 210}],
                "cost": 0.02, "ms": 300}):
            out = dataforseo.backlinks_summary("https://www.example.com/path")
        self.assertEqual(out["domain"], "example.com")
        self.assertEqual(out["backlinks"], 812)
        self.assertEqual(out["provenance"], "imported")

    def test_api_error_envelope_raises(self):
        class FakeResp:
            def read(self):
                return json.dumps(envelope([], status=40501)).encode()
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        with mock.patch("urllib.request.urlopen", return_value=FakeResp()):
            with self.assertRaises(dataforseo.DataForSEOError):
                dataforseo.search_volume(["x"])

    def test_task_error_envelope_raises(self):
        class FakeResp:
            def read(self):
                return json.dumps(envelope([], task_status=40401)).encode()
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        with mock.patch("urllib.request.urlopen", return_value=FakeResp()):
            with self.assertRaises(dataforseo.DataForSEOError):
                dataforseo.search_volume(["x"])


class GuardTests(unittest.TestCase):
    def test_keyword_cap_is_enforced(self):
        with self.assertRaises(ValueError) as ctx:
            dataforseo._clean_terms([f"kw{i}" for i in range(dataforseo.MAX_KEYWORDS + 1)])
        self.assertIn("caps a single call", str(ctx.exception))

    def test_empty_keyword_list_rejected(self):
        for bad in ([], ["", "  "]):
            with self.assertRaises(ValueError):
                dataforseo._clean_terms(bad)

    def test_domain_normalisation(self):
        for raw in ("https://www.Example.com/a/b", "http://example.com", "www.example.com", "example.com"):
            self.assertEqual(dataforseo._clean_domain(raw), "example.com")
        with self.assertRaises(ValueError):
            dataforseo._clean_domain("")


class MeasureKeywordsStageTests(unittest.TestCase):
    """The stage must never invent a figure, connected or not."""

    def setUp(self):
        self.stage = {"id": "measure_keywords", "title": "Measure", "kind": "tool",
                      "tool": "measure_keywords", "output": "json"}
        self.ctx = {"keywords": {"clusters": [{"cluster": "c", "keywords": ["alpha term", "beta term"]}],
                                 "quick_wins": [{"keyword": "gamma term"}]}}
        self.project = {"id": "p1", "name": "proj", "client_id": ""}

    def test_missing_connection_reports_unmeasured(self):
        with mock.patch.object(dataforseo, "status",
                               return_value={"ok": False, "error": "Not connected."}):
            content, _kind, note = runner._run_tool(self.stage, self.ctx, self.project, "r")
        out = json.loads(content)
        self.assertFalse(out["measured"])
        self.assertEqual(out["terms_proposed"], 3)
        self.assertIn("not measured", note)
        self.assertNotIn("volume", json.dumps(out.get("keywords", [])))

    def test_measured_values_are_attributed(self):
        vols = {"keywords": [{"keyword": "alpha term", "volume": 100, "cpc": 1.0, "competition": 0.1}],
                "cost_usd": 0.02, "ms": 10}
        with mock.patch.object(dataforseo, "status", return_value={"ok": True}), \
             mock.patch.object(dataforseo, "search_volume", return_value=vols), \
             mock.patch.object(dataforseo, "keyword_difficulty",
                               return_value={"keywords": [{"keyword": "alpha term", "difficulty": 33}],
                                             "cost_usd": 0.01, "ms": 5}):
            content, _kind, note = runner._run_tool(self.stage, self.ctx, self.project, "r")
        out = json.loads(content)
        self.assertTrue(out["measured"])
        rows = {r["keyword"]: r for r in out["keywords"]}
        self.assertEqual(rows["alpha term"]["volume"], 100)
        self.assertEqual(rows["alpha term"]["difficulty"], 33)
        self.assertEqual(rows["alpha term"]["provenance"], "imported")
        # Terms the API returned nothing for must not acquire a number.
        self.assertIsNone(rows["beta term"]["volume"])
        self.assertEqual(rows["beta term"]["provenance"], "unmeasured")
        self.assertEqual(out["cost_usd"], 0.03)

    def test_no_terms_fails_loudly(self):
        with self.assertRaises(ValueError):
            runner._run_tool(self.stage, {"keywords": {"clusters": []}}, self.project, "r")


class WiringTests(unittest.TestCase):
    def test_stage_is_in_the_playbook_before_content_plan(self):
        ids = [s["id"] for s in playbooks.get("seo_campaign")["stages"]]
        self.assertIn("measure_keywords", ids)
        self.assertLess(ids.index("keywords"), ids.index("measure_keywords"))
        self.assertLess(ids.index("measure_keywords"), ids.index("content_plan"))

    def test_content_plan_sees_the_measurements(self):
        stage = next(s for s in playbooks.get("seo_campaign")["stages"] if s["id"] == "content_plan")
        text = stage["prompt"]({"name": "t", "brief": "b", "url": "u",
                                "measure_keywords": "MEASURED_MARKER"})
        self.assertIn("MEASURED_MARKER", text)
        self.assertIn("do not", text.lower())

    def test_keyword_prompt_still_forbids_invention(self):
        """The agent proposes terms; the tool supplies numbers. Both rules hold."""
        stage = next(s for s in playbooks.get("seo_campaign")["stages"] if s["id"] == "keywords")
        self.assertIn("no search volumes", stage["prompt"]({"name": "t", "brief": "b", "url": "u"}).lower())


if __name__ == "__main__":
    unittest.main()
