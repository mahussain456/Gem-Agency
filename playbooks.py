"""Playbooks — the ordered work a project actually performs.

A playbook is a list of stages. Each stage is one of:

  llm    — sends a composed prompt to a named Hermes agent and stores the reply
  json   — same, but the reply must parse as JSON (schema stated in the prompt)
  tool   — runs local code (e.g. a real website audit); no model involved
  gate   — pauses the run and raises a human approval

Stage prompts receive a context dict: the project brief plus every earlier
artifact in this run, addressed by stage id. That is what makes the chain a
machine rather than a sequence of disconnected chats.

Honesty rules encoded in the prompts themselves:
  · agents must never invent search volumes, rankings, traffic or backlink counts
  · anything an agent proposes is 'estimated' until a real source verifies it
  · outreach is drafted, never sent — sending is gated to a human elsewhere
"""

from __future__ import annotations

from typing import Any

HOUSE_RULES = (
    "You are part of a production agency pipeline; your output is used directly, not reviewed line by line. "
    "Never invent metrics. You do not have access to search volume, keyword difficulty, traffic, ranking "
    "position or backlink data — if a number would be needed, name the source that must supply it instead of "
    "guessing. Be specific and concrete; no filler, no 'it depends' without an answer. Write for a smart "
    "operator who wants to act on this immediately."
)


def _ctx(context: dict[str, Any], key: str, limit: int = 4000) -> str:
    """Pull a prior artifact into a prompt, trimmed."""
    val = context.get(key)
    if val is None:
        return "(not available)"
    if isinstance(val, (dict, list)):
        import json
        val = json.dumps(val, ensure_ascii=False, indent=2)
    return str(val)[:limit]


SKIP_REBUILD_BELOW = 0.15   # p(worth rebuilding) under which the repair stage is skipped


def _repair_needed(c: dict[str, Any]) -> tuple[bool, str]:
    """Decide whether the repair stage has anything worth doing.

    The cheap, certain cases are settled in code: no measurements means run it,
    no defects means skip it. Only the genuinely marginal case -- defects exist
    but none are serious -- is worth asking a model about, and that is a typed
    decision, not an essay, so it goes to Jev rather than costing a frontier
    call on a 20,000-token page.

    Returns (skip, reason). When Jev is not connected the stage simply runs;
    nothing is assumed on its behalf.
    """
    qa = c.get("visual_qa")
    if not isinstance(qa, dict):
        return False, ""
    defects = qa.get("defects") or []
    if not defects:
        return True, "No defects were measured in the build, so there is nothing to repair."

    serious = [d for d in defects if d.get("severity") in ("critical", "high")]
    if serious:
        return False, f"{len(serious)} serious defect(s) measured; repairing."

    try:
        import jev
        if not jev.connected():
            return False, f"{len(defects)} minor defect(s); repairing (no decision engine to triage)."
        summary = "\n".join(
            f"- [{d.get('severity')}] {d.get('title')}: {d.get('detail', '')[:160]}" for d in defects[:12])
        res = jev.decide(
            f"A generated web page was measured in a real browser. Defects found:\n{summary}",
            {"worth_rebuilding": jev.noul(
                "Do these defects justify regenerating the whole page?",
                {"yes": "at least one defect would be visible to a visitor or hurt search ranking",
                 "no": "all of them are cosmetic or pedantic and a rebuild risks more than it fixes"})},
            timeout=20)
        answer = res["answers"]["worth_rebuilding"]
        p = jev.probability(answer)
        # Skip only when the engine is sure the defects are cosmetic. Measured on Laya's
        # checkpoints (2026-10-07), a broken contact form scored 0.23 and a hidden mobile
        # call-to-action 0.31: at the usual 0.5 those repairs would have been skipped.
        if p < SKIP_REBUILD_BELOW:
            return True, (f"{res.get('engine_label', 'The decision engine')} judged {len(defects)} minor "
                          f"defect(s) not worth a rebuild (p={p:.2f}, {res['ms']}ms).")
        return False, (f"{res.get('engine_label', 'The decision engine')} judged the defects worth "
                       f"repairing (p={p:.2f}, {res['ms']}ms).")
    except Exception as exc:
        # A triage failure must never skip real work.
        return False, f"{len(defects)} minor defect(s); repairing (triage unavailable: {exc})."


