"""Image & Video studio: generation through Muapi.ai, from Open Generative AI.

Integrates https://github.com/Anil-matcha/Open-Generative-AI (MIT). That
project is a front end for Muapi.ai: its "600+ models" (Flux, Seedream,
Kling, Veo, Sora, Wan, Seedance, ...) run on Muapi's servers, reached with an
x-api-key header on a submit-then-poll protocol. This module speaks the same
protocol with the same model catalogue (synced into data/media_models.json by
scripts/sync_media_models.mjs), so generation lives inside the dashboard: the
key stays on the server, results are saved next to the projects they are for,
and Jarvis can load a brief.

Be clear about where work happens: prompts and any reference images go to
muapi.ai and are billed to the operator's Muapi account. Nothing here runs a
model locally. (Upstream's own local mode only works inside its Electron app.)

Safety of the catalogue is decided at sync time -- see the exclusions in the
sync script. Here, only a model that is in the synced catalogue can be
called, and only with the inputs that model declares.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent
CATALOG_PATH = PROJECT_DIR / "data" / "media_models.json"
CREDS_PATH = PROJECT_DIR / ".muapi_credentials.json"
MEDIA_DIR = PROJECT_DIR / "workspace" / "media"
API = "https://api.muapi.ai"

POLL_EVERY = 2.0
IMAGE_TIMEOUT = 5 * 60
VIDEO_TIMEOUT = 30 * 60
MAX_REFERENCE_BYTES = 12 * 1024 * 1024
MAX_PROMPT = 4000
KINDS = ("text-to-image", "image-to-image", "text-to-video", "image-to-video")
_SUCCESS = {"completed", "succeeded", "success"}
_FAILURE = {"failed", "error", "cancelled", "canceled"}
# Inputs that carry media. Values are URLs Muapi can fetch -- either hosted
# already, or a reference the operator uploaded through upload_reference().
MEDIA_INPUTS = {"image_url", "images_list", "image_urls", "last_image", "last_image_url", "first_image",
                "end_image_url", "first_frame_url", "last_frame_url", "image", "reference_images",
                "video_url", "videos_list", "video_files", "reference_videos", "reference_video_urls",
                "audio_url", "audios_list", "audio_files", "reference_audios"}


class MediaError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- key

def _key() -> str:
    try:
        stored = json.loads(CREDS_PATH.read_text(encoding="utf-8")).get("api_key", "")
    except (FileNotFoundError, json.JSONDecodeError):
        stored = ""
    return stored or os.environ.get("MUAPI_API_KEY", "")


def save_key(api_key: str) -> None:
    k = (api_key or "").strip()
    if not k:
        raise ValueError("paste your Muapi API key")
    if len(k) < 16 or re.search(r"\s", k):
        raise ValueError("that does not look like a Muapi API key")
    CREDS_PATH.write_text(json.dumps({"api_key": k}), encoding="utf-8")


def clear_key() -> None:
    try:
        CREDS_PATH.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------- catalogue

_CATALOG: dict[str, Any] = {"mtime": 0.0, "data": None}


def catalog() -> dict[str, Any]:
    try:
        mtime = CATALOG_PATH.stat().st_mtime
    except FileNotFoundError:
        raise MediaError("the model catalogue is missing; run: node scripts/sync_media_models.mjs") from None
    if _CATALOG["data"] is None or mtime != _CATALOG["mtime"]:
        _CATALOG["data"] = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        _CATALOG["mtime"] = mtime
        _CATALOG["by_id"] = {m["id"]: m for m in _CATALOG["data"]["models"]}
    return _CATALOG["data"]


def model(model_id: str) -> dict[str, Any]:
    catalog()
    m = _CATALOG["by_id"].get(str(model_id or ""))
    if not m:
        raise ValueError(f"'{model_id}' is not in the model catalogue")
    return m


def status() -> dict[str, Any]:
    try:
        c = catalog()
        cat = {"count": len(c["models"]), "counts": c.get("counts", {}), "synced_at": c.get("synced_at", ""),
               "source": c.get("source", ""), "license": c.get("license", "")}
        err = ""
    except MediaError as exc:
        cat, err = {"count": 0}, str(exc)
    return {"connected": bool(_key()), "key_source": "stored" if CREDS_PATH.exists() else ("environment" if _key() else ""),
            "catalog": cat, "error": err, "api": API,
            "running": sum(1 for j in _JOBS.values() if j["status"] in ("queued", "running"))}


# ---------------------------------------------------------------- HTTP

def _tls_context() -> ssl.SSLContext:
    """Verify certificates with the operating system, as Chrome and curl do.

    Muapi's certificate chains to Let's Encrypt's newer Root YE. Python's
    OpenSSL builds that chain from the Windows store, picks up an expired
    cross-certificate and rejects a valid site ("certificate has expired").
    The OS verifier builds the chain correctly. Expired or untrusted
    certificates are still refused.
    """
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


_TLS = _tls_context()


def _request(method: str, path: str, body: bytes | None = None, headers: dict | None = None,
             timeout: float = 60) -> dict[str, Any]:
    key = _key()
    if not key:
        raise MediaError("Muapi is not connected. Add your API key in the Image & Video section.")
    h = {"x-api-key": key, "Accept": "application/json", **(headers or {})}
    req = urllib.request.Request(API + path, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_TLS) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise MediaError(f"Muapi {exc.code}: {_detail(detail)}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise MediaError(f"Muapi unreachable: {getattr(exc, 'reason', exc)}") from None
    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        raise MediaError(f"Muapi returned something that is not JSON: {raw[:160]}") from None


_NO_CREDITS = "your Muapi account has no credits. Top up at muapi.ai, then try again (uploads need a balance too)"


def _detail(text: str) -> str:
    """The readable message in a Muapi error body, however deeply it is nested.

    Seen in the wild: {"detail": {"error": {"code": ..., "message": ...}}},
    plain {"detail": "..."}, and JSON encoded inside a string.
    """
    def walk(v: Any, depth: int = 0) -> str:
        if depth > 5 or v is None:
            return ""
        if isinstance(v, str):
            if v.lstrip()[:1] in ("{", '"'):
                try:
                    return walk(json.loads(v), depth + 1)
                except json.JSONDecodeError:
                    pass
            return v
        if isinstance(v, dict):
            if v.get("code") == "INSUFFICIENT_CREDITS":
                return _NO_CREDITS
            for k in ("detail", "error", "message"):
                found = walk(v.get(k), depth + 1)
                if found:
                    return found
        return ""
    return (walk(text) or text.strip())[:300] or "no detail"


# ---------------------------------------------------------------- uploads

def upload_reference(filename: str, data_b64: str) -> str:
    """Upload a reference image or clip to Muapi; returns the hosted URL."""
    raw = base64.b64decode((data_b64 or "").split(",", 1)[-1], validate=False)
    if not raw:
        raise ValueError("the file is empty")
    if len(raw) > MAX_REFERENCE_BYTES:
        raise ValueError(f"the file is over {MAX_REFERENCE_BYTES // (1024 * 1024)} MB")
    name = re.sub(r"[^\w.\-]", "_", Path(filename or "reference").name)[:80] or "reference"
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if not mime.startswith(("image/", "video/", "audio/")):
        raise ValueError("upload an image, video or audio file")
    boundary = "----gem" + uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n").encode() + raw + f"\r\n--{boundary}--\r\n".encode()
    d = _request("POST", "/api/v1/upload_file", body,
                 {"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=120)
    url = d.get("url") or d.get("file_url") or (d.get("data") or {}).get("url")
    if not url:
        raise MediaError("Muapi accepted the upload but returned no URL")
    return url


# ---------------------------------------------------------------- building a request

def effective_inputs(m: dict[str, Any]) -> dict[str, Any]:
    """The model's inputs, including the image it starts from.

    About a hundred image-to-image and image-to-video models do not list their
    image in `inputs`; they name the field in a model-level `imageField` (and
    cap it with `maxImages`). Upstream's client reads it the same way
    (`modelInfo.imageField || 'image_url'`).
    """
    inputs = dict(m.get("inputs") or {})
    if m.get("kind", "").startswith("image-"):
        field = m.get("imageField") or ("image_url" if not any(k in MEDIA_INPUTS for k in inputs) else None)
        if field and field not in inputs:
            many = field.endswith("_list") or field.endswith("s")
            inputs[field] = {"name": field, "type": "array" if many else "string",
                             "title": "Images" if many else "Image", "maxItems": m.get("maxImages") or (4 if many else 1),
                             "description": "The picture to start from"}
    return inputs


def is_media(name: str, m: dict[str, Any] | None = None) -> bool:
    return name in MEDIA_INPUTS or bool(m and name == m.get("imageField"))


def build_payload(m: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Only inputs the model declares, coerced and checked against its schema."""
    inputs = effective_inputs(m)
    out: dict[str, Any] = {}
    unknown = [k for k in params if k not in inputs]
    if unknown:
        raise ValueError(f"{m['name']} does not take: {', '.join(sorted(unknown))}")
    for name, spec in inputs.items():
        if name not in params or params[name] in (None, "", []):
            continue
        v = params[name]
        typ = str(spec.get("type", "")).lower()
        if name == "prompt" or name == "negative_prompt":
            v = str(v).strip()
            if len(v) > MAX_PROMPT:
                raise ValueError(f"{spec.get('title', name)} is over {MAX_PROMPT} characters")
        elif is_media(name, m):
            vals = v if isinstance(v, list) else [v]
            for u in vals:
                if not re.match(r"^https://", str(u)):
                    raise ValueError(f"{spec.get('title', name)} must be an uploaded file or an https:// URL")
            if spec.get("maxItems") and len(vals) > int(spec["maxItems"]):
                raise ValueError(f"{spec.get('title', name)} takes at most {spec['maxItems']}")
            v = vals if typ in ("array", "list") else vals[0]
        elif typ in ("array", "list"):
            if not isinstance(v, list):
                raise ValueError(f"{spec.get('title', name)} must be a list")
        elif typ in ("int", "integer"):
            v = int(v)
        elif typ in ("float", "number"):
            v = float(v)
        elif typ in ("bool", "boolean"):
            v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
        else:
            v = str(v)
        enum = spec.get("enum")
        if enum and not isinstance(v, list) and v not in enum and str(v) not in [str(e) for e in enum]:
            raise ValueError(f"{spec.get('title', name)} must be one of: {', '.join(map(str, enum[:20]))}")
        for bound, cmp in (("minValue", lambda a, b: a < b), ("maxValue", lambda a, b: a > b)):
            if isinstance(v, (int, float)) and not isinstance(v, bool) and spec.get(bound) is not None \
                    and cmp(v, spec[bound]):
                raise ValueError(f"{spec.get('title', name)} must be between {spec.get('minValue')} and {spec.get('maxValue')}")
        out[name] = v
    if "prompt" in inputs and not out.get("prompt") and m["kind"].startswith("text-"):
        raise ValueError("write a prompt first")
    needs_media = m["kind"].startswith("image-") and not any(is_media(k, m) for k in out)
    if needs_media:
        raise ValueError("this model starts from an image: upload one first")
    return out


