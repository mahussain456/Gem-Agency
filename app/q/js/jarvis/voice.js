// ============================================================
// VOICE — the browser's speech recognition and synthesis, wrapped so
// every failure comes back as a sentence a person can act on.
//
// Privacy, stated in the UI too: recognition is the browser's own
// service (Microsoft's in Edge, Google's in Chrome), so audio of a
// command leaves the machine while the mic is open. Gem Agency does
// not record or store it. The mic opens only on a click, the hotkey,
// or — if the operator turns it on — while listening for "Jarvis".
// ============================================================

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
export const canListen = !!SR;
export const canSpeak = "speechSynthesis" in window;

const REASONS = {
  "not-allowed": "Microphone permission was denied. Allow it from the address bar, or type instead.",
  "service-not-allowed": "This browser's speech service is turned off. Type the command instead.",
  "audio-capture": "No microphone was found. Plug one in, or type instead.",
  "network": "The browser's speech service could not be reached. Check the connection, or type instead.",
  "no-speech": "I didn't hear anything.",
  "aborted": "",
  "language-not-supported": "Speech recognition does not support this language setting.",
};
export const reason = code => REASONS[code] ?? `Speech recognition failed (${code}).`;

/* ---------------- one command ---------------- */
/** Listen for a single utterance.
    Calls onInterim(text) while speaking; resolves with the final text,
    "" on silence; rejects with Error(reason) on failure. `.stop()` ends early. */
export function listenOnce({ onInterim, lang } = {}) {
  if (!SR) {
    const p = Promise.reject(new Error("This browser has no speech recognition. Edge or Chrome do; you can type instead."));
    p.stop = () => {};
    return p;
  }
  const rec = new SR();
  rec.lang = lang || navigator.language || "en-US";
  rec.interimResults = true;
  rec.continuous = false;
  rec.maxAlternatives = 1;
  let final = "", settled = false;
  const p = new Promise((resolve, reject) => {
    rec.onresult = ev => {
      let interim = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const r = ev.results[i];
        if (r.isFinal) final += r[0].transcript;
        else interim += r[0].transcript;
      }
      onInterim && onInterim((final + " " + interim).trim());
    };
    rec.onerror = ev => {
      if (settled) return;
      if (ev.error === "no-speech" || ev.error === "aborted") { settled = true; resolve(final.trim()); return; }
      settled = true;
      reject(Object.assign(new Error(reason(ev.error)), { code: ev.error }));
    };
    rec.onend = () => { if (!settled) { settled = true; resolve(final.trim()); } };
  });
  try { rec.start(); } catch (e) { rec.onerror({ error: "aborted" }); }
  p.stop = () => { try { rec.stop(); } catch { /* already stopped */ } };
  p.abort = () => { try { rec.abort(); } catch { /* already stopped */ } };
  return p;
}

/* ---------------- wake word ---------------- */
/** Continuous listening for "Jarvis". Returns a controller with stop().
    Recognition sessions end on their own every so often; this restarts
    them with a backoff, and gives up (calling onFatal) on permission
    errors rather than retrying into a wall. */
export function wakeListener({ onWake, onFatal, lang }) {
  if (!SR) { onFatal && onFatal(new Error("No speech recognition in this browser.")); return { stop() {} }; }
  let rec = null, stopped = false, backoff = 400, timer = null;
  const WAKE = /\b(?:hey |ok |okay )?(?:jarvis|jervis|travis|javis|jarves)\b[\s,.]*(.*)$/i;

  const start = () => {
    if (stopped || document.hidden) return;
    rec = new SR();
    rec.lang = lang || navigator.language || "en-US";
    rec.continuous = true;
    rec.interimResults = true;
    rec.onresult = ev => {
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const r = ev.results[i];
        const m = r[0].transcript.match(WAKE);
        // act on a final result, or on an interim one that is just the name
        if (m && (r.isFinal || !m[1].trim())) {
          backoff = 400;
          const rest = r.isFinal ? m[1].trim() : "";
          try { rec.abort(); } catch { /* ignore */ }
          rec = null;
          onWake(rest);
          return;
        }
      }
    };
    rec.onerror = ev => {
      if (["not-allowed", "service-not-allowed", "audio-capture"].includes(ev.error)) {
        stopped = true;
        onFatal && onFatal(Object.assign(new Error(reason(ev.error)), { code: ev.error }));
      } else if (ev.error === "network") backoff = Math.min(backoff * 2, 30000);
    };
    rec.onend = () => { rec = null; if (!stopped) timer = setTimeout(start, backoff); };
    try { rec.start(); } catch { timer = setTimeout(start, backoff); }
  };
  const onVis = () => { if (!document.hidden && !rec && !stopped) start(); };
  document.addEventListener("visibilitychange", onVis);
  start();
  return {
    stop() {
      stopped = true; clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVis);
      try { rec && rec.abort(); } catch { /* ignore */ }
      rec = null;
    },
  };
}

