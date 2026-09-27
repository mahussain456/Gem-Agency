"""Tests for browser control and the rendered-DOM comparison.

The capture tests need a real Chrome or Edge; they skip cleanly when none is
installed rather than failing, so the suite still runs on a bare machine. The
guard and comparison tests never launch a browser and always run.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import audit
import browser

HAS_BROWSER = browser.find_browser() is not None
needs_browser = unittest.skipUnless(HAS_BROWSER, "no Chrome or Edge installed")

JS_PAGE = """<!DOCTYPE html><html lang="en"><head><title>Served Title</title></head>
<body><div id="t">SERVED_ONLY</div>
<script>
document.title = "Rendered Title";
document.getElementById('t').textContent = 'RENDERED';
const h = document.createElement('h1'); h.textContent = 'Runtime H1';
document.body.appendChild(h);
const s = document.createElement('script');
s.type = 'application/ld+json';
s.textContent = JSON.stringify({"@context":"https://schema.org","@type":"FAQPage"});
document.head.appendChild(s);
</script></body></html>"""

STATIC_PAGE = """<!DOCTYPE html><html lang="en"><head><title>Static</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Article"}</script>
</head><body><h1>Static H1</h1>
<p>This page states everything it has to say in the HTML the server sends, with
no client-side rendering involved at all, which is what we want to verify.</p>
</body></html>"""


class GuardTests(unittest.TestCase):
    """URL validation runs before any process is spawned."""

    def test_requires_a_url(self):
        with self.assertRaises(browser.BrowserError):
            browser._validate("")

    def test_rejects_unsupported_scheme(self):
        for bad in ("ftp://example.com", "data:text/html,<h1>x", "javascript:alert(1)"):
            with self.assertRaises(browser.BrowserError):
                browser._validate(bad)

    def test_rejects_file_outside_project(self):
        with self.assertRaises(browser.BrowserError) as ctx:
            browser._validate("file:///C:/Windows/System32/drivers/etc/hosts")
        self.assertIn("restricted to this project", str(ctx.exception))

    def test_allows_file_inside_project(self):
        target = browser.PROJECT_DIR / "server.py"
        self.assertTrue(browser._validate(browser.file_url(target)).startswith("file:"))

    def test_bare_host_gets_https(self):
        self.assertEqual(browser._validate("example.com"), "https://example.com")

    def test_known_viewports(self):
        for name in ("desktop", "tablet", "mobile"):
            self.assertIn(name, browser.VIEWPORTS)
        width, _ = browser.VIEWPORTS["mobile"]
        self.assertLess(width, browser.VIEWPORTS["desktop"][0])


class StatusTests(unittest.TestCase):
    def test_status_shape(self):
        st = browser.status()
        self.assertIn("ok", st)
        if st["ok"]:
            self.assertTrue(Path(st["path"]).is_file())
            self.assertIn(st["browser"], ("Google Chrome", "Microsoft Edge"))
        else:
            self.assertIn("error", st)


class RenderGapTests(unittest.TestCase):
    """The comparison is the product: what only exists after JavaScript."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # Files must live inside the project for the file:// guard to allow them.
        self.dir = browser.PROJECT_DIR / "workspace" / "_test_browser"
        self.dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()
        for f in self.dir.glob("*"):
            f.unlink()
        self.dir.rmdir()

    def _write(self, name: str, html: str) -> Path:
        path = self.dir / name
        path.write_text(html, encoding="utf-8")
        return path

    @needs_browser
    def test_detects_javascript_injected_content(self):
        path = self._write("js.html", JS_PAGE)
        gap = audit.render_gap(browser.file_url(path), JS_PAGE)
        self.assertEqual(gap["served"]["title"], "Served Title")
        self.assertEqual(gap["rendered"]["title"], "Rendered Title")
        titles = [f["title"] for f in gap["findings"]]
        self.assertTrue(any("Title changes" in t for t in titles), titles)
        self.assertTrue(any("H1 changes" in t for t in titles), titles)
        self.assertTrue(any("injected by JavaScript" in t for t in titles), titles)
        self.assertEqual(gap["provenance"], "verified")

    @needs_browser
    def test_clean_static_page_reports_no_gap(self):
        path = self._write("static.html", STATIC_PAGE)
        gap = audit.render_gap(browser.file_url(path), STATIC_PAGE)
        self.assertEqual(gap["served"]["title"], gap["rendered"]["title"])
        severities = {f["severity"] for f in gap["findings"]}
        self.assertNotIn("critical", severities)
        self.assertNotIn("high", severities)
        titles = [f["title"] for f in gap["findings"]]
        self.assertTrue(any("present without JavaScript" in t for t in titles), titles)

    @needs_browser
    def test_screenshot_and_viewports(self):
        path = self._write("shot.html", STATIC_PAGE)
        shots = browser.capture_set(browser.file_url(path), self.dir,
                                    viewports=("desktop", "mobile"))
        self.assertEqual(len(shots), 2)
        for shot in shots:
            self.assertGreater(shot["bytes"], 0)
            self.assertTrue(Path(shot["path"]).is_file())
            self.assertEqual(shot["provenance"], "verified")
        self.assertNotEqual(shots[0]["width"], shots[1]["width"])

    @needs_browser
    def test_render_returns_executed_dom(self):
        path = self._write("dom.html", JS_PAGE)
        result = browser.render(browser.file_url(path))
        self.assertIn("RENDERED", result["html"])
        self.assertNotIn("SERVED_ONLY", result["html"])


class PlaybookWiringTests(unittest.TestCase):
    """The browser stages must be registered and dispatchable."""

    def test_stages_present_in_playbooks(self):
        import playbooks
        website = [s["id"] for s in playbooks.get("website_build")["stages"]]
        seo = [s["id"] for s in playbooks.get("seo_campaign")["stages"]]
        self.assertIn("visual_qa", website)
        self.assertIn("render_check", seo)

    def test_tools_are_known_to_the_runner(self):
        import runner
        source = Path(runner.__file__).read_text(encoding="utf-8")
        for tool in ("visual_qa", "render_check"):
            self.assertIn(f'if tool == "{tool}":', source)

    def test_unknown_tool_still_rejected(self):
        import runner
        with self.assertRaises(ValueError):
            runner._run_tool({"id": "x", "title": "x", "kind": "tool", "tool": "nope"},
                             {}, {"name": "p", "id": "i"}, "run")


if __name__ == "__main__":
    unittest.main()
