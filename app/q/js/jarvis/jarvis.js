// ============================================================
// JARVIS — the voice of the dashboard.
//
//   Ctrl+J, the orb in the top bar, or (opt-in) saying "Jarvis".
//
// Flow: speech → text → intents.js. Common commands are answered here
// from the same store the pages render, so the spoken numbers are the
// on-screen numbers. Anything else goes to /api/agency/voice/ask,
// which answers from a digest of real rows and may only navigate.
//
// Two display modes: "full" (the HUD over the page) and "dock" (a
// small pill at the bottom, so a page Jarvis just opened is visible
// while he talks about it).
// ============================================================
import { store, refresh, esc, icon, apiPost, token } from "../core.js";
import { openPalette } from "../palette.js";
import { PAGES } from "../nav.js";
import { isTestProject } from "../workflow.js";
import { parseIntent, HELP } from "./intents.js";
import { prefs, setPref, onPrefs, ACCENTS, motionLevel } from "../prefs.js";
import * as V from "./voice.js";
import { createOrb } from "./orb.js";
import { initFlow, hasWhisper } from "./flow.js";

let deps = {};                // injected by boot: attentionItems, renderRoute, setCollapsed, openPrefs, openShortcuts
let root = null, orb = null, btn = null;
let mode = "closed";          // closed | full | dock
let listening = null;         // the in-flight listenOnce promise
let meter = null;             // mic level for the orb
let wake = null;              // wake-word controller
let turns = [];               // [{ role: "user" | "jarvis", text }]
let typer = 0, dockTimer = 0, lastFocus = null;

/* A conversation: hands-free turns until goodbye, silence, Esc or close.
   speaker: the reply being spoken; abort: cancels the reply being fetched. */
const talk = { on: false, silent: 0, speaker: null, abort: null };

/* Said to Jarvis these are instant and free, with no model call: control,
   pages and appearance. Everything else is a conversation with the brain. */
const LOCAL = new Set(["empty", "dismiss", "hush", "voice", "nav", "record", "history", "scroll", "sidebar",
                       "accent", "density", "motion", "ambient", "palette", "shortcuts", "prefs", "time"]);

const STATE_TEXT = {
  idle: "Standing by", listening: "Listening", thinking: "Thinking",
  speaking: "Speaking", error: "Something went wrong",
};

/* ============================================================
   setup
   ============================================================ */
export function initJarvis(injected) {
  deps = injected || {};
  btn = document.createElement("button");
  btn.className = "jv-btn";
  btn.id = "jvBtn";
  btn.type = "button";
  btn.dataset.state = "idle";
  btn.title = "Talk to Jarvis (Ctrl+J)";
  btn.setAttribute("aria-label", "Talk to Jarvis, Control J");
  btn.innerHTML = `<span class="jv-btn-orb" aria-hidden="true"><i></i></span>
    <span class="jv-btn-t">Jarvis</span><kbd>Ctrl J</kbd>`;
  const bell = document.getElementById("bellBtn");
  bell.parentNode.insertBefore(btn, bell);
  btn.addEventListener("click", () => {
    if (interrupt()) return;
    if (mode === "full" && listening) return stopListening();
    open("full"); listen();
  });

  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "j") {
      e.preventDefault();
      if (interrupt()) return;
      if (listening) return stopListening();
      open("full"); listen();
    } else if (e.key === "Escape" && mode !== "closed" && !document.querySelector(".pal, .ovl")) {
      e.preventDefault(); e.stopPropagation();
      if (listening) stopListening();
      else close();
    }
  }, true);

  onPrefs((p, path) => {
    if (orb && (path === "accent")) setTimeout(() => orb.refreshColours(), 30);
    if (path === "voice.wake") syncWake();
  });
  syncWake();

  // Ctrl+Space: talk instead of type, anywhere (flow.js)
  initFlow({
    handle,
    micBusy(on) {                // one microphone: flow borrows it from Jarvis
      if (on) {
        if (listening) stopListening(true);
        if (talk.speaker) talk.speaker.stop();
        V.stopSpeaking();
        pauseWake();
      } else resumeWake();
    },
  });
}

/* ============================================================
   HUD
   ============================================================ */
