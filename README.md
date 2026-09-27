# Gem Agency

A local AI agency dashboard. You describe a website idea. Gem Agency researches it, designs and builds it, checks it, hands it to you for review and launch, then keeps working on its search traffic.

It runs on your own machine: a Python server, a SQLite database and a browser dashboard. Claude and ChatGPT are the brain, with a local Ollama model as the fallback.

![Overview, before and after the refinement pass](docs/ui-refine/overview-before-after.png)

---

## What it does

| Section | What you get |
|---|---|
| **Overview** | Every website with a real preview (a built page or a screenshot of the live site), its stage, and the one decision waiting on you. |
| **Websites** | Each project's pipeline: idea → research → design/content → build and checks → review → launch → growth. Includes a "Mark as live" step that asks for the published address. |
| **Builder** | Pipeline runs, their stages and their outputs. |
| **Growth (SEO / AEO / GEO)** | Audits, findings, keywords and backlinks. These stages always run on Claude or ChatGPT, never on the local model. With no data source connected, it says so and invents nothing. |
| **Approvals** | Anything that needs your sign-off before the pipeline continues. |
| **Jarvis** | Voice assistant. Open conversation, like ChatGPT voice. Hands-free mode, interrupt it by speaking, sentence-by-sentence speech. It can navigate and trigger actions from a checked list. |
| **Computer use** | Claude drives your installed Chrome through Playwright (headless, or visible so you can watch) to do browser tasks. Local and private network addresses are blocked. Sessions are saved under `workspace/computer`. |
| **Image & Video** | 402 image and video models from [Open Generative AI](https://github.com/anil-matcha/open-generative-ai), run through the Muapi API. Supports text-to-X, image-to-X and uploads. |
| **Models** | Connect Claude, ChatGPT and Ollama. See each provider's health and the order the brain chain will try them. |

### The brain chain

Every AI call tries **Claude → ChatGPT → Ollama (local)**, in that order.

- A provider that is failing (out of credit, rate limited, offline) moves to the back of the line for 15 minutes. You see the real error in the UI.
- Streaming replies fall back only before the first word arrives, so an answer never switches brain halfway through.
- Ollama only uses models installed on your machine. Cloud models are refused.
- SEO, AEO and GEO work is locked to Claude or ChatGPT.

---

## Requirements

- **Windows 10/11, macOS or Linux.** It is built and used day to day on Windows.
- **Python 3.11+**
- **Google Chrome**, for website screenshots and computer use.
- **At least one brain:**
  - an Anthropic API key or a signed-in `ant` CLI, or
  - an OpenAI API key, or
  - [Ollama](https://ollama.com) with a local model, e.g. `ollama pull llama3.1`
- *Optional:*
  - a Muapi API key (Image & Video)
  - Google Search Console OAuth credentials (real search data)
  - DataForSEO credentials (keyword and backlink data)
- *Optional:* Node 18+, only to re-sync the media model catalogue.

---

## Setup

```bash
git clone https://github.com/mahussain456/Gem-Agency.git
cd Gem-Agency
python -m venv .venv
```

Activate the virtual environment:

```bash
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate
```

Install the dependencies and the browser driver:

```bash
pip install -r requirements.txt
```

Playwright uses your installed Chrome or Edge, so there is no browser download. If you have neither, run `python -m playwright install chromium`.

Start the server:

```bash
python server.py
```

Open **http://127.0.0.1:51764**. On first start the server creates its databases (`agency.db` and the others) and a local access token (`.agency_token`). Nothing needs to exist beforehand.

On Windows, `start-mission-control.cmd` starts the server (plus the optional Hermes gateway). `install-mission-control-watchdog.cmd` sets up a watchdog that restarts it if it stops.

### Connect the brain

Go to **Models** in the sidebar.

| Provider | How to connect |
|---|---|
| Claude | Paste an Anthropic API key, or sign in once with the `ant` CLI (`ant auth login`). An `ANTHROPIC_API_KEY` environment variable also works. |
| ChatGPT | Paste an OpenAI API key, or set `OPENAI_API_KEY`. |
| Ollama | Install Ollama and pull a model. The dashboard finds it at `http://127.0.0.1:11434`; set `OLLAMA_HOST` to use a different address. |

After you connect a provider, the model list comes from the account itself, so model IDs are never guessed. Health on the Models page comes from real calls. A bad key or an empty balance shows the provider's actual error.

### Optional integrations

| Integration | Where | Stored in (gitignored) |
|---|---|---|
| Image & Video (Muapi) | Image & Video → API key, or `MUAPI_API_KEY` | `.muapi_credentials.json` |
| Google Search Console | Integrations → Search Console (OAuth client ID and secret) | `.gsc_credentials.json`, `.gsc_token.json` |
| DataForSEO | Integrations → DataForSEO | `.dataforseo_credentials.json` |
| Hermes gateway (optional, advanced) | Runs separately on `127.0.0.1:8643`; set `HERMES_HOME` if it is not in `%LOCALAPPDATA%\hermes` | its own `.env` |

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MISSION_CONTROL_PORT` | `51764` | Dashboard port |
| `MISSION_CONTROL_HOST` | `127.0.0.1` | Bind address. Keep it on loopback unless you add your own auth in front. |
| `HERMES_HOME` | `%LOCALAPPDATA%\hermes` | Hermes gateway home (optional) |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OLLAMA_HOST`, `MUAPI_API_KEY` | — | Alternatives to entering keys in the UI |

---

## Using it

1. **New website:** describe the idea. The pipeline runs research, design/content, build and checks.
2. **Approvals:** review what it made and approve or request changes.
3. **Launch:** publish the site yourself, then click **Mark as live** and paste the address.
4. **Growth:** audits and SEO/AEO/GEO work start on the live site.
5. **Jarvis:** click the orb or press **Ctrl/Cmd + J**, then talk. Turn on hands-free mode to keep the conversation going.
6. **Ctrl K** opens the command palette to jump anywhere.

---

## Tests

```bash
python -m unittest discover -s tests
node tests/intents.test.mjs
node tests/voice.test.mjs
```

The Python suite covers the pipeline, brain chain, Jarvis, computer use, media and API routes. External APIs are mocked, so the tests are free and need no keys. The Node tests cover Jarvis's intent parsing and speech handling.

To refresh the Image & Video catalogue from upstream:

```bash
node scripts/sync_media_models.mjs
```

---

## Project layout

```
server.py          HTTP server, static files, legacy mission/bridge APIs
agency.py          Gem Agency API: projects, runs, approvals, media, computer use, voice
runner.py          Persistent pipeline runner
playbooks.py       Pipeline definitions (website build, SEO campaign)
providers.py       Brain chain: Claude → ChatGPT → Ollama, health tracking, streaming
jarvis.py          Conversational voice endpoint and action vocabulary
computer.py        Claude computer use over Playwright + Chrome
media.py           Muapi client for Image & Video
browser.py         Headless screenshots
gsc.py, dataforseo.py, audit.py   Search data and site audits
app/q/             Dashboard frontend (vanilla ES modules + CSS)
data/              Media model catalogue (MIT, from Open Generative AI)
agency/            Specialist agent briefs
tests/             Unit tests
docs/              Architecture, design notes, screenshots
DESIGN.md          Design system (graphite + teal "Growth Command Center")
```

---

## Security and honesty rules

- **Local first.** The server binds to `127.0.0.1`. Requests must use a localhost `Host` header, which blocks DNS rebinding. Every write also needs the bearer token in `.agency_token`.
- **Secrets never enter git.** All credential files, tokens, databases, `workspace/`, `uploads/` and logs are in `.gitignore`.
- **Computer use can't reach your network.** It refuses loopback, private and link-local addresses, checked after DNS resolution.
- **No invented numbers.** Traffic, rankings, leads and spend only appear when a real source is connected. A finished build is never shown as a live website.

---

## Credits

- Image & Video model catalogue: [anil-matcha/open-generative-ai](https://github.com/anil-matcha/open-generative-ai), MIT licence. The full licence is in `data/media_models.LICENSE`.
- Built with the Anthropic and OpenAI Python SDKs and Playwright.
