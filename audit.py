"""Real website auditor — technical SEO, schema, AEO and GEO.

Every finding here comes from actually fetching and parsing the page, so all
results carry provenance 'verified'. Nothing is modelled, scored by vibes, or
returned when the fetch fails: a failed fetch is reported as a failed fetch.

Deliberately stdlib-only (urllib + html.parser) for the core audit, which
reads exactly what the server sends -- that is what a non-rendering crawler
sees, and it is the honest baseline.

render_gap() additionally drives the local browser (see browser.py) to get
the DOM after JavaScript runs, and reports the difference. Content present
only after rendering is a real ranking and AI-citation risk, because most
crawlers never execute scripts.
"""

from __future__ import annotations

import gzip
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

UA = "Mozilla/5.0 (compatible; HermesLedgerAudit/1.0; +local operator tool)"
TIMEOUT = 20
MAX_BYTES = 3_000_000

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "good": 4}

# Question openers used to detect answer-shaped (AEO) headings.
QUESTION_WORDS = ("how ", "what ", "why ", "when ", "where ", "which ", "who ",
                  "can ", "do ", "does ", "is ", "are ", "should ")


class AuditError(Exception):
    pass


# ---------------------------------------------------------------- fetching

def fetch(url: str, *, timeout: int = TIMEOUT) -> dict[str, Any]:
    """Fetch a URL, following redirects, returning body + metadata."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip",
    })
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_BYTES)
            if resp.headers.get("Content-Encoding") == "gzip":
                try:
                    raw = gzip.decompress(raw)
                except OSError:
                    pass
            charset = resp.headers.get_content_charset() or "utf-8"
            return {
                "ok": True,
                "status": resp.status,
                "final_url": resp.geturl(),
                "redirected": resp.geturl().rstrip("/") != url.rstrip("/"),
                "headers": {k.lower(): v for k, v in resp.headers.items()},
                "body": raw.decode(charset, errors="replace"),
                "bytes": len(raw),
                "ms": int((time.time() - started) * 1000),
            }
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "final_url": url, "error": f"HTTP {exc.code}",
                "body": "", "bytes": 0, "ms": int((time.time() - started) * 1000), "headers": {}}
    except Exception as exc:
        return {"ok": False, "status": 0, "final_url": url, "error": f"{type(exc).__name__}: {exc}",
                "body": "", "bytes": 0, "ms": int((time.time() - started) * 1000), "headers": {}}


# ---------------------------------------------------------------- parsing

class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta: dict[str, str] = {}
        self.og: dict[str, str] = {}
        self.canonical = ""
        self.lang = ""
        self.headings: list[tuple[int, str]] = []
        self.images: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.jsonld: list[Any] = []
        self.scripts_external = 0
        self.scripts_inline = 0
        self.stylesheets = 0
        self.has_viewport = False
        self.text_chars = 0
        self._stack: list[str] = []
        self._grab: str | None = None
        self._buf: list[str] = []
        self._svg_depth = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        self._stack.append(tag)
        if tag == "svg":
            self._svg_depth += 1
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "title":
            # <title> inside an inline <svg> is the graphic's accessible name,
            # not the document title — ignore it.
            if self._svg_depth == 0 and not self.title:
                self._grab, self._buf = "title", []
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or a.get("http-equiv") or "").lower()
            if name:
                content = a.get("content", "")
                if name.startswith("og:") or name.startswith("twitter:"):
                    self.og[name] = content
                else:
                    self.meta[name] = content
                if name == "viewport":
                    self.has_viewport = True
        elif tag == "link":
            rel = (a.get("rel") or "").lower()
            if "canonical" in rel:
                self.canonical = a.get("href", "")
            if "stylesheet" in rel:
                self.stylesheets += 1
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._grab, self._buf = tag, []
        elif tag == "img":
            self.images.append({"src": a.get("src", ""), "alt": a.get("alt"),
                                "width": a.get("width", ""), "height": a.get("height", ""),
                                "loading": a.get("loading", "")})
        elif tag == "a":
            self.links.append({"href": a.get("href", ""), "rel": (a.get("rel") or "").lower()})
        elif tag == "script":
            stype = (a.get("type") or "").lower()
            if stype == "application/ld+json":
                self._grab, self._buf = "ld", []
            elif a.get("src"):
                self.scripts_external += 1
            else:
                self.scripts_inline += 1

    def handle_endtag(self, tag):
        if tag == "svg" and self._svg_depth:
            self._svg_depth -= 1
        if self._stack and tag in self._stack:
            while self._stack and self._stack.pop() != tag:
                pass
        if self._grab and (tag == self._grab or (self._grab == "ld" and tag == "script")):
            text = "".join(self._buf).strip()
            if self._grab == "title":
                self.title = text
            elif self._grab == "ld":
                try:
                    self.jsonld.append(json.loads(text))
                except json.JSONDecodeError:
                    self.jsonld.append({"__parse_error__": text[:120]})
            else:
                self.headings.append((int(self._grab[1]), text))
            self._grab, self._buf = None, []

    def handle_data(self, data):
        if self._grab:
            self._buf.append(data)
        elif not any(t in self._stack for t in ("script", "style")):
            self.text_chars += len(data.strip())


def schema_types(blocks: list[Any]) -> list[str]:
    """Flatten @type values out of JSON-LD, including @graph nodes."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            t = node.get("@type")
            if isinstance(t, str):
                found.append(t)
            elif isinstance(t, list):
                found.extend(str(x) for x in t)
            for key in ("@graph", "mainEntity", "itemListElement", "hasPart"):
                if key in node:
                    walk(node[key])
    walk(blocks)
    return sorted(set(found))


