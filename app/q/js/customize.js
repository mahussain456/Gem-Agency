// ============================================================
// CUSTOMISE + SHORTCUTS — make the dashboard fit the operator.
//   Appearance drawer: accent, density, motion, ambient light, voice.
//   "?" opens the shortcut sheet; "g" then a letter jumps to a page.
// ============================================================
import { drawer, closeDrawer, esc, icon } from "/app/q/js/core.js";
import { prefs, setPref, ACCENTS, motionLevel } from "/app/q/js/prefs.js";
import { PAGES } from "/app/q/js/nav.js";

const seg = (name, value, opts) => `<div class="seg-ctl" role="radiogroup" aria-label="${esc(name)}">${
  opts.map(([v, t]) => `<button type="button" role="radio" aria-checked="${v === value}" data-v="${esc(v)}" class="${v === value ? "on" : ""}">${esc(t)}</button>`).join("")}</div>`;

export function openPrefs() {
  const osReduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const dr = drawer({
    title: "Customise",
    sub: "Saved in this browser only.",
    body: `
      <section class="cz">
        <h4>Accent</h4>
        <div class="swatches" role="radiogroup" aria-label="Accent colour">
          ${ACCENTS.map(a => `<button type="button" class="sw ${prefs.accent === a.id ? "on" : ""}" role="radio"
              aria-checked="${prefs.accent === a.id}" data-accent="${a.id}" style="--c:${a.c}">
              <i></i><span>${esc(a.t)}</span></button>`).join("")}
        </div>
      </section>
      <section class="cz">
        <h4>Density</h4>
        <div data-k="density">${seg("Density", prefs.density, [["comfortable", "Comfortable"], ["compact", "Compact"]])}</div>
      </section>
      <section class="cz">
        <h4>Motion</h4>
        <div data-k="motion">${seg("Motion", prefs.motion, [["full", "Full"], ["calm", "Calm"], ["off", "Off"]])}</div>
        <p class="cz-note">${osReduced ? "Your operating system asks for reduced motion, so animation stays off whatever you pick here."
          : "Full: page entrances, counting numbers, light that follows the cursor. Calm: fades only. Off: nothing moves."}</p>
        <label class="cz-row"><span>Ambient background light</span>
          <input type="checkbox" data-bool="ambient" ${prefs.ambient ? "checked" : ""}></label>
      </section>
      <section class="cz">
        <h4>Jarvis</h4>
        <label class="cz-row"><span>Speak replies aloud</span>
          <input type="checkbox" data-bool="voice.speak" ${prefs.voice.speak ? "checked" : ""}></label>
        <label class="cz-row"><span>Sound cues when the mic opens</span>
          <input type="checkbox" data-bool="voice.chime" ${prefs.voice.chime ? "checked" : ""}></label>
        <label class="cz-row"><span>Listen for “Jarvis”</span>
          <input type="checkbox" data-bool="voice.wake" ${prefs.voice.wake ? "checked" : ""}></label>
        <p class="cz-note">With the wake word on, the microphone stays open while this tab is visible and your browser's speech service hears what is said nearby. Voice choice and speed are in Jarvis's own settings (the gear in the Jarvis panel).</p>
      </section>
      <section class="cz">
        <h4>Keyboard</h4>
        <button type="button" class="btn sm" id="czKeys">${icon("key")} Show keyboard shortcuts</button>
      </section>`,
  });

  dr.querySelectorAll("[data-accent]").forEach(b => b.addEventListener("click", () => {
    setPref("accent", b.dataset.accent);
    dr.querySelectorAll("[data-accent]").forEach(x => {
      x.classList.toggle("on", x === b); x.setAttribute("aria-checked", String(x === b));
    });
  }));
  dr.querySelectorAll("[data-k]").forEach(g => g.querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
    setPref(g.dataset.k, b.dataset.v);
    g.querySelectorAll("button").forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-checked", String(x === b)); });
  })));
  dr.querySelectorAll("[data-bool]").forEach(c => c.addEventListener("change", () => setPref(c.dataset.bool, c.checked)));
  dr.querySelector("#czKeys").addEventListener("click", () => { closeDrawer(); openShortcuts(); });
}

/* ---------------- shortcuts ---------------- */
export function openShortcuts() {
  if (document.querySelector(".keys-ovl")) return;
  const ovl = document.createElement("div");
  ovl.className = "ovl keys-ovl";
  const rows = [
    ["Ctrl K", "Search, jump, or brief the agency"],
    ["Ctrl J", "Talk to Jarvis (again to stop listening)"],
    ["?", "This sheet"],
    ["Esc", "Close whatever is open"],
  ];
  ovl.innerHTML = `<div class="modal keys" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts">
      <h3>Keyboard shortcuts</h3><div class="mb">
      <div class="keys-grid">
        <div><h4>Everywhere</h4>${rows.map(([k, t]) => `<div class="kr"><span>${esc(t)}</span><span>${k.split(" ").map(x => `<kbd>${esc(x)}</kbd>`).join("")}</span></div>`).join("")}</div>
        <div><h4>Go to — press <kbd>g</kbd> then</h4>${PAGES.filter(p => p.key).map(p =>
          `<div class="kr"><span>${esc(p.t)}</span><span><kbd>g</kbd><kbd>${esc(p.key)}</kbd></span></div>`).join("")}</div>
      </div></div>
      <div class="mf"><button class="btn" type="button" data-x>Close</button></div></div>`;
  document.getElementById("modalRoot").appendChild(ovl);
  requestAnimationFrame(() => ovl.classList.add("on"));
  const close = () => { ovl.remove(); document.removeEventListener("keydown", onKey, true); };
  const onKey = e => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); } };
  document.addEventListener("keydown", onKey, true);
  ovl.addEventListener("click", e => { if (e.target === ovl || e.target.closest("[data-x]")) close(); });
  ovl.querySelector("[data-x]").focus();
}

/* ---------------- key sequences ---------------- */
function typing(t) {
  return t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
}
export function installKeys() {
  let gAt = 0;
  document.addEventListener("keydown", e => {
    if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
    if (document.querySelector(".ovl, .pal, .jv[data-mode='full']")) return;
    if (e.key === "?") { e.preventDefault(); openShortcuts(); return; }
    if (e.key === "g") { gAt = Date.now(); return; }
    if (gAt && Date.now() - gAt < 1200) {
      const p = PAGES.find(x => x.key === e.key.toLowerCase());
      gAt = 0;
      if (p) { e.preventDefault(); location.hash = p.h; }
    }
  });
}

export { motionLevel };
