# 🌐 Web Browser MCP Server

> A Python **Model Context Protocol (MCP)** server that drives a **real Chrome browser** with Playwright — research the web in parallel, read and interact with any website using deterministic element refs, and keep your **LinkedIn profile** updated with human-in-the-loop confirmation.

[![Tests](https://github.com/Pawangunjkar/web-browser-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/Pawangunjkar/web-browser-mcp/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP Protocol](https://img.shields.io/badge/protocol-MCP-green.svg)](https://modelcontextprotocol.io/)
[![Playwright](https://img.shields.io/badge/browser-Playwright-2EAD33.svg)](https://playwright.dev/python/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 📋 Table of Contents

- [Features](#-features)
- [How It Works](#-how-it-works)
- [Safety Model](#-safety-model)
- [Project Layout](#-project-layout)
- [Installation](#-installation)
- [Configuring Your AI Client](#-configuring-your-ai-client)
- [First Run: Sign In to LinkedIn](#-first-run-sign-in-to-linkedin)
- [Example Prompts](#-example-prompts)
- [Complete Tool Reference (34 tools)](#-complete-tool-reference-34-tools)
- [Environment Variables](#-environment-variables)
- [LinkedIn Notes](#-linkedin-notes)
- [Troubleshooting](#-troubleshooting)
- [Development & Tests](#-development--tests)
- [Author & License](#-author--license)

---

## ✨ Features

| Area | What It Provides |
|---|---|
| **Parallel Web Research** | `web_research` searches and reads the top results concurrently across background tabs in one call. `web_search` and `web_read_page` give step-by-step control. |
| **Any Website Navigation & Reading** | Open URLs, follow redirects, inspect paged text (with `main_content_only` filtering), extract links, and return viewport, full-page, or element screenshots as images. |
| **Element Ref Targeting** | `browser_snapshot` indexes every interactive element on the page, including inside web components (open shadow DOM), or only inside an open modal via `scope="dialog"`, with short refs (`e1`, `e2`, …) for reliable clicking, filling, and dropdown selection. |
| **Persistent Browser Profiles** | Each `session_name` maps to a persistent Playwright user-data directory on disk (`~/.web-browser-mcp`). Sign in once and stay signed in across restarts, or isolate work/personal sessions. |
| **LinkedIn Profile Management** | Read structured profile sections (`about`, `experience`, `education`, `skills`, etc.), preview and save headline/About/skill updates, or open section forms with mapped fields. |
| **Built-in Guardrails** | Refuses to type into password, card-number, or OTP fields; gates submit/save/send/delete actions and all LinkedIn edits behind preview + `confirm=true`; reports CAPTCHAs instead of bypassing them. |
| **stdio & HTTP Transports** | Runs over `stdio` for local IDEs by default, or `streamable-http` / `sse` with a `/health` endpoint via `--http` or `MCP_TRANSPORT`. |

**Works on any site you use in Chrome**, for example:

- **Job portals (Naukri, LinkedIn Jobs, Indeed):** search jobs, read descriptions, compare roles; clicking *Apply* requires confirmation.
- **Webmail (Gmail, Outlook):** read and summarize unread threads or draft replies; clicking *Send* requires confirmation.
- **Docs, blogs, GitHub:** read long pages in chunks, extract links, capture screenshots.
- **Web forms:** fill inputs and dropdowns by ref; submitting requires confirmation.

---

## ⚙️ How It Works

```text
AI Client (Claude, Cursor, Antigravity, Windsurf, Cline)
        │
        │  MCP (stdio by default, or streamable-http / sse)
        ▼
web_browser_mcp.server (FastMCP — 34 tools + /health)
        ├── BrowserManager (browser_manager.py)
        │     ├── Persistent Playwright Chromium/Chrome/Edge contexts per session_name
        │     ├── URL policy check (http/https only, allowlist/blocklist)
        │     ├── Snapshot DOM walker -> assigns short refs (e1, e2, ...) + dialog scoping
        │     └── Submit/secret heuristics -> gates mutating clicks, blocks password/OTP fills
        ├── Web Research (web_research.py)
        │     ├── DuckDuckGo HTML -> automatic Bing fallback on bot check
        │     ├── Unwraps search redirect URLs (uddg / base64 u=)
        │     └── Concurrent background tabs extract main readable article text
        └── LinkedInHelper + WriteLimiter (linkedin.py, config.py)
              ├── Reads profile sections and opens native section edit dialogs
              ├── Enforces preview-before-save (confirm=true) + post-save verification
              └── Persists daily edit count & cooldown across restarts
```

1. **Session & Profile Lifecycle:** Calling any tool starts (or reuses) the named `session_name` browser context using installed Google Chrome (`BROWSER_CHANNEL=chrome`), Microsoft Edge (`msedge`), or Playwright's bundled Chromium (`chromium`). Each session stores cookies and local storage under `~/.web-browser-mcp/<session_name>`.
2. **Deterministic Ref Targeting:** `browser_snapshot` inspects visible interactive controls in the DOM and stores locator metadata on the session. Subsequent `browser_click`, `browser_fill`, or `browser_select_option` calls resolve `ref="e12"` directly, and stale refs after navigation tell the client to re-snapshot.
3. **Human-in-the-Loop Confirmation:** When `browser_click` targets a button matching save/submit/send/post/delete/buy/apply heuristics (or when any LinkedIn edit tool is called without `confirm=true`), the server stops and returns a preview so the user can approve before the action runs.
4. **Parallel Research:** `web_research` executes `web_search`, unwraps tracking links, and opens background tabs concurrently with `asyncio.gather` to extract clean article text while flagging sign-in walls or CAPTCHAs.

---

## 🛡️ Safety Model

| Protection | How It Works |
|---|---|
| **You sign in, not the AI** | `linkedin_open_login` opens a visible browser window so you enter your own credentials and 2FA. `browser_fill` **refuses to type into password, credit-card, or one-time-code fields** on any site. |
| **Preview before every change** | LinkedIn tools return *current vs. new* text first and only save when called again with `confirm=true`. Mutating clicks (`Save`, `Submit`, `Post`, `Send`, `Delete`, `Buy`, `Follow`, `Connect`) follow the same gate. |
| **Site pop-ups follow the same gate** | JavaScript `alert` / `confirm` / `prompt` pop-ups are reported in the tool output and dismissed, unless they appear during a `browser_click(confirm=true)` the user already approved. |
| **Rate limiting on LinkedIn** | Enforces a daily cap (default 20 edits/day) and minimum cooldown (default 10 s between edits), persisted on disk across server restarts. |
| **No CAPTCHA bypassing** | Bot challenges are reported to the user; `web_search` falls back across search engines rather than attempting to solve or evade challenges. |
| **URL & download guardrails** | `file://`, `javascript:`, and browser-internal URLs are blocked by default. Optional `BROWSER_ALLOWED_DOMAINS` / `BROWSER_BLOCKED_DOMAINS` restrict navigation; file downloads are disabled. |
| **Local-first defaults** | Profiles stay in `~/.web-browser-mcp`. HTTP mode binds to `127.0.0.1` unless `HOST` is overridden. |

---

## 📁 Project Layout

```text
web-browser-mcp/
├── src/web_browser_mcp/
│   ├── __init__.py          # Package version
│   ├── __main__.py          # `python -m web_browser_mcp` entry point
│   ├── server.py            # FastMCP instance, 34 tools, /health, CLI transport setup
│   ├── browser_manager.py   # Persistent Playwright contexts, snapshots, refs, click/fill guards
│   ├── web_research.py      # Search engine fallback, redirect unwrapping, parallel tab reader
│   ├── linkedin.py          # LinkedIn login check, profile reader, section editors
│   └── config.py            # Environment settings, URL policy, persistent WriteLimiter
├── tests/
│   ├── fake_site.py         # Local HTTP mock server (pages, search engines, LinkedIn UI)
│   └── test_web_browser_mcp.py
├── pyproject.toml           # Hatchling build config + `web-browser-mcp` console script
├── Dockerfile               # Headless Chromium container (HTTP mode, LinkedIn disabled)
├── mcpize.yaml              # Hosted HTTP runtime config
└── smithery.yaml            # Smithery stdio config
```

---

## 📦 Installation

Requires **Python 3.10+**, [uv](https://docs.astral.sh/uv/), and **Google Chrome** (or Microsoft Edge, or set `BROWSER_CHANNEL=chromium` and run `uv run playwright install chromium`).

```bash
git clone https://github.com/Pawangunjkar/web-browser-mcp.git
cd web-browser-mcp
uv sync                       # or: pip install -e .
```

Verify the server starts over stdio (`Ctrl+C` to stop):

```bash
uv run web-browser-mcp
```

---

## 🔌 Configuring Your AI Client

### Claude Code

```bash
claude mcp add web-browser -- uv --directory /absolute/path/to/web-browser-mcp run --frozen web-browser-mcp
```

### Claude Desktop / Cursor / Antigravity / Windsurf / Cline

```json
{
  "mcpServers": {
    "web-browser": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/web-browser-mcp", "run", "--frozen", "web-browser-mcp"],
      "env": {
        "BROWSER_CHANNEL": "chrome"
      }
    }
  }
}
```

**Offline / enterprise startup alternative (skip `uv` on every start):**

Point your client directly at the virtual environment's Python interpreter so startup never contacts PyPI:

```json
{
  "mcpServers": {
    "web-browser": {
      "command": "C:\\AI_Workspaces\\Anti_Workspace\\web-browser-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "web_browser_mcp"],
      "env": {
        "BROWSER_CHANNEL": "chrome"
      }
    }
  }
}
```

*(On macOS/Linux use `/absolute/path/to/web-browser-mcp/.venv/bin/python`.)*

On Windows, use an escaped path such as `C:\\AI_Workspaces\\Anti_Workspace\\web-browser-mcp`.

---

## 🔑 First Run: Sign In to LinkedIn

1. Ask your assistant: *"Open LinkedIn login."* → it calls `linkedin_open_login` and opens a visible Chrome window.
2. **Sign in yourself** in that window (email, password, any 2-step code) and optionally tick "Keep me signed in".
3. Ask: *"Check my LinkedIn login."* → `linkedin_check_login` verifies the session and reports your profile URL.

Because the browser profile persists on disk, you stay signed in across restarts. To wipe a saved profile and sign out everywhere, call `browser_clear_profile(confirm=true)`.

---

## 💬 Example Prompts

- *"Read my LinkedIn profile and suggest a stronger headline for a senior DevOps / platform engineer. Show me options first."*
- *"Rewrite my About section to highlight Kubernetes, CI/CD, and MCP servers I've built. Keep it under 2,000 characters and show the preview."*
- *"Add Terraform, Argo CD, and GitHub Actions to my LinkedIn skills if they're missing."*
- *"Research best practices for Kubernetes canary rollouts and summarize the top sources."*
- *"Open Naukri, search for Senior DevOps Engineer jobs in Pune with 8+ years, and list the top 10 with company and salary."*
- *"Open my Gmail and summarize today's unread emails."*
- *"Open the Playwright Python docs, find how persistent contexts work, and take a screenshot."*

---

## 🧰 Complete Tool Reference (34 tools)

Every tool accepts an optional `session_name` (default `"default"`). Each session maintains its own persistent browser profile.

### Category 1: Browser Sessions (5)

| Tool | Description |
|---|---|
| `browser_launch` | Start a session (`headless`, `channel`, `start_url`). Sessions also auto-start on first use. |
| `browser_close` | Close a running session (keeps the saved profile on disk). |
| `browser_close_all` | Close all active sessions. |
| `browser_status` | List running sessions, open tabs, active page, and profile directories. |
| `browser_clear_profile` | Delete a saved profile directory. **Requires `confirm=true`.** |

### Category 2: Navigation (5)

| Tool | Description |
|---|---|
| `browser_navigate` | Open a URL in the active tab (enforces URL scheme and domain policies). |
| `browser_back` / `browser_forward` / `browser_reload` | History navigation and page reload. |
| `browser_wait_for` | Wait for text, a selector, or a URL change (useful after manual sign-in). |

### Category 3: Reading Pages (4)

| Tool | Description |
|---|---|
| `browser_get_text` | Read visible text of the page or a selector, paginated via `offset` / `next_offset`; supports `main_content_only`. |
| `browser_snapshot` | List interactive elements with short refs (`e1`, `e2`, …); `scope="dialog"` targets only an open modal. |
| `browser_get_links` | List page links, optionally filtered by substring. |
| `browser_screenshot` | Capture viewport, full page, or a specific ref as a JPEG image. |

### Category 4: Interacting (6)

| Tool | Description |
|---|---|
| `browser_click` | Click by `ref`, `selector`, or `text`. Save/submit/send/post/delete clicks require `confirm=true`. |
| `browser_fill` | Type into an input (optionally press Enter). Refuses password, card, and OTP fields. |
| `browser_select_option` | Select a dropdown option by value or label. |
| `browser_press_key` | Press a key or shortcut (`Enter`, `Escape`, `Control+A`, …). |
| `browser_hover` | Move the mouse over an element by `ref`, `selector`, or `text` to open hover menus or tooltips. |
| `browser_scroll` | Scroll `up`, `down`, `top`, `bottom`, or scroll an element ref into view. |

### Category 5: Tabs (4)

| Tool | Description |
|---|---|
| `browser_list_tabs` / `browser_new_tab` / `browser_switch_tab` / `browser_close_tab` | Inspect and manage tabs; links that open popups/new tabs switch automatically. |

### Category 6: Web Research (3)

| Tool | Description |
|---|---|
| `web_research` | Search and read the top `num_pages` results in parallel in one call. |
| `web_search` | Return titles, unwrapped URLs, and snippets (`engine="auto"` tries DuckDuckGo then Bing). |
| `web_read_page` | Extract main readable text from a URL in a background tab, flagging bot checks or login walls. |

### Category 7: LinkedIn Profile (7)

| Tool | Description |
|---|---|
| `linkedin_open_login` | Open LinkedIn sign-in in a visible window so you can log in manually. |
| `linkedin_check_login` | Verify sign-in status, profile URL, and remaining daily edits. |
| `linkedin_get_my_profile` | Read name, headline, location, and chosen sections (`include_details=true` expands "See all" lists). |
| `linkedin_update_headline` | Preview, then save headline (≤ 220 chars) with `confirm=true` and verify on profile. |
| `linkedin_update_about` | Preview, then replace About section (≤ 2,600 chars) with `confirm=true`. |
| `linkedin_add_skills` | Add up to 10 skills per call, skipping any already present. |
| `linkedin_open_section_editor` | Open the form for `intro`, `about`, `skills`, `experience`, `education`, `certifications`, or `projects` and list its fields. |

---

## ⚙️ Environment Variables

All settings are optional — see [`.env.example`](.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `BROWSER_CHANNEL` | `chrome` | `chrome`, `msedge`, or `chromium` (falls back to bundled Chromium if missing). |
| `BROWSER_HEADLESS` | visible on desktops | `true` hides the browser window (manual login requires visible mode). |
| `WEB_BROWSER_MCP_CACHE_DIR` | `~/.web-browser-mcp` | Directory for persistent browser profiles and rate-limit state. |
| `BROWSER_TIMEOUT_MS` | `30000` | Default Playwright navigation/action timeout. |
| `BROWSER_VIEWPORT_WIDTH` / `_HEIGHT` | `1366` / `900` | Browser viewport dimensions. |
| `BROWSER_CONFIRM_SUBMITS` | `true` | Require `confirm=true` on save/submit/delete-style clicks. |
| `BROWSER_ALLOWED_DOMAINS` | *(all)* | Comma-separated domain allowlist (subdomains included). |
| `BROWSER_BLOCKED_DOMAINS` | *(none)* | Comma-separated domain blocklist. |
| `BROWSER_ALLOW_FILE_URLS` | `false` | Allow opening local `file://` pages. |
| `LINKEDIN_MAX_WRITES_PER_DAY` | `20` | Daily limit on LinkedIn write operations. |
| `LINKEDIN_MIN_SECONDS_BETWEEN_WRITES` | `10` | Minimum delay between LinkedIn write operations. |
| `WEB_BROWSER_MCP_DISABLE_LINKEDIN` | `false` | Disable LinkedIn tools (enabled in headless Docker containers). |
| `MCP_TRANSPORT`, `HOST`, `PORT` | `stdio`, `127.0.0.1`, `8080` | Transport selection (`stdio`, `streamable-http`, `sse`; `--http` also enables HTTP). |

---

## 💼 LinkedIn Notes

- **Use only on your own account at human pace.** LinkedIn's User Agreement restricts automated access. This server only operates on your own signed-in session, never scrapes third-party profiles, never enters credentials, previews every change before saving, and enforces daily/per-action rate limits — yet any automation carries account risk, so use it at your own discretion.
- **UI changes:** LinkedIn updates DOM selectors frequently. Each editor tries multiple known entry points and falls back to listing open form fields so `browser_*` tools can finish edits if needed.
- **Container / HTTP mode:** Runs headless with `WEB_BROWSER_MCP_DISABLE_LINKEDIN=true`, because logging in requires a visible browser window on your local machine.

---

## 🩺 Troubleshooting

| Problem | Fix |
|---|---|
| *"Could not start a browser … Executable doesn't exist"* | Install Google Chrome, or set `BROWSER_CHANNEL=chromium` and run `uv run playwright install chromium`. |
| *"profile folder is in use"* | Another browser process is holding that profile; close it or pass a different `session_name`. |
| Search reports *CAPTCHA* | Headless traffic triggers bot checks more often. Use `engine="auto"`, run with `BROWSER_HEADLESS=false`, or solve it in the visible window. |
| *"Ref … run browser_snapshot again"* | The DOM updated or navigated after the snapshot; run `browser_snapshot` again to refresh refs. |
| LinkedIn: *"Not signed in"* | Call `linkedin_open_login`, sign in in the browser window, then call `linkedin_check_login`. |
| Windows: *"An Application Control policy has blocked this file"* | Smart App Control blocked a new native extension; install locked dependencies via `uv sync` or allow the binary. |

---

## 🧪 Development & Tests

```bash
uv sync
uv run python -m unittest discover -s tests -v
```

The test suite (**30 tests**, run by GitHub Actions on every push and pull request) launches a real headless browser against [tests/fake_site.py](tests/fake_site.py) — simulating regular pages, DuckDuckGo/Bing search results with CAPTCHA fallback, and a LinkedIn profile editor — so it requires **no internet access and no real LinkedIn account**. Set `BROWSER_CHANNEL=chromium` to test against bundled Chromium instead of Chrome.

---

## 👤 Author & License

**Pawan Gunjkar** — pawangunjkar@gmail.com · [GitHub](https://github.com/Pawangunjkar)

MIT License — see [LICENSE](LICENSE).