# ---------------------------------------------------------------- findings

def _f(sev: str, area: str, title: str, detail: str, fix: str = "") -> dict[str, str]:
    return {"severity": sev, "area": area, "title": title, "detail": detail, "fix": fix}


def audit_page(url: str) -> dict[str, Any]:
    """Audit one URL. Returns verified findings across SEO, schema, AEO, GEO."""
    if not re.match(r"^https?://", url):
        url = "https://" + url
    page = fetch(url)
    if not page["ok"]:
        raise AuditError(f"could not fetch {url} — {page.get('error', 'unknown error')}")

    p = PageParser()
    try:
        p.feed(page["body"])
    except Exception as exc:  # malformed markup shouldn't kill the audit
        p_err = f"HTML parser stopped early: {type(exc).__name__}"
    else:
        p_err = ""

    parsed = urllib.parse.urlparse(page["final_url"])
    origin = f"{parsed.scheme}://{parsed.netloc}"
    findings: list[dict[str, str]] = []

    # ---- transport / indexability
    if parsed.scheme != "https":
        findings.append(_f("critical", "technical", "Not served over HTTPS",
                           f"Final URL is {page['final_url']}.", "Serve the site over HTTPS and redirect HTTP."))
    else:
        findings.append(_f("good", "technical", "HTTPS in use", f"Served securely ({page['status']})."))
    if page["redirected"]:
        findings.append(_f("low", "technical", "Request was redirected",
                           f"{url} → {page['final_url']}.", "Link to the final URL directly to save a hop."))
    robots_meta = (p.meta.get("robots") or "").lower()
    if "noindex" in robots_meta:
        findings.append(_f("critical", "technical", "Page is set to noindex",
                           f"meta robots = '{robots_meta}'.", "Remove noindex if this page should rank."))
    # ---- title / description
    if not p.title:
        findings.append(_f("critical", "on-page", "Missing <title>", "No title element was found.",
                           "Add a unique 50–60 character title."))
    else:
        n = len(p.title)
        sev = "good" if 15 <= n <= 65 else "medium"
        findings.append(_f(sev, "on-page", f"Title is {n} characters",
                           f"“{p.title[:120]}”",
                           "" if sev == "good" else "Aim for roughly 50–60 characters."))
    desc = p.meta.get("description", "")
    if not desc:
        findings.append(_f("high", "on-page", "Missing meta description",
                           "No meta description was found.", "Write a 140–160 character summary with the primary term."))
    else:
        n = len(desc)
        sev = "good" if 70 <= n <= 165 else "low"
        findings.append(_f(sev, "on-page", f"Meta description is {n} characters", f"“{desc[:160]}”",
                           "" if sev == "good" else "Aim for roughly 140–160 characters."))
    # ---- canonical / lang / viewport
    if not p.canonical:
        findings.append(_f("medium", "technical", "No canonical link",
                           "No rel=canonical was declared.", "Add a self-referencing canonical."))
    else:
        findings.append(_f("good", "technical", "Canonical declared", p.canonical))
    if not p.lang:
        findings.append(_f("medium", "technical", "No lang attribute on <html>",
                           "Screen readers and search engines infer language instead.", 'Add lang="en" (or the correct locale).'))
    if not p.has_viewport:
        findings.append(_f("high", "technical", "No mobile viewport meta",
                           "Mobile rendering will be unreliable.", 'Add <meta name="viewport" content="width=device-width, initial-scale=1">.'))
    # ---- headings
    h1s = [t for lvl, t in p.headings if lvl == 1]
    if not h1s:
        findings.append(_f("high", "on-page", "No H1 heading",
                           "The page states no primary subject in markup.", "Add exactly one descriptive H1."))
    elif len(h1s) > 1:
        findings.append(_f("medium", "on-page", f"{len(h1s)} H1 headings",
                           "Multiple H1s dilute the page's stated subject.", "Keep one H1; demote the rest to H2."))
    else:
        findings.append(_f("good", "on-page", "Single H1", f"“{h1s[0][:120]}”"))
    levels = [lvl for lvl, _ in p.headings]
    for i in range(1, len(levels)):
        if levels[i] - levels[i - 1] > 1:
            findings.append(_f("low", "on-page", "Heading levels skip",
                               f"H{levels[i-1]} is followed by H{levels[i]}.", "Keep heading levels sequential."))
            break
    # ---- images
    if p.images:
        missing = [i for i in p.images if i.get("alt") is None or not str(i.get("alt")).strip()]
        if missing:
            findings.append(_f("medium", "accessibility", f"{len(missing)} of {len(p.images)} images lack alt text",
                               "Images without alt are invisible to screen readers and image search.",
                               "Describe each meaningful image; use alt=\"\" for decorative ones."))
        else:
            findings.append(_f("good", "accessibility", "All images have alt text", f"{len(p.images)} images checked."))
        undim = [i for i in p.images if not i.get("width") or not i.get("height")]
        if undim:
            findings.append(_f("low", "performance", f"{len(undim)} images without width/height",
                               "Missing dimensions cause layout shift (CLS).", "Set width and height attributes."))
    # ---- links
    internal = external = 0
    for l in p.links:
        href = l["href"]
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        abs_url = urllib.parse.urljoin(page["final_url"], href)
        if urllib.parse.urlparse(abs_url).netloc == parsed.netloc:
            internal += 1
        else:
            external += 1
    if internal < 3:
        findings.append(_f("medium", "technical", f"Only {internal} internal links",
                           "Thin internal linking limits crawl paths and authority flow.",
                           "Link to related pages from the body copy."))
    else:
        findings.append(_f("good", "technical", f"{internal} internal links", f"{external} external links."))
    # ---- weight / render dependence
    kb = round(page["bytes"] / 1024)
    if kb > 500:
        findings.append(_f("medium", "performance", f"HTML document is {kb} KB",
                           "Large documents delay first render.", "Trim inline payloads and split code."))
    else:
        findings.append(_f("good", "performance", f"HTML document is {kb} KB", f"Fetched in {page['ms']} ms."))
    if p.text_chars < 500 and p.scripts_external > 3:
        findings.append(_f("high", "technical", "Little text in the served HTML",
                           f"Only ~{p.text_chars} characters of text with {p.scripts_external} external scripts — "
                           "content is likely rendered client-side. This audit does not execute JavaScript, so "
                           "crawler-visible content cannot be confirmed here.",
                           "Server-render or pre-render primary content."))

    # ---- structured data
    types = schema_types(p.jsonld)
    if not p.jsonld:
        findings.append(_f("high", "schema", "No JSON-LD structured data",
                           "No schema.org markup was found in the served HTML.",
                           "Add Organization/WebSite plus a page-type schema."))
    else:
        findings.append(_f("good", "schema", f"Structured data present: {', '.join(types) or 'unparsed'}",
                          f"{len(p.jsonld)} JSON-LD block(s)."))
        if any("__parse_error__" in b for b in p.jsonld if isinstance(b, dict)):
            findings.append(_f("high", "schema", "A JSON-LD block failed to parse",
                               "Malformed structured data is ignored by search engines.", "Validate the JSON-LD."))

    # ---- AEO / GEO (answer & generative engine readiness)
    q_headings = [t for _, t in p.headings if t.lower().startswith(QUESTION_WORDS) or t.strip().endswith("?")]
    aeo_types = {"FAQPage", "QAPage", "HowTo", "Question"}
    has_aeo_schema = bool(aeo_types & set(types))
    if has_aeo_schema:
        findings.append(_f("good", "aeo", "Answer-engine schema present", ", ".join(sorted(aeo_types & set(types)))))
    else:
        findings.append(_f("medium", "aeo", "No FAQ/HowTo/QA schema",
                           "Answer engines prefer explicitly marked question–answer pairs.",
                           "Add FAQPage or HowTo schema for the questions this page answers."))
    if q_headings:
        findings.append(_f("good", "aeo", f"{len(q_headings)} question-shaped heading(s)",
                          "; ".join(h[:60] for h in q_headings[:3])))
    else:
        findings.append(_f("medium", "aeo", "No question-shaped headings",
                           "Passages that answer a stated question are the unit AI answers quote.",
                           "Add headings phrased as the questions buyers actually ask, each followed by a direct answer."))
    author_signal = bool({"Person", "Article", "NewsArticle", "BlogPosting"} & set(types)) or \
        "author" in p.meta or any("author" in (k or "") for k in p.meta)
    if not author_signal:
        findings.append(_f("medium", "geo", "No author or article attribution",
                           "Experience and authorship signals (E-E-A-T) are absent from the markup.",
                           "Mark up an author with credentials and a published/updated date."))
    else:
        findings.append(_f("good", "geo", "Authorship/article signals present", "Author or Article schema detected."))
    if not p.og.get("og:title"):
        findings.append(_f("low", "geo", "No Open Graph title",
                           "Shared and cited links render without a controlled title.", "Add og:title, og:description, og:image."))
    else:
        findings.append(_f("good", "geo", "Open Graph tags present", p.og.get("og:title", "")[:100]))

    # ---- site-level files
    site = site_files(origin)
    if site["robots"]["ok"]:
        findings.append(_f("good", "technical", "robots.txt found",
                          f"{site['robots']['sitemaps'] and 'declares sitemap' or 'no sitemap directive'}."))
        if not site["robots"]["sitemaps"]:
            findings.append(_f("low", "technical", "robots.txt declares no sitemap",
                               "Crawlers must discover the sitemap another way.", "Add a Sitemap: line."))
    else:
        findings.append(_f("medium", "technical", "No robots.txt", f"{origin}/robots.txt did not return 200.",
                           "Publish robots.txt and reference your sitemap."))
    if site["sitemap"]["ok"]:
        findings.append(_f("good", "technical", f"XML sitemap found ({site['sitemap']['urls']} URLs)", site["sitemap"]["url"]))
    else:
        findings.append(_f("high", "technical", "No XML sitemap at the usual location",
                           f"{origin}/sitemap.xml did not return a sitemap.", "Publish and submit an XML sitemap."))
    if site["llms"]["ok"]:
        findings.append(_f("good", "geo", "llms.txt published", f"{site['llms']['bytes']} bytes — guides AI crawlers."))
    else:
        findings.append(_f("low", "geo", "No llms.txt",
                           "An emerging convention for telling AI crawlers what matters on your site.",
                           "Publish /llms.txt summarising the site and key URLs."))

    findings.sort(key=lambda f: SEV_ORDER.get(f["severity"], 9))
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1

    return {
        "url": url,
        "final_url": page["final_url"],
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": page["status"],
        "load_ms": page["ms"],
        "bytes": page["bytes"],
        "provenance": "verified",
        "note": "Findings come from fetching and parsing the served HTML. JavaScript is not executed."
                + (f" {p_err}" if p_err else ""),
        "page": {
            "title": p.title, "description": desc, "canonical": p.canonical, "lang": p.lang,
            "h1": h1s[0] if h1s else "", "headings": len(p.headings), "images": len(p.images),
            "internal_links": internal, "external_links": external,
            "schema_types": types, "text_chars": p.text_chars,
        },
        "site": site,
        "counts": counts,
        "score": health_score(counts),
        "findings": findings,
    }


