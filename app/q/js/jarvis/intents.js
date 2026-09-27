// ============================================================
// JARVIS INTENTS — what a spoken sentence asks the dashboard to do.
//
// Pure: text in, a plain intent object out. No DOM, no network, so it
// runs under Node for tests and answers the common commands instantly
// and for free. Anything it does not recognise becomes { type: "ask" }
// and goes to the language model, which can only answer or navigate.
//
// Commands that would spend money or change records (starting a build,
// sending work to an agent) never execute from here: they load the
// request into the page that does it and stop for a human click.
// ============================================================
import { PAGES } from "../nav.js";

const AGENTS = ["orchestrator", "scout", "scribe", "reach", "dev", "lumen",
                "antigravity", "chatgpt", "forge", "rank", "stitch"];

const ACCENT_WORDS = {
  teal: "teal", green: "teal", mint: "teal", default: "teal",
  amethyst: "violet", violet: "violet", purple: "violet", lavender: "violet",
  sky: "sky", blue: "sky", azure: "sky",
  orchid: "orchid", pink: "orchid", magenta: "orchid",
  mono: "mono", monochrome: "mono", white: "mono", silver: "mono", grey: "mono", gray: "mono",
};

const COUNTABLE = {
  website: "projects", websites: "projects", site: "projects", sites: "projects",
  project: "projects", projects: "projects",
  client: "clients", clients: "clients", customer: "clients", customers: "clients",
  approval: "approvals", approvals: "approvals", decision: "approvals", decisions: "approvals",
  agent: "agents", agents: "agents",
  run: "runs", runs: "runs", pipeline: "runs", pipelines: "runs", build: "runs", builds: "runs",
  keyword: "keywords", keywords: "keywords",
};

/* ---------------- normalising speech ---------------- */
const NUMBERS = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10 };