# --------------------------------------------------------------------------
# Website build — idea to launched, ranking-ready site
# --------------------------------------------------------------------------

WEBSITE_BUILD = {
    "id": "website_build",
    "name": "Website build — idea to launch",
    "summary": "Takes a raw idea to a built, SEO/AEO/GEO-ready site with a real audit before launch.",
    "stages": [
        {
            "id": "positioning", "title": "Positioning & audience", "kind": "llm", "agent": "scout",
            "output": "markdown",
            "prompt": lambda c: f"""A new website is being built.

IDEA: {c['brief']}
WORKING NAME: {c['name']}

Produce a positioning brief in markdown with these sections:
1. **Who this is for** — the specific buyer, their situation, and the moment they start looking.
2. **The problem** — in their words, not marketing words.
3. **The promise** — one sentence a visitor should be able to repeat after 5 seconds on the page.
4. **Why us** — three differentiators that a competitor could not copy by tomorrow.
5. **Objections** — the four reasons they will not buy, each with the counter.
6. **Primary conversion** — the single action the site must drive, and the secondary fallback.

{HOUSE_RULES}""",
        },
        {
            "id": "research", "title": "Market & competitor research", "kind": "llm", "agent": "scout",
            "output": "markdown",
            "prompt": lambda c: f"""Positioning brief:
{_ctx(c, 'positioning')}

Produce a competitive landscape in markdown:
1. **Likely competitors** — name the kinds of sites that will rank for this, and any specific well-known players you are confident exist. Mark any you are unsure about as "to verify".
2. **What the buyer compares** — the criteria used to choose between options.
3. **Content that wins here** — the formats that actually rank for this intent (calculators, comparisons, local pages, guides).
4. **Table stakes** — what every competitor has; the site fails without it.
5. **The wedge** — the angle currently underserved.

Do not state traffic, domain authority, or ranking numbers — you cannot see them. Where such a number would matter, write "verify in Search Console/OpenSEO".

{HOUSE_RULES}""",
        },
        {
            "id": "sitemap", "title": "Sitemap & URL architecture", "kind": "json", "agent": "orchestrator",
            "output": "json",
            "prompt": lambda c: f"""Positioning:
{_ctx(c, 'positioning', 2500)}

Research:
{_ctx(c, 'research', 2500)}

Design the site's information architecture. Reply with JSON exactly in this shape:
{{"pages": [{{"url": "/", "title": "...", "type": "home|service|location|product|article|about|contact|comparison",
  "purpose": "one line", "primary_keyword_theme": "the topic this page owns",
  "parent": "/ or null", "priority": 1-5}}],
 "url_rules": ["rule about slug style", "rule about depth", "..."],
 "internal_linking": [{{"from": "/a", "to": "/b", "reason": "..."}}]}}

Rules: every page must own a distinct topic (no two pages competing for the same theme), depth no greater than 3 clicks, slugs lowercase and hyphenated. 6–12 pages for a first launch.

{HOUSE_RULES}""",
        },
        {
            "id": "copy", "title": "Homepage copy", "kind": "llm", "agent": "scribe",
            "output": "markdown",
            "prompt": lambda c: f"""Positioning:
{_ctx(c, 'positioning', 3000)}

Sitemap:
{_ctx(c, 'sitemap', 1500)}

Write the complete homepage copy in markdown, section by section, ready to paste into a build:
- Hero: H1 (under 12 words), subhead (under 25 words), primary CTA label, secondary CTA label
- Proof strip: what goes here (logos, numbers, credentials) — if a number is needed, write [CLIENT TO SUPPLY: …] rather than inventing one
- Problem section: 3 short paragraphs in the buyer's language
- Solution / how it works: 3 steps, each a heading plus two sentences
- Differentiators: 3 blocks, heading + 2 sentences each
- Objection handling: 4 Q&A pairs, phrased as the questions buyers actually type
- Final CTA: heading, one line, button label

Constraints: one idea per sentence, no adjective stacking, no "innovative/seamless/cutting-edge". The Q&A pairs must be genuine questions someone would ask an answer engine.

{HOUSE_RULES}""",
        },
        {
            "id": "seo_plan", "title": "On-page SEO plan", "kind": "json", "agent": "rank",
            "output": "json",
            "prompt": lambda c: f"""Sitemap:
{_ctx(c, 'sitemap', 3000)}

Positioning:
{_ctx(c, 'positioning', 1500)}

Produce the on-page plan. Reply with JSON exactly in this shape:
{{"pages": [{{"url": "/", "title_tag": "50-60 chars", "meta_description": "140-160 chars",
   "h1": "...", "target_theme": "...", "supporting_terms": ["...", "..."],
   "internal_links_out": ["/x", "/y"]}}],
 "keyword_candidates": [{{"keyword": "...", "intent": "informational|commercial|transactional|navigational|local",
   "page": "/the-url-that-should-own-it", "rationale": "why this term matters"}}],
 "notes": ["..."]}}

CRITICAL: do NOT include search volume, difficulty, CPC or traffic estimates for any keyword — you cannot measure them. Those fields will be filled from Google Search Console once connected. Give intent and rationale only.

{HOUSE_RULES}""",
        },
        {
            "id": "aeo_geo", "title": "AEO / GEO plan", "kind": "markdown", "agent": "rank",
            "output": "markdown",
            "prompt": lambda c: f"""Homepage copy:
{_ctx(c, 'copy', 2500)}

SEO plan:
{_ctx(c, 'seo_plan', 2000)}

Produce the answer-engine (AEO) and generative-engine (GEO) plan in markdown — how this site gets quoted by Google AI Overviews, ChatGPT, Perplexity and Bing Copilot:

1. **Quotable passages** — write 5 answer blocks, each: the exact question as a heading, then a 40–60 word self-contained answer that makes sense quoted alone with no surrounding context.
2. **Entity clarity** — the entities this site must unambiguously establish (organisation, service, location, people) and where each is stated.
3. **Schema to implement** — which schema.org types on which URLs, and why each earns a rich result or citation.
4. **llms.txt** — the exact content to publish at /llms.txt.
5. **Citation surface** — what makes a passage citable here (definitions, numbers with sources, steps, comparisons) and what to avoid.
6. **Freshness signals** — what must carry visible published/updated dates and author attribution.

{HOUSE_RULES}""",
        },
        {
            "id": "schema", "title": "Structured data (JSON-LD)", "kind": "json", "agent": "dev",
            "output": "json",
            "prompt": lambda c: f"""AEO/GEO plan:
{_ctx(c, 'aeo_geo', 2500)}

Sitemap:
{_ctx(c, 'sitemap', 1500)}

Produce production-ready JSON-LD. Reply with JSON exactly in this shape:
{{"blocks": [{{"url": "/", "jsonld": {{ ...valid schema.org JSON-LD object... }}}}],
 "notes": ["..."]}}

Include at minimum: Organization or LocalBusiness, WebSite with SearchAction, and an FAQPage built from the real Q&A pairs in the copy. Use placeholder values in the form "[CLIENT: phone]" where real data is required — never fabricate an address, phone number, rating or review.

{HOUSE_RULES}""",
        },
        {
            "id": "build", "title": "Build the site", "kind": "llm", "agent": "dev",
            "output": "html", "writes_file": "index.html",
            "prompt": lambda c: f"""Build the homepage as a single self-contained HTML file.

COPY (use this text — do not rewrite it):
{_ctx(c, 'copy', 5000)}

ON-PAGE SEO (use these exact tags):
{_ctx(c, 'seo_plan', 2000)}

JSON-LD to embed:
{_ctx(c, 'schema', 2500)}

Requirements:
- One complete HTML5 document. All CSS inline in a <style> block. No external requests of any kind (no CDN, no web fonts, no tracking) — the file must render offline.
- Correct <title>, meta description, canonical, viewport, lang, and Open Graph tags.
- Exactly one H1; sequential heading levels; semantic landmarks (header/nav/main/section/footer).
- Every image an inline SVG placeholder with meaningful alt text — do not hotlink images.
- Embed the JSON-LD in a <script type="application/ld+json"> block.
- The FAQ section must be real markup (details/summary or headed Q&A), matching the FAQPage schema.
- Responsive with CSS grid/flex, readable at 360px and 1440px. Accessible contrast, visible focus states.
- Include a visible published/updated date and author line.

Reply with the HTML document only — no explanation, no markdown fences.

{HOUSE_RULES}""",
        },
        {
            "id": "review_gate", "title": "Operator review before launch", "kind": "gate",
            "gate_kind": "deploy",
            "gate_detail": "The site is built and audited below. Approve to mark it launch-ready.",
        },
        {
            "id": "self_audit", "title": "Audit the built page", "kind": "tool", "tool": "audit_artifact",
            "output": "json",
        },
        {
            "id": "visual_qa", "title": "See the built page", "kind": "tool", "tool": "visual_qa",
            "output": "json",
        },
        {
            "id": "repair", "title": "Fix what the browser found", "kind": "llm", "agent": "dev",
            "output": "html", "writes_file": "index.html", "backup_first": True,
            "skip_if": _repair_needed,
            "prompt": lambda c: f"""You are repairing a page that has already been built and inspected.

These defects were measured by auditing the file and rendering it in a real browser at
mobile, tablet and desktop widths. They are facts, not opinions:

AUDIT FINDINGS:
{_ctx(c, 'self_audit', 3000)}

BROWSER MEASUREMENTS (layout, render gap, screenshots):
{_ctx(c, 'visual_qa', 3500)}

THE CURRENT PAGE (repair this exact document):
{_ctx(c, 'build', 22000)}

Rules for the repair:
- Fix only the defects listed above. Do not redesign, restyle or reword anything that was not flagged.
- Keep every word of the existing copy, all structured data, and all existing sections intact.
- Where a layout defect names an element and an overhang in pixels, fix that element specifically —
  wrap long words, reduce the size at that breakpoint, or constrain the width. Do not hide overflow.
- Preserve the constraints of the original build: one complete HTML5 document, all CSS inline in a
  <style> block, no external requests of any kind, inline SVG placeholders only.
- If a listed defect cannot be fixed without breaking something else, leave it and add an HTML comment
  at the end of <body> saying which one and why.

Reply with the complete corrected HTML document only — no explanation, no markdown fences.

{HOUSE_RULES}""",
        },
        {
            "id": "verify_repair", "title": "Verify the repair", "kind": "tool",
            "tool": "verify_repair", "output": "json",
        },
        {
            "id": "launch_plan", "title": "Launch & ranking plan", "kind": "llm", "agent": "orchestrator",
            "output": "markdown",
            "prompt": lambda c: f"""Audit of the built page:
{_ctx(c, 'self_audit', 2500)}

What the browser measured (layout at mobile/tablet/desktop, render gap):
{_ctx(c, 'visual_qa', 2000)}

Result of the repair pass:
{_ctx(c, 'verify_repair', 1500)}

AEO/GEO plan:
{_ctx(c, 'aeo_geo', 1200)}

Write the launch and ranking plan in markdown:
1. **Fix first** — every issue from the audit above that must be resolved before launch, in priority order, each with the specific fix.
2. **Launch day** — the ordered checklist (DNS, HTTPS, robots.txt, sitemap submission to Google Search Console AND Bing Webmaster Tools, analytics, indexing request).
3. **Week one** — what to do, in order.
4. **Weeks 2–8** — the content and link cadence that moves this site toward page one, week by week.
5. **What to measure and where** — name the actual source for each metric (Search Console for impressions/position, Bing Webmaster for Bing, the audit tool for technical health). State plainly that ranking numbers are unavailable until Search Console is connected.
6. **Kill criteria** — the signals that mean this approach is not working and what to change.

{HOUSE_RULES}""",
        },
    ],
}


