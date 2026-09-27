// ============================================================
// MOTION — the dashboard's animation layer.
//
// Rules it keeps:
//   * Motion explains something: a page arriving, a number changing,
//     where you are in the nav, what you just pressed. Nothing loops
//     for decoration except the optional ambient light.
//   * Transform and opacity only, so it stays on the compositor.
//   * Auto-refresh repaints are quiet: nothing re-animates under a
//     reader (boot passes quiet=true).
//   * prefers-reduced-motion and the in-app "off" setting stop all of
//     it; "calm" keeps fades and drops movement and the ambient light.
// ============================================================
import { motionLevel } from "/app/q/js/prefs.js";

const full = () => motionLevel() === "full";
const on = () => motionLevel() !== "off";

/* ---------------- route progress bar ---------------- */
let bar = null, barTimer = 0;
export function routeStart() {
  if (!bar) {
    bar = document.createElement("div");
    bar.className = "route-bar";
    bar.setAttribute("aria-hidden", "true");
    document.body.appendChild(bar);
  }
  clearTimeout(barTimer);
  bar.className = "route-bar go";
}
export function routeEnd() {
  if (!bar) return;
  bar.className = "route-bar done";
  barTimer = setTimeout(() => { bar.className = "route-bar"; }, 420);
}

/* ---------------- after a page renders ---------------- */
export function pageEntered(work, { quiet = false } = {}) {
  if (quiet || !on()) return;
  stagger(work);
  if (full()) countUp(work);
  watchLate(work);
}

/* Cascade grid children and list rows the base CSS does not reach. */
function stagger(root) {
  const groups = root.querySelectorAll(
    ".grid, .command-metrics, .delivery-board, .command-bottom, .command-rail > div, .tbl tbody, .list, .rows, .cards, .kpis");
  groups.forEach(g => {
    [...g.children].slice(0, 16).forEach((c, i) => {
      c.style.setProperty("--i", i);
      c.classList.add("st");
    });
  });
}

/* Numbers roll up to their value. Only plain numbers animate — "—", dates,
   and anything else render exactly as written; the final frame is always
   the original text so a count-up can never display a wrong value. */
const NUM = /^(\D{0,2})(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(\D{0,12})$/;
export function countUp(root) {
  if (document.hidden) return;
  const els = root.querySelectorAll(".command-metric .value, .kpi .v, .stat .v, [data-count]");
  els.forEach(el => {
    if (el.dataset.counted || el.children.length) return;
    const text = el.textContent.trim();
    const m = text.match(NUM);
    if (!m) return;
    const target = Number((m[2] + (m[3] || "")).replace(/,/g, ""));
    if (!isFinite(target) || target === 0 || target > 1e9) return;
    el.dataset.counted = "1";
    const dec = m[3] ? m[3].length - 1 : 0, commas = m[2].includes(",");
    const fmt = v => m[1] + (commas ? v.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })
                                     : v.toFixed(dec)) + m[4];
    const dur = Math.min(1100, 480 + Math.log10(target + 1) * 220);
    // The true value is on screen until a frame actually runs: a hidden tab
    // gets no animation frames, and a zero left there would be a wrong number.
    // The timer is the backstop that always lands the real text.
    let t0 = 0, done = false;
    const finish = () => { if (!done) { done = true; el.textContent = text; } };
    const tick = now => {
      if (done || !el.isConnected) return;
      if (!t0) t0 = now;
      const k = Math.min(1, (now - t0) / dur);
      if (k >= 1) return finish();
      el.textContent = fmt(target * (1 - Math.pow(1 - k, 3)));
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
    setTimeout(finish, dur + 400);
  });
}

/* Some pages fill slots after their first paint (charts, audit lists).
   Watch briefly so late content counts up too. */
function watchLate(work) {
  if (!full() || !window.MutationObserver) return;
  const mo = new MutationObserver(() => countUp(work));
  mo.observe(work, { childList: true, subtree: true });
  setTimeout(() => mo.disconnect(), 2500);
}

/* ---------------- nav indicator ---------------- */
let ind = null;
export function moveNavIndicator(nav) {
  if (!nav) return;
  if (!ind) {
    ind = document.createElement("span");
    ind.className = "nav-ind";
    ind.setAttribute("aria-hidden", "true");
    nav.prepend(ind);
  } else if (!nav.contains(ind)) nav.prepend(ind);
  const a = nav.querySelector("a.on");
  if (!a) { ind.style.opacity = "0"; return; }
  ind.style.opacity = "1";
  ind.style.transform = `translateY(${a.offsetTop}px)`;
  ind.style.height = a.offsetHeight + "px";
}

/* ---------------- pointer effects ---------------- */
export function installPointerEffects() {
  // spotlight: a soft light follows the cursor across the panel under it
  let lit = null, raf = 0, px = 0, py = 0;
  document.addEventListener("pointermove", e => {
    if (!full() || e.pointerType !== "mouse") return;
    const p = e.target.closest && e.target.closest(".panel, .command-rail, .decision-card, .jv-card");
    if (p !== lit) { lit && lit.classList.remove("lit"); lit = p; lit && lit.classList.add("lit"); }
    if (!lit) return;
    px = e.clientX; py = e.clientY;
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      if (!lit) return;
      const r = lit.getBoundingClientRect();
      lit.style.setProperty("--mx", `${px - r.left}px`);
      lit.style.setProperty("--my", `${py - r.top}px`);
    });
  }, { passive: true });
  document.addEventListener("pointerleave", () => { lit && lit.classList.remove("lit"); lit = null; });

  // ripple: a pressed button shows where it was pressed
  document.addEventListener("pointerdown", e => {
    if (!on()) return;
    const b = e.target.closest && e.target.closest(".btn, .jv-chip, .nav a, .pal-i");
    if (!b || b.disabled) return;
    const r = b.getBoundingClientRect();
    const s = document.createElement("span");
    s.className = "ripple";
    const d = Math.max(r.width, r.height) * 2.2;
    s.style.cssText = `width:${d}px;height:${d}px;left:${e.clientX - r.left - d / 2}px;top:${e.clientY - r.top - d / 2}px`;
    b.appendChild(s);
    setTimeout(() => s.remove(), 650);
  }, { passive: true });
}

/* ---------------- small flourishes ---------------- */
/** Pop an element (a badge whose number went up). */
export function pop(el) {
  if (!el || !on()) return;
  el.classList.remove("pop");
  void el.offsetWidth;          // restart the animation
  el.classList.add("pop");
}
