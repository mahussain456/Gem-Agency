"""Browser control — lets the agency actually see and render a web page.

`audit.py` reads the HTML a server sends. That is what a non-rendering crawler
sees, and it is the right baseline, but it cannot answer the question that
matters for a modern site: what is actually on the page after JavaScript runs?
This module answers it by driving the Chrome or Edge already installed on this
machine in headless mode.

No new dependencies. Chrome's own command line does the work:
  --dump-dom    prints the DOM after scripts have run
  --screenshot  writes a PNG of the rendered viewport

Every invocation gets a throwaway profile directory, so a headless run can
never touch, lock, or read the operator's real browser profile, and never
collides with a Chrome the operator already has open.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent

# Ordered by preference: Chrome first, Edge as the Windows fallback.
_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)

DEFAULT_TIMEOUT = 60
# Chrome fast-forwards timers up to this budget before capturing, so async
# content settles without an arbitrary sleep.
VIRTUAL_TIME_MS = 5000

# Chrome refuses to open a window narrower than roughly this many pixels.
MIN_WINDOW_WIDTH = 500

# Frames the page at its true width so a narrow shot is a real render rather
# than a crop of a wider layout.
_SHOT_WRAPPER = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>html,body{margin:0;padding:0;background:#f2f2f4}
iframe{border:0;display:block;width:__W__px;height:__H__px;background:#fff}</style></head>
<body><iframe src="__SRC__"></iframe></body></html>
"""

VIEWPORTS = {
    "desktop": (1440, 900),
    "tablet": (834, 1112),
    "mobile": (390, 844),
}


class BrowserError(RuntimeError):
    """Raised when no browser is available or a capture fails."""


def find_browser() -> str | None:
    """Path to a usable Chromium-family browser, or None."""
    override = os.environ.get("MISSION_CONTROL_BROWSER")
    if override and Path(override).is_file():
        return override
    for candidate in _CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    return None


def status() -> dict[str, Any]:
    """Capability probe for the UI: is browser control usable right now?"""
    path = find_browser()
    if not path:
        return {"ok": False, "error": "No Chrome or Edge found. Set MISSION_CONTROL_BROWSER "
                                      "to a Chromium-family executable to enable browser control."}
    name = Path(path).stem
    return {"ok": True, "browser": "Microsoft Edge" if "edge" in name.lower() else "Google Chrome",
            "path": path, "viewports": sorted(VIEWPORTS)}


def _validate(url: str) -> str:
    """Only http(s), or a file inside this project (built sites live there)."""
    url = (url or "").strip()
    if not url:
        raise BrowserError("a URL is required")
    if not urllib.parse.urlparse(url).scheme:
        url = "https://" + url
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme in ("http", "https"):
        return url
    if parsed.scheme == "file":
        target = Path(urllib.parse.unquote(parsed.path.lstrip("/"))).resolve()
        try:
            target.relative_to(PROJECT_DIR)
        except ValueError:
            raise BrowserError("file:// captures are restricted to this project directory") from None
        if not target.is_file():
            raise BrowserError(f"file not found: {target}")
        return url
    raise BrowserError(f"unsupported URL scheme: {parsed.scheme}")


def file_url(path: Path | str) -> str:
    """file:// URL for a built page on disk."""
    return Path(path).resolve().as_uri()


