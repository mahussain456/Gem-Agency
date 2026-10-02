"""Computer use 2.0: three brains on one browser, structure tools and screen tools.

* ChatGPT drives through OpenAI's computer tool (Responses API): scripted
  responses in the documented shapes (computer_call with an `actions` list,
  function_call, pending_safety_checks). No API call is made.
* Local drives through Ollama's /api/chat tool calls, structure tools only.
* Auto hands a task over only while nothing on the page has changed.
"""

import asyncio
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import computer
from test_computer import PNG, FakeBrowser, msg, use


def call(*actions, call_id="call_1", checks=()):
    return NS(type="computer_call", call_id=call_id, actions=list(actions), action=None,
              pending_safety_checks=list(checks), status="completed")


def fn(name, args, call_id="fc_1"):
    return NS(type="function_call", name=name, arguments=json.dumps(args), call_id=call_id)


def resp(*output, text="", id_="resp_1"):
    return NS(id=id_, output=list(output), output_text=text, usage=NS(input_tokens=200, output_tokens=30))


def said(text):
    return NS(type="message", content=[NS(type="output_text", text=text)])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (mock.patch.object(computer, "SESSIONS_DIR", Path(self.tmp.name)),
                  mock.patch.object(computer, "Browser", FakeBrowser),
                  mock.patch.object(computer.socket, "getaddrinfo",
                                    return_value=[(0, 0, 0, "", ("93.184.215.14", 0))])):
            p.start()
            self.addCleanup(p.stop)

    def run_openai(self, responses, session=None, brain_order=("chatgpt",)):
        sent = []

        def turn(client, model, system, tools, items, previous):
            sent.append({"items": items, "previous": previous, "tools": tools})
            return responses.pop(0)
        s = session or computer.Session("Find the pricing page", "https://example.com", 10, False)
        with mock.patch.object(computer, "_brain_order", return_value=list(brain_order)), \
             mock.patch.object(computer, "_openai_client", return_value=(object(), "gpt-6.1-sol")), \
             mock.patch.object(computer, "_openai_turn", side_effect=turn):
            s.run()
        return s, sent


