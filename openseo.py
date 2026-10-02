"""OpenSEO, run and managed by the dashboard.

OpenSEO (https://github.com/every-app/open-seo, MIT) is an open-source SEO
suite: keyword research, rank tracking, backlinks, domain overview, site
audits, AI search visibility, Search Console views, and an MCP server. It is
self-hosted here, on this machine, and shown inside the dashboard.

Its SEO data comes from DataForSEO (pay as you go). The dashboard already
holds DataForSEO credentials for its own keyword enrichment, so OpenSEO is
started with those same credentials: the operator enters the key once, on
the Integrations page, and both use it. Change it there and OpenSEO restarts
with the new key.

How it runs (the official Docker image's steps, natively, since this machine
has no Docker): database migrations, a production build (skipped while the
code and build settings are unchanged), then `vite preview` on 127.0.0.1.
Node runs open-seo's own tool scripts directly, so no global pnpm is needed.

Safety: OpenSEO's self-host mode has no login (AUTH_MODE=local_noauth), so it
only ever listens on 127.0.0.1. Its clickjacking guard (frame-ancestors
'self') is kept; openseo.patch adds one setting that lets this dashboard, and
only loopback origins, embed it. Telemetry is off.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent
OPENSEO_DIR = PROJECT_DIR / "openseo"
PATCH = PROJECT_DIR / "openseo.patch"
REPO = "https://github.com/every-app/open-seo.git"
PINNED = "db8bde1"                      # the commit this integration is built and tested against
PNPM = "pnpm@10.30.1"                   # the version open-seo's lockfile declares
STATE_DIR = PROJECT_DIR / "workspace" / "openseo"
LOG = STATE_DIR / "openseo.log"
PIDFILE = STATE_DIR / "server.json"
PORT = int(os.environ.get("OPENSEO_PORT", "3001"))
URL = f"http://127.0.0.1:{PORT}"
START_TIMEOUT = 180                     # seconds for vite preview to answer after the build

_lock = threading.Lock()
_state: dict[str, Any] = {"phase": "stopped", "error": "", "proc": None, "since": 0.0}


# ---------------------------------------------------------------- facts

def installed() -> bool:
    return (OPENSEO_DIR / "package.json").is_file() and (OPENSEO_DIR / "node_modules" / "vite").is_dir()


def _node() -> str:
    exe = shutil.which("node")
    if not exe:
        raise RuntimeError("Node.js is not installed (OpenSEO needs Node 20 or newer)")
    return exe


def _tool(*parts: str) -> str:
    return str(OPENSEO_DIR.joinpath("node_modules", *parts))


def _dataforseo_key() -> str:
    """The dashboard's DataForSEO credentials in OpenSEO's format: base64(login:password)."""
    try:
        import dataforseo
        c = dataforseo.credentials()
    except Exception:
        return ""
    if not c.get("login") or not c.get("password"):
        return ""
    return base64.b64encode(f"{c['login']}:{c['password']}".encode()).decode()


def _dashboard_port() -> int:
    return int(os.environ.get("MISSION_CONTROL_PORT", "51764"))


# OpenSEO's Cloudflare build copies its whole process environment into
# dist/*/.dev.vars, so it gets a clean one: what Node and Windows need to run,
# plus its own settings. Nothing else from the dashboard's environment (API
# keys, session tokens) can end up in its files.
_PASS = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "HOME",
         "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
         "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "LANG", "TZ", "XDG_CONFIG_HOME",
         "XDG_CACHE_HOME", "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")


def _env() -> dict[str, str]:
    up = {k.upper(): v for k, v in os.environ.items()}
    env = {k: up[k] for k in _PASS if k in up}
    env.update({
        "AUTH_MODE": "local_noauth",
        "CLOUDFLARE_INCLUDE_PROCESS_ENV": "true",
        "PORT": str(PORT),
        "OPENSEO_TELEMETRY_DISABLED": "1",
        "DO_NOT_TRACK": "1",
        "VITE_SHOW_DEVTOOLS": "false",
        "NODE_OPTIONS": "--max-old-space-size=4096",     # the SSR build needs more than Node's default heap
        "OPENSEO_FRAME_ANCESTORS": f"http://127.0.0.1:{_dashboard_port()} http://localhost:{_dashboard_port()}",
    })
    key = _dataforseo_key()
    if key:
        env["DATAFORSEO_API_KEY"] = key
    else:
        env.pop("DATAFORSEO_API_KEY", None)
    return env


def _key_fingerprint() -> str:
    return hashlib.sha256(_dataforseo_key().encode()).hexdigest()[:16]


def _build_fingerprint() -> str:
    """What changes the build output: the code (commit + local patch) and build-time settings."""
    h = hashlib.sha256()
    try:
        head = subprocess.run(["git", "-C", str(OPENSEO_DIR), "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception:
        head = ""
    h.update(head.encode())
    for rel in ("src/server.ts", "package.json", "pnpm-lock.yaml", "vite.config.ts"):
        f = OPENSEO_DIR / rel
        if f.is_file():
            h.update(f.read_bytes())
    h.update(b"AUTH_MODE=local_noauth;VITE_SHOW_DEVTOOLS=false")
    return h.hexdigest()


def _write_dev_vars() -> None:
    """`vite preview` reads its variables from dist/*/.dev.vars, which the build
    writes once. Rewrite them from the clean environment before every start,
    so a DataForSEO key added or changed later applies without a rebuild."""
    env = {**_env(), "CLOUDFLARE_VITE_BUILD": "true", "NODE_ENV": "production"}
    env.pop("NODE_OPTIONS", None)

    def quote(v: str) -> str:
        return "'" + v + "'" if "'" not in v else '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    text = "".join(f"{k}={quote(v)}\n" for k, v in sorted(env.items()) if "\n" not in v)
    for target in ("server", "open_seo_audit"):
        d = OPENSEO_DIR / "dist" / target
        if d.is_dir():
            (d / ".dev.vars").write_text(text, encoding="utf-8")


def _build_marker() -> Path:
    return OPENSEO_DIR / "dist" / ".gem-build"


def built() -> bool:
    m = _build_marker()
    return m.is_file() and m.read_text(encoding="utf-8").strip() == _build_fingerprint()


def health(timeout: float = 2.0) -> dict[str, Any] | None:
    """OpenSEO's own setup/health report, or None when nothing answers."""
    try:
        with urllib.request.urlopen(URL + "/api/health", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
            return data if isinstance(data, dict) and "status" in data else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


# ---------------------------------------------------------------- processes

def _log(line: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line.rstrip() + "\n")


def _run(args: list[str], phase: str, timeout: int) -> None:
    _state["phase"] = phase
    _log(f"$ {' '.join(args)}")
    with LOG.open("a", encoding="utf-8") as out:
        r = subprocess.run(args, cwd=OPENSEO_DIR, env=_env(), stdout=out, stderr=subprocess.STDOUT,
                           timeout=timeout, stdin=subprocess.DEVNULL, **_no_window())
    if r.returncode != 0:
        raise RuntimeError(f"{phase} failed (exit {r.returncode}); see {LOG}")


def _no_window() -> dict[str, Any]:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def _kill_tree(pid: int) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=20)
        else:
            os.killpg(os.getpgid(pid), 15)
    except Exception:
        pass


def _spawn(target, name: str) -> None:
    threading.Thread(target=target, name=name, daemon=True).start()


def _recorded() -> dict[str, Any]:
    try:
        return json.loads(PIDFILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _start_worker() -> None:
    try:
        node = _node()
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _run([node, _tool("wrangler", "bin", "wrangler.js"), "d1", "migrations", "apply", "DB", "--local"],
             "migrating", 300)
        if not built():
            _run([node, _tool("vite", "bin", "vite.js"), "build"], "building", 1500)
            _build_marker().write_text(_build_fingerprint(), encoding="utf-8")
        _state["phase"] = "starting"
        _write_dev_vars()
        log = LOG.open("a", encoding="utf-8")
        kw: dict[str, Any] = dict(cwd=OPENSEO_DIR, env=_env(), stdout=log, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, **_no_window())
        if sys.platform != "win32":
            kw["start_new_session"] = True
        proc = subprocess.Popen([node, _tool("vite", "bin", "vite.js"), "preview",
                                 "--host", "127.0.0.1", "--port", str(PORT), "--strictPort"], **kw)
        _state["proc"] = proc
        PIDFILE.write_text(json.dumps({"pid": proc.pid, "key": _key_fingerprint(), "port": PORT}), encoding="utf-8")
        deadline = time.time() + START_TIMEOUT
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"OpenSEO exited while starting (code {proc.returncode}); see {LOG}")
            if health():
                _state.update(phase="running", error="")
                _log(f"OpenSEO is up at {URL}")
                return
            time.sleep(1.5)
        raise RuntimeError(f"OpenSEO did not answer within {START_TIMEOUT} s; see {LOG}")
    except Exception as exc:
        _state.update(phase="failed", error=str(exc)[:500])
        _log(f"FAILED: {exc}")
        proc = _state.get("proc")
        if proc and proc.poll() is None:
            _kill_tree(proc.pid)


def start() -> dict[str, Any]:
    """Start OpenSEO if it is not already up. Returns immediately; poll status()."""
    with _lock:
        if not installed():
            raise ValueError("OpenSEO is not installed yet: run Install first")
        if _state["phase"] in ("migrating", "building", "starting", "installing"):
            return status()
        rec = _recorded()
        if health():
            if rec.get("key") and rec.get("key") != _key_fingerprint():
                # running with an old DataForSEO key: restart it with the current one
                _kill_tree(int(rec.get("pid") or 0))
                time.sleep(1.5)
            else:
                _state.update(phase="running", error="")
                return status()
        _state.update(phase="starting", error="", since=time.time())
        _spawn(_start_worker, "openseo-start")
    return status()


def stop() -> dict[str, Any]:
    with _lock:
        proc = _state.get("proc")
        pid = proc.pid if proc and proc.poll() is None else int(_recorded().get("pid") or 0)
        if pid:
            _kill_tree(pid)
        PIDFILE.unlink(missing_ok=True)
        _state.update(phase="stopped", error="", proc=None)
    return status()


def restart_if_running() -> None:
    """After the DataForSEO key changes: a running OpenSEO picks the new key up."""
    if health(timeout=1.0) or _state["phase"] == "running":
        stop()
        time.sleep(1.0)
        start()


# ---------------------------------------------------------------- install

def _install_worker() -> None:
    try:
        git = shutil.which("git")
        if not git:
            raise RuntimeError("git is not installed")
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _state["phase"] = "installing"
        if not (OPENSEO_DIR / ".git").is_dir():
            OPENSEO_DIR.mkdir(parents=True, exist_ok=True)
            for args in (["init", "-q"], ["remote", "add", "origin", REPO],
                         ["fetch", "-q", "--depth", "1", "origin", PINNED], ["checkout", "-q", "FETCH_HEAD"]):
                r = subprocess.run([git, "-C", str(OPENSEO_DIR), *args], capture_output=True, text=True, timeout=600)
                if r.returncode:
                    raise RuntimeError(f"git {args[0]} failed: {r.stderr.strip()[:300]}")
            if PATCH.is_file():
                r = subprocess.run([git, "-C", str(OPENSEO_DIR), "apply", str(PATCH)], capture_output=True,
                                   text=True, timeout=60)
                if r.returncode:
                    raise RuntimeError(f"applying openseo.patch failed: {r.stderr.strip()[:300]}")
        corepack = shutil.which("corepack")
        if not corepack:
            raise RuntimeError("corepack is missing (it ships with Node.js 20-24)")
        env = dict(os.environ, COREPACK_ENABLE_DOWNLOAD_PROMPT="0")
        with LOG.open("a", encoding="utf-8") as out:
            r = subprocess.run([corepack, PNPM, "install", "--frozen-lockfile"], cwd=OPENSEO_DIR, env=env,
                               stdout=out, stderr=subprocess.STDOUT, timeout=1800, **_no_window())
        if r.returncode:
            raise RuntimeError(f"installing packages failed (exit {r.returncode}); see {LOG}")
        _state.update(phase="stopped", error="")
        _log("Installed.")
    except Exception as exc:
        _state.update(phase="failed", error=str(exc)[:500])
        _log(f"INSTALL FAILED: {exc}")


def install() -> dict[str, Any]:
    """Download OpenSEO at the pinned commit, apply the local patch, install its packages."""
    with _lock:
        if installed():
            return status()
        if _state["phase"] == "installing":
            return status()
        _state.update(phase="installing", error="", since=time.time())
        _spawn(_install_worker, "openseo-install")
    return status()


# ---------------------------------------------------------------- status

def _log_tail(n: int = 12) -> list[str]:
    try:
        lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [ln[-300:] for ln in lines[-n:]]


def status() -> dict[str, Any]:
    h = health(timeout=1.5)
    phase = _state["phase"]
    if h and phase in ("stopped", "failed", "starting"):
        phase = "running"                    # answering is the truth, whoever started it
    elif not h and phase == "running":
        phase = "stopped"                    # it was stopped or crashed outside the dashboard
    checks = (h or {}).get("checks") or {}
    return {
        "installed": installed(),
        "built": installed() and built(),
        "phase": phase,
        "running": bool(h),
        "error": _state["error"],
        "url": URL,
        "port": PORT,
        "commit": PINNED,
        "dataforseo": bool(_dataforseo_key()),
        "health": h.get("status") if h else "",
        "checks": {k: {"status": v.get("status", ""), "message": str(v.get("message", ""))[:300]}
                   for k, v in checks.items() if isinstance(v, dict)},
        "log": _log_tail() if phase in ("failed", "installing", "migrating", "building", "starting") else [],
        "busy_for": int(time.time() - _state["since"]) if phase in ("installing", "migrating", "building", "starting") else 0,
    }