export function normalise(raw) {
  let s = String(raw || "").toLowerCase()
    .replace(/[’‘]/g, "'")
    .replace(/[^a-z0-9@'&.\-\s]/g, " ")
    .replace(/\.(?=\s|$)/g, " ")
    .replace(/\s+/g, " ").trim();
  // wake word and politeness carry no meaning for the command
  s = s.replace(/^(?:(?:hey|hi|ok|okay|yo|oi)\s+)?jarvis\b[\s,]*/, "");
  s = s.replace(/[\s,]+jarvis$/, "");
  let prev;
  do {
    prev = s;
    s = s.replace(/^(?:please|kindly|now|so|and|um+|uh+|er+|well|alright|all right|right)\s+/, "")
         .replace(/^(?:can|could|would|will) you(?: please)?\s+/, "")
         .replace(/^(?:i want to|i'd like to|i would like to|i need to|let's|let me|i wanna)\s+/, "")
         .replace(/^(?:i want|i need|i'd like|give me|show me)\s+(?=(?:a|an|the)\s)/, "")
         .replace(/\s+(?:please|for me|now|thanks|thank you)$/, "");
  } while (s !== prev);
  return s.trim();
}

const any = (s, list) => list.some(re => re.test(s));

/* ---------------- matching records ---------------- */
const FILLER = new Set(["the", "my", "our", "a", "an", "project", "website", "site", "client",
                        "account", "page", "for", "of"]);
const words = s => normalise(s).replace(/[^a-z0-9\s]/g, " ").split(/\s+/)
  .filter(w => w.length > 1 && !FILLER.has(w));

/** Score how well a spoken phrase names a record: 0 (no) … 1 (exact).
    Whole words only — "site" must not match "Website redesign". */
export function nameScore(spoken, name) {
  const a = words(spoken), b = words(name);
  if (!a.length || !b.length) return 0;
  if (a.join(" ") === b.join(" ")) return 1;
  const hits = a.filter(w => b.includes(w)).length;
  if (hits === a.length && a.join("").length >= 4) return 0.75 + 0.25 * (a.length / b.length);
  return hits / Math.max(a.length, b.length);
}

function bestRecord(spoken, ctx) {
  let best = null;
  for (const kind of ["projects", "clients"]) {
    for (const r of (ctx && ctx[kind]) || []) {
      const score = nameScore(spoken, r.name);
      if (score >= 0.66 && (!best || score > best.score))
        best = { kind: kind === "projects" ? "project" : "client", id: r.id, name: r.name, score };
    }
  }
  return best;
}

/* ---------------- pages ---------------- */
function pageFor(phrase) {
  const p = phrase.replace(/^(?:the|my|our)\s+/, "").replace(/\s+(?:page|screen|tab|view|section|panel|area)$/, "").trim();
  for (const page of PAGES) {
    if (page.say.includes(p) || page.t.toLowerCase() === p) return page;
  }
  return null;
}

const NAV_VERB = /^(?:open|go to|goto|go|show|show me|take me to|switch to|navigate to|bring up|pull up|display|jump to|view|head to|load|see)\s+(?:up\s+)?(.+)$/;

/* ============================================================ */
export function parseIntent(raw, ctx = {}) {
  const s = normalise(raw);
  if (!s) return { type: "empty" };

  /* ---- conversation control ---- */
  if (any(s, [/^(?:stop|cancel|never ?mind|forget it|dismiss|close|exit|goodbye|bye|that's all|that is all|that'll be all|nothing|go away|done)$/]))
    return { type: "dismiss" };
  if (any(s, [/^(?:stop talking|be quiet|quiet|shush|shut up|silence|hush|enough)$/]))
    return { type: "hush" };
  if (any(s, [/^(?:mute|mute (?:yourself|your voice|voice|replies)|stop speaking replies|turn off (?:your )?voice|text only)$/]))
    return { type: "voice", speak: false };
  if (any(s, [/^(?:unmute|unmute (?:yourself|voice)|speak replies|turn on (?:your )?voice|talk to me|use your voice)$/]))
    return { type: "voice", speak: true };
  if (any(s, [/^(?:help|what can you do|what can i say|what do you do|commands|list commands|show commands|how do i use you|what are my options)$/]))
    return { type: "help" };
  if (any(s, [/^(?:thanks?|thank you|thank you very much|cheers|nice|great|perfect|good job|well done|awesome|brilliant)(?: jarvis)?$/]))
    return { type: "thanks" };

  /* ---- situational awareness (answered from local state) ---- */
  if (any(s.replace(/^(?:a|an|the)\s+/, ""), [/^(?:status|status report|report|sitrep|situation report|brief me|briefing|daily briefing|morning briefing|morning report|give me (?:a |the )?(?:status|briefing|rundown|summary|update)|update me|catch me up|what's (?:going on|happening|new|the status|up)|what is (?:going on|happening|new)|how are (?:we|things) (?:doing|looking)|how's (?:it going|everything|the agency)|where are we|summary|rundown|overview report)$/]))
    return { type: "brief" };
  if (/^(?:good )?(?:morning|afternoon|evening)$|^(?:hello|hi|hey|hiya|yo|greetings|wake up|you there|are you there)$/.test(s))
    return { type: "greet" };
  if (any(s, [/(?:what|anything) (?:needs?|requires?) (?:my )?(?:attention|me|action)/, /^(?:any(?:thing)? (?:urgent|alerts?|issues?|problems?|fires?))$/,
               /^(?:what's|what is) urgent$/, /^(?:alerts|notifications|my notifications|priorities|my priorities|inbox|to ?do|todo list)$/,
               /^what should i (?:do|work on|look at)(?: (?:next|first|now|today))?$/, /^(?:what's|what is) (?:broken|failing|wrong)$/]))
    return { type: "attention" };
  if (any(s, [/^(?:what's|what is|is anything|is something|anything) running$/, /^what are (?:the )?agents (?:doing|working on)$/,
               /^(?:agent|agents|pipeline|pipelines|build|builds) status$/, /^(?:who's|who is) working$/, /^what's in (?:flight|progress)$/]))
    return { type: "running" };
  {
    const m = s.match(/^how many ([a-z]+)(?:\s.*)?$/);
    if (m && COUNTABLE[m[1]]) return { type: "count", what: COUNTABLE[m[1]] };
  }
  if (any(s, [/^what(?:'s| is) the time$/, /^what time is it$/, /^time$/, /^(?:what's|what is) (?:the )?(?:date|day)(?: today)?$/, /^what day is (?:it|today)$/, /^today's date$/]))
    return { type: "time" };

  /* ---- browser and layout ---- */
  if (/^(?:go )?back$|^previous page$|^go to the previous page$/.test(s)) return { type: "history", dir: -1 };
  if (/^(?:go )?forward$|^next page$/.test(s)) return { type: "history", dir: 1 };
  if (/^(?:refresh|reload|resync|sync|update the data|refresh (?:the )?(?:data|page|dashboard)|reload (?:the )?(?:page|data))$/.test(s))
    return { type: "refresh" };
  {
    const m = s.match(/^scroll (up|down|to (?:the )?(?:top|bottom))$|^(?:go to )?(?:the )?(top|bottom)(?: of (?:the )?page)?$/);
    if (m) {
      const w = (m[1] || m[2]);
      return { type: "scroll", to: /top/.test(w) ? "top" : /bottom/.test(w) ? "bottom" : w };
    }
  }
  {
    const m = s.match(/^(collapse|hide|close|minimi[sz]e|expand|show|open) (?:the )?(?:side ?bar|navigation|nav|menu|side menu)$/);
    if (m) return { type: "sidebar", collapsed: /collapse|hide|close|minimi/.test(m[1]) };
  }
  if (/^(?:open |show |bring up )?(?:the )?command palette$|^palette$/.test(s)) return { type: "palette", q: "" };
  if (/^(?:show |open )?(?:the )?(?:keyboard )?shortcuts$|^(?:what are the )?keyboard shortcuts$|^hotkeys$/.test(s)) return { type: "shortcuts" };
  if (/^(?:open |show )?(?:the )?(?:settings|preferences|appearance|customi[sz]e|customi[sz]ation|display settings)(?: panel| page)?$|^customi[sz]e (?:the )?(?:dashboard|look|appearance)$/.test(s))
    return { type: "prefs" };

  /* ---- appearance ---- */
  {
    const m = s.match(/^(?:change|set|switch|make|turn|use)\s+(?:the\s+)?(?:accent|colou?r|theme|highlight|colou?r scheme)?\s*(?:colou?r\s+)?(?:to|into)?\s*([a-z]+)(?:\s+(?:theme|mode|accent|colou?r))?$/)
      || s.match(/^([a-z]+)\s+(?:theme|mode|accent)$/);
    if (m && ACCENT_WORDS[m[1]]) return { type: "accent", accent: ACCENT_WORDS[m[1]] };
  }
  if (/^(?:compact|dense|denser|tight|condensed)(?: (?:mode|view|layout|density))?$|^make it (?:more )?(?:compact|denser|dense|tighter|smaller)$/.test(s))
    return { type: "density", density: "compact" };
  if (/^(?:comfortable|roomy|spacious|relaxed|normal|default)(?: (?:mode|view|layout|density))?$|^make it (?:more )?(?:comfortable|roomier|spacious|bigger|breathe)$/.test(s))
    return { type: "density", density: "comfortable" };
  if (/^(?:turn off|disable|stop|kill|no|reduce) (?:the )?(?:animations?|motion|effects)$|^(?:no|still) motion$/.test(s))
    return { type: "motion", motion: "off" };
  if (/^(?:calm|subtle|gentle|less|softer) (?:animations?|motion|mode)$|^tone down (?:the )?(?:animations?|motion)$/.test(s))
    return { type: "motion", motion: "calm" };
  if (/^(?:turn on|enable|start|full|more) (?:the )?(?:animations?|motion|effects)$|^animate everything$/.test(s))
    return { type: "motion", motion: "full" };
  if (/^(?:turn off|disable|hide) (?:the )?(?:background|ambient)(?: (?:light|glow|effect|animation))?$/.test(s))
    return { type: "ambient", on: false };
  if (/^(?:turn on|enable|show) (?:the )?(?:background|ambient)(?: (?:light|glow|effect|animation))?$/.test(s))
    return { type: "ambient", on: true };

  /* ---- work that costs money: load it, never launch it ---- */
  {
    const m = s.match(/^(?:build|create|make|design|start|spin up|launch|set up|setup|put together|draft|generate)\s+(?:me\s+|us\s+)?(?:a\s+|an\s+|the\s+)?(?:new\s+)?(?:premium\s+|modern\s+|simple\s+|beautiful\s+)?(website|web site|site|landing page|web app|webapp|online store|store|shop|homepage|home page|portfolio|blog)\b(.*)$/);
    if (m) {
      const rest = m[2].trim();
      if (rest.length >= 4) return { type: "build", brief: capitalise(raw.replace(/^\s*(?:(?:hey|ok|okay)\s+)?jarvis[\s,]*/i, "").trim()) };
      return { type: "nav", route: "builder", title: "Website builder" };
    }
  }
  {
    const m = s.match(/^(?:ask|tell|have|get|message|send)\s+(?:the\s+)?@?([a-z]+)\s+(?:to\s+)?(.{4,})$/);
    if (m) {
      const who = m[1];
      const target = AGENTS.includes(who) ? "@" + who
        : ["agency", "agents", "team", "fleet", "everyone", "everybody", "all"].includes(who) ? (/^(?:agents|fleet|everyone|everybody|all)$/.test(who) ? "@all" : "@orchestrator")
        : null;
      if (target) return { type: "agency", target, text: capitalise(m[2].trim()) };
    }
  }
  if (/^(?:run|start|do|perform)\s+(?:a\s+|an\s+)?(?:full\s+|complete\s+|quick\s+)?(?:seo\s+|site\s+|website\s+|technical\s+)?audit\b/.test(s))
    return { type: "agency", target: "@orchestrator", text: capitalise(s) };

  {
    // "make an image of ...", "generate a video of ..." -> loaded into Image & Video, not generated
    const m = s.match(/^(?:generate|create|make|design|render|produce|draw)\s+(?:me\s+|us\s+)?(?:a\s+|an\s+|some\s+)?(?:new\s+)?(image|picture|photo|illustration|graphic|hero image|banner|thumbnail|video|clip|animation|reel|ad video|video ad)s?\s+(?:of|for|showing|with|about)\s+(.{3,})$/);
    if (m) return { type: "media", mode: /video|clip|animation|reel/.test(m[1]) ? "text-to-video" : "text-to-image",
                    brief: capitalise(m[2]) };
  }
  {
    const m = s.match(/^(?:use the computer|use the browser|use computer use|have claude browse|browse the web|go online|get on the web)\s+(?:to\s+|and\s+)?(.{4,})$/);
    if (m) return { type: "computer", task: capitalise(m[1]) };
  }

  /* ---- search ---- */
  {
    const m = s.match(/^(?:search for|search|find me|find|look up|lookup|look for|where is|where's)\s+(.+)$/);
    if (m && !pageFor(m[1])) return { type: "search", q: m[1].replace(/^(?:the|a|an|my)\s+/, "") };
  }

  /* ---- pages and records ---- */
  const bare = pageFor(s);
  if (bare) return { type: "nav", route: bare.h, title: bare.t };
  const nv = s.match(NAV_VERB);
  if (nv) {
    const target = nv[1].trim();
    const page = pageFor(target);
    if (page) return { type: "nav", route: page.h, title: page.t };
    const rec = bestRecord(target.replace(/\s+(?:project|website|site|client|account)$/, ""), ctx);
    if (rec) return { type: "record", ...rec };
  }
  {
    const rec = s.split(" ").length <= 6 ? bestRecord(s, ctx) : null;
    if (rec && rec.score >= 0.85) return { type: "record", ...rec };
  }

  return { type: "ask", text: String(raw || "").replace(/^\s*(?:(?:hey|ok|okay)\s+)?jarvis[\s,]*/i, "").trim() || s };
}

function capitalise(t) {
  t = String(t || "").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

/* What Jarvis offers when asked for help. Each line is a real, parsed command. */
export const HELP = [
  ["Status report", "a spoken briefing of the whole agency"],
  ["What needs my attention?", "approvals, deadlines and failures, most urgent first"],
  ["Open approvals", "or any page: websites, growth, clients, models…"],
  ["Build a website for a dentist in Austin", "loads the brief into the builder for you to start"],
  ["Tell Scout to research our competitors", "hands the job to an agent, ready to send"],
  ["Search Northgate", "finds clients, projects and keywords"],
  ["Use the computer to check who ranks for roof repair in Denver", "loads a browser task for Claude to run"],
  ["Make an image of a sunlit dental clinic reception", "loads the brief into Image & Video"],
  ["Switch to amethyst", "accent colours: teal, amethyst, sky, orchid, mono"],
  ["Compact mode", "or 'turn off animations', 'collapse the sidebar'"],
  ["Anything else", "is answered by your connected model, from live agency data"],
];