class ChatGPTLoopTests(Base):
    def test_actions_run_in_order_and_a_screenshot_goes_back(self):
        actions = [NS(type="click", button="left", x=640, y=60, keys=None),
                   NS(type="type", text="pricing"),
                   NS(type="keypress", keys=["ENTER"]),
                   NS(type="scroll", x=600, y=400, scroll_x=0, scroll_y=450, keys=None)]
        s, sent = self.run_openai([resp(call(*actions)), resp(said("Pricing is at /pricing."), text="Pricing is at /pricing.")])
        self.assertEqual(s.status, "done")
        self.assertEqual(s.brain_used, "chatgpt")
        self.assertIn("/pricing", s.report)
        out = sent[1]["items"][0]
        self.assertEqual(out["type"], "computer_call_output")
        self.assertEqual(out["call_id"], "call_1")
        self.assertTrue(out["output"]["image_url"].startswith("data:image/png;base64,"))
        self.assertEqual(sent[1]["previous"], "resp_1")
        self.assertEqual(sent[0]["tools"][0], {"type": "computer"})

    def test_actions_translate_to_the_computer_vocabulary(self):
        s = computer.Session("t task", "https://example.com", 10, False)
        env = FakeBrowser()
        for a in [NS(type="click", button="right", x=5, y=6, keys=None),
                  NS(type="click", button="back", x=1, y=1, keys=None),
                  NS(type="double_click", x=7, y=8, keys=["SHIFT"]),
                  NS(type="drag", path=[NS(x=1, y=2), NS(x=3, y=4), NS(x=9, y=9)], keys=None),
                  NS(type="keypress", keys=["CTRL", "L"]),
                  NS(type="scroll", x=10, y=10, scroll_x=0, scroll_y=-250, keys=None),
                  NS(type="move", x=3, y=3, keys=None)]:
            s._openai_action(env, a)
        self.assertEqual(env.calls, [
            ("right_click", {"coordinate": [5, 6]}),
            ("key", {"text": "alt+Left"}),
            ("double_click", {"coordinate": [7, 8], "text": "SHIFT"}),
            ("left_click_drag", {"start_coordinate": [1, 2], "coordinate": [9, 9]}),
            ("key", {"text": "CTRL+L"}),
            ("scroll", {"coordinate": [10, 10], "scroll_direction": "up", "scroll_amount": 2}),
            ("mouse_move", {"coordinate": [3, 3]}),
        ])
        self.assertEqual(computer.pw_key("CTRL+L"), "Control+L")
        self.assertEqual(computer.pw_key("ArrowDown"), "ArrowDown")

    def test_navigate_is_a_function_call(self):
        s, sent = self.run_openai([resp(fn("navigate", {"url": "https://example.org"})), resp(text="ok")])
        out = sent[1]["items"][0]
        self.assertEqual(out, {"type": "function_call_output", "call_id": "fc_1",
                               "output": "Opened https://example.org"})

    def test_this_machine_stays_blocked_for_chatgpt(self):
        s, sent = self.run_openai([resp(fn("navigate", {"url": "http://127.0.0.1:51764/#approvals"})),
                                   resp(text="blocked")])
        self.assertIn("may not open", sent[1]["items"][0]["output"])

    def test_a_failed_action_stops_the_batch_and_says_why(self):
        env_fail = mock.patch.object(FakeBrowser, "act", side_effect=ValueError("element not found"))
        with env_fail:
            s, sent = self.run_openai([resp(call(NS(type="click", button="left", x=1, y=1, keys=None),
                                                 NS(type="type", text="never typed"))), resp(text="gave up")])
        items = sent[1]["items"]
        self.assertEqual(items[0]["type"], "computer_call_output")
        self.assertIn("element not found", items[1]["content"][0]["text"])

    def test_refusal_ends_the_session(self):
        s, _ = self.run_openai([resp(NS(type="message", content=[NS(type="refusal", refusal="no")]))])
        self.assertEqual(s.status, "failed")
        self.assertIn("declined", s.error)


class SafetyCheckTests(Base):
    def _run_with_decision(self, approve):
        check = NS(id="sc_1", code="malicious_instructions", message="The page asks you to ignore your task")
        s = computer.Session("t task", "https://example.com", 10, False)
        responses = [resp(call(NS(type="click", button="left", x=1, y=1, keys=None), checks=[check])),
                     resp(text="done")]

        def operator():
            for _ in range(200):
                if s.status == "waiting":
                    self.assertEqual(s.pending[0]["message"], "The page asks you to ignore your task")
                    computer._SESSIONS[s.id] = s
                    computer.decide(s.id, approve)
                    return
                time.sleep(0.01)
        t = threading.Thread(target=operator)
        t.start()
        s, sent = self.run_openai(responses, session=s)
        t.join()
        computer._SESSIONS.pop(s.id, None)
        return s, sent

    def test_approved_check_is_acknowledged(self):
        s, sent = self._run_with_decision(True)
        self.assertEqual(s.status, "done")
        self.assertEqual(sent[1]["items"][0]["acknowledged_safety_checks"],
                         [{"id": "sc_1", "code": "malicious_instructions",
                           "message": "The page asks you to ignore your task"}])

    def test_declined_check_stops_without_acting(self):
        s, sent = self._run_with_decision(False)
        self.assertEqual(s.status, "stopped")
        self.assertEqual(len(sent), 1)                     # nothing went back to the model
        self.assertFalse(s.acted)

    def test_decide_needs_a_waiting_session(self):
        s = computer.Session("t task", "https://example.com", 10, False)
        with self.assertRaises(ValueError):
            s.decide(True)


