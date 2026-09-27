"""Tests for the detect-fix-verify loop.

The layout probe must measure the real target width (Chrome will not open a
window narrower than ~500px, so a naive capture reports a cropped, misleading
layout), and the verify stage must keep a genuine improvement while undoing a
regression. Browser-dependent tests skip cleanly when none is installed.
"""

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import browser
import playbooks
import runner

HAS_BROWSER = browser.find_browser() is not None
needs_browser = unittest.skipUnless(HAS_BROWSER, "no Chrome or Edge installed")

BROKEN_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Deliberately Broken Layout Page</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{margin:0}.wide{width:900px}.tiny{font-size:9px}</style></head>
<body><h1>Broken</h1><div class="wide">Fixed at 900px, cannot fit a phone.</div>
<p class="tiny">Small print far too small to read.</p>
<a href="/x" style="display:inline-block;width:20px;height:18px">go</a></body></html>"""

HEALTHY_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>A Healthy Page That Fits Every Screen</title>
<meta name="description" content="Healthy reference page used to prove the verify stage keeps a genuine improvement.">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="canonical" href="https://x.test/">
<meta property="og:title" content="Healthy">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"FAQPage"}</script>
</head><body><h1>Healthy Page</h1><h2>How wide is it?</h2>
<p>Everything here fits inside a phone viewport without horizontal overflow.</p></body></html>"""


class LayoutProbeTests(unittest.TestCase):
    """The probe must measure the real target width, not Chrome's floor."""

    def setUp(self):
        self.dir = browser.PROJECT_DIR / "workspace" / "_test_probe"
        self.dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _page(self, name, html):
        path = self.dir / name
        path.write_text(html, encoding="utf-8")
        return path

    @needs_browser
    def test_measures_true_mobile_width(self):
        probe = browser.layout_probe(self._page("p.html", HEALTHY_PAGE), viewport="mobile", timeout=120)
        self.assertEqual(probe["viewport_width"], browser.VIEWPORTS["mobile"][0])
        self.assertEqual(probe["provenance"], "verified")

    @needs_browser
    def test_detects_overflow_and_names_the_offender(self):
        probe = browser.layout_probe(self._page("b.html", BROKEN_PAGE), viewport="mobile", timeout=120)
        self.assertTrue(probe["horizontal_overflow"])
        self.assertGreater(probe["overflow_px"], 100)
        self.assertTrue(probe["offenders"], "overflow reported with no offending element")
        self.assertIn("wide", probe["offenders"][0]["selector"])
        self.assertTrue(probe["tiny_text"])
        self.assertTrue(probe["small_targets"])

    @needs_browser
    def test_healthy_page_reports_no_overflow(self):
        probe = browser.layout_probe(self._page("h.html", HEALTHY_PAGE), viewport="mobile", timeout=120)
        self.assertFalse(probe["horizontal_overflow"])
        self.assertEqual(probe["overflow_px"], 0)

    @needs_browser
    def test_findings_shape(self):
        probe = browser.layout_probe(self._page("f.html", BROKEN_PAGE), viewport="mobile", timeout=120)
        findings = browser.layout_findings(probe)
        overflow = [f for f in findings if "overflow" in f["title"].lower()]
        self.assertEqual(overflow[0]["severity"], "high")
        for f in findings:
            self.assertIn(f["severity"], {"good", "low", "medium", "high", "critical"})
            self.assertTrue(f["title"])

    @needs_browser
    def test_probe_leaves_no_files_behind(self):
        browser.layout_probe(self._page("clean.html", HEALTHY_PAGE), viewport="mobile", timeout=120)
        leftovers = [f.name for f in self.dir.glob(".hermes-*")]
        self.assertEqual(leftovers, [], f"probe left temp files: {leftovers}")

    @needs_browser
    def test_narrow_screenshot_is_a_render_not_a_crop(self):
        path = self._page("s.html", HEALTHY_PAGE)
        shot = browser.screenshot(browser.file_url(path), self.dir / "s.png",
                                  viewport="mobile", timeout=120)
        self.assertEqual(shot["content_width"], browser.VIEWPORTS["mobile"][0])
        self.assertGreaterEqual(shot["image_width"], browser.MIN_WINDOW_WIDTH)
        self.assertIn("true", shot["note"].lower())


class RepairLoopTests(unittest.TestCase):
    """verify_repair keeps a real improvement and undoes a regression."""

    def setUp(self):
        self.project = {"id": "verify01-aaaa", "name": "verifyloop"}
        self.dir = runner._project_dir(self.project)
        self.stage = {"id": "verify_repair", "title": "Verify", "kind": "tool",
                      "tool": "verify_repair", "output": "json"}

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, before_html, after_html):
        (self.dir / "index.before-repair.html").write_text(before_html, encoding="utf-8")
        (self.dir / "index.html").write_text(after_html, encoding="utf-8")
        content, _kind, _note = runner._run_tool(self.stage, {}, self.project, "r")
        return json.loads(content)

    @needs_browser
    def test_regression_is_reverted(self):
        out = self._run(HEALTHY_PAGE, BROKEN_PAGE)
        self.assertTrue(out["reverted"])
        self.assertLess(out["after"]["score"], out["before"]["score"])
        self.assertIn("A Healthy Page", (self.dir / "index.html").read_text(encoding="utf-8"))

    @needs_browser
    def test_improvement_is_kept(self):
        out = self._run(BROKEN_PAGE, HEALTHY_PAGE)
        self.assertFalse(out["reverted"])
        self.assertGreater(out["after"]["score"], out["before"]["score"])
        self.assertIn("A Healthy Page", (self.dir / "index.html").read_text(encoding="utf-8"))

    def test_missing_baseline_fails_loudly(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "index.html").write_text(HEALTHY_PAGE, encoding="utf-8")
        with self.assertRaises(ValueError):
            runner._run_tool(self.stage, {}, self.project, "r")


class RepairStageTests(unittest.TestCase):
    def test_repair_stage_is_wired_in_order(self):
        stages = {s["id"]: s for s in playbooks.get("website_build")["stages"]}
        self.assertIn("repair", stages)
        self.assertIn("verify_repair", stages)
        self.assertEqual(stages["repair"]["writes_file"], "index.html")
        self.assertTrue(stages["repair"].get("backup_first"),
                        "repair must back up the build before overwriting it")
        ids = [s["id"] for s in playbooks.get("website_build")["stages"]]
        self.assertLess(ids.index("visual_qa"), ids.index("repair"))
        self.assertLess(ids.index("repair"), ids.index("verify_repair"))
        self.assertLess(ids.index("verify_repair"), ids.index("launch_plan"))

    def test_repair_prompt_forbids_redesign(self):
        stage = next(s for s in playbooks.get("website_build")["stages"] if s["id"] == "repair")
        text = stage["prompt"]({"name": "t", "brief": "b", "url": "u"}).lower()
        self.assertIn("fix only the defects", text)
        self.assertIn("do not hide overflow", text)

    def test_launch_plan_sees_the_repair_result(self):
        stage = next(s for s in playbooks.get("website_build")["stages"] if s["id"] == "launch_plan")
        text = stage["prompt"]({"name": "t", "brief": "b", "url": "u",
                                "verify_repair": "VERIFY_MARKER", "visual_qa": "VISUAL_MARKER"})
        self.assertIn("VERIFY_MARKER", text)
        self.assertIn("VISUAL_MARKER", text)


if __name__ == "__main__":
    unittest.main()
