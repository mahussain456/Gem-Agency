"""Laya, the local decision engine, run and managed by the dashboard.

Laya (https://github.com/NandhaKishorM/laya, Apache-2.0) answers typed
questions (yes/no, pick one, score a rubric) about a piece of text in one
forward pass, with calibrated probabilities and no generated prose. Its HTTP
server speaks TypeSafe Jev's /v1/systemone protocol, so jev.py talks to it
exactly as it talks to Jev's cloud API: Laya is the free, local default and
Jev is used only when a TypeSafe key is added and Laya is unavailable.

How it runs on this machine
  * Its packages (PyTorch CPU build, transformers) live in laya-pkgs/, apart
    from the dashboard's own environment, and run on the dashboard's Python.
    A separate virtual environment is not possible here: Windows Application
    Control blocks the copied python.exe a new environment needs.
  * Model weights download from Hugging Face on first use into
    workspace/laya/hf. Checkpoints load on demand and unload after 15 idle
    minutes, so an idle Laya holds little memory.
  * It listens on 127.0.0.1 only and requires a bearer token that the
    dashboard generates and keeps in workspace/laya/token.
  * It gets an allowlisted environment (no API keys or session tokens), and
    Hugging Face telemetry is off.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent
PKGS = PROJECT_DIR / "laya-pkgs"
STATE_DIR = PROJECT_DIR / "workspace" / "laya"
HF_HOME = STATE_DIR / "hf"
TOKEN_FILE = STATE_DIR / "token"
LOG = STATE_DIR / "laya.log"
PIDFILE = STATE_DIR / "server.json"
VERSION = "0.4.0"                       # the release this integration is built and tested against
PORT = int(os.environ.get("LAYA_ENGINE_PORT", "8017"))
URL = f"http://127.0.0.1:{PORT}"
START_TIMEOUT = 90                      # the server itself; models load on the first decision

_lock = threading.Lock()
_state: dict[str, Any] = {"phase": "stopped", "error": "", "proc": None, "since": 0.0}

_PASS = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "HOME",
         "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "NUMBER_OF_PROCESSORS",
         "PROCESSOR_ARCHITECTURE", "OS", "LANG", "TZ", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
         "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")


# ---------------------------------------------------------------- facts

def installed() -> bool:
    return (PKGS / "laya" / "serve.py").is_file() and (PKGS / "torch").is_dir()


def token() -> str:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not TOKEN_FILE.is_file():
        TOKEN_FILE.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


def _env() -> dict[str, str]:
    up = {k.upper(): v for k, v in os.environ.items()}
    env = {k: up[k] for k in _PASS if k in up}
    env.update({
        "PYTHONPATH": str(PKGS),
        "PYTHONNOUSERSITE": "1",
        "HF_HOME": str(HF_HOME),
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",   # Windows without Developer Mode copies instead
        "LAYA_HOST": "127.0.0.1",
        "LAYA_PORT": str(PORT),
        "LAYA_API_KEY": token(),
        "LAYA_PRELOAD": "0",            # load a checkpoint when a decision needs it
        "LAYA_MAX_LOADED": "1",         # one checkpoint resident at a time keeps memory modest
        "LAYA_IDLE_UNLOAD_SECONDS": "900",
        "LAYA_LOG_LEVEL": "warning",
    })
    return env


def health(timeout: float = 2.0, detail: bool = False) -> dict[str, Any] | None:
    """Laya's /health, or None when nothing answers. With the token it includes device detail."""
    req = urllib.request.Request(URL + "/health",
                                 headers={"Authorization": f"Bearer {token()}"} if detail else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
            return data if isinstance(data, dict) else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


# ---------------------------------------------------------------- processes

def _log(line: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line.rstrip() + "\n")


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
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        log = LOG.open("a", encoding="utf-8")
        kw: dict[str, Any] = dict(cwd=STATE_DIR, env=_env(), stdout=log, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, **_no_window())
        if sys.platform != "win32":
            kw["start_new_session"] = True
        proc = subprocess.Popen([sys.executable, "-c", "from laya.serve import main; main()"], **kw)
        _state["proc"] = proc
        PIDFILE.write_text(json.dumps({"pid": proc.pid, "port": PORT}), encoding="utf-8")
        deadline = time.time() + START_TIMEOUT
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"Laya exited while starting (code {proc.returncode}); see {LOG}")
            if health():
                _state.update(phase="running", error="")
                _log(f"Laya is up at {URL}")
                return
            time.sleep(1.0)
        raise RuntimeError(f"Laya did not answer within {START_TIMEOUT} s; see {LOG}")
    except Exception as exc:
        _state.update(phase="failed", error=str(exc)[:500])
        _log(f"FAILED: {exc}")
        proc = _state.get("proc")
        if proc and proc.poll() is None:
            _kill_tree(proc.pid)