/* ---------------- mic level for the orb ---------------- */
/** Opens the mic only to meter it. Returns { level(): 0..1, bins(n), close() }.
    Resolves to null (no meter, the orb animates on its own) if the mic is
    unavailable — metering is decoration and must never block listening. */
export async function micMeter() {
  if (!navigator.mediaDevices?.getUserMedia) return null;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const src = ctx.createMediaStreamSource(stream);
    const an = ctx.createAnalyser();
    an.fftSize = 256;
    an.smoothingTimeConstant = 0.72;
    src.connect(an);
    const freq = new Uint8Array(an.frequencyBinCount);
    const time = new Uint8Array(an.fftSize);
    return {
      level() {
        an.getByteTimeDomainData(time);
        let sum = 0;
        for (let i = 0; i < time.length; i++) { const v = (time[i] - 128) / 128; sum += v * v; }
        return Math.min(1, Math.sqrt(sum / time.length) * 3.2);
      },
      bins(n) {
        an.getByteFrequencyData(freq);
        const out = new Array(n);
        const usable = Math.floor(freq.length * 0.7);   // the top end is mostly empty for speech
        for (let i = 0; i < n; i++) out[i] = freq[Math.floor((i / n) * usable)] / 255;
        return out;
      },
      close() { stream.getTracks().forEach(t => t.stop()); ctx.close().catch(() => {}); },
    };
  } catch { return null; }
}

/* ---------------- speaking ---------------- */
let voices = [];
function loadVoices() { voices = canSpeak ? speechSynthesis.getVoices() : []; return voices; }
if (canSpeak) { loadVoices(); speechSynthesis.addEventListener?.("voiceschanged", loadVoices); }

export function listVoices() {
  return (voices.length ? voices : loadVoices())
    .filter(v => /^en[-_]/i.test(v.lang))
    .sort((a, b) => score(b) - score(a));
}

/* A composed British male voice reads most like the name. Edge ships
   "Microsoft Ryan Online (Natural)", which is ideal; everything else is
   ranked below it and any English voice beats silence. */
function score(v) {
  let s = 0;
  if (/en[-_]GB/i.test(v.lang)) s += 40;
  if (/natural|neural|online/i.test(v.name)) s += 25;
  if (/\b(ryan|thomas|george|arthur|oliver|daniel|brian|guy|alfie|elliot|noah)\b/i.test(v.name)) s += 30;
  if (/\b(sonia|libby|maisie|hazel|susan|kate|serena|female)\b/i.test(v.name)) s -= 10;
  if (v.localService) s += 2;
  return s;
}

export function pickVoice(name) {
  const list = listVoices();
  return (name && list.find(v => v.name === name)) || list[0] || null;
}

