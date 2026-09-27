// ============================================================
// PREFERENCES — how this operator likes the dashboard to look and
// sound. Per browser, never shared, never sent to the server.
//
// Storage can be missing (private windows, blocked site data), so
// every read and write is guarded and the defaults always render.
// Visual prefs land as attributes on <html>; CSS does the rest.
// ============================================================

const KEY = "gem-prefs";

/* No accent may resemble a status colour: amber reads as "warning" and
   rose as "failed", so neither is offered. */
export const ACCENTS = [
  { id: "teal",   t: "Teal",     c: "#64d4bd" },
  { id: "violet", t: "Amethyst", c: "#a594ff" },
  { id: "sky",    t: "Sky",      c: "#7cc4ff" },
  { id: "orchid", t: "Orchid",   c: "#e39cf2" },
  { id: "mono",   t: "Mono",     c: "#e6ebe9" },
];

const DEFAULTS = {
  accent: "teal",
  density: "comfortable",   // comfortable | compact
  motion: "full",           // full | calm | off
  ambient: false,           // the slow background light; DESIGN.md retires atmosphere, so opt-in
  voice: {
    speak: true,            // read Jarvis's replies aloud
    voiceName: "",          // a speechSynthesis voice; "" picks one automatically
    rate: 1.03,
    wake: false,            // listen for "Jarvis" while the tab is open — opt in
    chime: true,
    conversation: true,     // hands-free: after answering, listen again until goodbye
    barge: true,            // talking over Jarvis stops him (headphones make this cleanest)
  },
};

function read() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "{}");
    return { ...DEFAULTS, ...raw, voice: { ...DEFAULTS.voice, ...(raw.voice || {}) } };
  } catch { return structuredClone(DEFAULTS); }
}

export const prefs = read();
const listeners = new Set();

export function setPref(path, value) {
  const [a, b] = path.split(".");
  if (b) prefs[a] = { ...prefs[a], [b]: value };
  else prefs[a] = value;
  try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch { /* session-only then */ }
  applyPrefs();
  listeners.forEach(fn => { try { fn(prefs, path); } catch (e) { console.error(e); } });
}
export const onPrefs = fn => (listeners.add(fn), () => listeners.delete(fn));

/* The OS "reduce motion" setting always wins over the in-app choice. */
export function motionLevel() {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return "off";
  return prefs.motion;
}

export function applyPrefs() {
  const d = document.documentElement;
  d.dataset.accent = ACCENTS.some(a => a.id === prefs.accent) ? prefs.accent : "teal";
  d.dataset.density = prefs.density === "compact" ? "compact" : "comfortable";
  d.dataset.motion = motionLevel();
  d.dataset.ambient = prefs.ambient && motionLevel() !== "off" ? "on" : "off";
}
applyPrefs();
matchMedia("(prefers-reduced-motion: reduce)").addEventListener?.("change", applyPrefs);
