// ============================================================
// FLOW — talk instead of type, like Wispr Flow.
//
//   Hold Ctrl+Space, speak the way you think, let go.
//   Tap Ctrl+Space once for hands-free; tap again to finish. Esc cancels.
//
// Where the words go depends on what has focus when you start:
//   a text box            -> dictation: cleaned up (fillers out, "no, I mean"
//                            corrections applied, punctuation) and typed in
//                            at the cursor, undoable with Ctrl+Z
//   selected text in one  -> an edit: "make this shorter", "more formal"
//   nothing               -> a request to Jarvis, understood in plain language
// Ears: Whisper on this PC when it is installed (about 0.6 s, punctuated,
// free, audio never leaves the machine), else the browser's recognition.
// Plain speech is then only stripped of "um"s, with no model call; a
// correction, a dictated list or an edit gets one model call through the
// brain chain. If no model answers, the words go in as heard and the pill
// says so.
// ============================================================
import { apiGet, apiPost, esc, toast } from "../core.js";
import { prefs } from "../prefs.js";
import * as V from "./voice.js";

const HOLD_MS = 280;          // shorter than this is a tap: hands-free
const CORRECTION = /\b(?:no wait|no no|i mean|actually|scratch that|delete that|sorry|or rather|um+|uh+|erm)\b/i;

let deps = {};                // { handle(text, {voice}), micBusy(on) }
let pill = null, meterTimer = 0, meter = null;
let cap = null;               // the capture in progress
let ears = null;              // Whisper on this PC: { installed, ready }

export function initFlow(injected) {
  deps = injected || {};
  document.addEventListener("keydown", onDown, true);
  document.addEventListener("keyup", onUp, true);
  window.addEventListener("blur", () => { if (cap && !cap.handsFree) finish(); });
  apiGet("/api/agency/voice/whisper").then(s => { ears = s; }).catch(() => { ears = null; });   // also warms the model
}