def _run(browser: str, args: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Run headless with a disposable profile so the real one is untouched."""
    profile = tempfile.mkdtemp(prefix="hermes-headless-")
    base = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--mute-audio",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-background-networking",
        # Windows display scaling otherwise inflates CSS pixels: a
        # --window-size of 390 reported innerWidth 526, so a "mobile"
        # probe silently measured a tablet.
        "--force-device-scale-factor=1",
        f"--user-data-dir={profile}",
    ]
    try:
        result = subprocess.run(base + args, capture_output=True, timeout=timeout)
        if result.returncode != 0 and b"--headless=new" in b" ".join(a.encode() for a in base):
            # Older builds only understand the legacy flag.
            legacy = [a if a != "--headless=new" else "--headless" for a in base]
            result = subprocess.run(legacy + args, capture_output=True, timeout=timeout)
        return result
    except subprocess.TimeoutExpired:
        raise BrowserError(f"browser timed out after {timeout}s") from None
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def render(url: str, *, timeout: int = DEFAULT_TIMEOUT, viewport: str = "desktop") -> dict[str, Any]:
    """Return the DOM after JavaScript has run."""
    browser = find_browser()
    if not browser:
        raise BrowserError(status()["error"])
    url = _validate(url)
    width, height = VIEWPORTS.get(viewport, VIEWPORTS["desktop"])
    started = time.time()
    result = _run(browser, [
        f"--window-size={width},{height}",
        f"--virtual-time-budget={VIRTUAL_TIME_MS}",
        "--dump-dom", url,
    ], timeout)
    html = result.stdout.decode("utf-8", errors="replace")
    if not html.strip():
        detail = result.stderr.decode("utf-8", errors="replace")[:300].strip()
        raise BrowserError(f"browser returned an empty DOM for {url}" + (f" — {detail}" if detail else ""))
    return {
        "url": url, "html": html, "bytes": len(html), "viewport": viewport,
        "ms": int((time.time() - started) * 1000), "provenance": "verified",
    }


def screenshot(url: str, out_path: Path | str, *, viewport: str = "desktop",
               timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Capture a PNG of the rendered viewport.

    Chrome will not make its window narrower than about 500px. Asking for a
    390px shot therefore laid the page out at 500 and cropped the image to 390,
    which invents clipping that does not exist -- a screenshot that lies. For
    local files the page is framed at its true width inside a wider window so
    the render is genuine; for remote URLs, which cannot be reliably framed,
    the shot is taken at the real minimum and the metadata says so rather than
    cropping.
    """
    browser_path = find_browser()
    if not browser_path:
        raise BrowserError(status()["error"])
    url = _validate(url)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    width, height = VIEWPORTS.get(viewport, VIEWPORTS["desktop"])
    started = time.time()

    wrapper: Path | None = None
    target_url, shot_width = url, width
    note = ""
    if width < MIN_WINDOW_WIDTH:
        if url.startswith("file:"):
            source = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path.lstrip("/"))).resolve()
            wrapper = source.with_name(f".hermes-shot-{viewport}.html")
            wrapper.write_text(
                _SHOT_WRAPPER.replace("__SRC__", urllib.parse.quote(source.name))
                             .replace("__W__", str(width)).replace("__H__", str(height)),
                encoding="utf-8")
            target_url, shot_width = file_url(wrapper), MIN_WINDOW_WIDTH
            note = (f"Page framed at a true {width}px inside a {MIN_WINDOW_WIDTH}px window, because "
                    f"Chrome will not open a narrower one. The render is genuine at {width}px.")
        else:
            shot_width = MIN_WINDOW_WIDTH
            note = (f"Requested {width}px, captured at {MIN_WINDOW_WIDTH}px: Chrome cannot open a "
                    f"narrower window and cropping would fake a clipped layout. Use layout_probe() "
                    f"for true narrow-width measurements of local builds.")

    try:
        result = _run(browser_path, [
            f"--window-size={shot_width},{height}",
            f"--virtual-time-budget={VIRTUAL_TIME_MS}",
            "--allow-file-access-from-files",
            f"--screenshot={out}", target_url,
        ], timeout)
    finally:
        if wrapper is not None:
            wrapper.unlink(missing_ok=True)

    if not out.is_file() or out.stat().st_size == 0:
        detail = result.stderr.decode("utf-8", errors="replace")[:300].strip()
        raise BrowserError(f"no screenshot produced for {url}" + (f" — {detail}" if detail else ""))
    return {
        "url": url, "path": str(out), "bytes": out.stat().st_size,
        "viewport": viewport, "width": width, "height": height,
        "image_width": shot_width, "content_width": width,
        "note": note, "ms": int((time.time() - started) * 1000), "provenance": "verified",
    }