def health_score(counts: dict[str, int]) -> int:
    """Blunt, explainable score: penalties per severity, floored at 0."""
    penalty = (counts.get("critical", 0) * 25 + counts.get("high", 0) * 10
               + counts.get("medium", 0) * 4 + counts.get("low", 0) * 1)
    return max(0, 100 - penalty)


def site_files(origin: str) -> dict[str, Any]:
    """Check robots.txt, sitemap.xml and llms.txt — all really fetched."""
    out: dict[str, Any] = {}
    r = fetch(f"{origin}/robots.txt", timeout=10)
    sitemaps: list[str] = []
    if r["ok"] and r["status"] == 200 and "html" not in (r["headers"].get("content-type", "")):
        for line in r["body"].splitlines():
            if line.lower().startswith("sitemap:"):
                sitemaps.append(line.split(":", 1)[1].strip())
        out["robots"] = {"ok": True, "sitemaps": sitemaps, "bytes": r["bytes"]}
    else:
        out["robots"] = {"ok": False, "sitemaps": []}

    sm_url = sitemaps[0] if sitemaps else f"{origin}/sitemap.xml"
    s = fetch(sm_url, timeout=12)
    if s["ok"] and s["status"] == 200 and ("<urlset" in s["body"] or "<sitemapindex" in s["body"]):
        out["sitemap"] = {"ok": True, "url": sm_url, "urls": s["body"].count("<loc>"),
                          "index": "<sitemapindex" in s["body"]}
    else:
        out["sitemap"] = {"ok": False, "url": sm_url, "urls": 0}

    l = fetch(f"{origin}/llms.txt", timeout=8)
    out["llms"] = {"ok": bool(l["ok"] and l["status"] == 200 and l["bytes"] > 0), "bytes": l.get("bytes", 0)}
    return out


