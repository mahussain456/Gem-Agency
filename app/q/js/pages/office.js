// ============================================================
// THE OFFICE — the agent team as a living 3D office.
// Every agent has a desk. An agent sits and types only while a live
// pipeline stage (or an Ask-the-agency run) is theirs, raises a hand while
// a run waits on your approval, and is in the lounge otherwise. State comes
// from /api/agency/office every few seconds; nothing here is simulated.
// three.js (MIT) is bundled in /app/q/vendor and loads only on this page.
// Labs (Antigravity, ChatGPT) was retired 2026-10-08; the Build Studio has its floor.
// ============================================================
import { apiGet, esc, icon, bindGo, ago } from "/app/q/js/core.js";

let world = null;

export default async function officePage(el) {
  officeTeardown();
  el.innerHTML = `<div class="page office-page">
    <div class="phead"><div><h1>The office</h1>
      <p>Your agents at work, live: who is busy, on what, and who is free. Click a name to see their work.</p></div></div>
    <div class="of-stage" id="ofStage">
      <div class="of-tags" id="ofTags"></div>
      <div class="of-feed"><h3>Just now</h3><ul id="ofFeed"></ul></div>
      <div class="of-idle" id="ofIdle" hidden><b>Nobody is on a task right now.</b>
        <span>Start a website or a campaign and the team gets to work.</span>
        <button class="btn sm pri" data-go="builder/seo_campaign">${icon("play")} Start a campaign</button></div>
      <div class="of-legend"><span><i class="w"></i>Working</span><span><i class="f"></i>Free</span><span><i class="q"></i>Waiting on you</span></div>
      <div class="of-hud"><div><b id="ofWork">0</b><span>Working</span></div><div><b id="ofFree">0</b><span>In the lounge</span></div>
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
  scene.add(new THREE.HemisphereLight("#dfe9ef", "#2a2622", 1.15));
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

  /* ---------------- layout ---------------- */
  const ROOMS = [
    { id: "command", name: "COMMAND", x: 0, z: 0, w: 5, d: 5, floor: "#2b3a3d", accent: "#64d4bd", door: "front" },
    { id: "research", name: "RESEARCH", x: 5, z: 0, w: 5, d: 5, floor: "#2c3542", accent: "#8bbff0", door: "front" },
    { id: "growth", name: "GROWTH", x: 10, z: 0, w: 8, d: 5, floor: "#2d3a31", accent: "#78dba6", door: "front" },
    { id: "build", name: "BUILD STUDIO", x: 0, z: 7, w: 18, d: 5.5, floor: "#3a3530", accent: "#c3a6ff", door: "back" },
    { id: "lounge", name: "LOUNGE", x: 18, z: 0, w: 6, d: 12.5, floor: "#263a35", accent: "#64d4bd" },
  ];
  const ROOM = Object.fromEntries(ROOMS.map(r => [r.id, r]));
  const CORRIDOR_Z = 6, LOUNGE_DOOR = [18.4, CORRIDOR_Z];
  // the known team, each with a desk and a look; anyone new gets a spare desk in Labs
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
  // free desks for agents added later, along the studio's front
  const SPARE = [[4.3, 11.6], [13.7, 11.6], [16.4, 11.6]];
  const SPARE_LOOKS = [
    { shirt: "#7d8f99", pants: "#2c3338", hair: "#3b2618", skin: "#d9a982", style: "short" },
    { shirt: "#b07ab8", pants: "#30353a", hair: "#141414", skin: "#a8714d", style: "long" },
    { shirt: "#9aa86a", pants: "#2c3338", hair: "#7a4a22", skin: "#f0cfb0", style: "bun" },
  ];

  function floorTexture(base) {
    const c = document.createElement("canvas"); c.width = c.height = 128;
    const g = c.getContext("2d"); g.fillStyle = base; g.fillRect(0, 0, 128, 128);
    g.strokeStyle = "#00000022"; g.lineWidth = 2;
    for (let i = 0; i <= 128; i += 32) { g.beginPath(); g.moveTo(i, 0); g.lineTo(i, 128); g.stroke(); }
    g.globalAlpha = 0.5; for (let i = 0; i <= 128; i += 32) { g.beginPath(); g.moveTo(0, i); g.lineTo(128, i); g.stroke(); }
    const t = new THREE.CanvasTexture(c); t.wrapS = t.wrapT = THREE.RepeatWrapping; t.colorSpace = THREE.SRGBColorSpace;
    return t;
  }
  mesh(new THREE.PlaneGeometry(18, 2), mat("#22292d"), 9, 0.001, CORRIDOR_Z, scene, false).rotation.x = -Math.PI / 2;
  for (const r of ROOMS) {
    const t = floorTexture(r.floor); t.repeat.set(r.w / 2, r.d / 2);
    mesh(new THREE.PlaneGeometry(r.w, r.d), new THREE.MeshStandardMaterial({ map: t, roughness: 0.9 }),
      r.x + r.w / 2, 0.002, r.z + r.d / 2, scene, false).rotation.x = -Math.PI / 2;
  }
  box(24.2, 1.6, 0.12, "#33404a", 12, 0.8, -0.06);
  box(0.12, 1.6, 12.6, "#2e3a43", -0.06, 0.8, 6.25);
  const windowMat = new THREE.MeshStandardMaterial({ color: "#9fd8e6", emissive: "#3f7d8a", emissiveIntensity: 0.9, roughness: 0.2 });
  for (let x = 1.2; x < 23; x += 2.4) mesh(new THREE.PlaneGeometry(1.5, 0.75), windowMat, x, 0.95, 0.005, scene, false);
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
    const c = document.createElement("canvas"); c.width = 512; c.height = 96;
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
    const draw = () => {
      const g = c.getContext("2d"); g.clearRect(0, 0, 512, 96);
      g.fillStyle = "#141719e0"; g.beginPath(); g.roundRect(4, 4, 504, 88, 18); g.fill();
      g.strokeStyle = accent; g.lineWidth = 4; g.stroke();
      g.fillStyle = accent; g.font = '600 46px "JetBrains Mono", ui-monospace, monospace';
      g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(text, 256, 50); t.needsUpdate = true;
    };
    draw(); fontReady.then(draw);
    mesh(new THREE.PlaneGeometry(2.7, 0.51), new THREE.MeshBasicMaterial({ map: t, transparent: true }), x, 0.012, z, scene, false).rotation.x = -Math.PI / 2;
  }
  function plant(x, z, s = 1) {
    const g = new THREE.Group(); g.position.set(x, 0, z); g.scale.setScalar(s); scene.add(g);
    mesh(new THREE.CylinderGeometry(0.16, 0.12, 0.3, 14), mat("#b98b62"), 0, 0.15, 0, g);
    for (const [dx, dy, dz, r] of [[0, 0.5, 0, 0.22], [0.12, 0.62, 0.05, 0.16], [-0.1, 0.66, -0.06, 0.15], [0.02, 0.78, 0.02, 0.12]])
      mesh(new THREE.IcosahedronGeometry(r, 1), mat(dy > 0.6 ? "#5fae7f" : "#3f8f63", { flatShading: true }), dx, dy, dz, g);
  }
  function chair(x, z) {
    const g = new THREE.Group(); g.position.set(x, 0, z); scene.add(g);
    mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.2, 8), mat("#22282c"), 0, 0.12, 0, g);
    mesh(new THREE.CylinderGeometry(0.17, 0.17, 0.03, 5), mat("#22282c"), 0, 0.02, 0, g);
    box(0.36, 0.06, 0.34, "#2a3439", 0, 0.24, 0, g);
    box(0.34, 0.36, 0.05, "#2a3439", 0, 0.46, 0.17, g);
  }
  const glowTex = (() => {
    const c = document.createElement("canvas"); c.width = c.height = 64; const g = c.getContext("2d");
    const r = g.createRadialGradient(32, 32, 0, 32, 32, 32); r.addColorStop(0, "#ffffffcc"); r.addColorStop(1, "#ffffff00");
    g.fillStyle = r; g.fillRect(0, 0, 64, 64); return new THREE.CanvasTexture(c);
  })();
  const MUGS = ["#e8e2d6", "#64d4bd", "#f3c56e", "#c76a8a", "#8bbff0"];
  function workstation(a, i) {
    const [x, z] = a.desk, g = new THREE.Group(); g.position.set(x, 0, z); scene.add(g);
    box(1.1, 0.04, 0.55, "#c9a47c", 0, 0.42, 0, g);
    for (const sx of [-0.5, 0.5]) box(0.04, 0.4, 0.5, "#8a6d52", sx, 0.2, 0, g);
    box(0.05, 0.05, 0.08, "#2a2f33", 0, 0.47, -0.17, g);
    box(0.48, 0.3, 0.03, "#1b1f22", 0, 0.66, -0.19, g);
    a.screen = new THREE.MeshStandardMaterial({ color: "#0d1316", emissive: "#64d4bd", emissiveIntensity: 0.05 });
    mesh(new THREE.PlaneGeometry(0.44, 0.26), a.screen, 0, 0.66, -0.174, g, false);
    box(0.36, 0.015, 0.12, "#2f3639", 0, 0.45, 0.05, g);
    mesh(new THREE.CylinderGeometry(0.03, 0.025, 0.07, 10), mat(MUGS[i % MUGS.length]), 0.38, 0.475, 0.05, g);
    a.glow = new THREE.Sprite(new THREE.SpriteMaterial({ color: "#64d4bd", transparent: true, opacity: 0, depthWrite: false,
      blending: THREE.AdditiveBlending, map: glowTex }));
    a.glow.position.set(0, 0.66, -0.12); a.glow.scale.set(1.1, 0.7, 1); g.add(a.glow);
    chair(x, z + 0.48);
    a.seat = [x, z + 0.46];
  }
  function sofa(x, z, rot, color) {
    const g = new THREE.Group(); g.position.set(x, 0, z); g.rotation.y = rot; scene.add(g);
    box(1.5, 0.2, 0.55, color, 0, 0.2, 0, g);
    box(1.5, 0.42, 0.14, color, 0, 0.38, -0.22, g);
    for (const sx of [-0.72, 0.72]) box(0.1, 0.32, 0.55, color, sx, 0.26, 0, g);
    for (const sx of [-0.4, 0, 0.4]) box(0.38, 0.06, 0.45, new THREE.Color(color).offsetHSL(0, 0, 0.05).getStyle(), sx, 0.33, 0.03, g);
  }
  sofa(20.2, 3.2, 0, "#3f6f66"); sofa(22.6, 4.6, -Math.PI / 2, "#4a5a8a"); sofa(20.4, 10.6, Math.PI, "#6a4a5a");
  box(0.9, 0.04, 0.5, "#c9a47c", 20.5, 0.24, 4.15);
  for (const [dx, dz] of [[-0.4, -0.2], [0.4, -0.2], [-0.4, 0.2], [0.4, 0.2]]) box(0.04, 0.22, 0.04, "#8a6d52", 20.5 + dx, 0.11, 4.15 + dz);
  box(1.8, 0.48, 0.42, "#2f3a40", 22.9, 0.24, 8.4);
  box(0.3, 0.3, 0.26, "#9baab1", 22.75, 0.63, 8.4);
  box(0.95, 0.05, 0.6, "#c9a47c", 20.3, 0.64, 7.4); box(0.08, 0.62, 0.08, "#2f3a40", 20.3, 0.31, 7.4);
  box(0.45, 0.03, 0.35, "#2f3a40", 20.3, 0.015, 7.4);
  const SPOTS = [
    { at: [19.8, 3.35], face: 0, mode: "sitSofa" }, { at: [20.6, 3.35], face: 0, mode: "sitSofa" },
    { at: [22.45, 4.2], face: -Math.PI / 2, mode: "sitSofa" }, { at: [22.45, 5.0], face: -Math.PI / 2, mode: "sitSofa" },
    { at: [19.9, 10.45], face: Math.PI, mode: "sitSofa" }, { at: [20.9, 10.45], face: Math.PI, mode: "sitSofa" },
    { at: [19.7, 7.4], face: Math.PI / 2, mode: "stand", chat: true }, { at: [20.9, 7.4], face: -Math.PI / 2, mode: "stand", chat: true },
    { at: [20.3, 8.05], face: Math.PI, mode: "stand" }, { at: [22.3, 7.9], face: 0, mode: "stand" }, { at: [22.3, 8.9], face: Math.PI, mode: "stand" },
    { at: [21.4, 1.4], face: Math.PI, mode: "stand" }, { at: [23.2, 2.4], face: -Math.PI / 2, mode: "stand" }, { at: [19.4, 12], face: Math.PI, mode: "stand" },
  ];
  plant(23.5, 0.5, 1.3); plant(18.6, 12, 1.1); plant(0.5, 4.5); plant(9.6, 0.5); plant(17.5, 4.5); plant(0.5, 12); plant(17.5, 12);
  for (const r of ROOMS) if (r.id !== "lounge") plate(r.name, r.accent, r.x + r.w / 2, r.door === "front" ? r.z + r.d - 0.45 : r.z + 0.45);
  plate("LOUNGE", "#64d4bd", 21, 6);

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
    const cup = mesh(new THREE.CylinderGeometry(0.028, 0.022, 0.07, 10), mat("#efe9df"), 0, -0.02, 0.03, arms[1].hand);
    cup.visible = false;
    return { root, hips, chest, head, eyes, arms, legs, cup };
  }
  function poseFor(a, t) {
    const p = { hipsY: 0.43, lean: 0, headX: 0, headY: 0, sh: [[0, 0, 0.08], [0, 0, -0.08]], el: [0, 0], hip: [0, 0], knee: [0, 0], cup: false };
    const s = a.seed;
    if (a.mode === "walk") {
      const w = t * 11 + s, sw = Math.sin(w);   // steps keep pace with the walk
      p.hip = [sw * 0.55, -sw * 0.55]; p.knee = [Math.max(0, -Math.cos(w)) * 0.9, Math.max(0, Math.cos(w)) * 0.9];
      p.sh = [[-sw * 0.5, 0, 0.1], [sw * 0.5, 0, -0.1]]; p.el = [-0.35, -0.35];
      p.hipsY = 0.43 + Math.abs(Math.cos(w)) * 0.018; p.lean = 0.06;
    } else if (a.mode === "sitDesk") {
      p.hipsY = 0.3 / PERSON + 0.02; p.hip = [-1.45, -1.45]; p.knee = [1.4, 1.4]; p.lean = 0.12;
      if (a.state === "waiting") {
        const up = (Math.sin(t * 1.3 + s) + 1) / 2 > 0.55;
        p.sh = [[-0.6, 0, 0.1], up ? [-2.7, 0, -0.25] : [-0.6, 0, -0.1]]; p.el = [-0.9, up ? -0.2 : -0.9];
        p.headX = -0.15; p.lean = -0.02;
      } else {
        p.sh = [[-0.95, 0, 0.18], [-0.95, 0, -0.18]];
        p.el = [-0.75 + Math.sin(t * 17 + s) * 0.06, -0.75 + Math.sin(t * 15 + s + 1) * 0.06];
        p.headX = 0.12 + Math.sin(t * 0.7 + s) * 0.04; p.headY = Math.sin(t * 0.4 + s) * 0.12;
      }
    } else if (a.mode === "sitSofa") {
      p.hipsY = 0.38 / PERSON; p.hip = [-1.4, -1.4]; p.knee = [1.35, 1.35]; p.lean = -0.12;
      const sip = (t * 0.35 + s) % 1 < 0.22;
      p.sh = [[-0.3, 0, 0.2], sip ? [-1.4, 0.2, -0.2] : [-0.5, 0, -0.2]]; p.el = [-1.1, sip ? -2.1 : -1.3];
      p.cup = true; p.headY = Math.sin(t * 0.5 + s) * 0.35; p.headX = sip ? -0.15 : 0;
    } else {
      const sip = (t * 0.3 + s) % 1 < 0.2, talk = a.chat;
      p.sh = [[talk ? -0.4 + Math.sin(t * 3 + s) * 0.25 : 0.05, 0, 0.12], sip ? [-1.35, 0.2, -0.2] : [-0.45, 0, -0.16]];
      p.el = [talk ? -0.9 : -0.1, sip ? -2.15 : -1.35];
      p.cup = true; p.headY = Math.sin(t * 0.6 + s) * 0.3; p.headX = sip ? -0.2 : 0;
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
    b.cup.visible = P.cup;
    const blink = ((t + a.seed * 3) % 4.2) < 0.12;
    b.eyes.forEach(e => { e.scale.y = blink ? 0.15 : 1; });
  }

  /* ---------------- routes between desks and the lounge ---------------- */
  const doorOf = a => {
    const r = ROOM[a.room], x = r.x + r.w / 2;
    return r.door === "front" ? [[x, r.z + r.d - 0.5], [x, CORRIDOR_Z]] : [[x, r.z + 0.5], [x, CORRIDOR_Z]];
  };
  const routeToLounge = (a, spot) => {
    const [inside, outside] = doorOf(a), aisle = a.seat[1] + 0.3;
    return [[a.seat[0], aisle], [inside[0], aisle], inside, outside, LOUNGE_DOOR, [19.2, CORRIDOR_Z], spot.at];
  };
  const routeToDesk = (a, from) => {
    const [inside, outside] = doorOf(a), aisle = a.seat[1] + 0.3;
    return [from, [19.2, CORRIDOR_Z], LOUNGE_DOOR, outside, inside, [inside[0], aisle], [a.seat[0], aisle], a.seat];
  };

  /* ---------------- agents, tags, interaction ---------------- */
  const agents = new Map(), freeSpots = new Set(SPOTS.map((_, i) => i));
  let spare = 0;
  const tagsEl = stage.querySelector("#ofTags"), card = stage.querySelector("#ofCard");
  function agentFor(info) {
    let a = agents.get(info.id);
    if (a) return a;
    const look = LOOK[info.id] || (spare < SPARE.length
      ? { room: "build", desk: SPARE[spare], ...SPARE_LOOKS[spare++] } : null);
    if (!look) return null;                                   // more agents than desks: they stay off the floor
    a = { id: info.id, name: info.name, ...look, seed: agents.size * 1.37, state: "free" };
    workstation(a, agents.size);
    a.body = makePerson(a);
    a.tag = document.createElement("button");
    a.tag.type = "button"; a.tag.className = "of-tag";
    a.tag.addEventListener("click", ev => { ev.stopPropagation(); showCard(a); });
    tagsEl.appendChild(a.tag);
    agents.set(info.id, a);
    return a;
  }
  function takeSpot(a) {
    const idx = [...freeSpots][Math.floor(Math.random() * freeSpots.size)];
    if (idx === undefined) return null;
    freeSpots.delete(idx); a.spot = idx; return SPOTS[idx];
  }
  function place(a, pos, mode, face, chat) { a.pos = [...pos]; a.mode = mode; a.face = face; a.chat = chat; a.path = null; }
  function walk(a, path, then) {
    if (reduced) { const end = path[path.length - 1]; a.pos = [...end]; then(); return; }
    a.path = path.map(p => [...p]); a.mode = "walk"; a.then = then;
  }
  let openCard = null;
  function showCard(a) {
    openCard = a;
    const st = a.state === "working" ? "Working" : a.state === "waiting" ? "Waiting on you" : "Free";
    card.innerHTML = `<button class="of-x" aria-label="Close">${icon("x")}</button>
      <div class="of-card-h"><b>${esc(a.name)}</b><span class="of-st ${a.state}">${st}</span></div>
      <div class="s">${esc(a.role || "")}</div>
      ${a.task ? `<p>${esc(a.task)}${a.project ? ` <span class="s">· ${esc(a.project)}</span>` : ""}</p>` : `<p class="s">Not on a task. In the lounge until the next stage needs them.</p>`}
      ${a.since && a.state !== "free" ? `<div class="s">Since ${ago(a.since)}</div>` : ""}
      <div class="of-card-a">${a.project_id ? `<button class="btn sm pri" data-go="project/${esc(a.project_id)}">${icon("arrowR")} Open the project</button>` : ""}
        ${a.state === "waiting" ? `<button class="btn sm" data-go="approvals">${icon("approve")} Approvals</button>` : ""}
        <button class="btn sm" data-go="monitor">${icon("bolt")} Activity</button></div>`;
    card.hidden = false;
    card.querySelector(".of-x").addEventListener("click", () => { card.hidden = true; openCard = null; });
    bindGo(card);
  }
  const closeCard = ev => { if (!card.contains(ev.target)) { card.hidden = true; openCard = null; } };
  stage.addEventListener("click", closeCard);

  const feedEl = stage.querySelector("#ofFeed");
  let first = true;
  function apply(data) {
    for (const info of data.agents || []) {
      const a = agentFor(info);
      if (!a) continue;
      const busy = info.state === "working" || info.state === "waiting";
      Object.assign(a, { role: info.role, task: info.task, project: info.project, project_id: info.project_id, since: info.since });
      const wasBusy = a.state === "working" || a.state === "waiting";
      a.state = info.state;
      if (first) {
        if (busy) place(a, a.seat, "sitDesk", Math.PI);
        else { const sp = takeSpot(a) || SPOTS[0]; place(a, sp.at, sp.mode, sp.face, sp.chat); }
        a.body.root.position.set(a.pos[0], 0, a.pos[1]); a.body.root.rotation.y = a.face;
      } else if (busy && !wasBusy) {
        if (a.spot !== undefined) { freeSpots.add(a.spot); a.spot = undefined; }
        walk(a, routeToDesk(a, a.pos), () => { a.mode = "sitDesk"; a.face = Math.PI; });
      } else if (!busy && wasBusy) {
        const sp = takeSpot(a) || SPOTS[0];
        walk(a, routeToLounge(a, sp), () => { a.mode = sp.mode; a.face = sp.face; a.chat = sp.chat; });
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

  /* ---------------- frame loop ---------------- */
  const v3 = new THREE.Vector3(), DOT = { working: "#2fa36b", free: "#8b979c", waiting: "#d99a1c" };
  function tags() {
    const W = stage.clientWidth, H = stage.clientHeight, placed = [];
    for (const a of agents.values()) {
      a.body.head.getWorldPosition(v3); v3.y += 0.2; v3.project(camera);
      a.sx = (v3.x + 1) / 2 * W; a.sy = (1 - v3.y) / 2 * H;
      const line = a.state === "waiting" ? "Needs your approval" : a.path ? (a.state === "working" ? "Heading to desk" : "Taking a break")
        : a.state === "working" ? a.task : a.mode === "sitSofa" ? "On a coffee break" : "In the lounge";
      const html = `<b><i style="background:${DOT[a.state]}"></i>${esc(a.name)}</b><span>${esc(line || "")}</span>`;
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
    camera.zoom = Math.min(0.94 / mx, 0.86 / my);
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(fit); ro.observe(stage); fit();

  const clock = new THREE.Clock();
  let t = 0, last = 0;
  renderer.setAnimationLoop(() => {
    if (document.hidden) { clock.getDelta(); return; }              // paused while the tab is hidden
    const raw = clock.getDelta();
    const dt = Math.min(0.05, raw);                 // smoothing step for poses and screens
    const move = Math.min(0.25, raw);               // walking uses real time, so slow frames do not slow the walk
    if (reduced && (t += dt) - last < 0.2) return;                    // reduced motion: a few frames a second, no idling
    last = t; if (!reduced) t += dt;
    const anim = reduced ? 0 : t;
    for (const a of agents.values()) {
      if (a.path) {
        const [tx, tz] = a.path[0], dx = tx - a.pos[0], dz = tz - a.pos[1], d = Math.hypot(dx, dz), v = move * 1.6;   // a brisk walk
        if (d < 0.0001 || d <= v) { a.pos = [tx, tz]; a.path.shift(); if (!a.path.length) { a.path = null; a.then(); } }
        else { a.pos[0] += dx / d * v; a.pos[1] += dz / d * v; a.face = Math.atan2(dx, dz); }
      }
      const root = a.body.root;
      root.position.set(a.pos[0], 0, a.pos[1] - (a.mode === "sitDesk" ? 0.02 : 0));
      let dr = a.face - root.rotation.y; dr = Math.atan2(Math.sin(dr), Math.cos(dr));
      root.rotation.y += dr * Math.min(1, dt * 10);
      const on = a.state === "working" && a.mode === "sitDesk";
      a.screen.emissiveIntensity = lerp(a.screen.emissiveIntensity, on ? 0.9 + Math.sin(anim * 3 + a.seed) * 0.15 : 0.05, Math.min(1, dt * 4));
      a.glow.material.opacity = lerp(a.glow.material.opacity, on ? 0.35 : 0, Math.min(1, dt * 4));
      applyPose(a, anim, reduced ? 1 : Math.min(1, dt * 8));
    }
    renderer.render(scene, camera);
    tags();
  });

  return {
    apply, poll: 0,
    dispose() {
      clearInterval(this.poll);
      renderer.setAnimationLoop(null);
      ro.disconnect();
      stage.removeEventListener("click", closeCard);
      scene.traverse(o => {
        if (o.geometry) o.geometry.dispose();
        const m = o.material; if (m) [].concat(m).forEach(x => { if (x.map) x.map.dispose(); x.dispose(); });
      });
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