# ---------------------------------------------------------------- jobs

_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def _job_path(job_id: str) -> Path:
    return MEDIA_DIR / "jobs" / f"{job_id}.json"


def _save(job: dict[str, Any]) -> None:
    p = _job_path(job["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(job, indent=2), encoding="utf-8")


def generate(model_id: str, params: dict[str, Any], project_id: str = "", note: str = "") -> dict[str, Any]:
    if not _key():
        raise ValueError("Muapi is not connected. Add your API key first.")
    m = model(model_id)
    payload = build_payload(m, dict(params or {}))
    job = {"id": uuid.uuid4().hex[:12], "model": m["id"], "model_name": m["name"], "kind": m["kind"],
           "provider": m.get("provider_name", ""), "endpoint": m["endpoint"], "params": payload,
           "prompt": payload.get("prompt", ""), "project_id": project_id or "", "note": note[:300],
           "status": "queued", "request_id": "", "outputs": [], "files": [], "error": "",
           "created_at": _now(), "finished_at": "", "cost": None}
    with _LOCK:
        running = sum(1 for j in _JOBS.values() if j["status"] in ("queued", "running"))
        if running >= 4:
            raise ValueError("four generations are already running; wait for one to finish")
        _JOBS[job["id"]] = job
    _save(job)
    threading.Thread(target=_run, args=(job,), name=f"media-{job['id']}", daemon=True).start()
    return job


def _run(job: dict[str, Any]) -> None:
    try:
        job["status"] = "running"
        submit = _request("POST", f"/api/v1/{job['endpoint']}", json.dumps(job["params"]).encode(),
                          {"Content-Type": "application/json"}, timeout=120)
        rid = submit.get("request_id") or submit.get("id")
        job["request_id"] = rid or ""
        _save(job)
        result = submit if not rid else _poll(job, rid)
        urls = _output_urls(result)
        if not urls:
            raise MediaError("the model finished but returned no files")
        job["outputs"] = urls
        job["cost"] = result.get("cost") if isinstance(result.get("cost"), dict) else None
        job["files"] = [_download(job, u, i) for i, u in enumerate(urls)]
        job["status"] = "done"
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)[:500]
    finally:
        job["finished_at"] = _now()
        _save(job)


def _poll(job: dict[str, Any], rid: str) -> dict[str, Any]:
    limit = VIDEO_TIMEOUT if "video" in job["kind"] else IMAGE_TIMEOUT
    started = time.time()
    while time.time() - started < limit:
        if job.get("cancel"):
            raise MediaError("cancelled in the dashboard (Muapi may still finish and bill it)")
        time.sleep(POLL_EVERY)
        try:
            r = _request("GET", f"/api/v1/predictions/{rid}/result", timeout=60)
        except MediaError as exc:
            if re.search(r"Muapi 5\d\d|unreachable", str(exc)):
                continue                           # transient: keep polling, as upstream does
            raise
        st = str(r.get("status", "")).lower()
        job["progress"] = st
        if st in _SUCCESS:
            return r
        if st in _FAILURE:
            err = r.get("error")
            msg = err.get("message") if isinstance(err, dict) else err or r.get("message") or "no detail"
            refund = " The cost was refunded." if isinstance(r.get("cost"), dict) and r["cost"].get("refunded") else ""
            raise MediaError(f"generation failed: {msg}.{refund}")
    raise MediaError(f"still not finished after {limit // 60} minutes (request {rid}); it may complete on muapi.ai")


def _output_urls(result: dict[str, Any]) -> list[str]:
    outs = result.get("outputs")
    urls = [o if isinstance(o, str) else (o or {}).get("url") for o in outs] if isinstance(outs, list) else []
    urls += [result.get("url"), (result.get("output") or {}).get("url") if isinstance(result.get("output"), dict) else None]
    return [u for u in dict.fromkeys(urls) if isinstance(u, str) and u.startswith("http")]


_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif",
        "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}


def _download(job: dict[str, Any], url: str, i: int) -> str:
    """Keep a local copy: hosted result URLs expire, and client work shouldn't."""
    folder = MEDIA_DIR / job["id"]
    folder.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "GemAgency/1.0"})
    with urllib.request.urlopen(req, timeout=300, context=_TLS) as resp:
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        ext = _EXT.get(ctype) or Path(url.split("?")[0]).suffix.lower() or ".bin"
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm", ".mov"):
            ext = ".bin"
        path = folder / f"output-{i + 1}{ext}"
        with open(path, "wb") as f:
            while chunk := resp.read(1 << 16):
                f.write(chunk)
    return str(path)