def capture_set(url: str, out_dir: Path | str, *, viewports: tuple[str, ...] = ("desktop", "mobile"),
                timeout: int = DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
    """Screenshot one page across several viewports (responsive QA)."""
    out_dir = Path(out_dir)
    shots: list[dict[str, Any]] = []
    for viewport in viewports:
        target = out_dir / f"{viewport}.png"
        shots.append(screenshot(url, target, viewport=viewport, timeout=timeout))
    return shots


# ---------------------------------------------------------------- layout probe

# The page is measured inside an iframe of the exact target width, because
# Chrome refuses to make its own window narrower than ~500px: asking for 390
# yielded innerWidth 500, so a "mobile" probe silently measured a tablet.
# Media queries inside an iframe respond to the iframe's width, so this gives
# true small-screen layout.
_PROBE_WRAPPER = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>html,body{margin:0;padding:0;background:#fff}iframe{border:0;display:block}</style></head>
<body><iframe id="__f" src="__SRC__" style="width:__W__px;height:__H__px"></iframe>
<script>
(function () {
  function selector(el) {
    var s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    else if (el.className && typeof el.className === 'string') {
      var c = el.className.trim().split(/\s+/).slice(0, 2).join('.');
      if (c) s += '.' + c;
    }
    return s;
  }
  function measure() {
    var f = document.getElementById('__f');
    var out = { viewport_width: __W__, error: null, offenders: [], tiny_text: [], small_targets: [] };
    try {
      var d = f.contentDocument, w = f.contentWindow;
      if (!d || !d.body) { out.error = 'iframe document unavailable'; return emit(out); }
      var vw = w.innerWidth || __W__;
      out.viewport_width = vw;
      out.scroll_width = d.documentElement.scrollWidth;
      out.horizontal_overflow = out.scroll_width > vw + 1;
      out.overflow_px = Math.max(0, out.scroll_width - vw);
      var over = [];
      Array.prototype.slice.call(d.querySelectorAll('body *')).forEach(function (el) {
        var r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) return;
        if (r.right > vw + 1 || r.width > vw + 1) over.push({ el: el, r: r });
        var fs = parseFloat(w.getComputedStyle(el).fontSize || '16');
        var isTextLeaf = el.childNodes.length === 1 && el.firstChild.nodeType === 3;
        var txt = isTextLeaf ? (el.textContent || '').trim() : '';
        if (txt && fs && fs < 12 && out.tiny_text.length < 6) {
          out.tiny_text.push({ selector: selector(el), font_px: Math.round(fs * 10) / 10,
                               text: txt.slice(0, 40) });
        }
        if ((el.tagName === 'A' || el.tagName === 'BUTTON') && r.width > 0 &&
            (r.height < 32 || r.width < 32) && out.small_targets.length < 6) {
          out.small_targets.push({ selector: selector(el), w: Math.round(r.width),
                                   h: Math.round(r.height), text: (el.textContent || '').trim().slice(0, 30) });
        }
      });
      over.forEach(function (o) {
        var inner = over.some(function (p) { return p !== o && o.el.contains(p.el); });
        if (!inner && out.offenders.length < 10) {
          out.offenders.push({ selector: selector(o.el), width: Math.round(o.r.width),
                               right_edge: Math.round(o.r.right),
                               overhang_px: Math.round(o.r.right - vw),
                               text: (o.el.textContent || '').trim().slice(0, 60) });
        }
      });
      out.offenders.sort(function (a, b) { return b.overhang_px - a.overhang_px; });
    } catch (e) {
      out.error = String(e && e.message ? e.message : e);
    }
    emit(out);
  }
  var emitted = false;
  function emit(out) {
    if (emitted) return;
    emitted = true;
    var tag = document.createElement('script');
    tag.type = 'application/json';
    tag.id = '__hermes_probe_result';
    tag.textContent = JSON.stringify(out);
    document.body.appendChild(tag);
  }
  // An iframe's initial about:blank already reports readyState 'complete',
  // so trusting that measured an empty document and reported no defects.
  // Wait until the real page has actually populated a body.
  var f = document.getElementById('__f');
  var attempts = 0;
  function loaded() {
    try {
      var d = f.contentDocument;
      return !!(d && d.body && d.body.children.length > 0 && d.readyState === 'complete');
    } catch (e) { return false; }
  }
  function tick() {
    if (loaded() || ++attempts > 150) { measure(); return; }
    setTimeout(tick, 20);
  }
  f.addEventListener('load', tick);
  tick();
})();
</script></body></html>
"""

_PROBE_RESULT_RE = re.compile(
    r'<script[^>]*id="__hermes_probe_result"[^>]*>(.*?)</script>', re.S | re.I)


def layout_probe(file_path: Path | str, *, viewport: str = "mobile",
                 timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Measure real layout defects in a built page at a true target width.

    A screenshot shows a heading running off the screen; this returns which
    element does it and by how many pixels, which is what a repair stage needs
    in order to fix it.

    The original file is never modified: a wrapper is written beside it and
    removed afterwards.
    """
    source = Path(file_path).resolve()
    if not source.is_file():
        raise BrowserError(f"file not found: {source}")
    width, height = VIEWPORTS.get(viewport, VIEWPORTS["mobile"])
    wrapper_html = (_PROBE_WRAPPER
                    .replace("__SRC__", urllib.parse.quote(source.name))
                    .replace("__W__", str(width))
                    .replace("__H__", str(height)))
    wrapper = source.with_name(f".hermes-probe-{viewport}.html")
    wrapper.write_text(wrapper_html, encoding="utf-8")
    try:
        browser_path = find_browser()
        if not browser_path:
            raise BrowserError(status()["error"])
        result = _run(browser_path, [
            f"--window-size={max(width, 800)},{height}",
            f"--virtual-time-budget={VIRTUAL_TIME_MS}",
            # The wrapper reads the page's DOM through the iframe; for file://
            # URLs Chrome treats each file as a distinct origin without this.
            "--allow-file-access-from-files",
            "--dump-dom", file_url(wrapper),
        ], timeout)
        html = result.stdout.decode("utf-8", errors="replace")
    finally:
        wrapper.unlink(missing_ok=True)

    match = _PROBE_RESULT_RE.search(html)
    if not match:
        raise BrowserError("layout probe did not report — the page may have failed to execute scripts")
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise BrowserError(f"layout probe returned unreadable output: {exc}") from None
    if data.get("error"):
        raise BrowserError(f"layout probe failed inside the page: {data['error']}")
    data["viewport"] = viewport
    data["provenance"] = "verified"
    return data


def layout_findings(probe: dict[str, Any]) -> list[dict[str, str]]:
    """Turn probe numbers into the finding shape the rest of the system uses."""
    viewport = probe.get("viewport", "?")
    width = probe.get("viewport_width", "?")
    out: list[dict[str, str]] = []
    if probe.get("horizontal_overflow"):
        worst = (probe.get("offenders") or [{}])[0]
        detail = (f"The page scrolls to {probe.get('scroll_width')}px inside a {width}px viewport "
                  f"({probe.get('overflow_px')}px too wide).")
        if worst.get("selector"):
            detail += (f" Widest offender: {worst['selector']} at {worst.get('width')}px"
                       f"{', text ' + repr(worst['text'][:40]) if worst.get('text') else ''}.")
        out.append({"severity": "high", "area": "layout",
                    "title": f"Horizontal overflow at {viewport} width ({width}px)",
                    "detail": detail,
                    "fix": "Constrain the offending element: wrap long words, reduce the font size at "
                           "this breakpoint, or set max-width:100% with overflow-wrap:break-word."})
    else:
        out.append({"severity": "good", "area": "layout",
                    "title": f"No horizontal overflow at {viewport} width ({width}px)",
                    "detail": f"Content fits within {width}px.", "fix": ""})
    for t in probe.get("tiny_text") or []:
        out.append({"severity": "medium", "area": "layout",
                    "title": f"Text below 12px at {viewport} width",
                    "detail": f"{t['selector']} renders at {t['font_px']}px: {t['text']!r}.",
                    "fix": "Raise body text to at least 14px on small screens."})
    for s in probe.get("small_targets") or []:
        out.append({"severity": "medium", "area": "layout",
                    "title": f"Tap target smaller than 32px at {viewport} width",
                    "detail": f"{s['selector']} is {s['w']}x{s['h']}px: {s['text']!r}.",
                    "fix": "Give interactive controls at least 44x44px of touch area."})
    return out
