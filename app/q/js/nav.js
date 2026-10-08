// ============================================================
// NAVIGATION MODEL — one list for the sidebar, the command palette,
// keyboard shortcuts and Jarvis. It used to be copied into each of
// them and drifted (the palette still said "Monitor" and "AI Command"
// after the sidebar was renamed).
//
// Only destinations with a real backend are listed. Pruned 2026-09-14:
// pages with no data or that duplicated a view inside Projects or
// Clients. Their API endpoints are untouched.
//   say  — extra words Jarvis accepts for the page
//   key  — the "g then key" keyboard shortcut
// ============================================================

export const NAV = [
  { group: "Workspace", items: [
    { h: "", ic: "deck", t: "Overview", s: "The whole agency at a glance", key: "o",
      say: ["overview", "home", "dashboard", "command deck", "main page", "start page"] },
    { h: "projects", ic: "proj", t: "Websites", s: "Everything being built", key: "w",
      say: ["websites", "website list", "projects", "sites", "my sites", "all sites"] },
    { h: "growth", ic: "chart", t: "Growth", s: "Your search results, fixes and link outreach", key: "r",
      say: ["growth", "search performance", "traffic", "analytics"] },
    { h: "seo", ic: "seo", t: "OpenSEO", s: "Research: keywords, competitors, backlinks, rank tracking", key: "s",
      say: ["openseo", "open seo", "seo", "seo suite", "seo tools", "keyword research", "rank tracking",
            "keywords", "keyword list", "search terms", "tracked keywords",
            "rank tracker", "rankings", "backlink checker", "domain overview", "ai visibility"] },
    { h: "clients", ic: "users", t: "Clients", s: "Who is paying, and their reports", key: "c",
      say: ["clients", "customers", "accounts", "client list"] },
    { h: "monitor", ic: "bolt", t: "Activity", s: "What is being worked on right now", key: "a", badge: "working",
      say: ["activity", "monitor", "live activity", "what's running", "running work", "feed"] },
    { h: "office", ic: "office", t: "The office", s: "Your agents at work, live", key: "f",
      say: ["the office", "office", "virtual office", "team floor", "who is working", "show me the team"] },
  ]},
  { group: "Agency tools", items: [
    { h: "builder", ic: "build", t: "Website builder", s: "Turn an idea into a site", key: "b",
      say: ["website builder", "builder", "build page", "new website page"] },
    { h: "approvals", ic: "approve", t: "Approvals", s: "Decisions waiting on you", key: "p", badge: "approvals",
      say: ["approvals", "decisions", "approval queue", "pending approvals", "sign offs"] },
    { h: "ai", ic: "ai", t: "Ask the agency", s: "Send work to the agent fleet", key: "k",
      say: ["ask the agency", "agent chat", "chat", "command centre", "command center", "ai command"] },
    { h: "agency", ic: "users", t: "The Agency", s: "Specialists, divisions and runtime agents", key: "t",
      say: ["the agency", "agency", "team", "agents", "specialists", "staff"] },
    { h: "media", ic: "image", t: "Image & Video", s: "Generate images and video with 400+ models", key: "v",
      say: ["image and video", "images and video", "image studio", "video studio", "media", "media studio",
            "images", "videos", "image generation", "video generation", "generate images", "generate videos"] },
    { h: "computer", ic: "cursor", t: "Computer use", s: "An AI operates a browser for you", key: "e",
      say: ["computer use", "computer", "the computer", "browser agent", "browser", "operator"] },
    { h: "models", ic: "bot", t: "Models", s: "Claude, ChatGPT, Ollama and Jev", key: "m",
      say: ["models", "engines", "ai models", "providers", "claude", "chatgpt", "jev"] },
    { h: "integrations", ic: "plug", t: "Integrations", s: "Connected data sources", key: "i", badge: "integrations",
      say: ["integrations", "connections", "data sources", "search console", "connectors"] },
  ]},
];

/* Reachable pages that are not in the sidebar; they live under Growth. */
export const EXTRA = [
  { h: "optimize", ic: "seo", t: "Website audits", s: "SEO, AEO and GEO audits", key: "u",
    say: ["website audits", "audits", "audit page", "optimize", "optimisation", "optimization", "aeo", "geo"] },
  { h: "backlinks", ic: "link", t: "Link outreach", s: "Sites to earn links from, and where each stands", key: "l",
    say: ["link outreach", "backlinks", "links", "link building", "outreach", "link prospects"] },
];

export const PAGES = NAV.flatMap(g => g.items).concat(EXTRA);

export const TITLES = Object.fromEntries(PAGES.map(p => [p.h, p.t]));
Object.assign(TITLES, { agents: "The Agency", project: "Websites", client: "Clients" });