# --------------------------------------------------------------------------
# SEO campaign — take an existing site toward page one
# --------------------------------------------------------------------------

SEO_CAMPAIGN = {
    "id": "seo_campaign",
    "name": "SEO campaign — audit to page one",
    "summary": "Audits a real URL, then plans fixes, content, AEO/GEO and ethical outreach from what it found.",
    "stages": [
        {
            "id": "audit", "title": "Technical audit (real fetch)", "kind": "tool", "tool": "audit_url",
            "output": "json",
        },
        {
            "id": "render_check", "title": "Render check (real browser)", "kind": "tool",
            "tool": "render_check", "output": "json",
        },
        {
            "id": "fix_plan", "title": "Technical fix plan", "kind": "llm", "agent": "rank",
            "output": "markdown",
            "prompt": lambda c: f"""A real audit was just run against {c.get('url', 'the site')}. These findings come from
actually fetching and parsing the page — they are verified, not estimated:

{_ctx(c, 'audit', 6000)}

Write the technical fix plan in markdown:
1. **Fix this week** — every critical and high finding, each with: the issue, the exact fix, where to make it, and what it unlocks.
2. **Fix this month** — medium findings, same format.
3. **Worth doing** — low findings.
4. **Already correct** — briefly acknowledge what passed, so nobody "fixes" it.
5. **Cannot be judged from HTML alone** — what still needs Search Console, a crawl of the full site, or field performance data, and why.

Reference the findings above specifically. Do not invent issues that were not reported.

{HOUSE_RULES}""",
        },
        {
            "id": "keywords", "title": "Keyword & intent map", "kind": "json", "agent": "rank",
            "output": "json",
            "prompt": lambda c: f"""Site audited: {c.get('url', '')}
What the page is about, from the audit:
{_ctx(c, 'audit', 2500)}

Business context: {c['brief']}

Produce the keyword and intent map. Reply with JSON exactly in this shape:
{{"clusters": [{{"cluster": "topic name", "intent": "informational|commercial|transactional|local",
   "pillar_page": "/suggested-url", "keywords": ["term", "term"],
   "questions": ["question a buyer would ask an AI assistant"],
   "content_type": "guide|comparison|calculator|location page|product page|FAQ",
   "why_this_wins": "one line"}}],
 "quick_wins": [{{"keyword": "...", "why": "...", "action": "..."}}],
 "cannibalisation_risks": ["..."]}}

CRITICAL: no search volumes, no difficulty scores, no traffic estimates, no ranking positions — you cannot measure any of those. They will be filled from Google Search Console. Provide terms, intent, grouping and reasoning only.

{HOUSE_RULES}""",
        },
        {
            "id": "measure_keywords", "title": "Measure the keywords", "kind": "tool",
            "tool": "measure_keywords", "output": "json",
        },
        {
            "id": "content_plan", "title": "Content plan & briefs", "kind": "markdown", "agent": "scribe",
            "output": "markdown",
            "prompt": lambda c: f"""Keyword map:
{_ctx(c, 'keywords', 3000)}

Measured demand for those terms (volume, difficulty, CPC). Where a figure is present it is
real, measured data — prioritise by it. Where it says unmeasured or the source is not
connected, say so plainly and prioritise on intent and business value instead; do not
invent a number to fill the gap:
{_ctx(c, 'measure_keywords', 2500)}

Produce the content plan in markdown:
1. **Publishing order** — the first 8 pieces, in the order they should ship, each with: working title, target cluster, URL, format, and the single job that piece does.
2. **Full brief for piece #1** — including: search intent, the exact questions to answer, required H2/H3 outline, entities and terms to include, internal links in and out, schema to mark up, word range, and the 40–60 word quotable answer block that goes near the top.
3. **Refresh candidates** — how to decide when to update an existing page instead of writing a new one.
4. **Internal linking** — the rules for connecting this cluster.

{HOUSE_RULES}""",
        },
        {
            "id": "aeo_geo", "title": "AEO / GEO strategy", "kind": "markdown", "agent": "rank",
            "output": "markdown",
            "prompt": lambda c: f"""Audit findings:
{_ctx(c, 'audit', 2500)}

Keyword map:
{_ctx(c, 'keywords', 2500)}

Write the answer-engine and generative-engine strategy in markdown — how this site earns citations in Google AI Overviews, ChatGPT, Perplexity and Bing Copilot:
1. **Current AEO/GEO state** — what the audit showed about schema, question headings, authorship, llms.txt.
2. **Quotable passage library** — 6 question/answer pairs, each answer 40–60 words, self-contained.
3. **Schema roadmap** — types, URLs, and the citation or rich result each targets.
4. **Entity & authority** — how to make the organisation and its people unambiguous to an AI (sameAs, author bios, credentials, consistent NAP if local).
5. **llms.txt** — the exact file content to publish.
6. **Bing & Copilot specifics** — what differs from Google, including IndexNow and Bing Webmaster Tools.
7. **How to check it worked** — the real ways to test AI visibility, and an honest statement that AI citation share has no official API and must be spot-checked manually.

{HOUSE_RULES}""",
        },
        {
            "id": "backlinks", "title": "Ethical backlink prospects", "kind": "json", "agent": "reach",
            "output": "json",
            "prompt": lambda c: f"""Business: {c['brief']}
Site: {c.get('url', '')}
Content plan:
{_ctx(c, 'content_plan', 2500)}

Propose ethical link-earning opportunities. Reply with JSON exactly in this shape:
{{"prospects": [{{"domain": "example.com", "kind": "resource_page|broken_link|guest_post|digital_pr|partnership|unlinked_mention",
   "why_relevant": "one line", "the_asset": "what of ours they would link to",
   "draft": "a personalised 60-90 word outreach email, specific to them, no template language",
   "confidence": "high|medium|low — how sure you are this site exists and accepts this kind of link"}}],
 "linkable_assets_to_build": [{{"asset": "...", "why_people_link": "..."}}],
 "avoid": ["..."]}}

Rules: 8–12 prospects, real site types that plausibly exist — every one will be verified by fetching it before any contact, and anything unreachable is discarded. Absolutely no PBNs, paid links, link exchanges, comment/forum spam, or scraped contact blasts. Each draft must reference something specific about that site. Nothing is ever sent without human approval.

{HOUSE_RULES}""",
        },
        {
            "id": "verify_prospects", "title": "Verify prospects (real fetch)", "kind": "tool",
            "tool": "verify_prospects", "output": "json",
        },
        {
            "id": "outreach_gate", "title": "Approve outreach programme", "kind": "gate",
            "gate_kind": "outreach",
            "gate_detail": "Verified prospects and drafts are ready. Approving records your sign-off on the programme; each individual send still requires its own approval.",
        },
        {
            "id": "measurement", "title": "Measurement & reporting plan", "kind": "llm", "agent": "orchestrator",
            "output": "markdown",
            "prompt": lambda c: f"""Fix plan:
{_ctx(c, 'fix_plan', 2000)}
Content plan:
{_ctx(c, 'content_plan', 1500)}

Write the measurement plan in markdown:
1. **Baseline to capture now** — exactly what to record today, and from which source.
2. **Weekly review** — the 5 numbers to check, each with its real source (Google Search Console, Bing Webmaster Tools, the audit tool). State clearly which of these are not yet connected in this system and therefore unavailable.
3. **Leading vs lagging** — which movements predict rankings and which merely confirm them.
4. **Reporting to the client** — what goes in the monthly report and what must never be claimed without a verified source.
5. **Decision rules** — at what point you double down, change the angle, or stop.

{HOUSE_RULES}""",
        },
    ],
}