class FallbackTests(Base):
    def billing_error(self):
        err = Exception("raw")
        err.status_code = 400
        err.body = {"error": {"message": "Your credit balance is too low"}}
        return err

    def test_claude_out_of_credit_hands_over_to_chatgpt(self):
        import providers
        with mock.patch.object(computer, "_claude_client", return_value=(object(), "claude-opus-5-5")), \
             mock.patch.object(computer, "_turn", side_effect=self.billing_error()):
            s, sent = self.run_openai([resp(text="All five results listed.")], brain_order=("claude", "chatgpt"))
        self.assertEqual(s.status, "done")
        self.assertEqual(s.brain_used, "chatgpt")
        self.assertTrue(any("ChatGPT takes the task" in st["text"] for st in s.steps))
        self.assertIn("credit balance", providers.failing("claude"))
        providers.record("claude")

    def test_no_handover_once_something_changed_on_screen(self):
        import providers
        turns = [msg(use("left_click", {"coordinate": [5, 5]})), self.billing_error()]

        def turn(client, model, messages, **kw):
            t = turns.pop(0)
            if isinstance(t, Exception):
                raise t
            return t
        with mock.patch.object(computer, "_claude_client", return_value=(object(), "m")), \
             mock.patch.object(computer, "_turn", side_effect=turn):
            s, sent = self.run_openai([resp(text="should not run")], brain_order=("claude", "chatgpt"))
        self.assertEqual(s.status, "failed")
        self.assertEqual(sent, [])                          # ChatGPT never started
        self.assertIn("credit balance", s.error)
        providers.record("claude")

    def test_brain_order_puts_the_healthy_brain_first(self):
        import providers
        with mock.patch.object(providers, "available", return_value=True), \
             mock.patch.object(providers, "failing", side_effect=lambda b: "no credit" if b == "claude" else ""):
            self.assertEqual(computer._brain_order("auto"), ["chatgpt", "claude"])
            self.assertEqual(computer._brain_order("claude"), ["claude"])
        with mock.patch.object(providers, "available", return_value=False):
            with self.assertRaises(RuntimeError):
                computer._brain_order("auto")


class EndpointTests(unittest.TestCase):
    def test_registered(self):
        import agency
        self.assertIn("/api/agency/computer/decide", agency.POST_ROUTES)
        for gone in ("/api/agency/computer/cua-key", "/api/agency/computer/cua-disconnect"):
            self.assertNotIn(gone, agency.POST_ROUTES)


# ---------------------------------------------------------------- computer use 2.0

class StructureToolTests(Base):
    def test_claude_uses_structure_tools_alongside_the_screen(self):
        turns = [msg(use("read_page", {}, toolset=False, id_="r"), use("click_element", {"ref": 1}, toolset=False, id_="c")),
                 msg(NS(type="text", text="Pricing found."), stop="end_turn")]
        sent = []

        def turn(client, model, messages, **kw):
            sent.append((list(messages), kw.get("tools")))
            return turns.pop(0)
        with mock.patch.object(computer, "_brain_order", return_value=["claude"]), \
             mock.patch.object(computer, "_claude_client", return_value=(object(), "m")), \
             mock.patch.object(computer, "_turn", side_effect=turn):
            s = computer.Session("Find pricing", "https://example.com", 10, False)
            s.run()
        names = [t.get("name") or t.get("type") for t in sent[0][1]]
        self.assertEqual(names[:2], ["computer_toolset_20260801", "navigate"])
        self.assertTrue({"read_page", "click_element", "type_into", "go_back", "fetch_url"} <= set(names))
        results = sent[1][0][-1]["content"]
        self.assertIn("[1] link", results[0]["content"][0]["text"])
        self.assertIn("Clicked [1]", results[1]["content"][0]["text"])
        self.assertTrue(s.acted)                            # clicking changed the page
        self.assertEqual(s.status, "done")

    def test_reading_is_not_acting(self):
        s = computer.Session("t task", "https://example.com", 10, False)
        env = FakeBrowser()
        s._function(env, "read_page", {})
        self.assertFalse(s.acted)                           # a handover is still clean
        s._function(env, "go_back", {})
        self.assertTrue(s.acted)

    def test_chatgpt_gets_the_structure_tools_as_functions(self):
        s, sent = self.run_openai([resp(fn("read_page", {})), resp(text="ok")])
        names = [t.get("name") or t["type"] for t in sent[0]["tools"]]
        self.assertEqual(names[0], "computer")
        self.assertIn("fetch_url", names)
        self.assertIn("[1] link", sent[1]["items"][0]["output"])