function build() {
  root = document.createElement("div");
  root.className = "jv";
  root.dataset.mode = "closed";
  root.dataset.state = "idle";
  root.innerHTML = `
    <div class="jv-bg" data-close></div>
    <div class="jv-stage" role="dialog" aria-modal="true" aria-label="Jarvis voice assistant">
      <header class="jv-top">
        <div class="jv-brand"><b>J.A.R.V.I.S.</b><span class="jv-state" id="jvState">${STATE_TEXT.idle}</span></div>
        <div class="jv-top-r">
          <button class="jv-pill" id="jvConv" type="button" aria-pressed="true" title="After answering, listen again until you say goodbye">Hands-free</button>
          <button class="jv-ic" id="jvSetBtn" type="button" aria-label="Voice settings" aria-expanded="false" title="Voice settings">${icon("gear")}</button>
          <button class="jv-ic jv-dock-btn" id="jvDockBtn" type="button" aria-label="Minimise to the bottom" title="Minimise">${svg("M5 19h14")}</button>
          <button class="jv-ic" data-close type="button" aria-label="Close Jarvis" title="Close (Esc)">${icon("x")}</button>
        </div>
      </header>

      <div class="jv-main">
        <button class="jv-orb" id="jvOrb" type="button" aria-label="Start listening"><canvas></canvas></button>
        <div class="jv-words">
          <div class="jv-log" id="jvLog" aria-live="polite" aria-label="Conversation"></div>
          <div class="jv-heard" id="jvHeard"></div>
          <div class="jv-reply" id="jvReply" aria-live="polite"></div>
          <div class="jv-meta" id="jvMeta"></div>
        </div>
        <div class="jv-mini-acts">
          <button class="jv-ic" id="jvExpand" type="button" aria-label="Expand Jarvis" title="Expand">${svg("M4 14v6h6M20 10V4h-6M4 20l7-7M20 4l-7 7")}</button>
          <button class="jv-ic" data-close type="button" aria-label="Close Jarvis" title="Close">${icon("x")}</button>
        </div>
      </div>

      <div class="jv-cards" id="jvCards"></div>
      <div class="jv-chips" id="jvChips"></div>

      <form class="jv-input" id="jvForm" autocomplete="off">
        <button type="button" class="jv-mic" id="jvMic" aria-pressed="false" aria-label="Start listening">${svg("M12 2a3 3 0 00-3 3v7a3 3 0 006 0V5a3 3 0 00-3-3zM19 10v2a7 7 0 01-14 0v-2M12 19v3")}</button>
        <input id="jvText" type="text" placeholder="Talk, or type anything…" aria-label="Type a command for Jarvis" spellcheck="false">
        <button type="submit" class="jv-send" aria-label="Send">${icon("send")}</button>
      </form>
      <div class="jv-foot">
        <span><kbd>Ctrl J</kbd> talk</span><span><kbd>Esc</kbd> close</span>
        <span class="jv-priv">${esc(V.canListen ? "Speech is transcribed by your browser's speech service. Gem Agency never records audio." : "This browser has no speech recognition — type commands, or open the dashboard in Edge or Chrome.")}</span>
      </div>
      <div class="jv-set" id="jvSet" hidden></div>
    </div>`;
  document.body.appendChild(root);

  orb = createOrb(root.querySelector("canvas"));
  root.querySelectorAll("[data-close]").forEach(b => b.addEventListener("click", close));
  root.querySelector("#jvOrb").addEventListener("click", () => (interrupt() || (listening ? stopListening() : (mode === "dock" ? open("full") : listen()))));
  root.querySelector("#jvMic").addEventListener("click", () => (interrupt() || (listening ? stopListening() : listen())));
  root.querySelector("#jvConv").addEventListener("click", () => {
    setPref("voice.conversation", !prefs.voice.conversation);
    paintConv();
    if (!prefs.voice.conversation) talk.on = false;
  });
  paintConv();
  root.querySelector("#jvExpand").addEventListener("click", () => open("full"));
  root.querySelector("#jvDockBtn").addEventListener("click", () => dock(false));
  root.querySelector("#jvSetBtn").addEventListener("click", toggleSettings);
  root.querySelector("#jvForm").addEventListener("submit", e => {
    e.preventDefault();
    const inp = root.querySelector("#jvText");
    const v = inp.value.trim();
    if (!v) return;
    inp.value = "";
    stopListening(true);
    handle(v, { voice: false });
  });
  root.addEventListener("keydown", trapFocus);
  window.addEventListener("resize", () => orb && orb.resize());
  paintChips();
}

function svg(d) {
  return `<svg viewBox="0 0 24 24" aria-hidden="true">${d.split("M").filter(Boolean).map(s => `<path d="M${s}"/>`).join("")}</svg>`;
}

function paintChips() {
  const chips = ["Status report", "What needs my attention?", "Open approvals",
                 "Build a website for a dentist in Austin", "Switch to amethyst", "What can you do?"];
  const el = root.querySelector("#jvChips");
  el.innerHTML = chips.map((c, i) => `<button type="button" class="jv-chip" style="--i:${i}">${esc(c)}</button>`).join("");
  el.querySelectorAll(".jv-chip").forEach(b => b.addEventListener("click", () => { stopListening(true); handle(b.textContent, { voice: false }); }));
}

export function open(m = "full") {
  if (!root) build();
  clearTimeout(dockTimer);
  if (mode === "closed") {
    lastFocus = document.activeElement;
    pauseWake();
  }
  mode = m;
  root.dataset.mode = m;
  document.documentElement.classList.toggle("jv-open", m === "full");
  requestAnimationFrame(() => { orb.start(); orb.resize(); });
  // canvas size changes with the mode transition; size again once it settles
  setTimeout(() => orb && orb.resize(), 380);
  if (m === "full") setTimeout(() => root.querySelector("#jvText")?.focus({ preventScroll: true }), 60);
  setState(root.dataset.state || "idle");
}

function dock(autoHide = true) {
  if (mode === "closed") return;
  open("dock");
  if (autoHide) scheduleDockHide();
}

function scheduleDockHide() {
  clearTimeout(dockTimer);
  dockTimer = setTimeout(() => {
    if (mode === "dock" && !listening && !V.isSpeaking() && root.dataset.state !== "thinking") close();
  }, 6500);
}

