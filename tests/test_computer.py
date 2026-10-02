"""Computer use: Claude drives an isolated browser.

The agent loop is exercised against a scripted Claude that returns the exact
block shapes Anthropic documents for computer_toolset_20260801 (member name
as the tool name, toolset_name "computer", batches of several calls). No API
call is made. A second class drives the real installed Chrome through every
action on a local page, with no network.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import computer

PNG = b"\x89PNG\r\n\x1a\nfake"


def use(name, inp=None, toolset=True, id_=None):
    b = NS(type="tool_use", id=id_ or f"toolu_{name}_{id(inp)}", name=name, input=inp or {})
    if toolset:
        b.toolset_name = "computer"
    return b


def msg(*content, stop="tool_use"):
    return NS(content=list(content), stop_reason=stop, usage=NS(input_tokens=100, output_tokens=20))


class FakeBrowser:
    def __init__(self, visible=False):
        self.calls, self.blocked, self.fail_on = [], [], set()

    def navigate(self, url):
        self.calls.append(("navigate", url))
        computer.check_url(url)
        return f"Opened {url}"

    def screenshot(self):
        return PNG

    def act(self, name, inp):
        self.calls.append((name, inp))
        if name in self.fail_on:
            raise ValueError("element not found")
        return PNG if name in ("screenshot", "zoom") else "OK"

    # computer use 2.0: structure
    def read_page(self, start=0):
        self.calls.append(("read_page", start))
        return 'URL: https://example.com/\nTitle: Example\n\nElements (1):\n  [1] link "Pricing" -> /pricing'

    def click_ref(self, ref):
        self.calls.append(("click_ref", ref))
        if int(ref) != 1:
            raise ValueError(f"there is no element {ref} on this page now; call read_page again")
        return 'Clicked [1] "Pricing". Now at https://example.com/pricing'

    def type_ref(self, ref, text, submit=False):
        self.calls.append(("type_ref", ref, text, submit))
        return f'Typed "{text}" into [{ref}]'

    def back(self):
        self.calls.append(("back",))
        return "Went back. Now at https://example.com/"

    def close(self):
        pass


class UrlSafetyTests(unittest.TestCase):
    def test_only_public_http_urls(self):
        with mock.patch.object(computer.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("93.184.215.14", 0))]):
            self.assertEqual(computer.check_url("https://example.com/a"), "https://example.com/a")
        for bad in ("file:///C:/Windows/win.ini", "javascript:alert(1)", "chrome://settings",
                    "http://localhost:51764/#approvals", "http://127.0.0.1:8643/v1", "http://[::1]/",
                    "http://10.0.0.5/", "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data",
                    "http://printer.local/"):
            with self.assertRaises(ValueError, msg=bad):
                computer.check_url(bad)

    def test_a_public_name_that_resolves_to_this_machine_is_blocked(self):
        computer._DNS.clear()
        with mock.patch.object(computer.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("127.0.0.1", 0))]):
            self.assertTrue(computer.blocked_host("127.0.0.1.nip.io"))
        computer._DNS.clear()

    def test_key_names_map_to_playwright(self):
        self.assertEqual(computer.pw_key("Return"), "Enter")
        self.assertEqual(computer.pw_key("ctrl+shift+Page_Down"), "Control+Shift+PageDown")
        self.assertEqual(computer.pw_key("alt+Tab"), "Alt+Tab")
        self.assertEqual(computer.pw_key("f5"), "F5")
        self.assertEqual(computer.pw_key("a"), "a")
        with self.assertRaises(ValueError):
            computer.pw_key("")


class AgentLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (mock.patch.object(computer, "SESSIONS_DIR", Path(self.tmp.name)),
                  mock.patch.object(computer, "Browser", FakeBrowser),
                  # Claude alone: the fallback to ChatGPT has its own tests, and must never reach the real API
                  mock.patch.object(computer, "_brain_order", return_value=["claude"]),
                  mock.patch.object(computer.socket, "getaddrinfo",
                                    return_value=[(0, 0, 0, "", ("93.184.215.14", 0))])):
            p.start()
            self.addCleanup(p.stop)

    def run_session(self, turns, **kw):
        sent = []

        def turn(client, model, messages, **kw):
            sent.append([dict(m) for m in messages])
            return turns.pop(0)
        with mock.patch.object(computer, "_claude_client", return_value=(object(), "claude-opus-5-5")), \
             mock.patch.object(computer, "_turn", side_effect=turn):
            s = computer.Session("Find the pricing page", "https://example.com", kw.get("steps", 10), False)
            s.run()
        return s, sent

    def test_batch_runs_in_order_and_results_echo_the_toolset(self):
        s, sent = self.run_session([
            msg(use("left_click", {"coordinate": [640, 60]}, id_="a"), use("type", {"text": "pricing"}, id_="b"),
                use("key", {"text": "Return"}, id_="c"), use("screenshot", id_="d")),
            msg(NS(type="text", text="Pricing is at /pricing: three plans."), stop="end_turn"),
        ])
        self.assertEqual(s.status, "done")
        self.assertIn("three plans", s.report)
        results = sent[1][-1]["content"]
        self.assertEqual([r["tool_use_id"] for r in results], ["a", "b", "c", "d"])
        self.assertTrue(all(r["toolset_name"] == "computer" for r in results))
        self.assertEqual(results[0]["content"], [{"type": "text", "text": "OK"}])
        self.assertEqual(results[3]["content"][0]["type"], "image")
        self.assertEqual(results[3]["content"][0]["source"]["media_type"], "image/png")

    def test_assistant_content_is_appended_unchanged(self):
        first = msg(use("screenshot", id_="s1"))
        _, sent = self.run_session([first, msg(NS(type="text", text="done"), stop="end_turn")])
        self.assertIs(sent[1][1]["content"], first.content)   # thinking blocks must survive untouched

    def test_after_a_failure_the_rest_of_the_batch_is_not_executed(self):
        turns = [msg(use("left_click", {"coordinate": [1, 1]}, id_="x"), use("type", {"text": "a"}, id_="y")),
                 msg(NS(type="text", text="gave up"), stop="end_turn")]
        with mock.patch.object(FakeBrowser, "act", side_effect=ValueError("element not found")):
            _, sent = self.run_session(turns)
        results = sent[1][-1]["content"]
        self.assertTrue(results[0]["is_error"])
        self.assertIn("element not found", results[0]["content"])
        self.assertEqual(results[1]["content"], "Not executed: an earlier computer action in this turn failed.")

    def test_navigate_is_a_plain_tool_without_toolset_name(self):
        _, sent = self.run_session([msg(use("navigate", {"url": "https://example.org"}, toolset=False, id_="n")),
                                    msg(NS(type="text", text="ok"), stop="end_turn")])
        r = sent[1][-1]["content"][0]
        self.assertNotIn("toolset_name", r)
        self.assertIn("Opened", r["content"][0]["text"])

    def test_navigating_to_this_machine_is_refused_to_the_model(self):
        _, sent = self.run_session([msg(use("navigate", {"url": "http://localhost:51764/#approvals"},
                                            toolset=False, id_="n")),
                                    msg(NS(type="text", text="blocked"), stop="end_turn")])
        r = sent[1][-1]["content"][0]
        self.assertTrue(r["is_error"])
        self.assertIn("may not open", r["content"])

    def test_refusal_and_step_limit_end_the_session(self):
        s, _ = self.run_session([msg(stop="refusal")])
        self.assertEqual(s.status, "failed")
        s, _ = self.run_session([msg(use("screenshot")) for _ in range(5)], steps=3)
        self.assertEqual(s.status, "failed")
        self.assertIn("step limit", s.error)

    def test_operator_stop(self):
        def turn(client, model, messages, **kw):
            sess.stop()
            return msg(use("screenshot"))
        with mock.patch.object(computer, "_claude_client", return_value=(object(), "m")), \
             mock.patch.object(computer, "_turn", side_effect=turn):
            sess = computer.Session("t task", "https://example.com", 10, False)
            sess.run()
        self.assertEqual(sess.status, "stopped")

    def test_api_errors_are_readable_and_mark_claude_failing(self):
        import providers
        err = Exception("raw")
        err.status_code = 400
        err.body = {"error": {"message": "Your credit balance is too low"}}
        with mock.patch.object(computer, "_claude_client", return_value=(object(), "m")), \
             mock.patch.object(computer, "_turn", side_effect=err):
            s = computer.Session("t task", "https://example.com", 10, False)
            s.run()
        self.assertEqual(s.error, "Claude API 400: Your credit balance is too low")
        self.assertIn("credit balance", providers.failing("claude"))
        providers.record("claude")

    def test_session_is_persisted(self):
        s, _ = self.run_session([msg(NS(type="text", text="done"), stop="end_turn")])
        self.assertEqual(computer.get(s.id)["status"], "done")
        self.assertTrue((Path(self.tmp.name) / s.id / "session.json").is_file())


@unittest.skipUnless(__import__("browser").find_browser(), "no Chrome or Edge installed")
class RealBrowserTests(unittest.TestCase):
    """Every action against the installed Chrome, on a local page (no network)."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.b = computer.Browser()
        except Exception as exc:  # Playwright missing
            raise unittest.SkipTest(f"cannot start the browser: {exc}")
        cls.b.page.set_content("""<body style="margin:0;height:3000px">
          <input id=q style="position:absolute;left:100px;top:100px;width:300px;height:30px">
          <button id=b style="position:absolute;left:100px;top:200px;width:120px;height:40px"
            onclick="this.textContent='clicked'">go</button></body>""")

    @classmethod
    def tearDownClass(cls):
        cls.b.close()

    def test_actions(self):
        b = self.b
        self.assertTrue(b.act("screenshot", {}).startswith(b"\x89PNG"))
        b.act("left_click", {"coordinate": [250, 115]})
        b.act("type", {"text": "roof repair"})
        self.assertEqual(b.page.input_value("#q"), "roof repair")
        b.act("key", {"text": "ctrl+a"})
        b.act("key", {"text": "BackSpace"})
        self.assertEqual(b.page.input_value("#q"), "")
        b.act("left_click", {"coordinate": [160, 220]})
        self.assertEqual(b.page.text_content("#b"), "clicked")
        self.assertEqual(b.act("cursor_position", {}), "X=160, Y=220")
        b.act("scroll", {"scroll_direction": "down", "scroll_amount": 5, "coordinate": [600, 400]})
        self.assertGreater(b.page.evaluate("window.scrollY"), 0)
        self.assertTrue(b.act("zoom", {"region": [100, 100, 400, 250]}).startswith(b"\x89PNG"))
        with self.assertRaises(ValueError):
            b.act("left_click", {"coordinate": [5000, 10]})

    def test_structure_tools(self):
        b = self.b
        b.page.set_content("""<title>Shop</title><main><h1>Roof repair</h1>
          <input placeholder="Search" id=s><input type=password id=p placeholder="Password">
          <input name=cardnumber id=c placeholder="Card"><input autocomplete="cc-number" id=c2>
          <a href="#p" onclick="document.querySelector('h1').textContent='Pricing'">Pricing</a>
          <a href="#h" style="display:none">Hidden</a></main>""")
        page = b.read_page()
        self.assertIn("h1: Roof repair", page)
        self.assertIn('[1] field "Search"', page)
        self.assertIn('link "Pricing" -> #p', page)
        self.assertNotIn("Hidden", page)                      # invisible elements are not offered
        b.type_ref(1, "roof repair")
        self.assertEqual(b.page.input_value("#s"), "roof repair")
        for ref in (2, 3, 4):                                  # password, card by name, card by autocomplete
            with self.assertRaisesRegex(ValueError, "never types"):
                b.type_ref(ref, "secret")
        self.assertEqual(b.page.input_value("#p"), "")
        b.click_ref(5)
        self.assertEqual(b.page.text_content("h1"), "Pricing")
        with self.assertRaisesRegex(ValueError, "read_page again"):
            b.click_ref(42)

    def test_loopback_is_blocked_at_the_network_layer(self):
        with self.assertRaises(ValueError):
            self.b.navigate("http://127.0.0.1:51764/")
        # a page script cannot reach it either
        res = self.b.page.evaluate("""() => fetch('http://127.0.0.1:51764/api/agency/token')
            .then(() => 'reached').catch(() => 'blocked')""")
        self.assertEqual(res, "blocked")


class EndpointTests(unittest.TestCase):
    def test_registered(self):
        import agency
        for path in ("/api/agency/computer/start", "/api/agency/computer/stop"):
            self.assertIn(path, agency.POST_ROUTES)


if __name__ == "__main__":
    unittest.main()