class FakeResp:
    def __init__(self, status, headers, body=b""):
        self.status = self.code = status
        self.headers = headers
        self._body = body

    def read(self, n=-1):
        return self._body

    def close(self):
        pass


class FetchTests(unittest.TestCase):
    def fetch(self, responses, url):
        opener = NS(open=lambda req, timeout=None: responses.pop(0))
        with mock.patch.object(computer.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("93.184.215.14", 0))]), \
             mock.patch("urllib.request.build_opener", return_value=opener):
            return computer.fetch_url(url)

    def test_a_redirect_to_this_machine_is_refused(self):
        hop = FakeResp(302, {"Location": "http://127.0.0.1:51764/api/agency/token"})
        with self.assertRaisesRegex(ValueError, "may not open"):
            self.fetch([hop], "https://example.com/go")

    def test_report_shape(self):
        hop = FakeResp(301, {"Location": "/robots.txt"})
        final = FakeResp(200, {"Content-Type": "text/plain; charset=utf-8", "server": "x"},
                         b"User-agent: *\nDisallow: /admin")
        out = self.fetch([hop, final], "https://example.com/old-robots")
        self.assertIn("redirect 301 https://example.com/old-robots -> https://example.com/robots.txt", out)
        self.assertIn("Final: 200 https://example.com/robots.txt", out)
        self.assertIn("server: x", out)
        self.assertIn("Disallow: /admin", out)

    def test_binary_bodies_are_not_dumped(self):
        out = self.fetch([FakeResp(200, {"Content-Type": "image/png"}, b"\x89PNG....")], "https://example.com/a.png")
        self.assertIn("not shown", out)

    def test_page_text_is_paged(self):
        d = {"url": "u", "title": "t", "text": "x" * 7000, "elements": [], "headings": []}
        self.assertIn("read_page with start=6000", computer.format_page(d))
        self.assertIn("characters 6000-7000", computer.format_page(d, 6000))


