"""Google Search Console adapter.

OAuth 2.0 desktop-app flow using only the Python stdlib (token exchange and
refresh are plain HTTPS POSTs — no JWT signing needed, so no crypto deps).

Setup (one time, by the operator):
  1. In Google Cloud Console: create a project, enable "Google Search Console
     API", create OAuth credentials of type "Desktop app".
  2. POST the client_id/client_secret to /api/agency/gsc/setup (the GSC widget
     has a form for this). They are stored in .gsc_credentials.json —
     gitignored, never committed, never logged.
  3. Click Connect: browser opens Google's consent page; the redirect lands on
     /api/agency/gsc/callback on this server, which stores the refresh token
     in .gsc_token.json (gitignored).

Data policy: everything imported from GSC is written with provenance
'verified' and source 'gsc'. If the connection breaks, status() reports it —
no cached value is ever presented as fresh.
"""

from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent
CREDS_PATH = PROJECT_DIR / ".gsc_credentials.json"
TOKEN_PATH = PROJECT_DIR / ".gsc_token.json"

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
API_BASE = "https://www.googleapis.com/webmasters/v3"

# short-lived CSRF states for the OAuth dance: state -> expiry epoch
_pending_states: dict[str, float] = {}


class GSCError(Exception):
    pass


# ---------- config / token storage ----------

def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def save_credentials(client_id: str, client_secret: str) -> None:
    client_id = client_id.strip()
    client_secret = client_secret.strip()
    if not client_id or not client_secret:
        raise ValueError("client_id and client_secret are both required")
    _write_json(CREDS_PATH, {"client_id": client_id, "client_secret": client_secret})


def credentials() -> dict[str, Any]:
    return _read_json(CREDS_PATH)


def disconnect() -> None:
    try:
        TOKEN_PATH.unlink()
    except FileNotFoundError:
        pass


def status() -> dict[str, Any]:
    creds = credentials()
    token = _read_json(TOKEN_PATH)
    out = {
        "configured": bool(creds.get("client_id") and creds.get("client_secret")),
        "connected": bool(token.get("refresh_token")),
        "error": "",
    }
    if out["connected"]:
        try:
            _access_token()
        except GSCError as exc:
            out["error"] = _explain(str(exc))
            out["connected"] = False
            out["reconnect"] = "invalid_grant" in str(exc)
    return out


def _explain(error: str) -> str:
    """Google's invalid_grant is opaque; say what it means and how to stop it recurring."""
    if "invalid_grant" in error:
        return ("Google ended this connection (the sign-in expired or was revoked). Click Reconnect. "
                "If it keeps expiring about every 7 days, the Google Cloud OAuth app is in Testing mode: "
                "publish it (OAuth consent screen, Publish app) and sign-ins stop expiring.")
    return error


# ---------- oauth ----------

def make_state() -> str:
    now = time.time()
    for s, exp in list(_pending_states.items()):
        if exp < now:
            _pending_states.pop(s, None)
    state = secrets.token_urlsafe(24)
    _pending_states[state] = now + 600
    return state


def consume_state(state: str) -> bool:
    exp = _pending_states.pop(state, 0)
    return exp >= time.time()


def redirect_uri(host: str, port: int) -> str:
    return f"http://{host}:{port}/api/agency/gsc/callback"


def auth_url(host: str, port: int) -> str:
    creds = credentials()
    if not creds.get("client_id"):
        raise GSCError("GSC is not configured — save client_id/client_secret first")
    params = {
        "client_id": creds["client_id"],
        "redirect_uri": redirect_uri(host, port),
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": make_state(),
    }
    return AUTH_ENDPOINT + "?" + urllib.parse.urlencode(params)