def start() -> dict[str, Any]:
    """Start the Laya server if it is not up. Returns immediately; poll status()."""
    with _lock:
        if not installed():
            raise ValueError("Laya is not installed yet: run Install first")
        if _state["phase"] in ("starting", "installing"):
            return status()
        if health():
            _state.update(phase="running", error="")
            return status()
        _state.update(phase="starting", error="", since=time.time())
        _spawn(_start_worker, "laya-start")
    return status()


def ensure(timeout: float = START_TIMEOUT) -> bool:
    """For a caller that needs a decision now: start Laya if needed and wait for it."""
    if health(timeout=1.0):
        return True
    if not installed():
        return False
    try:
        start()
    except ValueError:
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        if health(timeout=1.0):
            return True
        if _state["phase"] == "failed":
            return False
        time.sleep(1.0)
    return False


def stop() -> dict[str, Any]:
    with _lock:
        proc = _state.get("proc")
        pid = proc.pid if proc and proc.poll() is None else int(_recorded().get("pid") or 0)
        if pid:
            _kill_tree(pid)
        PIDFILE.unlink(missing_ok=True)
        _state.update(phase="stopped", error="", proc=None)
    return status()


# ---------------------------------------------------------------- install

def _install_worker() -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as out:
            r = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                                "--target", str(PKGS), "--upgrade", f"laya[serve]=={VERSION}"],
                               cwd=PROJECT_DIR, stdout=out, stderr=subprocess.STDOUT, timeout=3600,
                               env={**os.environ, "PIP_NO_INPUT": "1"}, **_no_window())
        if r.returncode:
            raise RuntimeError(f"installing Laya failed (exit {r.returncode}); see {LOG}")
        _state.update(phase="stopped", error="")
        _log("Installed.")
    except Exception as exc:
        _state.update(phase="failed", error=str(exc)[:500])
        _log(f"INSTALL FAILED: {exc}")


def install() -> dict[str, Any]:
    """Install Laya and its PyTorch (CPU) dependencies into laya-pkgs/ (about 700 MB)."""
    with _lock:
        if installed() or _state["phase"] == "installing":
            return status()
        _state.update(phase="installing", error="", since=time.time())
        _spawn(_install_worker, "laya-install")
    return status()


# ---------------------------------------------------------------- status

def _models_on_disk() -> list[str]:
    hub = HF_HOME / "hub"
    if not hub.is_dir():
        return []
    return sorted(d.name.split("--", 2)[-1] for d in hub.iterdir()
                  if d.is_dir() and d.name.startswith("models--convaiinnovations--"))


def status() -> dict[str, Any]:
    h = health(timeout=1.5, detail=True)
    phase = _state["phase"]
    if h and phase in ("stopped", "failed", "starting"):
        phase = "running"
    elif not h and phase == "running":
        phase = "stopped"
    try:
        lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-10:] if phase == "failed" else []
    except OSError:
        lines = []
    return {
        "installed": installed(),
        "running": bool(h),
        "phase": phase,
        "error": _state["error"],
        "version": VERSION,
        "url": URL,
        "device": (h or {}).get("device", ""),
        "loaded": (h or {}).get("loaded") or (h or {}).get("checkpoints") or [],
        "models_downloaded": _models_on_disk(),
        "log": lines,
        "busy_for": int(time.time() - _state["since"]) if phase in ("installing", "starting") else 0,
    }