/* Written text reads badly aloud: symbols, dashes, bare URLs. */
export function speakable(text) {
  return String(text || "")
    .replace(/https?:\/\/\S+/g, "the link")
    .replace(/[—–]/g, ", ")
    .replace(/&/g, " and ")
    .replace(/(\d)\s*\/\s*(\d)/g, "$1 of $2")
    .replace(/[*_#`>|]/g, "")
    .replace(/\$(\d[\d,]*)/g, "$1 dollars")
    .replace(/\s+/g, " ").trim();
}

let current = null;
/** Speak text; onBoundary fires per word (drives the orb). Resolves when done. */
export function speak(text, { voiceName, rate = 1.03, onBoundary } = {}) {
  return new Promise(resolve => {
    if (!canSpeak || !text) return resolve();
    stopSpeaking();
    const u = new SpeechSynthesisUtterance(speakable(text));
    const v = pickVoice(voiceName);
    if (v) { u.voice = v; u.lang = v.lang; }
    u.rate = rate;
    u.pitch = 0.92;
    u.onboundary = () => onBoundary && onBoundary();
    u.onend = u.onerror = () => { if (current === u) current = null; resolve(); };
    current = u;
    speechSynthesis.speak(u);
    // Chrome sometimes never fires onend for long utterances; don't hang the UI on it
    setTimeout(() => { if (current === u && !speechSynthesis.speaking) { current = null; resolve(); } }, 400 + text.length * 120);
  });
}
export function stopSpeaking() { current = null; if (canSpeak) speechSynthesis.cancel(); }

/* ---------------- speaking a reply while it streams ----------------
   Text arrives token by token. Each finished sentence is queued as its own
   utterance, so Jarvis starts talking on the first sentence (the way a
   spoken ChatGPT does) and long replies never hit Chrome's habit of going
   silent partway through one long utterance. */
export function createSpeaker({ voiceName, rate = 1.03, onBoundary, onStart, enabled = true } = {}) {
  let buf = "", pending = 0, ended = false, stopped = false, started = false, spoken = "";
  let resolveDone;
  const done = new Promise(r => { resolveDone = r; });
  const settle = () => { if (ended && pending <= 0) resolveDone(); };

  function say(raw) {
    const text = speakable(raw);
    if (!text || stopped) return;
    spoken += " " + text;
    if (!enabled || !canSpeak) return;
    const u = new SpeechSynthesisUtterance(text);
    const v = pickVoice(voiceName);
    if (v) { u.voice = v; u.lang = v.lang; }
    u.rate = rate;
    u.pitch = 0.92;
    let finished = false;
    const finish = () => { if (finished) return; finished = true; pending--; settle(); };
    u.onstart = () => { if (!started) { started = true; onStart && onStart(); } };
    u.onboundary = () => onBoundary && onBoundary();
    u.onend = u.onerror = finish;
    // some engines never fire onend; never let one sentence hang the conversation
    setTimeout(finish, 2500 + text.length * 110);
    pending++;
    speechSynthesis.speak(u);
  }

  return {
    push(text) {
      if (stopped) return;
      buf += text;
      // speak every complete sentence; keep the unfinished tail. Sticky and
      // lazy: each sentence starts exactly where the last ended and runs to
      // the first ". " / "! " / "? " or newline -- "3.5" is not an ending.
      const re = /[\s\S]*?(?:[.!?]+["')\]]*(?=\s)|\n)/y;
      let m, last = 0;
      re.lastIndex = 0;
      while ((m = re.exec(buf))) {
        if (m[0].trim().length >= 2) say(m[0]);
        last = re.lastIndex;
        if (!m[0].length) break;
      }
      buf = buf.slice(last);
    },
    end() { if (buf.trim()) say(buf); buf = ""; ended = true; settle(); },
    stop() {
      stopped = true; ended = true; pending = 0;
      if (canSpeak) speechSynthesis.cancel();
      resolveDone();
    },
    get spoken() { return spoken; },
    get active() { return !stopped && (!ended || pending > 0); },
    done,
  };
}

/** Is `heard` just the microphone picking up what Jarvis is saying? */
export function isEcho(heard, spoken) {
  const words = s => String(s || "").toLowerCase().replace(/[^a-z0-9\s']/g, " ").split(/\s+/).filter(w => w.length > 1);
  const h = words(heard), said = new Set(words(spoken));
  if (!h.length) return true;
  const overlap = h.filter(w => said.has(w)).length / h.length;
  return overlap >= 0.6;
}
export const isSpeaking = () => canSpeak && speechSynthesis.speaking;

/* ---------------- chime ---------------- */
let actx = null;
/** Two soft sine notes: rising when the mic opens, falling when it closes. */
export function chime(up = true) {
  try {
    actx = actx || new (window.AudioContext || window.webkitAudioContext)();
    const t = actx.currentTime;
    [[up ? 660 : 880, 0], [up ? 990 : 590, 0.075]].forEach(([f, d]) => {
      const o = actx.createOscillator(), g = actx.createGain();
      o.type = "sine"; o.frequency.value = f;
      g.gain.setValueAtTime(0, t + d);
      g.gain.linearRampToValueAtTime(0.06, t + d + 0.012);
      g.gain.exponentialRampToValueAtTime(0.0001, t + d + 0.18);
      o.connect(g).connect(actx.destination);
      o.start(t + d); o.stop(t + d + 0.2);
    });
  } catch { /* audio is a nicety */ }
}