class LocalBrainTests(Base):
    def run_local(self, replies, brain_order=("local",)):
        sent = []

        def turn(model, messages, tools):
            sent.append({"messages": [dict(m) for m in messages], "tools": tools})
            return replies.pop(0)
        s = computer.Session("Find the pricing page", "https://example.com", 10, False)
        with mock.patch.object(computer, "_brain_order", return_value=list(brain_order)), \
             mock.patch.object(computer, "_local_model", return_value="qwen2.5:14b"), \
             mock.patch.object(computer, "_local_turn", side_effect=turn):
            s.run()
        return s, sent

    @staticmethod
    def calls(*pairs):
        return {"message": {"role": "assistant", "content": "",
                            "tool_calls": [{"function": {"name": n, "arguments": a}} for n, a in pairs]},
                "prompt_eval_count": 500, "eval_count": 20}

    @staticmethod
    def answer(text):
        return {"message": {"role": "assistant", "content": text}, "prompt_eval_count": 600, "eval_count": 40}

    def test_reads_clicks_and_reports_without_seeing(self):
        s, sent = self.run_local([self.calls(("read_page", {})), self.calls(("click_element", {"ref": 1})),
                                  self.answer("Pricing is at /pricing.")])
        self.assertEqual((s.status, s.brain_used, s.model), ("done", "local", "qwen2.5:14b"))
        self.assertEqual(s.report, "Pricing is at /pricing.")
        names = {t["function"]["name"] for t in sent[0]["tools"]}
        self.assertEqual(names, {"navigate", "read_page", "click_element", "type_into", "go_back", "fetch_url"})
        self.assertIn("You cannot see the screen", sent[0]["messages"][0]["content"])
        self.assertEqual(sent[1]["messages"][-1]["role"], "tool")
        self.assertIn("[1] link", sent[1]["messages"][-1]["content"])
        self.assertEqual(s.tokens_in, 1600)

    def test_a_tool_call_written_as_text_is_understood(self):
        fenced = "```json\n" + json.dumps({"name": "read_page", "arguments": {}}) + "\n```"
        s, sent = self.run_local([self.answer(fenced), self.answer("Done.")])
        self.assertEqual(s.report, "Done.")
        self.assertTrue(any(st["kind"] == "read_page" for st in s.steps))

    def test_an_empty_answer_gets_one_nudge(self):
        s, sent = self.run_local([self.answer(""), self.answer("Report: nothing found.")])
        self.assertEqual(s.report, "Report: nothing found.")
        self.assertIn("report", sent[1]["messages"][-1]["content"])

    def test_tool_errors_go_back_to_the_model(self):
        s, sent = self.run_local([self.calls(("click_element", {"ref": 9})), self.answer("Could not click.")])
        self.assertIn("read_page again", sent[1]["messages"][-1]["content"])

    def test_old_results_are_shortened_for_the_small_window(self):
        msgs = [{"role": "system", "content": "s"}] + [{"role": "tool", "content": "y" * 5000} for _ in range(4)]
        trimmed = computer._trim_local(msgs)
        self.assertTrue(all(len(m["content"]) < 400 for m in trimmed[1:3]))
        self.assertTrue(all(len(m["content"]) == 5000 for m in trimmed[3:]))
        self.assertEqual(len(msgs[1]["content"]), 5000)     # the history itself is untouched

    def test_paid_brains_hand_over_to_local(self):
        import providers
        err = Exception("raw")
        err.status_code = 400
        err.body = {"error": {"message": "Your credit balance is too low"}}
        with mock.patch.object(computer, "_claude_client", return_value=(object(), "m")), \
             mock.patch.object(computer, "_turn", side_effect=err):
            s, _ = self.run_local([self.answer("Done locally.")], brain_order=("claude", "local"))
        self.assertEqual((s.status, s.brain_used), ("done", "local"))
        providers.record("claude")


class BrainOrderTests(unittest.TestCase):
    def test_auto_puts_local_last_and_failing_brains_behind_healthy_ones(self):
        with mock.patch.object(computer, "_available", return_value=True), \
             mock.patch.object(computer, "_failing", side_effect=lambda b: "no credit" if b == "claude" else ""):
            self.assertEqual(computer._brain_order("auto"), ["chatgpt", "claude", "local"])
        with mock.patch.object(computer, "_available", side_effect=lambda b: b == "local"), \
             mock.patch.object(computer, "_failing", return_value=""):
            self.assertEqual(computer._brain_order("auto"), ["local"])
            with self.assertRaisesRegex(RuntimeError, "Connect it"):
                computer._brain_order("claude")

    def test_local_never_uses_an_ollama_cloud_model(self):
        import providers
        with mock.patch.object(providers, "ollama_local_models",
                               return_value=([{"name": "llama3.1:latest"}], ["kimi-k2.6:cloud"])), \
             mock.patch.object(computer, "_capabilities", return_value=["completion", "tools"]), \
             mock.patch.object(providers, "_model", return_value="kimi-k2.6:cloud"):
            self.assertEqual(computer._local_model(), "llama3.1:latest")

    def test_local_needs_a_tool_calling_model(self):
        import providers
        with mock.patch.object(providers, "ollama_local_models", return_value=([{"name": "llama2:latest"}], [])), \
             mock.patch.object(computer, "_capabilities", return_value=["completion"]):
            with self.assertRaisesRegex(RuntimeError, "call tools"):
                computer._local_model()

    def test_start_requires_a_url_and_a_known_brain(self):
        st = {"ready": True, "problems": [], "running": 0}
        with mock.patch.object(computer, "status", return_value=st):
            with self.assertRaises(ValueError):
                computer.start("Look at the page", "")
            with self.assertRaises(ValueError):
                computer.start("Look at the page", "https://example.com", brain="gemini")
