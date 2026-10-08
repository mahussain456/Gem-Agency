// ============================================================
// THE OFFICE — the agent team as a living 3D office.
// Every agent has a desk. An agent sits and types only while a live
// pipeline stage (or an Ask-the-agency run) is theirs, raises a hand while
// a run waits on your approval, and is free otherwise. State comes from
// /api/agency/office every few seconds; work is never simulated.
// Free time is: an agent with nothing to do wanders the lounge (coffee,
// table tennis, the bookshelf, a chat), the office dog Biscuit potters
// about, and the light follows your real clock. That is decoration, and the
// status, the tags and the card always say "Free".
// three.js (MIT) is bundled in /app/q/vendor and loads only on this page.
// ============================================================
import { apiGet, esc, icon, bindGo, ago } from "/app/q/js/core.js";
import { openTeamChat } from "/app/q/js/team.js";

let world = null;

export default async function officePage(el) {
  officeTeardown();
  el.innerHTML = `<div class="page office-page">
    <div class="phead"><div><h1>The office</h1>
      <p>Your team at work, live: who is busy, on what, and who is free. Click anyone to see their work.</p></div></div>
    <div class="of-stage" id="ofStage">
      <div class="of-tags" id="ofTags"></div>
      <div class="of-feed"><h3>Just now</h3><ul id="ofFeed"></ul></div>
      <div class="of-idle" id="ofIdle" hidden><b>Nobody is on a task right now.</b>
        <span>Start a website or a campaign and the team gets to work.</span>
        <button class="btn sm pri" data-go="builder/seo_campaign">${icon("play")} Start a campaign</button></div>
      <div class="of-legend"><span><i class="w"></i>Working</span><span><i class="f"></i>Free</span><span><i class="q"></i>Waiting on you</span></div>
      <div class="of-hud"><div><b id="ofWork">0</b><span>Working</span></div><div><b id="ofFree">0</b><span>Free</span></div>
        <div><b id="ofWait">0</b><span>Waiting on you</span></div></div>
      <div class="of-card" id="ofCard" hidden></div>
      <div class="of-msg" id="ofMsg">Opening the office…</div>
    </div></div>`;
  bindGo(el);
  const stage = el.querySelector("#ofStage");
  let THREE;
  try {
    THREE = await import("/app/q/vendor/three.module.min.js");
  } catch (e) {
    stage.querySelector("#ofMsg").textContent = `The 3D engine could not load: ${e.message || e}`;
    return;
  }
  if (!document.getElementById("ofStage")) return;               // navigated away while loading
  const probe = document.createElement("canvas");
  if (!(probe.getContext("webgl2") || probe.getContext("webgl"))) {
    stage.querySelector("#ofMsg").textContent = "This browser has WebGL turned off, so the office cannot be drawn. The Activity page shows the same information as a list.";
    return;
  }
  world = buildWorld(THREE, stage);
  stage.querySelector("#ofMsg").remove();
  const refresh = async () => {
    try { world && world.apply(await apiGet("/api/agency/office")); }
    catch (e) { /* the next tick retries; a stale office is better than a blank one */ }
  };
  await refresh();
  world.poll = setInterval(refresh, 3000);
}

export function officeTeardown() {
  if (!world) return;
  world.dispose();
  world = null;
}

/* ================================================================
   The scene. 1 unit = one floor tile; X right, Z toward the viewer.
   ================================================================ */