# ---------------------------------------------------- rendered-DOM comparison

def _parse(html: str) -> PageParser:
    parser = PageParser()
    try:
        parser.feed(html)
    except Exception:
        pass  # malformed markup: keep whatever was parsed before the fault
    return parser


def render_gap(url: str, served_html: str, *, viewport: str = "desktop") -> dict[str, Any]:
    """Compare the HTML the server sends with the DOM after JavaScript runs.

    This is the question a non-rendering audit cannot answer: is the content
    that ranks actually in the response, or does it only appear once scripts
    execute? Google renders, but not on the first pass and not reliably for
    every page, and most AI crawlers do not render at all -- so content that
    exists only after JavaScript is a real ranking and citation risk.
    """
    import browser  # imported lazily so audits still work without a browser

    result = browser.render(url, viewport=viewport)
    served, rendered = _parse(served_html), _parse(result["html"])

    served_h1 = next((t for lvl, t in served.headings if lvl == 1), "")
    rendered_h1 = next((t for lvl, t in rendered.headings if lvl == 1), "")
    served_types, rendered_types = set(schema_types(served.jsonld)), set(schema_types(rendered.jsonld))

    findings: list[dict[str, str]] = []
    text_gain = rendered.text_chars - served.text_chars

    if served.text_chars < 200 and rendered.text_chars > served.text_chars * 3:
        findings.append(_f("critical", "rendering", "Page content exists only after JavaScript",
                           f"The server sends ~{served.text_chars} characters of text; the rendered page has "
                           f"~{rendered.text_chars}. A crawler that does not execute JavaScript sees almost "
                           "nothing.",
                           "Server-render or pre-render the primary content."))
    elif text_gain > max(400, served.text_chars * 0.5):
        findings.append(_f("high", "rendering", f"{text_gain} characters of text appear only after JavaScript",
                           f"Served: ~{served.text_chars} characters. Rendered: ~{rendered.text_chars}.",
                           "Move content that must rank into the server response."))
    else:
        findings.append(_f("good", "rendering", "Content is present without JavaScript",
                           f"Served ~{served.text_chars} characters, rendered ~{rendered.text_chars}."))

    if served.title != rendered.title:
        findings.append(_f("high", "rendering", "Title changes when JavaScript runs",
                           f"Served: {served.title or '(none)'!r} — Rendered: {rendered.title or '(none)'!r}.",
                           "Set the final title server-side; crawlers may index the served one."))
    if served_h1 != rendered_h1:
        findings.append(_f("medium", "rendering", "H1 changes when JavaScript runs",
                           f"Served: {served_h1 or '(none)'!r} — Rendered: {rendered_h1 or '(none)'!r}.",
                           "Render the H1 server-side."))

    only_rendered = rendered_types - served_types
    if only_rendered:
        findings.append(_f("high", "rendering", "Structured data is injected by JavaScript",
                           f"Only in the rendered DOM: {', '.join(sorted(only_rendered))}.",
                           "Emit JSON-LD in the server response; several crawlers never see injected schema."))
    elif rendered_types:
        findings.append(_f("good", "rendering", "Structured data is in the server response",
                          ", ".join(sorted(rendered_types))))

    link_gain = len(rendered.links) - len(served.links)
    if link_gain > 5:
        findings.append(_f("medium", "rendering", f"{link_gain} links appear only after JavaScript",
                           f"Served {len(served.links)} links, rendered {len(rendered.links)}.",
                           "Crawl paths that exist only after rendering are discovered late, if at all."))

    return {
        "viewport": viewport,
        "render_ms": result["ms"],
        "provenance": "verified",
        "served": {"text_chars": served.text_chars, "title": served.title, "h1": served_h1,
                   "links": len(served.links), "schema_types": sorted(served_types)},
        "rendered": {"text_chars": rendered.text_chars, "title": rendered.title, "h1": rendered_h1,
                     "links": len(rendered.links), "schema_types": sorted(rendered_types)},
        "findings": findings,
        "note": "The served figures are what a non-rendering crawler sees; the rendered figures are what a "
                "browser shows after scripts run.",
    }
