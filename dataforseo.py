"""DataForSEO adapter — the metrics the agents are forbidden to invent.

Every generative prompt in playbooks.py tells the agent it cannot measure
search volume, keyword difficulty, ranking position or backlinks, and must
name the source that would supply them instead. This module is that source.

It is the same engine OpenSEO (github.com/every-app/open-seo) is built on. That
project is a full Cloudflare Workers + React product and cannot be dropped into
a Python-stdlib server, but its useful core is this API, so it is implemented
here directly in the shape the rest of this dashboard already uses.

Pay-as-you-go, so cost discipline is part of the design:
  · every call is bounded -- no unbounded batch can be triggered by a UI click
  · balance is surfaced by status() so spend is visible before work starts
  · nothing runs on a timer; a call happens because someone asked for it

Auth is HTTP Basic against api.dataforseo.com. Credentials live in a gitignored
file beside the other adapter secrets and are never logged.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent
CREDS_PATH = PROJECT_DIR / ".dataforseo_credentials.json"

API_BASE = "https://api.dataforseo.com/v3"
TIMEOUT = 60

# Bounds. DataForSEO bills per task and per row; these stop a stray call from
# turning into a large invoice.
MAX_KEYWORDS = 100
MAX_SERP_DEPTH = 100
DEFAULT_LOCATION = 2826      # United Kingdom
DEFAULT_LANGUAGE = "en"


class DataForSEOError(Exception):
    pass


# ---------------------------------------------------------------- credentials

def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_credentials(login: str, password: str) -> None:
    login, password = (login or "").strip(), (password or "").strip()
    if not login or not password:
        raise ValueError("both the DataForSEO login (email) and password are required")
    CREDS_PATH.write_text(json.dumps({"login": login, "password": password}, indent=2),
                          encoding="utf-8")


def credentials() -> dict[str, Any]:
    return _read_json(CREDS_PATH)


def disconnect() -> None:
    CREDS_PATH.unlink(missing_ok=True)


def _auth_header() -> str:
    creds = credentials()
    login, password = creds.get("login"), creds.get("password")
    if not login or not password:
        raise DataForSEOError("DataForSEO is not connected — add your API credentials first")
    token = base64.b64encode(f"{login}:{password}".encode()).decode()
    return f"Basic {token}"


# ---------------------------------------------------------------- transport

def _post(path: str, payload: list[dict[str, Any]] | None = None,
          method: str = "POST") -> dict[str, Any]:
    """Call the API and unwrap DataForSEO's task envelope.

    The API answers HTTP 200 with an error status_code inside the body, so the
    envelope is checked rather than trusting the HTTP status.
    """
    url = f"{API_BASE}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": _auth_header(),
        "Content-Type": "application/json",
    })
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        if exc.code in (401, 403):
            raise DataForSEOError("DataForSEO rejected the credentials (401/403)") from None
        raise DataForSEOError(f"DataForSEO HTTP {exc.code}: {detail}") from None
    except Exception as exc:
        raise DataForSEOError(f"could not reach DataForSEO: {type(exc).__name__}: {exc}") from None

    if body.get("status_code") != 20000:
        raise DataForSEOError(f"DataForSEO error {body.get('status_code')}: "
                              f"{body.get('status_message', 'unknown')}")
    tasks = body.get("tasks") or []
    if not tasks:
        raise DataForSEOError("DataForSEO returned no tasks")
    task = tasks[0]
    if task.get("status_code") != 20000:
        raise DataForSEOError(f"DataForSEO task error {task.get('status_code')}: "
                              f"{task.get('status_message', 'unknown')}")
    return {
        "result": task.get("result") or [],
        "cost": float(body.get("cost") or 0),
        "ms": int((time.time() - started) * 1000),
    }


# ---------------------------------------------------------------- status

def status() -> dict[str, Any]:
    """Is the adapter usable, and what does the account look like?"""
    creds = credentials()
    if not creds.get("login") or not creds.get("password"):
        return {"ok": False, "configured": False,
                "error": "Not connected. Add DataForSEO API credentials to enable "
                         "search volume, difficulty, rank tracking and backlink data."}
    try:
        res = _post("/appendix/user_data", method="GET")
    except DataForSEOError as exc:
        return {"ok": False, "configured": True, "error": str(exc)}
    info = (res["result"] or [{}])[0]
    money = (info.get("money") or {})
    return {
        "ok": True, "configured": True,
        "login": creds.get("login"),
        "balance": money.get("balance"),
        "currency": "USD",
        "rates_limit": (info.get("rates") or {}).get("limits_per_minute"),
        "note": "Pay-as-you-go. Every figure this adapter returns is measured by "
                "DataForSEO and carries provenance 'imported'.",
    }


# ---------------------------------------------------------------- keywords

def search_volume(keywords: list[str], *, location_code: int = DEFAULT_LOCATION,
                  language_code: str = DEFAULT_LANGUAGE) -> dict[str, Any]:
    """Real monthly search volume and CPC for up to MAX_KEYWORDS terms."""
    terms = _clean_terms(keywords)
    res = _post("/keywords_data/google_ads/search_volume/live", [{
        "keywords": terms,
        "location_code": location_code,
        "language_code": language_code,
    }])
    rows = []
    for item in res["result"]:
        rows.append({
            "keyword": item.get("keyword"),
            "volume": item.get("search_volume"),
            "cpc": item.get("cpc"),
            "competition": item.get("competition"),
            "provenance": "imported",
            "source": "dataforseo:google_ads",
        })
    return {"keywords": rows, "cost_usd": res["cost"], "ms": res["ms"],
            "requested": len(terms), "returned": len(rows)}


def keyword_difficulty(keywords: list[str], *, location_code: int = DEFAULT_LOCATION,
                       language_code: str = DEFAULT_LANGUAGE) -> dict[str, Any]:
    """Difficulty score (0-100) for up to MAX_KEYWORDS terms."""
    terms = _clean_terms(keywords)
    res = _post("/dataforseo_labs/google/bulk_keyword_difficulty/live", [{
        "keywords": terms,
        "location_code": location_code,
        "language_code": language_code,
    }])
    rows = [{
        "keyword": item.get("keyword"),
        "difficulty": item.get("keyword_difficulty"),
        "provenance": "imported",
        "source": "dataforseo:labs",
    } for item in res["result"]]
    return {"keywords": rows, "cost_usd": res["cost"], "ms": res["ms"]}


def keyword_ideas(seed: str, *, limit: int = 50, location_code: int = DEFAULT_LOCATION,
                  language_code: str = DEFAULT_LANGUAGE) -> dict[str, Any]:
    """Related keywords with real volume, for expanding a seed term."""
    seed = (seed or "").strip()
    if not seed:
        raise ValueError("a seed keyword is required")
    limit = max(1, min(int(limit), MAX_KEYWORDS))
    res = _post("/dataforseo_labs/google/keyword_ideas/live", [{
        "keywords": [seed],
        "location_code": location_code,
        "language_code": language_code,
        "limit": limit,
    }])
    items = (res["result"][0] or {}).get("items") or [] if res["result"] else []
    rows = []
    for item in items[:limit]:
        info = (item.get("keyword_info") or {})
        rows.append({
            "keyword": item.get("keyword"),
            "volume": info.get("search_volume"),
            "cpc": info.get("cpc"),
            "competition": info.get("competition"),
            "provenance": "imported",
            "source": "dataforseo:labs",
        })
    return {"seed": seed, "keywords": rows, "cost_usd": res["cost"], "ms": res["ms"]}


# ---------------------------------------------------------------- rankings

def serp_position(keyword: str, domain: str, *, location_code: int = DEFAULT_LOCATION,
                  language_code: str = DEFAULT_LANGUAGE, depth: int = 100) -> dict[str, Any]:
    """Where a domain actually ranks for one keyword, from a live SERP."""
    keyword = (keyword or "").strip()
    domain = _clean_domain(domain)
    if not keyword:
        raise ValueError("a keyword is required")
    depth = max(10, min(int(depth), MAX_SERP_DEPTH))
    res = _post("/serp/google/organic/live/advanced", [{
        "keyword": keyword,
        "location_code": location_code,
        "language_code": language_code,
        "depth": depth,
    }])
    items = (res["result"][0] or {}).get("items") or [] if res["result"] else []
    position: int | None = None
    landing_page = ""
    serp_features: list[str] = []
    for item in items:
        itype = item.get("type", "")
        if itype not in serp_features and itype != "organic":
            serp_features.append(itype)
        if itype == "organic" and position is None:
            item_domain = _clean_domain(item.get("domain") or "")
            if item_domain == domain or item_domain.endswith("." + domain):
                position = item.get("rank_absolute")
                landing_page = item.get("url", "")
    return {
        "keyword": keyword, "domain": domain,
        "position": position,
        "landing_page": landing_page,
        "found": position is not None,
        "checked_depth": depth,
        "serp_features": serp_features[:8],
        "provenance": "verified",   # a live SERP was actually fetched
        "source": "dataforseo:serp",
        "cost_usd": res["cost"], "ms": res["ms"],
        "note": "" if position is not None else f"Not found in the top {depth} organic results.",
    }


# ---------------------------------------------------------------- competitors

def competitors(domain: str, *, limit: int = 20, location_code: int = DEFAULT_LOCATION,
                language_code: str = DEFAULT_LANGUAGE) -> dict[str, Any]:
    """Domains competing for the same organic keywords."""
    domain = _clean_domain(domain)
    limit = max(1, min(int(limit), 50))
    res = _post("/dataforseo_labs/google/competitors_domain/live", [{
        "target": domain,
        "location_code": location_code,
        "language_code": language_code,
        "limit": limit,
    }])
    items = (res["result"][0] or {}).get("items") or [] if res["result"] else []
    rows = []
    for item in items[:limit]:
        metrics = ((item.get("metrics") or {}).get("organic") or {})
        rows.append({
            "domain": item.get("domain"),
            "intersections": item.get("intersections"),
            "keywords": metrics.get("count"),
            "estimated_traffic": metrics.get("etv"),
            "provenance": "imported",
            "source": "dataforseo:labs",
        })
    return {"domain": domain, "competitors": rows, "cost_usd": res["cost"], "ms": res["ms"]}


# ---------------------------------------------------------------- backlinks

def backlinks_summary(domain: str) -> dict[str, Any]:
    """Backlink profile totals for a domain."""
    domain = _clean_domain(domain)
    res = _post("/backlinks/summary/live", [{"target": domain, "internal_list_limit": 1}])
    item = (res["result"] or [{}])[0]
    return {
        "domain": domain,
        "backlinks": item.get("backlinks"),
        "referring_domains": item.get("referring_domains"),
        "referring_main_domains": item.get("referring_main_domains"),
        "rank": item.get("rank"),
        "broken_backlinks": item.get("broken_backlinks"),
        "dofollow": item.get("referring_links_types", {}).get("anchor"),
        "provenance": "imported",
        "source": "dataforseo:backlinks",
        "cost_usd": res["cost"], "ms": res["ms"],
    }


# ---------------------------------------------------------------- helpers

def _clean_terms(keywords: list[str]) -> list[str]:
    terms = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    if not terms:
        raise ValueError("at least one keyword is required")
    if len(terms) > MAX_KEYWORDS:
        raise ValueError(f"too many keywords: {len(terms)}. This adapter caps a single call at "
                         f"{MAX_KEYWORDS} to keep the bill predictable.")
    return terms


def _clean_domain(domain: str) -> str:
    d = (domain or "").strip().lower()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    d = d.split("/")[0].strip()
    if d.startswith("www."):
        d = d[4:]
    if not d:
        raise ValueError("a domain is required")
    return d