function buildWorld(THREE, stage) {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches
    || document.documentElement.dataset.motion === "off";
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(1.5, devicePixelRatio || 1));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  stage.prepend(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color("#11161a");
  const camera = new THREE.OrthographicCamera(-10, 10, 10, -10, 0.1, 200);
  const CENTER = new THREE.Vector3(12, 0, 6.6);
  camera.position.set(CENTER.x + 24, 22, CENTER.z + 24);
  camera.lookAt(CENTER);
  const hemi = new THREE.HemisphereLight("#dfe9ef", "#2a2622", 1.15);
  scene.add(hemi);
  const sun = new THREE.DirectionalLight("#fff2df", 2.2);
  sun.position.set(CENTER.x - 8, 18, CENTER.z + 14);
  sun.target.position.copy(CENTER);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -18, right: 18, top: 14, bottom: -14, near: 1, far: 60 });
  sun.shadow.bias = -0.0006; sun.shadow.normalBias = 0.02;
  scene.add(sun, sun.target);

  const M = {};
  const mat = (c, o = {}) => {
    const k = c + JSON.stringify(o);
    return M[k] || (M[k] = new THREE.MeshStandardMaterial({ color: c, roughness: 0.75, metalness: 0.02, ...o }));
  };
  function mesh(geo, material, x = 0, y = 0, z = 0, parent = scene, cast = true) {
    const m = new THREE.Mesh(geo, material); m.position.set(x, y, z);
    m.castShadow = cast; m.receiveShadow = true; parent.add(m); return m;
  }
  const box = (w, h, d, c, x, y, z, parent = scene) =>
    mesh(new THREE.BoxGeometry(w, h, d), typeof c === "string" ? mat(c) : c, x, y, z, parent);
  const canvasTex = (w, h) => {
    const c = document.createElement("canvas"); c.width = w; c.height = h;
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
    return { c, g: c.getContext("2d"), t };
  };

  /* ---------------- layout ---------------- */
  const ROOMS = [
    { id: "command", name: "COMMAND", x: 0, z: 0, w: 5, d: 5, floor: "#2b3a3d", accent: "#64d4bd", door: "front" },
    { id: "research", name: "RESEARCH", x: 5, z: 0, w: 5, d: 5, floor: "#2c3542", accent: "#8bbff0", door: "front" },
    { id: "growth", name: "GROWTH", x: 10, z: 0, w: 8, d: 5, floor: "#2d3a31", accent: "#78dba6", door: "front" },
    { id: "build", name: "BUILD STUDIO", x: 0, z: 7, w: 18, d: 5.5, floor: "#3a3530", accent: "#c3a6ff", door: "back" },
    { id: "lounge", name: "LOUNGE", x: 18, z: 0, w: 6, d: 12.5, floor: "#263a35", accent: "#64d4bd" },
  ];
  const ROOM = Object.fromEntries(ROOMS.map(r => [r.id, r]));
  const CORRIDOR_Z = 6, LOUNGE_DOOR = [18.4, CORRIDOR_Z], AISLE_X = 21.4;
  // the known team, each with a desk and a look; anyone new gets a spare desk in the studio
  const LOOK = {
    orchestrator: { room: "command", desk: [2.5, 2.2], shirt: "#3aa58c", pants: "#2c3338", hair: "#2a1d14", skin: "#e2b892", style: "short" },
    scout: { room: "research", desk: [7.5, 2.2], shirt: "#5a8fd0", pants: "#30353a", hair: "#7a4a22", skin: "#f0cfb0", style: "pony" },
    rank: { room: "growth", desk: [11.6, 2.2], shirt: "#4fa874", pants: "#2c3338", hair: "#141414", skin: "#a8714d", style: "short" },
    reach: { room: "growth", desk: [14, 2.2], shirt: "#d4864a", pants: "#3a3530", hair: "#c58a3a", skin: "#f1d0b2", style: "long" },
    scribe: { room: "growth", desk: [16.4, 2.2], shirt: "#8a6fc0", pants: "#2c3338", hair: "#3b2618", skin: "#c99572", style: "bun" },
    lumen: { room: "build", desk: [2.4, 9.6], shirt: "#c76a8a", pants: "#2e3236", hair: "#9a3a24", skin: "#f3d6bd", style: "long" },
    stitch: { room: "build", desk: [6.2, 9.6], shirt: "#6a7fd4", pants: "#2e3236", hair: "#111111", skin: "#8a5a3c", style: "short" },
    forge: { room: "build", desk: [11.8, 9.6], shirt: "#c9a03a", pants: "#3a3530", hair: "#555555", skin: "#d9a982", style: "bald" },
    dev: { room: "build", desk: [15.6, 9.6], shirt: "#3f7f9f", pants: "#2c3338", hair: "#231912", skin: "#b88462", style: "short" },
  };
  const SPARE = [[4.3, 11.6], [13.7, 11.6], [16.4, 11.6]];
  const SPARE_LOOKS = [
    { shirt: "#7d8f99", pants: "#2c3338", hair: "#3b2618", skin: "#d9a982", style: "short" },
    { shirt: "#b07ab8", pants: "#30353a", hair: "#141414", skin: "#a8714d", style: "long" },
    { shirt: "#9aa86a", pants: "#2c3338", hair: "#7a4a22", skin: "#f0cfb0", style: "bun" },
  ];

  function floorTexture(base) {
    const { g, t } = canvasTex(128, 128);
    g.fillStyle = base; g.fillRect(0, 0, 128, 128);
    g.strokeStyle = "#00000022"; g.lineWidth = 2;
    for (let i = 0; i <= 128; i += 32) { g.beginPath(); g.moveTo(i, 0); g.lineTo(i, 128); g.stroke(); }
    g.globalAlpha = 0.5; for (let i = 0; i <= 128; i += 32) { g.beginPath(); g.moveTo(0, i); g.lineTo(128, i); g.stroke(); }
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    return t;
  }
  mesh(new THREE.PlaneGeometry(18, 2), mat("#22292d"), 9, 0.001, CORRIDOR_Z, scene, false).rotation.x = -Math.PI / 2;
  for (const r of ROOMS) {
    const t = floorTexture(r.floor); t.repeat.set(r.w / 2, r.d / 2);
    mesh(new THREE.PlaneGeometry(r.w, r.d), new THREE.MeshStandardMaterial({ map: t, roughness: 0.9 }),
      r.x + r.w / 2, 0.002, r.z + r.d / 2, scene, false).rotation.x = -Math.PI / 2;
  }
  // rugs: the lounge sitting area and the studio
  const rug = (w, d, c, x, z) => { mesh(new THREE.PlaneGeometry(w, d), mat(c, { roughness: 1 }), x, 0.004, z, scene, false).rotation.x = -Math.PI / 2; };
  rug(2.6, 2.2, "#3d5a63", 21.0, 4.0); rug(2.4, 1.6, "#5a4a3d", 20.4, 9.9); rug(3.2, 1.4, "#3a3a55", 9, 10.6);
  box(24.2, 1.6, 0.12, "#33404a", 12, 0.8, -0.06);
  box(0.12, 1.6, 12.6, "#2e3a43", -0.06, 0.8, 6.25);
  const windowMat = new THREE.MeshStandardMaterial({ color: "#9fd8e6", emissive: "#3f7d8a", emissiveIntensity: 0.9, roughness: 0.2 });
  for (let x = 1.2; x < 23; x += 2.4) if (Math.abs(x - 18.9) > 0.9) mesh(new THREE.PlaneGeometry(1.5, 0.75), windowMat, x, 0.95, 0.005, scene, false);
  for (let z = 1.3; z < 12; z += 2.6) mesh(new THREE.PlaneGeometry(1.5, 0.75), windowMat, 0.005, 0.95, z, scene, false).rotation.y = Math.PI / 2;
  const glass = new THREE.MeshStandardMaterial({ color: "#cfe8ef", transparent: true, opacity: 0.18, roughness: 0.1, metalness: 0.1 });
  function partition(x1, z1, x2, z2, gap) {
    const horiz = z1 === z2, len = horiz ? x2 - x1 : z2 - z1;
    for (const [a, b] of gap ? [[0, gap[0]], [gap[1], len]] : [[0, len]]) {
      if (b - a < 0.05) continue;
      const mid = (a + b) / 2, L = b - a, cx = horiz ? x1 + mid : x1, cz = horiz ? z1 : z1 + mid;
      mesh(new THREE.BoxGeometry(horiz ? L : 0.04, 0.62, horiz ? 0.04 : L), glass, cx, 0.35, cz, scene, false);
      box(horiz ? L : 0.07, 0.05, horiz ? 0.07 : L, "#56656d", cx, 0.68, cz);
      box(horiz ? L : 0.07, 0.06, horiz ? 0.07 : L, "#56656d", cx, 0.03, cz);
    }
  }
  for (const r of ROOMS) {
    if (r.id === "lounge") continue;
    const doorX = r.w / 2 - 0.45;
    if (r.door === "front") partition(r.x, r.z + r.d, r.x + r.w, r.z + r.d, [doorX, doorX + 0.9]);
    else partition(r.x, r.z, r.x + r.w, r.z, [doorX, doorX + 0.9]);
    if (r.x + r.w < 18) partition(r.x + r.w, r.z, r.x + r.w, r.z + r.d);
  }
  partition(18, 0, 18, 5, null); partition(18, 7, 18, 12.5, null);
  const fontReady = document.fonts ? document.fonts.load('600 46px "JetBrains Mono"').catch(() => {}) : Promise.resolve();
  function plate(text, accent, x, z) {
    const { c, g, t } = canvasTex(512, 96);
    const draw = () => {
      g.clearRect(0, 0, 512, 96);
      g.fillStyle = "#141719e0"; g.beginPath(); g.roundRect(4, 4, 504, 88, 18); g.fill();
      g.strokeStyle = accent; g.lineWidth = 4; g.stroke();
      g.fillStyle = accent; g.font = '600 46px "JetBrains Mono", ui-monospace, monospace';
      g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(text, 256, 50); t.needsUpdate = true;
    };
    draw(); fontReady.then(draw);
    mesh(new THREE.PlaneGeometry(2.7, 0.51), new THREE.MeshBasicMaterial({ map: t, transparent: true }), x, 0.012, z, scene, false).rotation.x = -Math.PI / 2;
    return c;
  }
  function plant(x, z, s = 1) {
    const g = new THREE.Group(); g.position.set(x, 0, z); g.scale.setScalar(s); scene.add(g);
    mesh(new THREE.CylinderGeometry(0.16, 0.12, 0.3, 14), mat("#b98b62"), 0, 0.15, 0, g);
    const leaves = new THREE.Group(); g.add(leaves);
    for (const [dx, dy, dz, r] of [[0, 0.5, 0, 0.22], [0.12, 0.62, 0.05, 0.16], [-0.1, 0.66, -0.06, 0.15], [0.02, 0.78, 0.02, 0.12]])
      mesh(new THREE.IcosahedronGeometry(r, 1), mat(dy > 0.6 ? "#5fae7f" : "#3f8f63", { flatShading: true }), dx, dy, dz, leaves);
    plants.push({ leaves, seed: x * 0.7 + z });
  }
  const plants = [];
  function chair(x, z) {
    const g = new THREE.Group(); g.position.set(x, 0, z); scene.add(g);
    mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.2, 8), mat("#22282c"), 0, 0.12, 0, g);
    mesh(new THREE.CylinderGeometry(0.17, 0.17, 0.03, 5), mat("#22282c"), 0, 0.02, 0, g);
    box(0.36, 0.06, 0.34, "#2a3439", 0, 0.24, 0, g);
    box(0.34, 0.36, 0.05, "#2a3439", 0, 0.46, 0.17, g);
  }
  const glowTex = (() => {
    const { g, t } = canvasTex(64, 64);
    const r = g.createRadialGradient(32, 32, 0, 32, 32, 32); r.addColorStop(0, "#ffffffcc"); r.addColorStop(1, "#ffffff00");
    g.fillStyle = r; g.fillRect(0, 0, 64, 64); return t;
  })();
  const MUGS = ["#e8e2d6", "#64d4bd", "#f3c56e", "#c76a8a", "#8bbff0"];
  const SCREEN_ROWS = ["#64d4bd", "#8bbff0", "#c3a6ff", "#f3c56e", "#e8e2d6"];
  function workstation(a, i) {
    const [x, z] = a.desk, g = new THREE.Group(); g.position.set(x, 0, z); scene.add(g);
    box(1.1, 0.04, 0.55, "#c9a47c", 0, 0.42, 0, g);
    for (const sx of [-0.5, 0.5]) box(0.04, 0.4, 0.5, "#8a6d52", sx, 0.2, 0, g);
    box(0.05, 0.05, 0.08, "#2a2f33", 0, 0.47, -0.17, g);
    box(0.48, 0.3, 0.03, "#1b1f22", 0, 0.66, -0.19, g);
    // the screen: abstract lines that scroll while the agent really is working
    a.scr = canvasTex(128, 80);
    a.scrRows = Array.from({ length: 14 }, (_, k) => ({ w: 20 + ((k * 37 + i * 13) % 80), c: SCREEN_ROWS[(k + i) % SCREEN_ROWS.length], ind: (k * 7 + i) % 3 }));
    a.scrOff = 0; drawScreen(a);
    a.screen = new THREE.MeshStandardMaterial({ color: "#0d1316", emissive: "#ffffff", emissiveMap: a.scr.t, emissiveIntensity: 0.05 });
    mesh(new THREE.PlaneGeometry(0.44, 0.26), a.screen, 0, 0.66, -0.174, g, false);
    box(0.36, 0.015, 0.12, "#2f3639", 0, 0.45, 0.05, g);
    mesh(new THREE.CylinderGeometry(0.03, 0.025, 0.07, 10), mat(MUGS[i % MUGS.length]), 0.38, 0.475, 0.05, g);
    // a name plate on the desk front
    const np = canvasTex(256, 56);
    const drawPlate = () => {
      np.g.clearRect(0, 0, 256, 56); np.g.fillStyle = "#f4efe6"; np.g.beginPath(); np.g.roundRect(2, 2, 252, 52, 10); np.g.fill();
      np.g.fillStyle = "#1c2226"; np.g.font = '600 30px "JetBrains Mono", ui-monospace, monospace';
      np.g.textAlign = "center"; np.g.textBaseline = "middle"; np.g.fillText(a.first.toUpperCase(), 128, 30); np.t.needsUpdate = true;
    };
    drawPlate(); fontReady.then(drawPlate);
    mesh(new THREE.PlaneGeometry(0.42, 0.092), new THREE.MeshBasicMaterial({ map: np.t, transparent: true }), 0, 0.36, 0.281, g, false);
    a.glow = new THREE.Sprite(new THREE.SpriteMaterial({ color: "#64d4bd", transparent: true, opacity: 0, depthWrite: false,
      blending: THREE.AdditiveBlending, map: glowTex }));
    a.glow.position.set(0, 0.66, -0.12); a.glow.scale.set(1.1, 0.7, 1); g.add(a.glow);
    chair(x, z + 0.48);
    a.seat = [x, z + 0.46];
  }
  function drawScreen(a) {
    const { g, t } = a.scr;
    g.fillStyle = "#0b1114"; g.fillRect(0, 0, 128, 80);
    g.fillStyle = "#64d4bd22"; g.fillRect(0, 0, 128, 9);
    const off = Math.floor(a.scrOff);
    for (let k = 0; k < 9; k++) {
      const row = a.scrRows[(k + off) % a.scrRows.length];
      g.fillStyle = row.c; g.globalAlpha = 0.85;
      g.fillRect(8 + row.ind * 8, 14 + k * 7, row.w * 0.9, 3);
    }
    g.globalAlpha = 1; t.needsUpdate = true;
  }
  function sofa(x, z, rot, color) {
    const g = new THREE.Group(); g.position.set(x, 0, z); g.rotation.y = rot; scene.add(g);
    box(1.5, 0.2, 0.55, color, 0, 0.2, 0, g);
    box(1.5, 0.42, 0.14, color, 0, 0.38, -0.22, g);
    for (const sx of [-0.72, 0.72]) box(0.1, 0.32, 0.55, color, sx, 0.26, 0, g);
    for (const sx of [-0.4, 0, 0.4]) box(0.38, 0.06, 0.45, new THREE.Color(color).offsetHSL(0, 0, 0.05).getStyle(), sx, 0.33, 0.03, g);
  }
  sofa(20.2, 3.2, 0, "#3f6f66"); sofa(22.6, 4.6, -Math.PI / 2, "#4a5a8a"); sofa(20.4, 10.6, Math.PI, "#6a4a5a");
  box(0.9, 0.04, 0.5, "#c9a47c", 20.5, 0.24, 4.4);
  for (const [dx, dz] of [[-0.4, -0.2], [0.4, -0.2], [-0.4, 0.2], [0.4, 0.2]]) box(0.04, 0.22, 0.04, "#8a6d52", 20.5 + dx, 0.11, 4.4 + dz);
  // the coffee counter, with steam while someone is making one
  box(1.8, 0.48, 0.42, "#2f3a40", 22.9, 0.24, 8.4);
  box(0.3, 0.3, 0.26, "#9baab1", 22.75, 0.63, 8.4);
  box(0.12, 0.05, 0.1, "#1b1f22", 22.6, 0.53, 8.4);
  const steam = [0, 1, 2].map(k => {
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, color: "#ffffff", transparent: true, opacity: 0, depthWrite: false }));
    s.scale.setScalar(0.14); s.position.set(22.6, 0.6, 8.4); s.userData.k = k; scene.add(s); return s;
  });
  box(0.95, 0.05, 0.6, "#c9a47c", 20.3, 0.64, 7.4); box(0.08, 0.62, 0.08, "#2f3a40", 20.3, 0.31, 7.4);
  box(0.45, 0.03, 0.35, "#2f3a40", 20.3, 0.015, 7.4);
  // table tennis
  box(1.5, 0.04, 0.8, "#2f6b57", 21.2, 0.38, 1.3);
  box(1.5, 0.005, 0.02, "#e8efe9", 21.2, 0.403, 1.3);
  box(0.02, 0.08, 0.84, "#e8efe9", 21.2, 0.44, 1.3);
  for (const [dx, dz] of [[-0.65, -0.33], [0.65, -0.33], [-0.65, 0.33], [0.65, 0.33]]) box(0.04, 0.36, 0.04, "#22282c", 21.2 + dx, 0.18, 1.3 + dz);
  const ball = mesh(new THREE.SphereGeometry(0.025, 10, 8), mat("#ffffff", { emissive: "#ffffff", emissiveIntensity: 0.3 }), 21.2, 0.5, 1.3);
  ball.visible = false;
  // the bookshelf
  box(1.2, 1.1, 0.3, "#5a4535", 19.0, 0.55, 0.2);
  const BOOKS = ["#c76a8a", "#8bbff0", "#f3c56e", "#64d4bd", "#c3a6ff", "#d4864a", "#e8e2d6"];
  for (let shelf = 0; shelf < 3; shelf++) {
    box(1.12, 0.03, 0.26, "#6b5442", 19.0, 0.2 + shelf * 0.34, 0.24);
    let bx = 18.48;
    for (let k = 0; bx < 19.46; k++) {
      const w = 0.05 + ((k * 7 + shelf * 3) % 4) * 0.015, h = 0.2 + ((k * 5 + shelf) % 3) * 0.03;
      box(w, h, 0.18, BOOKS[(k + shelf * 2) % BOOKS.length], bx + w / 2, 0.215 + shelf * 0.34 + h / 2, 0.26);
      bx += w + 0.012;
    }
  }
  // a wall clock showing your real time
  const clock = canvasTex(128, 128);
  function drawClock() {
    const g = clock.g, d = new Date(), h = d.getHours() % 12 + d.getMinutes() / 60, m = d.getMinutes();
    g.clearRect(0, 0, 128, 128);
    g.fillStyle = "#f4efe6"; g.beginPath(); g.arc(64, 64, 60, 0, Math.PI * 2); g.fill();
    g.strokeStyle = "#1c2226"; g.lineWidth = 6; g.stroke();
    for (let k = 0; k < 12; k++) { const a = k / 12 * Math.PI * 2; g.fillStyle = "#1c2226"; g.fillRect(64 + Math.sin(a) * 48 - 2, 64 - Math.cos(a) * 48 - 2, 4, 4); }
    const hand = (a, len, w) => { g.lineWidth = w; g.lineCap = "round"; g.beginPath(); g.moveTo(64, 64); g.lineTo(64 + Math.sin(a) * len, 64 - Math.cos(a) * len); g.stroke(); };
    hand(h / 12 * Math.PI * 2, 28, 6); hand(m / 60 * Math.PI * 2, 42, 4);
    g.fillStyle = "#64d4bd"; g.beginPath(); g.arc(64, 64, 5, 0, Math.PI * 2); g.fill();
    clock.t.needsUpdate = true;
  }
  drawClock();
  mesh(new THREE.CircleGeometry(0.2, 32), new THREE.MeshBasicMaterial({ map: clock.t, transparent: true }), 2.4, 1.1, 0.01, scene, false);

  plant(23.5, 0.5, 1.3); plant(18.6, 12, 1.1); plant(0.5, 4.5); plant(9.6, 0.5); plant(17.5, 4.5); plant(0.5, 12); plant(17.5, 12);
  for (const r of ROOMS) if (r.id !== "lounge") plate(r.name, r.accent, r.x + r.w / 2, r.door === "front" ? r.z + r.d - 0.45 : r.z + 0.45);
  plate("LOUNGE", "#64d4bd", 21, 6);

  /* ---------------- daylight follows your clock ---------------- */
  const SKY = { day: new THREE.Color("#11161a"), night: new THREE.Color("#0a0d12") };
  function daylight() {
    const d = new Date(), h = d.getHours() + d.getMinutes() / 60;
    const day = Math.max(0, Math.min(1, Math.sin((h - 6) / 13 * Math.PI) * 1.4));
    sun.intensity = 1.25 + 1.0 * day;            // night is dimmer, never gloomy
    sun.color.set(day > 0.35 ? "#fff2df" : "#ffcf9e").lerp(new THREE.Color("#9fb4ff"), (1 - day) * 0.3);
    hemi.intensity = 0.95 + 0.25 * day;
    scene.background.copy(SKY.night).lerp(SKY.day, day);
    windowMat.emissive.set(day > 0.2 ? "#3f7d8a" : "#1f2c55");
    windowMat.emissiveIntensity = 0.35 + 0.6 * day;
    drawClock();
  }
  daylight();
  const skyTimer = setInterval(daylight, 60000);

  /* ---------------- people ---------------- */
  const PERSON = 1.22;
  function limb(radius, length, material, parent, y) {
    const pivot = new THREE.Group(); pivot.position.y = y; parent.add(pivot);
    mesh(new THREE.CapsuleGeometry(radius, length, 4, 10), material, 0, -length / 2 - radius * 0.5, 0, pivot);
    const end = new THREE.Group(); end.position.y = -length - radius; pivot.add(end);
    return { pivot, end };
  }
  function makePerson(a) {
    const root = new THREE.Group(); root.scale.setScalar(PERSON); scene.add(root);
    const skin = mat(a.skin), shirt = mat(a.shirt), pants = mat(a.pants), hair = mat(a.hair, { roughness: 0.9 });
    const hips = new THREE.Group(); hips.position.y = 0.43; root.add(hips);
    mesh(new THREE.CapsuleGeometry(0.115, 0.07, 4, 12), pants, 0, 0.02, 0, hips);
    const chest = new THREE.Group(); chest.position.y = 0.06; hips.add(chest);
    mesh(new THREE.CapsuleGeometry(0.13, 0.17, 4, 14), shirt, 0, 0.16, 0, chest).scale.set(1, 1, 0.78);
    const neck = new THREE.Group(); neck.position.y = 0.36; chest.add(neck);
    mesh(new THREE.CylinderGeometry(0.035, 0.04, 0.06, 8), skin, 0, 0.02, 0, neck);
    const head = new THREE.Group(); head.position.y = 0.13; neck.add(head);
    mesh(new THREE.SphereGeometry(0.105, 20, 16), skin, 0, 0, 0, head).scale.set(0.95, 1.05, 0.95);
    const eyes = [-0.036, 0.036].map(sx => mesh(new THREE.SphereGeometry(0.013, 8, 6), mat("#1b1b1b"), sx, 0.012, 0.094, head, false));
    mesh(new THREE.SphereGeometry(0.03, 10, 8), skin, 0, -0.012, 0.098, head, false).scale.set(0.5, 0.45, 0.5);
    const mouth = mesh(new THREE.BoxGeometry(0.03, 0.006, 0.01), mat("#6b3a33"), 0, -0.045, 0.096, head, false);
    if (a.style !== "bald") {
      mesh(new THREE.SphereGeometry(0.113, 20, 12, 0, Math.PI * 2, 0, Math.PI * 0.55), hair, 0, 0.012, -0.004, head).scale.set(0.98, 1.05, 1);
      if (a.style === "long") mesh(new THREE.CapsuleGeometry(0.085, 0.12, 4, 10), hair, 0, -0.08, -0.05, head).scale.set(1.15, 1, 0.6);
      if (a.style === "pony") mesh(new THREE.CapsuleGeometry(0.035, 0.12, 4, 8), hair, 0, -0.04, -0.12, head);
      if (a.style === "bun") mesh(new THREE.SphereGeometry(0.05, 12, 10), hair, 0, 0.1, -0.06, head);
    } else mesh(new THREE.SphereGeometry(0.108, 16, 10, 0, Math.PI * 2, Math.PI * 0.55, Math.PI * 0.2), hair, 0, 0, 0, head);
    const arms = [], legs = [];
    for (const side of [-1, 1]) {
      const shoulder = new THREE.Group(); shoulder.position.set(side * 0.165, 0.29, 0); chest.add(shoulder);
      mesh(new THREE.SphereGeometry(0.05, 10, 8), shirt, 0, 0, 0, shoulder);
      const upper = limb(0.042, 0.12, shirt, shoulder, 0);
      const fore = limb(0.036, 0.11, skin, upper.end, 0);
      mesh(new THREE.SphereGeometry(0.04, 10, 8), skin, 0, 0, 0, fore.end);
      arms.push({ shoulder, elbow: fore.pivot, hand: fore.end });
      const hip = new THREE.Group(); hip.position.set(side * 0.065, -0.02, 0); hips.add(hip);
      const thigh = limb(0.055, 0.14, pants, hip, 0);
      const shin = limb(0.045, 0.14, pants, thigh.end, 0);
      mesh(new THREE.BoxGeometry(0.085, 0.05, 0.16), mat("#24272a"), 0, -0.005, 0.035, shin.end);
      legs.push({ hip, knee: shin.pivot });
    }
    // things in hand: a mug, a bat, a book, a watering can
    const hand = arms[1].hand, props = {};
    props.cup = mesh(new THREE.CylinderGeometry(0.028, 0.022, 0.07, 10), mat("#efe9df"), 0, -0.02, 0.03, hand);
    props.paddle = new THREE.Group(); hand.add(props.paddle);
    mesh(new THREE.CylinderGeometry(0.075, 0.075, 0.012, 16), mat("#c0392b"), 0, -0.1, 0.02, props.paddle).rotation.x = Math.PI / 2;
    mesh(new THREE.BoxGeometry(0.025, 0.07, 0.02), mat("#8a6d52"), 0, -0.03, 0.02, props.paddle);
    props.book = mesh(new THREE.BoxGeometry(0.16, 0.2, 0.03), mat("#8bbff0"), -0.08, -0.02, 0.05, hand);
    props.can = mesh(new THREE.CylinderGeometry(0.04, 0.045, 0.09, 10), mat("#4fa874"), 0, -0.03, 0.05, hand);
    for (const p of Object.values(props)) p.visible = false;
    return { root, hips, chest, head, eyes, mouth, arms, legs, props };
  }

  /* poses: what the body does in each mode. t is seconds; s the agent's seed */
  function poseFor(a, t) {
    const p = { hipsY: 0.43, lean: 0, headX: 0, headY: 0, sh: [[0, 0, 0.08], [0, 0, -0.08]], el: [0, 0], hip: [0, 0], knee: [0, 0], prop: null, talk: false };
    const s = a.seed, m = a.mode;
    if (m === "walk") {
      const w = t * 11 + s, sw = Math.sin(w);
      p.hip = [sw * 0.55, -sw * 0.55]; p.knee = [Math.max(0, -Math.cos(w)) * 0.9, Math.max(0, Math.cos(w)) * 0.9];
      p.sh = [[-sw * 0.5, 0, 0.1], [sw * 0.5, 0, -0.1]]; p.el = [-0.35, -0.35];
      p.hipsY = 0.43 + Math.abs(Math.cos(w)) * 0.018; p.lean = 0.06;
      if (a.carry) p.prop = a.carry;
    } else if (m === "sitDesk") {
      p.hipsY = 0.3 / PERSON + 0.02; p.hip = [-1.45, -1.45]; p.knee = [1.4, 1.4]; p.lean = 0.12;
      if (a.state === "waiting") {
        const up = (Math.sin(t * 1.3 + s) + 1) / 2 > 0.55;
        p.sh = [[-0.6, 0, 0.1], up ? [-2.7, 0, -0.25] : [-0.6, 0, -0.1]]; p.el = [-0.9, up ? -0.2 : -0.9];
        p.headX = -0.15; p.lean = -0.02;
      } else {
        // typing, with a pause now and then to think
        const think = (t * 0.13 + s) % 1 < 0.14;
        p.sh = [[-0.95, 0, 0.18], think ? [-1.9, 0, -0.35] : [-0.95, 0, -0.18]];
        p.el = [-0.75 + Math.sin(t * 17 + s) * 0.06, think ? -2.3 : -0.75 + Math.sin(t * 15 + s + 1) * 0.06];
        p.headX = think ? -0.05 : 0.12 + Math.sin(t * 0.7 + s) * 0.04; p.headY = Math.sin(t * 0.4 + s) * 0.12;
      }
    } else if (m === "cheer") {
      const pump = Math.sin(t * 13) * 0.25;
      p.sh = [[-2.7 + pump, 0, 0.35], [-2.7 - pump, 0, -0.35]]; p.el = [-0.25, -0.25]; p.headX = -0.25; p.lean = -0.06;
      p.hipsY = 0.43 + Math.abs(Math.sin(t * 6.5)) * 0.03;
    } else if (m === "sitSofa") {
      p.hipsY = 0.38 / PERSON; p.hip = [-1.4, -1.4]; p.knee = [1.35, 1.35]; p.lean = -0.12;
      const sip = (t * 0.35 + s) % 1 < 0.22;
      p.sh = [[-0.3, 0, 0.2], sip ? [-1.4, 0.2, -0.2] : [-0.5, 0, -0.2]]; p.el = [-1.1, sip ? -2.1 : -1.3];
      p.prop = "cup"; p.headY = Math.sin(t * 0.5 + s) * 0.35; p.headX = sip ? -0.15 : 0; p.talk = a.chat;
    } else if (m === "paddle") {
      const sw = Math.sin(t * 2 * Math.PI * 0.9 + (a.side || 0) * Math.PI);
      p.hipsY = 0.41; p.hip = [-0.3, -0.3]; p.knee = [0.5, 0.5]; p.lean = 0.14;
      p.sh = [[-0.5, 0, 0.35], [-0.9 + sw * 0.45, sw * 0.5, -0.25]]; p.el = [-1.3, -0.7];
      p.prop = "paddle"; p.headY = sw * 0.25;
    } else if (m === "read") {
      const turn = (t * 0.2 + s) % 1 < 0.08;
      p.sh = [[-0.85, 0, 0.12], [-0.85, 0, -0.12]]; p.el = [-1.25, turn ? -0.7 : -1.25]; p.headX = 0.38;
      p.prop = "book"; p.hipsY = 0.43 + Math.sin(t * 1.4 + s) * 0.003;
    } else if (m === "water") {
      const tip = (Math.sin(t * 0.9 + s) + 1) / 2;
      p.sh = [[0.05, 0, 0.12], [-0.8 - tip * 0.3, 0, -0.1]]; p.el = [-0.1, -0.3]; p.headX = 0.3; p.lean = 0.1; p.prop = "can";
    } else if (m === "stretch") {
      const up = (t * 0.22 + s) % 1 < 0.4;
      p.sh = up ? [[-2.95, 0, 0.2], [-2.95, 0, -0.2]] : [[0.05, 0, 0.12], [0.05, 0, -0.12]];
      p.el = up ? [-0.1, -0.1] : [-0.1, -0.1]; p.lean = up ? -0.1 : 0; p.headX = up ? -0.3 : 0;
      p.hipsY = 0.43 + (up ? 0.015 : 0);
    } else if (m === "coffee") {
      const ph = (t * 0.25 + s) % 1, press = ph < 0.3, sip = ph > 0.7;
      p.sh = [[0.05, 0, 0.12], press ? [-1.3, 0, -0.1] : sip ? [-1.35, 0.2, -0.2] : [-0.45, 0, -0.16]];
      p.el = [-0.1, press ? -0.3 : sip ? -2.15 : -1.35]; p.prop = press ? null : "cup"; p.headX = press ? 0.2 : sip ? -0.2 : 0;
    } else {
      const sip = (t * 0.3 + s) % 1 < 0.2, talk = a.chat;
      p.sh = [[talk ? -0.4 + Math.sin(t * 3 + s) * 0.25 : 0.05, 0, 0.12], sip ? [-1.35, 0.2, -0.2] : [-0.45, 0, -0.16]];
      p.el = [talk ? -0.9 : -0.1, sip ? -2.15 : -1.35];
      p.prop = "cup"; p.headY = Math.sin(t * 0.6 + s) * 0.3; p.headX = sip ? -0.2 : 0; p.talk = talk;
      p.hipsY = 0.43 + Math.sin(t * 1.6 + s) * 0.004;
    }
    return p;
  }
  const lerp = (x, y, k) => x + (y - x) * k;
  function applyPose(a, t, k) {
    const P = poseFor(a, t), b = a.body;
    b.hips.position.y = lerp(b.hips.position.y, P.hipsY, k);
    b.chest.rotation.x = lerp(b.chest.rotation.x, P.lean, k);
    b.head.rotation.x = lerp(b.head.rotation.x, P.headX, k);
    b.head.rotation.y = lerp(b.head.rotation.y, P.headY, k);
    b.arms.forEach((arm, i) => {
      arm.shoulder.rotation.x = lerp(arm.shoulder.rotation.x, P.sh[i][0], k);
      arm.shoulder.rotation.y = lerp(arm.shoulder.rotation.y, P.sh[i][1], k);
      arm.shoulder.rotation.z = lerp(arm.shoulder.rotation.z, P.sh[i][2], k);
      arm.elbow.rotation.x = lerp(arm.elbow.rotation.x, P.el[i], k);
    });
    b.legs.forEach((leg, i) => {
      leg.hip.rotation.x = lerp(leg.hip.rotation.x, P.hip[i], k);
      leg.knee.rotation.x = lerp(leg.knee.rotation.x, P.knee[i], k);
    });
    for (const [name, obj] of Object.entries(b.props)) obj.visible = P.prop === name;
    b.mouth.scale.y = P.talk ? 1 + Math.abs(Math.sin(t * 9 + a.seed)) * 3 : 1;
    const blink = ((t + a.seed * 3) % 4.2) < 0.12;
    b.eyes.forEach(e => { e.scale.y = blink ? 0.15 : 1; });
  }

  /* ---------------- free time: places in the lounge ----------------
     via: a clear point beside the spot, joined to the lounge aisle (x 21.4) */
  const SLOTS = [
    { kind: "sofa", at: [19.8, 3.35], face: 0, mode: "sitSofa", via: [19.8, 3.85], chat: true, line: "On a coffee break" },
    { kind: "sofa", at: [20.6, 3.35], face: 0, mode: "sitSofa", via: [20.6, 3.85], chat: true, line: "On a coffee break" },
    { kind: "sofa", at: [22.45, 4.2], face: -Math.PI / 2, mode: "sitSofa", via: [21.9, 4.2], line: "Putting their feet up" },
    { kind: "sofa", at: [22.45, 5.0], face: -Math.PI / 2, mode: "sitSofa", via: [21.9, 5.0], line: "Putting their feet up" },
    { kind: "sofa", at: [19.9, 10.45], face: Math.PI, mode: "sitSofa", via: [19.9, 9.95], chat: true, line: "Catching up" },
    { kind: "sofa", at: [20.9, 10.45], face: Math.PI, mode: "sitSofa", via: [20.9, 9.95], chat: true, line: "Catching up" },
    { kind: "chat", at: [19.7, 7.4], face: Math.PI / 2, mode: "stand", via: [19.7, 6.6], chat: true, line: "Chatting" },
    { kind: "chat", at: [20.9, 7.4], face: -Math.PI / 2, mode: "stand", via: [21.4, 7.4], chat: true, line: "Chatting" },
    { kind: "chat", at: [20.3, 8.05], face: Math.PI, mode: "stand", via: [20.3, 8.6], chat: true, line: "Chatting" },
    { kind: "coffee", at: [21.85, 8.4], face: Math.PI / 2, mode: "coffee", via: [21.4, 8.4], line: "Making a coffee" },
    { kind: "pong", at: [20.25, 1.3], face: Math.PI / 2, mode: "paddle", via: [20.25, 1.95], side: 0, line: "Playing table tennis" },
    { kind: "pong", at: [22.15, 1.3], face: -Math.PI / 2, mode: "paddle", via: [22.15, 1.95], side: 1, line: "Playing table tennis" },
    { kind: "read", at: [19.0, 0.95], face: 0, mode: "read", via: [19.0, 1.95], line: "Reading" },
    { kind: "water", at: [19.15, 11.6], face: Math.atan2(-0.55, 0.4), mode: "water", via: [19.15, 11.15], line: "Watering the plants" },
    { kind: "stretch", at: [23.1, 2.4], face: -Math.PI / 2, mode: "stretch", via: [22.6, 2.4], line: "Stretching" },
  ];
  const pong = SLOTS.filter(s => s.kind === "pong");
  const taken = new Map();                                   // slot -> agent

  const doorOf = a => {
    const r = ROOM[a.room], x = r.x + r.w / 2;
    return r.door === "front" ? [[x, r.z + r.d - 0.5], [x, CORRIDOR_Z]] : [[x, r.z + 0.5], [x, CORRIDOR_Z]];
  };
  const toAisle = v => [[AISLE_X, v[1]]];
  const lounge = slot => [...toAisle(slot.via), slot.via, slot.at];
  const routeToLounge = (a, slot) => {
    const [inside, outside] = doorOf(a), aisle = a.seat[1] + 0.3;
    return [[a.seat[0], aisle], [inside[0], aisle], inside, outside, LOUNGE_DOOR, [19.2, CORRIDOR_Z], [AISLE_X, CORRIDOR_Z], ...lounge(slot)];
  };
  const routeToDesk = (a) => {
    const [inside, outside] = doorOf(a), aisle = a.seat[1] + 0.3, from = a.slot ? [a.slot.via, [AISLE_X, a.slot.via[1]]] : [];
    return [...from, [AISLE_X, CORRIDOR_Z], [19.2, CORRIDOR_Z], LOUNGE_DOOR, outside, inside, [inside[0], aisle], [a.seat[0], aisle], a.seat];
  };
  const routeBetween = (from, to) => [from.via, [AISLE_X, from.via[1]], ...lounge(to)];

  /* ---------------- agents, tags, interaction ---------------- */
  const agents = new Map();
  let spare = 0;
  const tagsEl = stage.querySelector("#ofTags"), card = stage.querySelector("#ofCard");
  function agentFor(info) {
    let a = agents.get(info.id);
    if (a) return a;
    const look = LOOK[info.id] || (spare < SPARE.length
      ? { room: "build", desk: SPARE[spare], ...SPARE_LOOKS[spare++] } : null);
    if (!look) return null;                                   // more agents than desks: they stay off the floor
    a = { id: info.id, name: info.name, first: String(info.name || info.id).split(/\s+/)[0], ...look,
          seed: agents.size * 1.37, state: "free", until: 0 };
    workstation(a, agents.size);
    a.body = makePerson(a);
    a.tag = document.createElement("button");
    a.tag.type = "button"; a.tag.className = "of-tag";
    a.tag.addEventListener("click", ev => { ev.stopPropagation(); showCard(a); });
    tagsEl.appendChild(a.tag);
    agents.set(info.id, a);
    return a;
  }
  function claim(a, slot) {
    release(a);
    taken.set(slot, a); a.slot = slot;
  }
  function release(a) {
    if (a.slot && taken.get(a.slot) === a) taken.delete(a.slot);
    a.slot = null;
  }
  const freeSlots = () => SLOTS.filter(s => !taken.has(s));
  function pickSlot(a) {
    const open = freeSlots().filter(s => s !== a.slot);
    if (!open.length) return null;
    // someone waiting at the table tennis table: a free hand joins them
    const partner = pong.find(s => taken.has(s) && !taken.has(pong[1 - pong.indexOf(s)]));
    if (partner && Math.random() < 0.6) return pong[1 - pong.indexOf(partner)];
    const singles = open.filter(s => s.kind !== "pong" || Math.random() < 0.25);
    return singles[Math.floor(Math.random() * singles.length)] || open[0];
  }
  function settle(a, slot) {
    a.mode = slot.mode; a.face = slot.face; a.chat = !!slot.chat; a.side = slot.side; a.carry = null;
    a.until = clockT + 25 + Math.random() * 35;
  }
  function place(a, slot) {
    claim(a, slot);
    a.pos = [...slot.at]; settle(a, slot); a.path = null;
  }
  function walk(a, path, then, carry = null) {
    a.carry = carry;
    if (reduced) { const end = path[path.length - 1]; a.pos = [...end]; then(); return; }
    a.path = path.map(p => [...p]); a.mode = "walk"; a.then = then;
  }
  let openCard = null;
  function showCard(a) {
    openCard = a;
    const st = a.state === "working" ? "Working" : a.state === "waiting" ? "Waiting on you" : "Free";
    card.innerHTML = `<button class="of-x" aria-label="Close">${icon("x")}</button>
      <div class="of-card-h"><span class="of-av" style="--c:${a.shirt}">${esc(initialsOf(a.name))}</span>
        <div><b>${esc(a.name)}</b><div class="s">${esc(a.role || "")} <span class="of-st ${a.state}">${st}</span></div></div></div>
      ${a.task ? `<p>${esc(a.task)}${a.project ? ` <span class="s">· ${esc(a.project)}</span>` : ""}</p>`
        : `<p class="s">Not on a task. ${esc(a.slot ? a.slot.line : "In the lounge")} until the next stage needs them.</p>`}
      ${a.since && a.state !== "free" ? `<div class="s">Since ${ago(a.since)}</div>` : ""}
      <div class="of-card-a"><button class="btn sm pri" data-chat>${icon("send")} Message ${esc(a.first)}</button>
        ${a.project_id ? `<button class="btn sm" data-go="project/${esc(a.project_id)}">${icon("arrowR")} Open the project</button>` : ""}
        ${a.state === "waiting" ? `<button class="btn sm" data-go="approvals">${icon("approve")} Approvals</button>` : ""}
        <button class="btn sm" data-go="monitor">${icon("bolt")} Activity</button></div>`;
    card.hidden = false;
    card.querySelector(".of-x").addEventListener("click", () => { card.hidden = true; openCard = null; });
    card.querySelector("[data-chat]").addEventListener("click", ev => { ev.stopPropagation(); openTeamChat(a.id, a.project_id || ""); });
    bindGo(card);
  }
  const initialsOf = n => String(n || "?").split(/\s+/).map(w => w[0]).join("").slice(0, 2).toUpperCase();
  const closeCard = ev => { if (!card.contains(ev.target)) { card.hidden = true; openCard = null; } };
  stage.addEventListener("click", closeCard);

  const feedEl = stage.querySelector("#ofFeed");
  let first = true;
  function apply(data) {
    for (const info of data.agents || []) {
      const a = agentFor(info);
      if (!a) continue;
      const busy = info.state === "working" || info.state === "waiting";
      Object.assign(a, { name: info.name, role: info.role, task: info.task, project: info.project, project_id: info.project_id, since: info.since });
      const wasBusy = a.state === "working" || a.state === "waiting";
      a.state = info.state;
      if (first) {
        if (busy) { a.pos = [...a.seat]; a.mode = "sitDesk"; a.face = Math.PI; }
        else place(a, pickSlot(a) || SLOTS[0]);
        a.body.root.position.set(a.pos[0], 0, a.pos[1]); a.body.root.rotation.y = a.face;
      } else if (busy && !wasBusy) {
        a.hold = null;
        walk(a, routeToDesk(a), () => { a.mode = "sitDesk"; a.face = Math.PI; }, a.slot && a.slot.mode === "read" ? "book" : null);
        release(a);
      } else if (!busy && wasBusy) {
        // a stage done: a small celebration at the desk, then a break
        a.mode = "cheer"; a.face = 0;
        a.hold = { until: clockT + 1.6, then: () => { const sl = pickSlot(a) || SLOTS[0]; claim(a, sl); walk(a, routeToLounge(a, sl), () => settle(a, sl)); } };
      }
    }
    first = false;
    const c = data.counts || {};
    stage.querySelector("#ofWork").textContent = c.working || 0;
    stage.querySelector("#ofFree").textContent = c.free || 0;
    stage.querySelector("#ofWait").textContent = c.waiting || 0;
    stage.querySelector("#ofIdle").hidden = (c.working || 0) + (c.waiting || 0) > 0;
    feedEl.innerHTML = (data.feed || []).slice(0, 4).map(f =>
      `<li><b>${esc(f.text)}</b>${f.project ? `<span>${esc(f.project)} · ${ago(f.at)}</span>` : `<span>${ago(f.at)}</span>`}</li>`).join("")
      || `<li><span>No stage has run yet.</span></li>`;
    if (openCard) showCard(openCard);
  }

  /* ---------------- free agents keep moving ---------------- */
  function lifeTick() {
    for (const a of agents.values()) {
      if (a.state !== "free" || a.path || a.hold || !a.slot || clockT < a.until) continue;
      if (Math.random() < 0.3) { a.until = clockT + 15 + Math.random() * 20; continue; }   // happy where they are
      const next = pickSlot(a);
      if (!next) { a.until = clockT + 20; continue; }
      const from = a.slot;
      claim(a, next);
      walk(a, routeBetween(from, next), () => settle(a, next), from.kind === "coffee" ? "cup" : null);
    }
  }

  /* ---------------- Biscuit, the office dog ---------------- */
  const dog = (() => {
    const root = new THREE.Group(); scene.add(root);
    const fur = mat("#c8955a"), dark = mat("#5a3b22");
    const body = new THREE.Group(); body.position.y = 0.2; root.add(body);
    mesh(new THREE.CapsuleGeometry(0.085, 0.2, 4, 10), fur, 0, 0, 0, body).rotation.x = Math.PI / 2;
    const head = new THREE.Group(); head.position.set(0, 0.08, 0.17); body.add(head);
    mesh(new THREE.SphereGeometry(0.075, 14, 10), fur, 0, 0, 0, head);
    mesh(new THREE.BoxGeometry(0.06, 0.05, 0.07), fur, 0, -0.02, 0.07, head);
    mesh(new THREE.SphereGeometry(0.015, 8, 6), mat("#1b1b1b"), 0, -0.005, 0.105, head, false);
    for (const sx of [-1, 1]) {
      mesh(new THREE.BoxGeometry(0.03, 0.07, 0.05), dark, sx * 0.055, 0.03, -0.01, head).rotation.z = sx * 0.4;
      mesh(new THREE.SphereGeometry(0.01, 6, 5), mat("#1b1b1b"), sx * 0.03, 0.02, 0.065, head, false);
    }
    const tail = new THREE.Group(); tail.position.set(0, 0.04, -0.17); body.add(tail);
    mesh(new THREE.CylinderGeometry(0.012, 0.02, 0.12, 6), fur, 0, 0.05, -0.02, tail).rotation.x = -0.6;
    const legs = [];
    for (const [lx, lz] of [[-0.05, 0.1], [0.05, 0.1], [-0.05, -0.1], [0.05, -0.1]]) {
      const hip = new THREE.Group(); hip.position.set(lx, -0.03, lz); body.add(hip);
      mesh(new THREE.CylinderGeometry(0.018, 0.016, 0.15, 6), fur, 0, -0.075, 0, hip);
      legs.push(hip);
    }
    const POINTS = [[21.4, 2.6], [21.4, 6], [21.4, 9.3], [21.0, 5.6], [15, 6], [9, 6], [3.5, 6], [19.6, 6.3], [22.7, 6.5], [23.2, 10.6], [23.0, 3.1]];
    return { root, body, head, tail, legs, pos: [21.4, 6], face: 0, path: null, mode: "sit", until: 4, POINTS };
  })();
  function dogRoute(from, to) {
    // two walkways: the corridor (z = 6) and the lounge aisle (x = 21.4)
    const onCorr = p => Math.abs(p[1] - CORRIDOR_Z) < 0.8 && p[0] < 21.6;
    if (onCorr(from) && onCorr(to)) return [[to[0], CORRIDOR_Z], to];
    return [[onCorr(from) ? from[0] : AISLE_X, CORRIDOR_Z], [AISLE_X, CORRIDOR_Z], [AISLE_X, to[1]], to];
  }
  function dogTick(dt, t) {
    const d = dog;
    if (d.path) {
      const [tx, tz] = d.path[0], dx = tx - d.pos[0], dz = tz - d.pos[1], dist = Math.hypot(dx, dz), v = dt * 1.3;
      if (dist <= v || dist < 1e-4) { d.pos = [tx, tz]; d.path.shift(); if (!d.path.length) { d.path = null; d.mode = Math.random() < 0.5 ? "sit" : "lie"; d.until = t + 5 + Math.random() * 14; } }
      else { d.pos[0] += dx / dist * v; d.pos[1] += dz / dist * v; d.face = Math.atan2(dx, dz); }
    } else if (t > d.until && !reduced) {
      const to = d.POINTS[Math.floor(Math.random() * d.POINTS.length)];
      d.path = dogRoute(d.pos, to); d.mode = "walk";
    }
    d.root.position.set(d.pos[0], 0, d.pos[1]);
    let dr = d.face - d.root.rotation.y; dr = Math.atan2(Math.sin(dr), Math.cos(dr));
    d.root.rotation.y += dr * Math.min(1, dt * 8);
    const k = Math.min(1, dt * 6), walking = d.mode === "walk";
    d.legs.forEach((leg, i) => {
      const target = walking ? Math.sin(t * 14 + (i % 2 ? Math.PI : 0) + (i > 1 ? Math.PI : 0)) * 0.6
        : d.mode === "lie" ? (i < 2 ? -1.4 : 1.4) : d.mode === "sit" && i > 1 ? -1.2 : 0;
      leg.rotation.x = lerp(leg.rotation.x, target, k);
    });
    d.body.position.y = lerp(d.body.position.y, d.mode === "lie" ? 0.09 : d.mode === "sit" ? 0.17 : 0.2 + (walking ? Math.abs(Math.sin(t * 14)) * 0.01 : 0), k);
    d.body.rotation.x = lerp(d.body.rotation.x, d.mode === "sit" ? -0.45 : 0, k);
    d.head.rotation.x = lerp(d.head.rotation.x, d.mode === "sit" ? 0.4 : 0, k);
    d.head.rotation.y = Math.sin(t * 0.7) * (walking ? 0.1 : 0.4);
    d.tail.rotation.y = Math.sin(t * (walking ? 16 : 9)) * 0.6;
  }

  /* ---------------- frame loop ---------------- */
  const v3 = new THREE.Vector3(), DOT = { working: "#2fa36b", free: "#8b979c", waiting: "#d99a1c" };
  function tags() {
    const W = stage.clientWidth, H = stage.clientHeight, placed = [];
    for (const a of agents.values()) {
      a.body.head.getWorldPosition(v3); v3.y += 0.2; v3.project(camera);
      a.sx = (v3.x + 1) / 2 * W; a.sy = (1 - v3.y) / 2 * H;
      const line = a.state === "waiting" ? "Needs your approval"
        : a.mode === "cheer" ? "Finished a stage"
        : a.path ? (a.state === "working" ? "Heading to desk" : "Free · on the move")
        : a.state === "working" ? a.task : (a.slot ? a.slot.line : "Free");
      const html = `<b><i style="background:${DOT[a.state]}"></i>${esc(a.first)}</b><span>${esc(line || "")}</span>`;
      if (a.tagHtml !== html) { a.tag.innerHTML = html; a.tagHtml = html; a.tag.className = "of-tag " + a.state; }
    }
    for (const a of [...agents.values()].sort((p, q) => p.sy - q.sy)) {
      const w = a.tag.offsetWidth || 120, h = a.tag.offsetHeight || 30;
      let x = a.sx, y = a.sy;
      for (let i = 0; i < 8; i++) {
        const hit = placed.find(r => Math.abs(r.x - x) < (r.w + w) / 2 + 4 && Math.abs(r.y - y) < (r.h + h) / 2 + 3);
        if (!hit) break; y = hit.y - (hit.h + h) / 2 - 4;
      }
      placed.push({ x, y, w, h });
      a.tag.style.transform = `translate(${x}px, ${y}px) translate(-50%, -100%)`;
    }
  }
  function fit() {
    const W = stage.clientWidth, H = stage.clientHeight;
    if (!W || !H) return;
    renderer.setSize(W, H, false);
    const aspect = W / H, size = 9.2;
    Object.assign(camera, { left: -size * aspect, right: size * aspect, top: size, bottom: -size, zoom: 1 });
    camera.updateProjectionMatrix(); camera.updateMatrixWorld();
    let mx = 0, my = 0;
    for (const [x, z] of [[0, 0], [24, 0], [0, 12.5], [24, 12.5]]) {
      v3.set(x, 0, z).project(camera); mx = Math.max(mx, Math.abs(v3.x)); my = Math.max(my, Math.abs(v3.y));
    }
    const zoom = Math.min(0.94 / mx, 0.86 / my);
    camera.zoom = zoom;
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(fit); ro.observe(stage); fit();

  const clock3 = new THREE.Clock();
  let t = 0, last = 0, clockT = 0, screenT = 0;
  renderer.setAnimationLoop(() => {
    if (document.hidden) { clock3.getDelta(); return; }              // paused while the tab is hidden
    const raw = clock3.getDelta();
    const dt = Math.min(0.05, raw);
    const move = Math.min(0.25, raw);
    clockT += move;
    if (reduced && (t += dt) - last < 0.2) return;                    // reduced motion: a few frames a second, no idling
    last = t; if (!reduced) t += dt;
    const anim = reduced ? 0 : t;
    if (!reduced) lifeTick();
    for (const a of agents.values()) {
      if (a.hold && clockT > a.hold.until) { const h = a.hold; a.hold = null; h.then(); }
      if (a.path) {
        const [tx, tz] = a.path[0], dx = tx - a.pos[0], dz = tz - a.pos[1], d = Math.hypot(dx, dz), v = move * 1.6;
        if (d < 0.0001 || d <= v) { a.pos = [tx, tz]; a.path.shift(); if (!a.path.length) { a.path = null; a.then(); } }
        else { a.pos[0] += dx / d * v; a.pos[1] += dz / d * v; a.face = Math.atan2(dx, dz); }
      }
      const root = a.body.root;
      root.position.set(a.pos[0], 0, a.pos[1] - (a.mode === "sitDesk" ? 0.02 : 0));
      let dr = a.face - root.rotation.y; dr = Math.atan2(Math.sin(dr), Math.cos(dr));
      root.rotation.y += dr * Math.min(1, dt * 10);
      const on = a.state === "working" && a.mode === "sitDesk";
      a.screen.emissiveIntensity = lerp(a.screen.emissiveIntensity, on ? 0.95 + Math.sin(anim * 3 + a.seed) * 0.1 : 0.05, Math.min(1, dt * 4));
      a.glow.material.opacity = lerp(a.glow.material.opacity, on ? 0.35 : 0, Math.min(1, dt * 4));
      if (on && !reduced) a.scrOff += dt * 2.2;
      applyPose(a, anim, reduced ? 1 : Math.min(1, dt * 8));
    }
    if ((screenT += dt) > 0.2) { screenT = 0; for (const a of agents.values()) if (a.state === "working") drawScreen(a); }
    // the rally: only while both players are at the table
    const p0 = taken.get(pong[0]), p1 = taken.get(pong[1]);
    const rally = p0 && p1 && !p0.path && !p1.path && p0.mode === "paddle" && p1.mode === "paddle";
    ball.visible = !!rally;
    if (rally) {
      const ph = (anim * 0.9) % 1, u = ph < 0.5 ? ph * 2 : 2 - ph * 2;
      ball.position.set(20.45 + u * 1.5, 0.42 + Math.abs(Math.sin(u * Math.PI * 2)) * 0.22, 1.3 + Math.sin(anim * 1.7) * 0.18);
    }
    const brewing = [...taken.entries()].some(([s, a]) => s.kind === "coffee" && !a.path);
    for (const s of steam) {
      const ph = (anim * 0.5 + s.userData.k / 3) % 1;
      s.position.y = 0.62 + ph * 0.35; s.position.x = 22.6 + Math.sin(ph * 6 + s.userData.k) * 0.03;
      s.material.opacity = brewing ? (1 - ph) * 0.45 : lerp(s.material.opacity, 0, 0.1);
    }
    for (const p of plants) p.leaves.rotation.z = Math.sin(anim * 0.8 + p.seed) * 0.03;
    dogTick(reduced ? 0 : move, clockT);
    renderer.render(scene, camera);
    tags();
  });

  return {
    apply, poll: 0,
    dispose() {
      clearInterval(this.poll); clearInterval(skyTimer);
      renderer.setAnimationLoop(null);
      ro.disconnect();
      stage.removeEventListener("click", closeCard);
      scene.traverse(o => {
        if (o.geometry) o.geometry.dispose();
        const m = o.material; if (m) [].concat(m).forEach(x => { if (x.map) x.map.dispose(); if (x.emissiveMap) x.emissiveMap.dispose(); x.dispose(); });
      });
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