def job(job_id: str) -> dict[str, Any]:
    j = _JOBS.get(job_id)
    if j:
        return j
    p = _job_path(re.sub(r"[^a-f0-9]", "", job_id or ""))
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    raise KeyError(f"no media job {job_id}")


def cancel(job_id: str) -> dict[str, Any]:
    j = _JOBS.get(job_id)
    if not j or j["status"] not in ("queued", "running"):
        raise KeyError(f"no running media job {job_id}")
    j["cancel"] = True
    return j


def jobs(limit: int = 60, project_id: str = "") -> list[dict[str, Any]]:
    rows = {j["id"]: j for j in _JOBS.values()}
    folder = MEDIA_DIR / "jobs"
    if folder.is_dir():
        for p in folder.glob("*.json"):
            if p.stem not in rows:
                try:
                    rows[p.stem] = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
    out = sorted(rows.values(), key=lambda r: r.get("created_at", ""), reverse=True)
    if project_id:
        out = [r for r in out if r.get("project_id") == project_id]
    return out[:limit]


# ---------------------------------------------------------------- the brain helps

def improve_prompt(prompt: str, kind: str) -> dict[str, Any]:
    """Turn a rough idea into a production prompt. Creative work: the brain chain."""
    import providers
    idea = " ".join(str(prompt or "").split())
    if len(idea) < 3:
        raise ValueError("write a few words first")
    medium = "a short video clip" if "video" in kind else "an image"
    res = providers.complete(
        f"Rewrite this idea as one production-ready prompt for an AI model that makes {medium} for a "
        f"business website or campaign. Be concrete about subject, setting, composition, lighting, lens "
        f"and style{', and camera movement and pacing' if 'video' in kind else ''}. No text or logos in "
        f"the frame unless the idea asks for them. Do not state duration, resolution, frame rate or aspect "
        f"ratio: the model's settings control those, and a prompt that contradicts them confuses it. "
        f"Reply with the prompt only.\n\nIdea: {idea[:1500]}",
        provider=providers.BRAIN, fallback=True, timeout=90)
    return {"prompt": res["text"].strip().strip('"'), "provider": res.get("provider"), "model": res.get("model")}


def seo_metadata(job_id: str) -> dict[str, Any]:
    """Alt text and a file name for search. SEO work: Claude or ChatGPT only."""
    import providers
    j = job(job_id)
    if j.get("status") != "done":
        raise ValueError("generate the asset first")
    res = providers.complete(
        "Write search-ready metadata for a generated website asset. Reply as JSON only: "
        '{"alt": "<alt text under 125 characters, describing what is shown>", '
        '"filename": "<lowercase-hyphenated-descriptive-name without extension>", '
        '"caption": "<one plain sentence>"}\n\n'
        f"Asset type: {j['kind']}\nWhat it was generated from: {j.get('prompt', '')[:1500]}",
        provider=providers.CLOUD, fallback=True, json_mode=True, timeout=90)
    import llm
    data = llm.extract_json(res["text"])
    meta = {"alt": str(data.get("alt", ""))[:180], "filename": re.sub(r"[^a-z0-9-]", "", str(data.get("filename", "")).lower())[:80],
            "caption": str(data.get("caption", ""))[:240], "by": f"{res.get('provider')} · {res.get('model', '')}"}
    j["seo"] = meta
    _save(j)
    return meta