PLAYBOOKS: dict[str, dict[str, Any]] = {p["id"]: p for p in (WEBSITE_BUILD, SEO_CAMPAIGN)}

# Search work: SEO, AEO and GEO. These stages think only with Claude or
# ChatGPT (providers.CLOUD) -- never the local Ollama fallback or the gateway.
# One explicit list, so what counts as search work is reviewable in one place.
SEARCH_STAGES: dict[str, tuple[str, ...]] = {
    "website_build": ("sitemap", "seo_plan", "aeo_geo", "schema", "launch_plan"),
    "seo_campaign": ("fix_plan", "keywords", "content_plan", "aeo_geo", "backlinks", "measurement"),
}
for _pid, _ids in SEARCH_STAGES.items():
    _by_id = {s["id"]: s for s in PLAYBOOKS[_pid]["stages"]}
    for _sid in _ids:
        _by_id[_sid]["provider"] = "cloud"       # providers.CLOUD; a literal keeps this module import-light
        _by_id[_sid]["search"] = True


def get(playbook_id: str) -> dict[str, Any]:
    if playbook_id not in PLAYBOOKS:
        raise KeyError(f"unknown playbook '{playbook_id}'")
    return PLAYBOOKS[playbook_id]


def summaries() -> list[dict[str, Any]]:
    return [{"id": p["id"], "name": p["name"], "summary": p["summary"],
             "stages": [{"id": s["id"], "title": s["title"], "kind": s["kind"],
                         "agent": s.get("agent", ""), "tool": s.get("tool", ""),
                         "search": bool(s.get("search")), "provider": s.get("provider", "")}
                        for s in p["stages"]]}
            for p in PLAYBOOKS.values()]