def _token_request(payload: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(TOKEN_ENDPOINT, data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise GSCError(f"Google token endpoint {exc.code}: {detail}") from exc
    except OSError as exc:
        raise GSCError(f"network error talking to Google: {exc}") from exc


def exchange_code(code: str, host: str, port: int) -> None:
    creds = credentials()
    data = _token_request({
        "client_id": creds.get("client_id", ""),
        "client_secret": creds.get("client_secret", ""),
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri(host, port),
    })
    if "refresh_token" not in data:
        raise GSCError("Google did not return a refresh token; retry with prompt=consent")
    _write_json(TOKEN_PATH, {
        "refresh_token": data["refresh_token"],
        "access_token": data.get("access_token", ""),
        "expires_at": time.time() + int(data.get("expires_in", 0)) - 60,
    })


def _access_token() -> str:
    token = _read_json(TOKEN_PATH)
    if not token.get("refresh_token"):
        raise GSCError("not connected to Google Search Console")
    if token.get("access_token") and time.time() < float(token.get("expires_at", 0)):
        return token["access_token"]
    creds = credentials()
    data = _token_request({
        "client_id": creds.get("client_id", ""),
        "client_secret": creds.get("client_secret", ""),
        "refresh_token": token["refresh_token"],
        "grant_type": "refresh_token",
    })
    token["access_token"] = data.get("access_token", "")
    token["expires_at"] = time.time() + int(data.get("expires_in", 0)) - 60
    _write_json(TOKEN_PATH, token)
    if not token["access_token"]:
        raise GSCError("token refresh returned no access token")
    return token["access_token"]


# ---------- API calls ----------

def _api(method: str, path: str, body: dict | None = None) -> dict[str, Any]:
    tok = _access_token()
    url = API_BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise GSCError(f"GSC API {exc.code} on {path}: {detail}") from exc
    except OSError as exc:
        raise GSCError(f"network error talking to GSC: {exc}") from exc


def list_sites() -> list[dict[str, Any]]:
    entries = _api("GET", "/sites").get("siteEntry", [])
    return [{"site_url": e.get("siteUrl", ""), "permission": e.get("permissionLevel", "")} for e in entries]


def query(site_url: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    path = f"/sites/{urllib.parse.quote(site_url, safe='')}/searchAnalytics/query"
    return _api("POST", path, body).get("rows", [])


def _date(days_ago: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - days_ago * 86400))


def top_queries(site_url: str, days: int = 28, limit: int = 250) -> list[dict[str, Any]]:
    rows = query(site_url, {
        # GSC data lags ~2 days; end the window there so totals are stable
        "startDate": _date(days + 2), "endDate": _date(2),
        "dimensions": ["query"], "rowLimit": limit,
    })
    return [{
        "keyword": r["keys"][0],
        "clicks": int(r.get("clicks", 0)),
        "impressions": int(r.get("impressions", 0)),
        "ctr": round(float(r.get("ctr", 0)) * 100, 2),
        "position": round(float(r.get("position", 0)), 1),
    } for r in rows]


def performance(site_url: str, days: int = 28, include_series: bool = False) -> dict[str, Any]:
    if not isinstance(days, int) or isinstance(days, bool) or days not in (28, 90):
        raise ValueError("days must be 28 or 90")
    def totals(start_ago: int, end_ago: int) -> dict[str, Any]:
        rows = query(site_url, {"startDate": _date(start_ago), "endDate": _date(end_ago), "dimensions": []})
        if not rows:
            return {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": 0.0}
        r = rows[0]
        return {
            "clicks": int(r.get("clicks", 0)),
            "impressions": int(r.get("impressions", 0)),
            "ctr": round(float(r.get("ctr", 0)) * 100, 2),
            "position": round(float(r.get("position", 0)), 1),
        }
    result = {
        "site_url": site_url,
        "days": days,
        # Search Console dates are inclusive; both comparison windows contain
        # exactly `days` days and leave two days for processing latency.
        "current": totals(days + 1, 2),
        "previous": totals(days * 2 + 1, days + 2),
        "start_date": _date(days + 1),
        "end_date": _date(2),
        "provenance": "verified",
        "source": "gsc",
    }
    if include_series:
        rows = query(site_url, {"startDate": result["start_date"], "endDate": result["end_date"],
                               "dimensions": ["date"], "rowLimit": days})
        result["daily"] = sorted([
            {"date": str(r["keys"][0]), "clicks": int(r.get("clicks", 0)),
             "impressions": int(r.get("impressions", 0))}
            for r in rows if r.get("keys")
        ], key=lambda r: r["date"])
    return result