const isFlowKey = e => e.code === "Space" && (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey;

function onDown(e) {
  if (e.key === "Escape" && cap) { e.preventDefault(); e.stopPropagation(); cancel(); return; }
  if (!isFlowKey(e)) return;
  e.preventDefault(); e.stopPropagation();
  if (e.repeat) return;
  if (cap) { if (cap.handsFree) finish(); return; }
  start();
}

function onUp(e) {
  if (!cap || cap.handsFree || cap.ending) return;
  if (e.code !== "Space" && e.key !== "Control" && e.key !== "Meta") return;
  if (performance.now() - cap.at < HOLD_MS) {           // a tap: keep listening until the next tap
    cap.handsFree = true;
    paint("listening");
    return;
  }
  finish();
}

/* ---------------- what has focus ---------------- */
function editable(el) {
  if (!el || el.disabled || el.readOnly) return null;
  if (el.isContentEditable) return el;
  if (el.tagName === "TEXTAREA") return el;
  if (el.tagName === "INPUT" && /^(?:text|search|email|url|tel|)$/i.test(el.type || "")) return el;
  return null;
}

function fieldName(el) {
  const lab = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
  return (el.getAttribute("aria-label") || (lab && lab.textContent) || el.placeholder || el.name || "").trim().slice(0, 100);
}

function snapshot() {
  const el = editable(document.activeElement);
  if (!el) return { el: null };
  if (el.isContentEditable) {
    const s = getSelection();
    const range = s.rangeCount ? s.getRangeAt(0).cloneRange() : null;
    return { el, range, selected: s.toString() };
  }
  const a = el.selectionStart ?? el.value.length, b = el.selectionEnd ?? a;
  return { el, a, b, selected: el.value.slice(a, b) };
}

/* ---------------- capture ---------------- */
function start() {
  if (!V.canListen) { toast("This browser has no speech recognition. Open the dashboard in Edge or Chrome to talk.", true); return; }
  deps.micBusy && deps.micBusy(true);
  const target = snapshot();
  const mode = !target.el ? "command" : target.selected.trim() ? "edit" : "dictate";
  cap = { at: performance.now(), handsFree: false, ending: false, target, mode, heard: "" };
  try {
    cap.ctl = V.listenHold({
      onInterim: t => { if (cap) { cap.heard = t; paintHeard(t); } },
      onError: err => { const c = cap; cancel(true); show("error", err.message); if (c) deps.micBusy && deps.micBusy(false); },
    });
  } catch (err) { cap = null; deps.micBusy && deps.micBusy(false); show("error", err.message); return; }
  if (prefs.voice.chime) V.chime(true);
  paint("listening");
  const c = cap;
  if (ears && ears.installed) {
    // record for Whisper; the recorder doubles as the level meter
    V.recordPCM().then(r => {
      if (!r) return;
      if (cap === c && !c.ending) { c.rec = r; meter = r; animate(); } else r.close();
    });
  } else {
    V.micMeter().then(m => { if (cap === c && !c.ending) { meter = m; animate(); } else if (m) m.close(); });
  }
}

function stopMeter() {
  cancelAnimationFrame(meterTimer);
  if (meter) { meter.close(); meter = null; }      // the recorder's close() keeps what it recorded
}

function cancel(silent = false) {
  if (!cap) return;
  cap.ctl && cap.ctl.abort();
  cap = null;
  stopMeter();
  deps.micBusy && deps.micBusy(false);
  if (!silent) hide();
}

async function finish() {
  const c = cap;
  if (!c || c.ending) return;
  c.ending = true;
  const pcm = c.rec ? c.rec.stop() : null;          // before the meter closes the mic
  stopMeter();
  if (prefs.voice.chime) V.chime(false);
  paint("thinking");
  const heard = ((await c.ctl.stop()) || c.heard || "").trim();
  cap = null;
  deps.micBusy && deps.micBusy(false);
  const audio = pcm && pcm.length > 16000 * 0.3 ? pcm : null;
  if (!heard && !audio) { show("error", "I didn't hear anything. Hold Ctrl+Space while you talk."); return; }
  try {
    if (audio) {
      let res = null;
      try {
        res = await apiPost("/api/agency/voice/dictate", {
          mode: c.mode, audio: V.pcmBase64(audio), heard,
          selection: c.mode === "edit" ? c.target.selected : "", field: c.target.el ? fieldName(c.target.el) : "",
        });
      } catch (err) {
        if (!heard) throw err;                        // fall through to the browser's words
      }
      if (res) {
        if (c.mode === "command") { hide(); deps.handle && deps.handle(res.text, { voice: true }); }
        else done(c, res);
        return;
      }
    }
    if (c.mode === "command") await command(heard);
    else await write(c, heard);
  } catch (err) {
    show("error", String(err.message || err));
  }
}

/* A spoken request for Jarvis. Clear speech goes straight to him; speech
   full of "no wait, I mean" is cleaned first, so the fast local commands
   are not fooled by the half you took back. */
async function command(heard) {
  let text = heard;
  if (CORRECTION.test(heard)) {
    try { text = (await apiPost("/api/agency/voice/flow", { mode: "command", text: heard })).text || heard; }
    catch { /* Jarvis's own model copes with the raw words */ }
  }
  hide();
  deps.handle && deps.handle(text, { voice: true });
}

async function write(c, heard) {
  const t = c.target;
  const res = await apiPost("/api/agency/voice/flow", {
    mode: c.mode, text: heard, selection: c.mode === "edit" ? t.selected : "", field: fieldName(t.el),
  });
  done(c, res);
}

function done(c, res) {
  const t = c.target;
  if (!t.el.isConnected) throw new Error("The text box closed before the words were ready. Here they are: " + res.text);
  insert(t, res.text, c.mode === "edit");
  const verb = c.mode === "edit" ? "Rewritten" : "Typed";
  const took = res.seconds ? ` in ${Number(res.seconds).toFixed(1)} s` : "";
  const by = res.provider === "local" ? " · Whisper on this PC"
    : (res.ears === "whisper" ? " · Whisper + " : " · cleaned up by ") + provName(res.provider || "");
  show(res.polished ? "done" : "warn", res.polished ? verb + took + by : res.note || "Typed as heard");
}

const provName = p => ({ claude: "Claude", chatgpt: "ChatGPT", ollama: "the local model" }[p] || p);

/* Type into the box the way a keyboard would, so the page sees an input
   event and Ctrl+Z takes it back. Dictation leaves a space where one is
   needed; an edit replaces the selection exactly. */
function insert(t, text, replace) {
  const el = t.el;
  el.focus();
  if (el.isContentEditable) {
    const s = getSelection();
    if (t.range) { s.removeAllRanges(); s.addRange(t.range); }
  } else {
    el.setSelectionRange(t.a, t.b);
  }
  let out = text;
  if (!replace) {
    const before = el.isContentEditable ? "" : el.value.slice(0, t.a);
    if (before && !/\s$/.test(before)) out = " " + out;
  }
  if (!document.execCommand("insertText", false, out) && !el.isContentEditable) {
    el.setRangeText(out, t.a, t.b, "end");
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }
}

/* ---------------- the pill ---------------- */
function build() {
  pill = document.createElement("div");
  pill.className = "flow";
  pill.setAttribute("role", "status");
  pill.setAttribute("aria-live", "polite");
  pill.innerHTML = `<span class="flow-wave" aria-hidden="true">${"<i></i>".repeat(9)}</span>
    <span class="flow-body"><b class="flow-t"></b><span class="flow-h"></span></span>
    <kbd class="flow-k"></kbd>`;
  document.body.appendChild(pill);
}

const LABEL = { dictate: "Dictating", edit: "Editing the selection", command: "Talking to Jarvis" };

function paint(state) {
  if (!pill) build();
  clearTimeout(pill._hide);
  pill.dataset.state = state;
  pill.hidden = false;
  requestAnimationFrame(() => pill.classList.add("on"));
  const c = cap;
  pill.querySelector(".flow-t").textContent = state === "thinking"
    ? (c && c.mode === "command" ? "Understanding…" : "Cleaning up…")
    : (c ? LABEL[c.mode] : "Listening");
  pill.querySelector(".flow-k").textContent = state === "listening"
    ? (c && c.handsFree ? "Ctrl Space to finish" : "Release to finish") : "";
  if (state === "listening" && c && !c.heard) paintHeard("");
}

function paintHeard(t) {
  if (!pill) return;
  const h = pill.querySelector(".flow-h");
  h.textContent = t ? (t.length > 90 ? "…" + t.slice(-90).replace(/^\S*\s/, "") : t) :(cap && cap.handsFree ? "Hands-free. Talk, then tap Ctrl Space." : "Go ahead, I'm listening.");
}

function show(state, text) {
  if (!pill) build();
  pill.dataset.state = state;
  pill.hidden = false;
  pill.classList.add("on");
  pill.querySelector(".flow-t").textContent = state === "error" ? "Didn't work" : state === "warn" ? "Typed as heard" : "Done";
  pill.querySelector(".flow-h").innerHTML = esc(text);
  pill.querySelector(".flow-k").textContent = "";
  clearTimeout(pill._hide);
  pill._hide = setTimeout(hide, state === "error" || state === "warn" ? 6000 : 2200);
}

function hide() {
  if (!pill) return;
  pill.classList.remove("on");
  clearTimeout(pill._hide);
  pill._hide = setTimeout(() => { if (pill && !pill.classList.contains("on")) pill.hidden = true; }, 250);
}

function animate() {
  const bars = pill ? [...pill.querySelectorAll(".flow-wave i")] : [];
  const tick = () => {
    if (!meter || !pill) return;
    const b = meter.bins(bars.length), lv = meter.level();
    bars.forEach((el, i) => { el.style.transform = `scaleY(${(0.18 + Math.min(1, b[i] * 1.3 + lv * 0.4) * 0.82).toFixed(3)})`; });
    meterTimer = requestAnimationFrame(tick);
  };
  tick();
}

/* for tests */
export const _internals = { editable, CORRECTION };
