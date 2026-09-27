// ============================================================
// ORB — Jarvis's face. A canvas "arc reactor" whose waveform ring
// moves with the real microphone while listening, pulses on each
// spoken word while talking, and sweeps a comet while thinking.
//
// State is legible without motion: colour and ring shape change per
// state, so with animation switched off one still frame still says
// listening / thinking / error.
// ============================================================
import { motionLevel } from "../prefs.js";

const BARS = 120;
const TAU = Math.PI * 2;

function rgb(hex) {
  const m = String(hex).trim().match(/^#?([0-9a-f]{6})$/i);
  if (!m) return [100, 212, 189];
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${Math.max(0, Math.min(1, a))})`;

export function createOrb(canvas, { detail = true } = {}) {
  const ctx = canvas.getContext("2d");
  let state = "idle", source = null, raf = 0, running = false;
  let W = 0, dpr = 1, t0 = performance.now();
  let ac = [100, 212, 189], crit = [255, 158, 171], warm = [243, 197, 110];
  let energy = 0;                         // speaking: bumped per word, decays
  let level = 0;                          // smoothed loudness
  const amp = new Float32Array(BARS);     // smoothed bar lengths

  function colours() {
    const cs = getComputedStyle(document.documentElement);
    ac = rgb(cs.getPropertyValue("--ac") || "#64d4bd");
    crit = rgb(cs.getPropertyValue("--crit") || "#ff9eab");
    warm = rgb(cs.getPropertyValue("--warn") || "#f3c56e");
  }

  function resize() {
    const r = canvas.getBoundingClientRect();
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = Math.max(40, Math.round(r.width));
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(W * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  /* target bar lengths for this frame, 0..1 */
  function targets(t) {
    const out = new Float32Array(BARS);
    if (state === "listening" && source) {
      const half = BARS / 2, bins = source.bins(half);
      for (let i = 0; i < half; i++) {
        const v = Math.pow(bins[i] || 0, 1.4);
        out[i] = v; out[BARS - 1 - i] = v;      // mirrored: reads as one voice, not a spectrum
      }
    } else if (state === "listening") {
      for (let i = 0; i < BARS; i++)
        out[i] = 0.12 + 0.1 * Math.sin(t * 5 + i * 0.35) * Math.sin(t * 1.7 + i * 0.11);
    } else if (state === "speaking") {
      for (let i = 0; i < BARS; i++) {
        const n = Math.sin(i * 0.47 + t * 9) * Math.sin(i * 0.13 - t * 5.3) * Math.sin(i * 0.071 + t * 2.1);
        out[i] = Math.abs(n) * (0.25 + energy * 0.9);
      }
    } else if (state === "thinking") {
      const head = (t * 1.6) % 1;
      for (let i = 0; i < BARS; i++) {
        let d = ((i / BARS) - head + 1) % 1;       // distance behind the comet head
        out[i] = d < 0.35 ? Math.pow(1 - d / 0.35, 2.2) * 0.55 : 0.02;
      }
    } else {
      for (let i = 0; i < BARS; i++)
        out[i] = 0.035 + 0.03 * (1 + Math.sin(t * 1.2 + i * 0.26)) * (0.6 + 0.4 * Math.sin(t * 0.7));
    }
    return out;
  }

  function frame(now) {
    const t = (now - t0) / 1000;
    const still = motionLevel() === "off";
    const c = W / 2, R = W * 0.25;
    const col = state === "error" ? crit : state === "thinking" ? mix(ac, warm, 0.35) : ac;

    // inputs
    const loud = state === "listening" && source ? source.level() : 0;
    level += (loud - level) * 0.25;
    energy *= 0.93;
    const tg = still ? new Float32Array(BARS).fill(state === "idle" ? 0.05 : 0.18) : targets(t);
    for (let i = 0; i < BARS; i++) amp[i] += (tg[i] - amp[i]) * (state === "listening" ? 0.35 : 0.18);

    ctx.clearRect(0, 0, W, W);

    // halo
    const glow = (state === "idle" ? 0.16 : 0.24) + level * 0.35 + energy * 0.15;
    const halo = ctx.createRadialGradient(c, c, R * 0.3, c, c, R * 2);
    halo.addColorStop(0, rgba(col, glow));
    halo.addColorStop(1, rgba(col, 0));
    ctx.fillStyle = halo;
    ctx.fillRect(0, 0, W, W);

    const spin = still ? 0 : t * (state === "thinking" ? 1.4 : state === "listening" ? 0.5 : 0.18);

    if (detail) {
      // outer instrument ring with ticks
      ctx.save();
      ctx.translate(c, c);
      ctx.rotate(-spin * 0.35);
      for (let i = 0; i < 72; i++) {
        const a = (i / 72) * TAU, long = i % 6 === 0;
        const r1 = R * 1.86, r2 = R * (long ? 1.95 : 1.9);
        ctx.strokeStyle = rgba(col, long ? 0.4 : 0.16);
        ctx.lineWidth = long ? 1.4 : 1;
        ctx.beginPath();
        ctx.moveTo(Math.cos(a) * r1, Math.sin(a) * r1);
        ctx.lineTo(Math.cos(a) * r2, Math.sin(a) * r2);
        ctx.stroke();
      }
      ctx.restore();

      // three sweeping arcs
      ctx.save();
      ctx.translate(c, c);
      ctx.rotate(spin);
      ctx.lineCap = "round";
      for (let k = 0; k < 3; k++) {
        ctx.strokeStyle = rgba(col, 0.55);
        ctx.lineWidth = 2;
        ctx.beginPath();
        const s = (k / 3) * TAU;
        ctx.arc(0, 0, R * 1.68, s, s + 0.7);
        ctx.stroke();
      }
      ctx.restore();
    }

    // waveform ring
    ctx.save();
    ctx.translate(c, c);
    ctx.rotate(-Math.PI / 2 + spin * 0.15);
    ctx.lineCap = "round";
    const base = R * 1.1;
    for (let i = 0; i < BARS; i++) {
      const a = (i / BARS) * TAU;
      const len = R * (0.05 + amp[i] * 0.6);
      ctx.strokeStyle = rgba(col, 0.35 + amp[i] * 0.9);
      ctx.lineWidth = Math.max(1.2, W / 190);
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * base, Math.sin(a) * base);
      ctx.lineTo(Math.cos(a) * (base + len), Math.sin(a) * (base + len));
      ctx.stroke();
    }
    ctx.restore();

    // reactor segments
    ctx.save();
    ctx.translate(c, c);
    ctx.rotate(-spin * 0.8);
    const segs = 10, gap = 0.12;
    for (let i = 0; i < segs; i++) {
      const a0 = (i / segs) * TAU + gap / 2, a1 = ((i + 1) / segs) * TAU - gap / 2;
      ctx.beginPath();
      ctx.arc(0, 0, R * 0.92, a0, a1);
      ctx.arc(0, 0, R * 0.76, a1, a0, true);
      ctx.closePath();
      ctx.fillStyle = rgba(col, 0.2 + level * 0.5 + energy * 0.25);
      ctx.fill();
    }
    ctx.restore();

    // core
    const cr = R * (0.6 + level * 0.1 + energy * 0.06 + (still ? 0 : 0.015 * Math.sin(t * 2.2)));
    const core = ctx.createRadialGradient(c, c, 0, c, c, cr);
    core.addColorStop(0, "rgba(255,255,255,0.95)");
    core.addColorStop(0.28, rgba(mix(col, [255, 255, 255], 0.55), 0.9));
    core.addColorStop(0.7, rgba(col, 0.55));
    core.addColorStop(1, rgba(col, 0.05));
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(c, c, cr, 0, TAU);
    ctx.fill();

    if (running && !still) raf = requestAnimationFrame(frame);
    else raf = 0;
  }

  function kick() {
    if (!running) return;
    if (!raf) raf = requestAnimationFrame(frame);
  }

  const api = {
    setState(s) { state = s; kick(); if (motionLevel() === "off") requestAnimationFrame(frame); },
    setSource(s) { source = s; },
    bump() { energy = Math.min(1, energy + 0.55); },
    refreshColours() { colours(); kick(); if (motionLevel() === "off") requestAnimationFrame(frame); },
    start() { if (running) return; running = true; colours(); resize(); t0 = performance.now(); raf = requestAnimationFrame(frame); },
    stop() { running = false; if (raf) cancelAnimationFrame(raf); raf = 0; },
    resize() { resize(); kick(); },
    get state() { return state; },
  };
  return api;
}

function mix(a, b, k) { return [0, 1, 2].map(i => Math.round(a[i] + (b[i] - a[i]) * k)); }