export function close() {
  if (!root || mode === "closed") return;
  endTalk();
  stopListening(true);
  V.stopSpeaking();
  clearTimeout(dockTimer);
  mode = "closed";
  root.dataset.mode = "closed";
  document.documentElement.classList.remove("jv-open");
  setState("idle");
  setTimeout(() => { if (mode === "closed") orb.stop(); }, 400);
  toggleSettings(false);
  if (lastFocus && document.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
  resumeWake();
}

function setState(s) {
  if (root) {
    root.dataset.state = s;
    root.querySelector("#jvState").textContent = STATE_TEXT[s] || s;
    root.querySelector("#jvMic").setAttribute("aria-pressed", String(s === "listening"));
    root.querySelector("#jvOrb").setAttribute("aria-label", s === "listening" ? "Stop listening" : "Start listening");
    orb && orb.setState(s);
  }
  if (btn) btn.dataset.state = mode === "closed" && wake ? "wake" : s;
}

function trapFocus(e) {
  if (e.key !== "Tab" || mode !== "full") return;
  const f = [...root.querySelectorAll("button, input, select, [tabindex]")].filter(n => n.offsetParent && !n.disabled);
  if (!f.length) return;
  const first = f[0], last = f[f.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

/* ============================================================
   listening
   ============================================================ */
async function listen({ barge = false } = {}) {
  if (!root) build();
  if (listening) return;
  if (mode === "closed") open("full");
  if (!barge) { if (talk.speaker) talk.speaker.stop(); V.stopSpeaking(); }
  if (!V.canListen) {
    show({ say: "", text: "This browser has no speech recognition. Type below, or open the dashboard in Edge or Chrome to talk.", error: true });
    root.querySelector("#jvText").focus();
    return;
  }
  clearTimeout(dockTimer);
  if (!barge && hasWhisper()) return listenWhisper();
  if (!barge) setState("listening");
  heard("", true);
  if (prefs.voice.chime && !barge) V.chime(true);
  let cut = false;
  const p = V.listenOnce({ onInterim: t => {
    // while Jarvis is talking, only a real interruption counts: two words
    // or more that are not just the microphone hearing his own voice
    if (barge && talk.speaker && talk.speaker.active) {
      if (!prefs.voice.barge || t.split(/\s+/).length < 2 || V.isEcho(t, talk.speaker.spoken)) return;
      talk.speaker.stop();
      cut = true;
      setState("listening");
    }
    heard(t, true);
  } });
  listening = p;
  // the orb meters the mic in parallel; failure there is cosmetic
  V.micMeter().then(m => {
    if (listening === p && m) { meter = m; orb.setSource(m); }
    else if (m) m.close();
  });
  try {
    let text = await p;
    if (listening !== p) return;           // cancelled
    endListening(!barge || cut);
    if (barge && !cut) {
      // heard nothing, or only himself, while he was talking: let him finish,
      // then carry on the conversation as normal
      if (text && talk.speaker && V.isEcho(text, talk.speaker.spoken)) text = "";
      if (!text) {
        if (talk.speaker && talk.speaker.active) await talk.speaker.done;
        if (talk.on && mode !== "closed" && !listening) listen();
        return;
      }
      if (talk.speaker) talk.speaker.stop();
    }
    if (!text) {
      if (talk.on) {
        talk.silent++;
        if (talk.silent < 2 && mode !== "closed") { listen(); return; }
        pauseTalk();
        return;
      }
      setState("idle");
      show({ say: "", text: "I didn't catch that. Try again, or type below." });
      if (mode === "dock") scheduleDockHide();
      return;
    }
    talk.silent = 0;
    handle(text, { voice: true });
  } catch (err) {
    if (listening !== p) return;
    endListening(true);
    talk.on = false;
    setState("error");
    show({ say: "", text: err.message, error: true,
           cards: err.code === "not-allowed" ? [{ t: "Allow the microphone", s: "Click the lock or camera icon in the address bar, allow the microphone for this site, then try again." }] : null });
  }
}

/* Listening with Whisper on this PC. The browser's recogniser only shows the
   words as you speak (it is not needed, and it is what said "I didn't catch
   that" when its service heard nothing); the microphone level decides when
   you have finished: speech, then a 1.1 s pause. Whisper writes it down. */
const VAD = { on: 0.1, quiet: 1100, wait: 8000, max: 30000 };
async function listenWhisper() {
  setState("listening");
  heard("", true);
  if (prefs.voice.chime) V.chime(true);
  let interim = "", ended = false, timer = 0, ctl = null;
  const rec = await V.recordPCM();
  if (!rec) {
    setState("error");
    show({ say: "", text: "I couldn't open the microphone. Allow it from the address bar, or type below.", error: true });
    return;
  }
  meter = rec; orb.setSource(rec);
  try { ctl = V.listenHold({ onInterim: t => { interim = t; heard(t, true); } }); } catch { ctl = null; }
  const began = performance.now();
  let spoke = 0, lastVoice = 0;
  const finish = async (keep = true) => {
    if (ended) return;
    ended = true; clearInterval(timer);
    const pcm = rec.stop();
    ctl && ctl.abort();
    if (listening === me) listening = null;
    endListening(keep);
    if (!keep) { setState("idle"); return; }
    if (!spoke) { nothingHeard(); return; }
    setState("thinking");
    let text = "";
    try {
      text = (await apiPost("/api/agency/voice/dictate", { mode: "command", audio: V.pcmBase64(pcm), heard: interim })).text || "";
    } catch (e) {
      text = interim;                                   // Whisper failed: the browser's words, if it had any
      if (!text) { setState("error"); show({ say: "", text: `I couldn't make that out: ${e.message || e}`, error: true }); return; }
    }
    if (!text.trim()) { nothingHeard(); return; }
    talk.silent = 0;
    handle(text, { voice: true });
  };
  const me = { stop: () => finish(true), abort: () => finish(false) };
  listening = me;
  timer = setInterval(() => {
    const now = performance.now(), lv = rec.level();
    if (lv > VAD.on) { if (!spoke) spoke = now; lastVoice = now; }
    if ((spoke && now - lastVoice > VAD.quiet) || (!spoke && now - began > VAD.wait) || now - began > VAD.max) finish(true);
  }, 50);
}

function nothingHeard() {
  if (talk.on) {
    talk.silent++;
    if (talk.silent < 2 && mode !== "closed") { listen(); return; }
    pauseTalk();
    return;
  }
  setState("idle");
  show({ say: "", text: "I didn't hear anything. Press Ctrl J and talk, or type below." });
  if (mode === "dock") scheduleDockHide();
}

/* Stop Jarvis mid-sentence and listen: the button, the orb and Ctrl J all do this. */
function interrupt() {
  if (!talk.speaker || !talk.speaker.active) return false;
  talk.speaker.stop();
  if (talk.abort) { talk.abort.abort(); talk.abort = null; }
  talk.on = prefs.voice.conversation;
  if (listening) setState("listening"); else listen();
  return true;
}

function endTalk() {
  talk.on = false;
  talk.silent = 0;
  if (talk.abort) { talk.abort.abort(); talk.abort = null; }
  if (talk.speaker) { talk.speaker.stop(); talk.speaker = null; }
}

function pauseTalk() {
  talk.on = false;
  talk.silent = 0;
  setState("idle");
  show({ say: "", text: "I'll be here. Press Ctrl J, or tap the orb, when you want me." });
  if (mode === "dock") scheduleDockHide();
}

function paintConv() {
  const b = root && root.querySelector("#jvConv");
  if (b) b.setAttribute("aria-pressed", String(!!prefs.voice.conversation));
}

function endListening(chime = true) {
  listening = null;
  if (meter) { meter.close(); meter = null; }
  orb && orb.setSource(null);
  if (prefs.voice.chime && chime) V.chime(false);
}

function stopListening(silent = false) {
  if (!listening) return;
  const p = listening;
  if (silent) { listening = null; p.abort && p.abort(); endListening(); setState("idle"); }
  else p.stop();   // resolves with whatever was heard so far
}

/* ---------------- wake word ---------------- */
function syncWake() {
  if (prefs.voice.wake && mode === "closed") startWake();
  else if (!prefs.voice.wake) stopWake();
}
function startWake() {
  if (wake || !V.canListen) return;
  wake = V.wakeListener({
    onWake: rest => {
      wake = null;
      open("full");
      if (rest && rest.split(/\s+/).length >= 1 && rest.length > 2) handle(rest, { voice: true });
      else { if (prefs.voice.chime) V.chime(true); listen(); }
    },
    onFatal: err => {
      wake = null;
      setPref("voice.wake", false);
      if (btn) btn.dataset.state = "idle";
      import("../core.js").then(c => c.toast(`Wake word turned off: ${err.message}`, true));
    },
  });
  if (btn) btn.dataset.state = "wake";
}
function stopWake() { if (wake) { wake.stop(); wake = null; } if (btn && mode === "closed") btn.dataset.state = "idle"; }
function pauseWake() { if (wake) { wake.stop(); wake = null; } }
function resumeWake() { if (prefs.voice.wake) setTimeout(() => { if (mode === "closed") startWake(); }, 500); }

/* ============================================================
   understanding and doing
   ============================================================ */
function heard(text, live = false) {
  const el = root.querySelector("#jvHeard");
  el.textContent = text ? `“${text}”` : live ? "" : "";
  el.classList.toggle("live", live);
}

function recordCtx() {
  const ov = store.overview || {};
  return {
    projects: (ov.projects || []).filter(p => p.status !== "archived").map(p => ({ id: p.id, name: p.name })),
    clients: (ov.clients || []).map(c => ({ id: c.id, name: c.name })),
  };
}

export async function handle(raw, { voice = false } = {}) {
  if (!root) build();
  if (mode === "closed") open("full");
  const text = String(raw || "").trim();
  heard(text);
  const intent = parseIntent(text, recordCtx());
  if (intent.type === "empty") {
    if (voice) listen();
    return;
  }
  if (voice && prefs.voice.conversation) talk.on = true;
  if (LOCAL.has(intent.type)) {
    turns.push({ role: "user", text });
    bubble("user", text);
    let out;
    try { out = await execute(intent, voice); }
    catch (e) { out = { say: "That didn't work.", text: `That didn't work: ${e.message || e}`, error: true }; }
    if (intent.type === "dismiss") talk.on = false;
    if (out) {
      if (out.say || out.text) bubble("jarvis", out.text || out.say);
      await respond(out, voice);
    } else if (voice && talk.on && mode !== "closed" && !listening) listen();
    return;
  }
  return converse(text, voice, intent);
}

/* ---------------- the conversation ---------------- */
function bubble(role, text = "", pending = false) {
  const log = root.querySelector("#jvLog");
  const el = document.createElement("div");
  el.className = `jv-msg ${role}${pending ? " pending" : ""}`;
  if (pending) el.innerHTML = `<span class="jv-dots"><i></i><i></i><i></i></span>`;
  else el.textContent = text;
  log.appendChild(el);
  while (log.children.length > 30) log.firstChild.remove();
  root.dataset.conv = "on";
  el.scrollIntoView({ block: "end", behavior: motionLevel() === "full" ? "smooth" : "auto" });
  return el;
}

async function converse(text, voice, intent) {
  turns.push({ role: "user", text });
  bubble("user", text);
  const reply = bubble("jarvis", "", true);
  const replyEl = root.querySelector("#jvReply");
  root.querySelector("#jvCards").innerHTML = "";
  root.querySelector("#jvMeta").textContent = "";
  setState("thinking");
  show({ pending: true });
  const ctrl = new AbortController();
  talk.abort = ctrl;
  const speaker = V.createSpeaker({
    voiceName: prefs.voice.voiceName, rate: prefs.voice.rate, enabled: prefs.voice.speak,
    onBoundary: () => orb && orb.bump(),
    onStart: () => { if (!listening || !talk.on) setState("speaking"); },
  });
  if (talk.speaker) talk.speaker.stop();
  talk.speaker = speaker;
  let full = "", meta = null, actions = [], failed = "", barging = false;
  try {
    const t = await token();
    const r = await fetch("/api/agency/voice/chat", {
      method: "POST", signal: ctrl.signal,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${t}` },
      body: JSON.stringify({ text, history: turns.slice(-13, -1), page: location.hash }),
    });
    if (!r.ok || !r.body) throw new Error(`the voice service answered ${r.status}`);
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const events = buf.split("\n\n");
      buf = events.pop() || "";
      for (const ev of events) {
        const line = ev.split("\n").find(l => l.startsWith("data:"));
        if (!line) continue;
        let msg;
        try { msg = JSON.parse(line.slice(5)); } catch { continue; }
        if (msg.type === "meta") meta = msg.data;
        else if (msg.type === "actions") actions = msg.data || [];
        else if (msg.type === "error") failed = String(msg.data || "unknown error");
        else if (msg.type === "token") {
          if (!full) { reply.classList.remove("pending"); replyEl.classList.remove("pending", "err"); if (!prefs.voice.speak) setState("idle"); }
          full += msg.data;
          reply.textContent = full;
          replyEl.textContent = full;
          reply.scrollIntoView({ block: "end" });
          speaker.push(msg.data);
          // hands-free: keep the mic open while he talks, so you can cut in
          if (voice && talk.on && prefs.voice.barge && prefs.voice.speak && !barging && V.canListen) {
            barging = true;
            setTimeout(() => { if (speaker.active && !listening && talk.speaker === speaker) listen({ barge: true }); }, 400);
          }
        }
      }
    }
  } catch (e) {
    if (e.name === "AbortError") { speaker.stop(); reply.classList.remove("pending"); return; }
    failed = String(e.message || e);
  }
  if (talk.abort === ctrl) talk.abort = null;

  if (failed && !full) {
    speaker.stop();
    reply.remove();
    // the brain is out of reach: answer what can be answered from the screen
    if (intent && intent.type !== "ask") {
      let out = null;
      try { out = await execute(intent, voice); } catch { out = null; }
      if (out) {
        if (out.say || out.text) bubble("jarvis", out.text || out.say);
        return respond(out, voice);
      }
    }
    const out = {
      say: "I can't reach a thinking engine right now.",
      text: "I can't reach a thinking engine right now, so only instant commands (pages, settings) work.",
      error: true,
      cards: [{ t: "Why", s: failed.slice(0, 280) }, { t: "Check the brain", s: "Models page", go: "models" }],
    };
    bubble("jarvis", out.text).classList.add("err");
    talk.on = false;
    return respond(out, false);
  }
  if (failed) reply.textContent = `${full}\n\n(${failed.slice(0, 160)})`;
  speaker.end();
  turns.push({ role: "jarvis", text: full.trim() });
  turns = turns.slice(-24);
  if (meta) root.querySelector("#jvMeta").textContent = `${meta.label}${meta.model ? " · " + meta.model : ""}${
    (meta.fell_back_from || []).length ? " · after " + meta.fell_back_from.map(a => a.provider).join(", ") + " failed" : ""}`;
  let end = false;
  for (const a of actions) {
    if (a.do === "end_conversation") end = true;
    else { try { await runAction(a); } catch (e) { console.error("jarvis action failed", a, e); } }
  }
  await speaker.done;
  if (talk.speaker === speaker) talk.speaker = null;
  if (mode === "closed") return;
  if (end) { talk.on = false; setTimeout(close, 500); return; }
  if (!listening) setState("idle");
  if (voice && talk.on && !listening) { listen(); return; }
  if (mode === "dock") scheduleDockHide();
}

/* What the brain may do, all reversible and none of it spending money.
   The server already checked each action against the vocabulary. */
async function runAction(a) {
  const here = location.hash.replace(/^#/, "").split("/")[0];
  const visit = async (route, refreshIfHere = false) => {
    if (here === route && refreshIfHere && deps.renderRoute) await deps.renderRoute();
    else go(route);
    if (mode === "full") dock(false);
  };
  const stash = (k, v) => { try { sessionStorage.setItem(k, v); } catch { /* the page opens empty */ } };
  switch (a.do) {
    case "open": return visit(a.page);
    case "open_record": return visit(`${a.kind}/${a.id}`);
    case "search": if (mode === "full") dock(false); return openPalette(a.query);
    case "draft_website": stash("gem-builder-brief", a.brief); return visit("builder", true);
    case "draft_agent_task": return visit(`ai/${encodeURIComponent(a.text)}?to=${encodeURIComponent(a.agent)}`);
    case "draft_browser_task":
      stash("gem-computer-task", a.task);
      if (a.url) stash("gem-computer-url", a.url);
      return visit("computer", true);
    case "draft_media": stash("gem-media-brief", a.prompt); stash("gem-media-mode", a.mode); return visit("media", true);
    case "set_accent": return setPref("accent", a.accent);
    case "set_density": return setPref("density", a.value);
    case "set_motion": return setPref("motion", a.value);
    case "sidebar": return deps.setCollapsed && deps.setCollapsed(a.collapsed);
    case "scroll": {
      const w = document.getElementById("work");
      if (a.to === "top") return w.scrollTo({ top: 0, behavior: "smooth" });
      if (a.to === "bottom") return w.scrollTo({ top: w.scrollHeight, behavior: "smooth" });
      return w.scrollBy({ top: (a.to === "up" ? -1 : 1) * w.clientHeight * 0.8, behavior: "smooth" });
    }
    case "back": return history.back();
    case "refresh": await refresh(); return deps.renderRoute && deps.renderRoute();
    default: return undefined;
  }
}

const pick = a => a[Math.floor(Math.random() * a.length)];
const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;
const go = hash => { location.hash = hash; };

const NEEDS_DATA = new Set(["greet", "brief", "attention", "running", "count"]);

async function execute(it, voice) {
  // Asked before the first load finished: fetch now rather than claim the
  // server is down. Only a failed fetch is reported as one.
  if (NEEDS_DATA.has(it.type) && !store.overview) {
    setState("thinking");
    try { await refresh(); }
    catch (e) { return { say: "I can't read the agency right now. The server didn't answer.", text: `I can't read the agency right now: ${e.message || e}`, error: true }; }
  }
  switch (it.type) {
    case "empty":
      if (voice) { listen(); return null; }
      return null;
    case "dismiss":
      return { say: pick(["Very good.", "Standing by.", "As you wish."]), close: true };
    case "hush":
      V.stopSpeaking();
      return { say: "", text: "Quiet it is." };
    case "voice":
      setPref("voice.speak", it.speak);
      return { say: it.speak ? "Voice replies are on." : "", text: it.speak ? "Voice replies are on." : "Muted. I'll answer in text." };
    case "help":
      return { say: "Here's what I can do. Tap any of these, or just say it.",
               cards: HELP.map(([t, s]) => ({ t, s, run: t === "Anything else" ? null : t })) };
    case "thanks":
      return { say: pick(["At your service.", "Always a pleasure.", "Happy to help."]) };
    case "greet":
      return { say: `${greeting()}. ${brief().say}`, cards: brief().cards };
    case "brief": {
      const b = brief();
      return { say: b.say, cards: b.cards };
    }
    case "attention": return attention();
    case "running": return running();
    case "count": return count(it.what);
    case "time": {
      const d = new Date();
      return { say: `It's ${d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} on ${d.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" })}.` };
    }
    case "history":
      history.go(it.dir);
      return { say: "", text: it.dir < 0 ? "Back." : "Forward.", dock: true };
    case "refresh":
      await refresh();
      deps.renderRoute && await deps.renderRoute();
      return { say: "Data refreshed.", dock: true };
    case "scroll": {
      const w = document.getElementById("work");
      if (it.to === "top") w.scrollTo({ top: 0, behavior: "smooth" });
      else if (it.to === "bottom") w.scrollTo({ top: w.scrollHeight, behavior: "smooth" });
      else w.scrollBy({ top: (it.to === "up" ? -1 : 1) * w.clientHeight * 0.8, behavior: "smooth" });
      return { say: "", text: "Scrolling.", dock: true };
    }
    case "sidebar":
      deps.setCollapsed && deps.setCollapsed(it.collapsed);
      return { say: it.collapsed ? "Sidebar collapsed." : "Sidebar expanded.", dock: true };
    case "palette":
      close(); openPalette(it.q || "");
      return null;
    case "shortcuts":
      close(); deps.openShortcuts && deps.openShortcuts();
      return null;
    case "prefs":
      close(); deps.openPrefs && deps.openPrefs();
      return null;
    case "accent": {
      setPref("accent", it.accent);
      const name = (ACCENTS.find(a => a.id === it.accent) || {}).t || it.accent;
      return { say: `${name} it is.` };
    }
    case "density":
      setPref("density", it.density);
      return { say: it.density === "compact" ? "Compact layout." : "Comfortable layout.", dock: true };
    case "motion":
      setPref("motion", it.motion);
      if (motionLevel() === "off" && it.motion !== "off")
        return { say: "Your system asks for reduced motion, so animation stays off. Change that in your operating system settings first." };
      return { say: it.motion === "off" ? "Animations off." : it.motion === "calm" ? "Calm motion." : "Full motion." };
    case "ambient":
      setPref("ambient", it.on);
      return { say: it.on ? "Background light on." : "Background light off.", dock: true };
    case "build":
      try { sessionStorage.setItem("gem-builder-brief", it.brief); } catch { /* the builder just opens empty */ }
      if (location.hash.replace(/^#/, "") === "builder" && deps.renderRoute) await deps.renderRoute();
      else go("builder");
      return { say: "I've loaded that brief into the website builder. Check the details and press Build when you're ready. I won't spend credits without you.",
               dock: true };
    case "agency":
      go(`ai/${encodeURIComponent(it.text)}?to=${encodeURIComponent(it.target)}`);
      return { say: `Drafted for ${it.target.replace("@", "")}. Press Send when it looks right.`, dock: true };
    case "search":
      close(); openPalette(it.q);
      return null;
    case "media":
      try { sessionStorage.setItem("gem-media-brief", it.brief); sessionStorage.setItem("gem-media-mode", it.mode); } catch { /* opens empty */ }
      if (location.hash.replace(/^#/, "").split("/")[0] === "media" && deps.renderRoute) await deps.renderRoute();
      else go("media");
      return { say: `I've loaded that into Image and Video. Pick a model and press Generate when it looks right.`, dock: true };
    case "computer":
      try { sessionStorage.setItem("gem-computer-task", it.task); } catch { /* opens empty */ }
      if (location.hash.replace(/^#/, "").split("/")[0] === "computer" && deps.renderRoute) await deps.renderRoute();
      else go("computer");
      return { say: "I've loaded that into Computer use. Add where it should start and press Start when ready.", dock: true };
    case "record":
      go(`${it.kind}/${it.id}`);
      return { say: `Opening ${it.name}.`, dock: true };
    case "nav": {
      const here = location.hash.replace(/^#/, "").split("/")[0];
      if (here === it.route) return { say: `You're already on ${it.title}.`, dock: true };
      go(it.route);
      return { say: `${pick(["Opening", "Here's", "Bringing up"])} ${it.title}.`, dock: true };
    }
    case "ask": return ask(it.text);
    default: return ask(String(it.text || ""));
  }
}

/* ---------------- local answers, from the rendered store ---------------- */
function greeting() {
  const h = new Date().getHours();
  return h < 5 ? "Working late" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

function scope() {
  const ov = store.overview || {};
  const all = (ov.projects || []).filter(p => p.status !== "archived");
  const projects = all.filter(p => !isTestProject(p));
  const ids = new Set(projects.map(p => p.id));
  const items = (deps.attentionItems ? deps.attentionItems() : [])
    .filter(i => !i.go.startsWith("project/") || ids.has(i.go.slice(8)));
  const inflight = store.runs.filter(r => ids.has(r.project_id) && ["running", "queued", "awaiting_approval"].includes(r.state));
  const busy = (ov.agents || []).filter(a => ["running", "planning", "waiting", "needs_approval"].includes(a.status));
  return { ov, projects, items, inflight, busy, live: projects.filter(p => p.status === "live" && p.url).length };
}

function brief() {
  if (!store.overview) return { say: "I can't read the agency right now. The server didn't answer.", cards: [] };
  const { ov, projects, items, inflight, busy, live } = scope();
  const clients = (ov.clients || []).length;
  const parts = [];
  parts.push(`You have ${plural(projects.length, "website")}${live ? `, ${live} live` : ""}, across ${plural(clients, "client")}.`);
  parts.push(inflight.length ? `${plural(inflight.length, "build")} ${inflight.length === 1 ? "is" : "are"} in progress.`
    : busy.length ? `${plural(busy.length, "agent")} ${busy.length === 1 ? "is" : "are"} working.` : "Nothing is building right now.");
  if (items.length) parts.push(`${plural(items.length, "thing")} need${items.length === 1 ? "s" : ""} you. The most urgent: ${items[0].t}.`);
  else parts.push("Nothing needs your attention.");
  const gw = (store.snapshot && store.snapshot.gateway && store.snapshot.gateway.state)
    || (ov.integrations && ov.integrations.hermes_gateway && ov.integrations.hermes_gateway.state);
  if (gw && !["connected", "running", "configured"].includes(gw)) parts.push(`The Hermes gateway is ${gw}.`);
  return { say: parts.join(" "), cards: items.slice(0, 3).map(attCard) };
}

function attCard(i) { return { t: i.t, s: i.s, go: i.go, sev: i.sev }; }

function attention() {
  const { items } = scope();
  if (!items.length) return { say: "All clear. No approvals, overdue work or broken connections." };
  const first = items.slice(0, 3).map(i => i.t.replace(/\.$/, ""));
  const say = `${plural(items.length, "item")} need${items.length === 1 ? "s" : ""} you. `
    + (first.length === 1 ? `${first[0]}.` : `First, ${first[0]}. Then ${first.slice(1).join(", and ")}.`);
  return { say, cards: items.slice(0, 6).map(attCard) };
}

function running() {
  const { inflight, busy } = scope();
  if (!inflight.length && !busy.length) return { say: "Nothing is running. The agency is idle." };
  const say = inflight.length
    ? `${plural(inflight.length, "pipeline")} in flight: ${inflight.slice(0, 3).map(r => `${r.project_name || "a project"}, ${String(r.state).replace(/_/g, " ")}`).join("; ")}.`
    : `${plural(busy.length, "agent")} working: ${busy.slice(0, 4).map(a => a.name).join(", ")}.`;
  return { say, cards: inflight.slice(0, 4).map(r => ({ t: r.project_name || "Pipeline", s: `${r.playbook} · ${String(r.state).replace(/_/g, " ")}`, go: `project/${r.project_id}` })), };
}

function count(what) {
  const { ov, projects, busy } = scope();
  const n = {
    projects: projects.length,
    clients: (ov.clients || []).length,
    approvals: ((ov.counts || {}).approvals_pending) || 0,
    agents: (ov.agents || []).length,
    runs: store.runs.length,
    keywords: ((ov.counts || {}).keywords) || 0,
  }[what];
  const extra = what === "agents" ? `, ${busy.length} working right now` : what === "runs" ? " on record" : "";
  const noun = { projects: "website", clients: "client", approvals: "approval pending", agents: "agent",
                 runs: "pipeline run", keywords: "tracked keyword" }[what];
  const say = what === "approvals"
    ? (n ? `${plural(n, "approval")} ${n === 1 ? "is" : "are"} pending.` : "No approvals are pending.")
    : `${plural(n, noun)}${extra}.`;
  return { say };
}

/* ---------------- the model ---------------- */
async function ask(text) {
  setState("thinking");
  show({ say: "", text: "", pending: true });
  const history = turns.slice(-7, -1);
  try {
    const r = await apiPost("/api/agency/voice/ask", { text, history });
    const act = (r.actions || []).find(a => a.type === "open");
    if (act) go(act.route);
    return {
      say: r.reply,
      meta: `${r.provider}${r.model ? " · " + r.model : ""}${r.seconds ? " · " + Number(r.seconds).toFixed(1) + "s" : ""}${r.fell_back ? " · after a failover" : ""}`,
      dock: !!act,
    };
  } catch (e) {
    const msg = String(e.message || e);
    return {
      say: "I can't reach a reasoning engine right now. Local commands still work.",
      text: "I can't reach a reasoning engine right now. Local commands — pages, status, attention, search — still work.",
      error: true,
      cards: [{ t: "Why", s: msg.slice(0, 280) }, { t: "Connect Claude or ChatGPT", s: "Models page", go: "models" }],
    };
  }
}

/* ============================================================
   responding
   ============================================================ */
function show({ say = "", text, error = false, cards = null, meta = "", pending = false }) {
  const reply = root.querySelector("#jvReply");
  const body = text != null && text !== "" ? text : say;
  clearInterval(typer);
  reply.classList.toggle("err", !!error);
  reply.classList.toggle("pending", !!pending);
  if (pending) { reply.innerHTML = `<span class="jv-dots"><i></i><i></i><i></i></span>`; }
  // hidden tabs throttle timers to once a second; show the text whole there
  else if (motionLevel() === "off" || !body || document.hidden) reply.textContent = body;
  else {
    // type the reply out at roughly speaking pace
    let i = 0;
    reply.textContent = "";
    const step = Math.max(1, Math.round(body.length / 90));
    typer = setInterval(() => {
      i = Math.min(body.length, i + step);
      reply.textContent = body.slice(0, i);
      if (i >= body.length) clearInterval(typer);
    }, 16);
  }
  root.querySelector("#jvMeta").textContent = meta || "";
  const c = root.querySelector("#jvCards");
  if (cards === null && !pending) c.innerHTML = "";
  if (cards && cards.length) {
    c.innerHTML = cards.map((k, i) => {
      const tagName = k.go != null || k.run ? "button" : "div";
      return `<${tagName} class="jv-card ${k.sev ? "sev-" + k.sev : ""}" style="--i:${i}" ${tagName === "button" ? 'type="button"' : ""}
        ${k.go != null ? `data-go="${esc(k.go)}"` : ""} ${k.run ? `data-run="${esc(k.run)}"` : ""}>
        <b>${esc(k.t)}</b>${k.s ? `<span>${esc(k.s)}</span>` : ""}${k.go != null ? icon("arrowR") : ""}</${tagName}>`;
    }).join("");
    c.querySelectorAll("[data-go]").forEach(b => b.addEventListener("click", () => { go(b.dataset.go); dock(); }));
    c.querySelectorAll("[data-run]").forEach(b => b.addEventListener("click", () => handle(b.dataset.run, { voice: false })));
  }
}

async function respond(out, voice) {
  const spoken = out.say || "";
  if (spoken || out.text) turns.push({ role: "jarvis", text: out.text || spoken });
  turns = turns.slice(-20);
  show(out);
  if (out.dock && mode === "full") dock(false);
  if (out.error) setState("error");
  if (spoken && prefs.voice.speak && V.canSpeak) {
    setState("speaking");
    await V.speak(spoken, { voiceName: prefs.voice.voiceName, rate: prefs.voice.rate, onBoundary: () => orb && orb.bump() });
    if (mode === "closed") return;
    if (!listening) setState(out.error ? "error" : "idle");
  } else if (!out.error) setState("idle");
  if (out.close) { setTimeout(close, 250); return; }
  // hands-free: after any spoken reply, listen for the next thing
  if (voice && talk.on && mode !== "closed" && !listening) { listen(); return; }
  // without it, a question asked out loud still gets a follow-up
  if (voice && /\?\s*$/.test(spoken) && mode !== "closed" && !listening) { listen(); return; }
  if (mode === "dock") scheduleDockHide();
}

/* ============================================================
   settings
   ============================================================ */
function toggleSettings(force) {
  if (!root) return;
  const panel = root.querySelector("#jvSet");
  const btnEl = root.querySelector("#jvSetBtn");
  const on = typeof force === "boolean" ? force : panel.hidden;
  panel.hidden = !on;
  btnEl.setAttribute("aria-expanded", String(on));
  if (!on) return;
  const voices = V.listVoices();
  const current = V.pickVoice(prefs.voice.voiceName);
  panel.innerHTML = `
    <h4>Voice</h4>
    <label class="jv-row"><span>Speak replies</span><input type="checkbox" data-p="voice.speak" ${prefs.voice.speak ? "checked" : ""}></label>
    <label class="jv-row col"><span>Voice</span>
      <select data-p="voice.voiceName" ${voices.length ? "" : "disabled"}>
        ${voices.length ? voices.map(v => `<option value="${esc(v.name)}" ${current && v.name === current.name ? "selected" : ""}>${esc(v.name.replace(/^Microsoft /, ""))}</option>`).join("")
          : `<option>No English voices installed</option>`}
      </select></label>
    <label class="jv-row col"><span>Speed <b id="jvRateV">${prefs.voice.rate.toFixed(2)}×</b></span>
      <input type="range" min="0.8" max="1.4" step="0.05" value="${prefs.voice.rate}" data-p="voice.rate"></label>
    <label class="jv-row"><span>Sound cues</span><input type="checkbox" data-p="voice.chime" ${prefs.voice.chime ? "checked" : ""}></label>
    <h4>Conversation</h4>
    <label class="jv-row"><span>Hands-free (keep listening)</span><input type="checkbox" data-p="voice.conversation" ${prefs.voice.conversation ? "checked" : ""}></label>
    <label class="jv-row"><span>Let me interrupt</span><input type="checkbox" data-p="voice.barge" ${prefs.voice.barge ? "checked" : ""}></label>
    <p class="jv-note">Interrupting works best with headphones; on speakers the microphone can hear Jarvis, and phrases that sound like his own words are ignored.</p>
    <h4>Wake word</h4>
    <label class="jv-row"><span>Listen for “Jarvis”</span><input type="checkbox" data-p="voice.wake" ${prefs.voice.wake ? "checked" : ""} ${V.canListen ? "" : "disabled"}></label>
    <p class="jv-note">While this tab is open and visible the microphone stays on and the browser's speech service hears everything said nearby. Off by default for that reason.</p>
    <button type="button" class="jv-test" id="jvTestVoice">Test voice</button>`;
  panel.querySelectorAll("[data-p]").forEach(inp => inp.addEventListener("change", () => {
    const k = inp.dataset.p;
    const v = inp.type === "checkbox" ? inp.checked : inp.type === "range" ? Number(inp.value) : inp.value;
    setPref(k, v);
  }));
  panel.querySelector("[data-p='voice.rate']").addEventListener("input", e => {
    panel.querySelector("#jvRateV").textContent = Number(e.target.value).toFixed(2) + "×";
  });
  panel.querySelector("#jvTestVoice").addEventListener("click", () => {
    setState("speaking");
    V.speak(`${greeting()}. All systems are online.`, { voiceName: prefs.voice.voiceName, rate: prefs.voice.rate, onBoundary: () => orb.bump() })
      .then(() => setState("idle"));
  });
}

/* Exposed for the palette and for testing in the console. */
export const jarvis = { open, close, listen, handle, get mode() { return mode; } };
