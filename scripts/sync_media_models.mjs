// Sync the Image & Video model catalogue from Open Generative AI.
//
//   node scripts/sync_media_models.mjs [git-ref]
//
// Upstream: https://github.com/Anil-matcha/Open-Generative-AI (MIT).
// Its packages/studio/src/models.js is the catalogue of Muapi.ai models it
// verifies against api.muapi.ai/openapi.json. That file imports small helper
// modules, so this script fetches it together with its relative imports into
// a temp directory, evaluates it, and writes the lists the dashboard uses to
// data/media_models.json. The dashboard never runs Node; this is a one-off
// maintenance step, re-run to pick up new models.
import { mkdtemp, writeFile, mkdir, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, dirname, posix } from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";

const REPO = "Anil-matcha/Open-Generative-AI";
const REF = process.argv[2] || "main";
const BASE = "packages/studio/src";
const RAW = p => `https://raw.githubusercontent.com/${REPO}/${REF}/${p}`;

const tmp = await mkdtemp(join(tmpdir(), "ogai-"));
const seen = new Set();

async function fetchWithImports(path) {
  if (seen.has(path)) return;
  seen.add(path);
  const res = await fetch(RAW(path));
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  const src = await res.text();
  const out = join(tmp, path);
  await mkdir(dirname(out), { recursive: true });
  await writeFile(out, src);
  for (const m of src.matchAll(/from\s+["'](\.{1,2}\/[^"']+)["']/g)) {
    await fetchWithImports(posix.normalize(posix.join(posix.dirname(path), m[1])));
  }
}

await fetchWithImports(`${BASE}/models.js`);
const mod = await import(pathToFileURL(join(tmp, BASE, "models.js")).href);

// What this dashboard leaves out, and why. It makes assets for client
// websites and campaigns, so:
//   * "Spicy" routes are the providers' adult-content variants; each has a
//     standard version that stays in the catalogue.
//   * Face swap puts a real person's face onto other imagery -- the core
//     deepfake operation -- with no consent step.
//   * Effect presets that turn an uploaded photo of someone into intimate or
//     sexualised footage of them.
// Everything else upstream ships is kept. Edit these to change the policy.
const EXCLUDE_MODEL = /spicy|face[-_ ]?swap/i;
const EXCLUDE_OPTION = /\bkiss|kissing|sexy\b|^spicy$/i;   // "spicy" is Grok Imagine's adult mode
const excluded = { models: [], options: [] };

// Keep what the dashboard renders and what the API call needs; drop prompt
// examples and trim long descriptions (the file is ~1 MB upstream).
function slim(model, kind) {
  const inputs = {};
  for (const [k, v] of Object.entries(model.inputs || {})) {
    if (!v || typeof v !== "object") continue;
    const { examples, ...rest } = v;
    if (Array.isArray(rest.enum)) {
      const kept = rest.enum.filter(e => !(typeof e === "string" && EXCLUDE_OPTION.test(e)));
      if (kept.length !== rest.enum.length) {
        excluded.options.push(`${model.id}.${k}: ${rest.enum.filter(e => !kept.includes(e)).join(", ")}`);
        rest.enum = kept;
        if (rest.default != null && !kept.includes(rest.default)) rest.default = kept[0];
      }
    }
    if (typeof rest.description === "string") rest.description = rest.description.slice(0, 220);
    inputs[k] = rest;
  }
  const keep = { id: model.id, name: model.name, endpoint: model.endpoint || model.id, kind, inputs };
  for (const k of ["provider", "provider_name", "imageField", "maxImages", "family", "description"]) {
    if (model[k] != null) keep[k] = typeof model[k] === "string" ? model[k].slice(0, 220) : model[k];
  }
  return keep;
}

const LISTS = {
  "text-to-image": mod.t2iModels,
  "image-to-image": mod.i2iModels,
  "text-to-video": mod.t2vModels,
  "image-to-video": mod.i2vModels,
};
const models = [];
for (const [kind, list] of Object.entries(LISTS)) {
  if (!Array.isArray(list)) throw new Error(`upstream no longer exports the ${kind} list`);
  for (const m of list) {
    if (!m || !m.id) continue;
    if (EXCLUDE_MODEL.test(`${m.id} ${m.name || ""}`)) { excluded.models.push(m.id); continue; }
    models.push(slim(m, kind));
  }
}

const here = dirname(fileURLToPath(import.meta.url));
const target = join(here, "..", "data", "media_models.json");
await mkdir(dirname(target), { recursive: true });
const payload = {
  source: `https://github.com/${REPO}`,
  license: "MIT",
  ref: REF,
  synced_at: new Date().toISOString(),
  api: "https://api.muapi.ai",
  counts: Object.fromEntries(Object.keys(LISTS).map(k => [k, models.filter(m => m.kind === k).length])),
  excluded,
  models,
};
await writeFile(target, JSON.stringify(payload));
console.log(`wrote ${models.length} models to data/media_models.json`, payload.counts,
  `(excluded ${excluded.models.length} models, ${excluded.options.length} option sets)`);
