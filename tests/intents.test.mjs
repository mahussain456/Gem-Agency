// Jarvis intent parser: spoken sentence -> intent. Run: node tests/intents.test.mjs
// Exits non-zero on any failure; tests/test_frontend_intents.py runs it in the suite.
import { parseIntent, nameScore, normalise } from "../app/q/js/jarvis/intents.js";

const ctx = {
  projects: [
    { id: "p1", name: "Northgate Plumbing" },
    { id: "p2", name: "Website redesign" },
    { id: "p3", name: "GlobalSync Audit" },
  ],
  clients: [{ id: "c1", name: "GlobalSync" }],
};

// [utterance, expected subset of the intent]
const CASES = [
  // wake word and politeness are noise
  ["Jarvis, open approvals", { type: "nav", route: "approvals" }],
  ["hey jarvis could you please show me the websites", { type: "nav", route: "projects" }],
  ["OK Jarvis. Take me to the growth page.", { type: "nav", route: "growth" }],
  ["approvals", { type: "nav", route: "approvals" }],
  ["go to models", { type: "nav", route: "models" }],
  ["open the dashboard", { type: "nav", route: "" }],
  ["go home", { type: "nav", route: "" }],
  ["show me the clients", { type: "nav", route: "clients" }],
  ["pull up integrations", { type: "nav", route: "integrations" }],
  ["open backlinks", { type: "nav", route: "backlinks" }],
  ["switch to keywords", { type: "nav", route: "seo" }],
  ["open seo", { type: "nav", route: "seo" }],
  ["open the website builder", { type: "nav", route: "builder" }],
  ["open the audits", { type: "nav", route: "optimize" }],
  ["go to the agency", { type: "nav", route: "agency" }],
  ["open activity", { type: "nav", route: "monitor" }],

  // situational awareness
  ["status report", { type: "brief" }],
  ["Jarvis, give me a status report", { type: "brief" }],
  ["what's going on", { type: "brief" }],
  ["brief me please", { type: "brief" }],
  ["how are we doing", { type: "brief" }],
  ["catch me up", { type: "brief" }],
  ["good morning", { type: "greet" }],
  ["hello jarvis", { type: "greet" }],
  ["jarvis", { type: "empty" }],
  ["what needs my attention", { type: "attention" }],
  ["anything urgent?", { type: "attention" }],
  ["what should I do next", { type: "attention" }],
  ["what's broken", { type: "attention" }],
  ["what's running", { type: "running" }],
  ["what are the agents doing", { type: "running" }],
  ["how many websites do we have", { type: "count", what: "projects" }],
  ["how many clients", { type: "count", what: "clients" }],
  ["how many approvals are pending", { type: "count", what: "approvals" }],
  ["what time is it", { type: "time" }],
  ["what's the date today", { type: "time" }],

  // conversation control
  ["stop", { type: "dismiss" }],
  ["never mind", { type: "dismiss" }],
  ["that's all jarvis", { type: "dismiss" }],
  ["be quiet", { type: "hush" }],
  ["mute", { type: "voice", speak: false }],
  ["unmute", { type: "voice", speak: true }],
  ["what can you do", { type: "help" }],
  ["thank you", { type: "thanks" }],

  // layout and appearance
  ["go back", { type: "history", dir: -1 }],
  ["refresh the data", { type: "refresh" }],
  ["scroll down", { type: "scroll", to: "down" }],
  ["scroll to the top", { type: "scroll", to: "top" }],
  ["collapse the sidebar", { type: "sidebar", collapsed: true }],
  ["expand the menu", { type: "sidebar", collapsed: false }],
  ["switch to amethyst", { type: "accent", accent: "violet" }],
  ["change the theme to purple", { type: "accent", accent: "violet" }],
  ["set the accent colour to pink", { type: "accent", accent: "orchid" }],
  ["monochrome theme", { type: "accent", accent: "mono" }],
  ["change the colour to amber", { type: "ask" }],   // no accent may look like a warning
  ["blue theme", { type: "accent", accent: "sky" }],
  ["make it compact", { type: "density", density: "compact" }],
  ["comfortable mode", { type: "density", density: "comfortable" }],
  ["turn off animations", { type: "motion", motion: "off" }],
  ["calm motion", { type: "motion", motion: "calm" }],
  ["turn on animations", { type: "motion", motion: "full" }],
  ["turn off the background", { type: "ambient", on: false }],
  ["open settings", { type: "prefs" }],
  ["keyboard shortcuts", { type: "shortcuts" }],
  ["open the command palette", { type: "palette" }],

  // spend-money work is loaded, not launched
  ["Build a website for a dentist in Austin", { type: "build", brief: "Build a website for a dentist in Austin" }],
  ["jarvis create me a premium landing page for a yoga studio", { type: "build" }],
  ["build a website", { type: "nav", route: "builder" }],
  ["tell scout to research our competitors", { type: "agency", target: "@scout", text: "Research our competitors" }],
  ["ask the agency to write a blog post about roofing", { type: "agency", target: "@orchestrator" }],
  ["ask everyone to report status", { type: "agency", target: "@all" }],
  ["run a full seo audit on globalsync-ai.com", { type: "agency", target: "@orchestrator" }],

  // search and records
  ["search for northgate", { type: "search", q: "northgate" }],
  ["find globalsync", { type: "search", q: "globalsync" }],
  ["open northgate plumbing", { type: "record", kind: "project", id: "p1" }],
  ["open the northgate project", { type: "record", kind: "project", id: "p1" }],
  ["northgate plumbing", { type: "record", id: "p1" }],
  ["open globalsync", { type: "record", kind: "client", id: "c1" }],
  ["open site", { type: "ask" }],   // must NOT substring-match "Website redesign"

  // anything else goes to the model
  ["which client is most at risk of churning", { type: "ask" }],
  ["summarise why the last pipeline failed", { type: "ask" }],
  ["tell me a joke", { type: "ask" }],
];

let fail = 0;
for (const [utter, want] of CASES) {
  const got = parseIntent(utter, ctx);
  const bad = Object.entries(want).filter(([k, v]) => got[k] !== v);
  if (bad.length) {
    fail++;
    console.error(`FAIL  "${utter}"\n      want ${JSON.stringify(want)}\n      got  ${JSON.stringify(got)}`);
  }
}

// unit checks
const check = (cond, msg) => { if (!cond) { fail++; console.error("FAIL  " + msg); } };
check(nameScore("site", "Website redesign") === 0, "whole-word matching only");
check(nameScore("northgate plumbing", "Northgate Plumbing") === 1, "exact name scores 1");
check(normalise("Hey Jarvis, please open approvals please") === "open approvals", "normalise strips wake word and politeness");
check(parseIntent("tell scout to research our competitors").type === "agency", "agent hand-off parses without ctx");
check(parseIntent("").type === "empty", "empty input");

console.log(`${CASES.length + 5 - fail}/${CASES.length + 5} intent checks passed`);
process.exit(fail ? 1 : 0);
