# 🌐 Web Browser MCP Server

> A Python **Model Context Protocol (MCP)** server that lets AI assistants drive a **real Chrome browser** with Playwright — on **any website**: research the web, read your webmail, browse job portals like Naukri, fill in forms, and keep your **LinkedIn profile** polished. You sign in yourself, and nothing is sent, posted, or saved without your approval.

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP Protocol](https://img.shields.io/badge/protocol-MCP-green.svg)](https://modelcontextprotocol.io/)
[![Playwright](https://img.shields.io/badge/browser-Playwright-2EAD33.svg)](https://playwright.dev/python/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 📋 Table of Contents

- [What It Does](#-what-it-does)
- [Safety Model](#-safety-model)
- [Installation](#-installation)
- [Configuring Your AI Client](#-configuring-your-ai-client)
- [First Run: Sign In to LinkedIn](#-first-run-sign-in-to-linkedin)
- [Example Prompts](#-example-prompts)
- [Complete Tool Reference (33 tools)](#-complete-tool-reference-33-tools)
- [Environment Variables](#-environment-variables)
- [LinkedIn Notes](#-linkedin-notes)
- [Troubleshooting](#-troubleshooting)
- [Development & Tests](#-development--tests)
- [Author & License](#-author--license)

---

## ✨ What It Does

| Area | What the AI can do |
|---|---|
| **Web research** | `web_research` searches the web and reads the top results in parallel in one call, ready to summarise with sources. `web_search` and `web_read_page` for finer control. |
| **Any website** | Open pages, read text (paged), list links, take screenshots, and interact: click, type, choose dropdown options, press keys, scroll, manage tabs. |
| **Reliable element targeting** | `browser_snapshot` lists every clickable/fillable element with a short ref (`e12`), so actions hit exactly the right button or field. Pop-ups/modals are detected and can be listed on their own. |
| **LinkedIn profile** | Read your whole profile, then update your **headline**, **About** section, and **skills** — each with a preview of current vs. new text first. Any other section (experience, education, certifications, projects) can be edited through its form. |
| **Your real logins** | Each `session_name` is a persistent browser profile. Sign in once and stay signed in across restarts; keep separate profiles for different purposes. |

**It works on any site you can use in a browser**, for example:

| Site type | Examples of what you can ask |
|---|---|
| Job portals (Naukri, LinkedIn Jobs, Indeed) | Search jobs, read listings, compare roles, update your profile — clicking **Apply** needs your approval |
| Webmail (Gmail, Outlook) | Read and summarise your inbox, find an email, draft a reply — clicking **Send** needs your approval |
| Docs, blogs, news, GitHub | Read, extract, and summarise pages; take screenshots |
| Any web form | Fill fields and dropdowns; submitting needs your approval |

LinkedIn simply has extra one-call tools (headline, About, skills); everywhere else the general `browser_*` tools do the work.

It runs **Google Chrome** installed on your machine by default (or Microsoft Edge, or Playwright's bundled Chromium).

---

## 🛡️ Safety Model

Designed so an AI can help with real accounts without surprises:

| Protection | How it works |
|---|---|
| **You sign in, not the AI** | `linkedin_open_login` opens the sign-in page in a visible window; you type your own password and 2-step code. The server **refuses to type into password, card-number, or one-time-code fields** on any site. |
| **Preview before every change** | LinkedIn edits return *current vs. new* first and only save when called again with `confirm=true` after you approve. Generic clicks on buttons such as Save, Submit, Post, Send, Delete, Buy, Follow, or Connect are gated the same way. |
| **Human pace on LinkedIn** | At most 20 edits per day and 10 s between edits by default (configurable). The daily count survives restarts. |
| **No CAPTCHA bypassing** | Bot checks are reported, never solved or evaded. Searches fall back to another engine; otherwise you solve it yourself in the window. |
| **Site restrictions** | Optional allowlist/blocklist of domains. `file://`, `javascript:`, and browser-internal URLs are blocked by default. Downloads are disabled. |
| **Local by default** | Profiles live in `~/.web-browser-mcp` on your machine. HTTP mode listens on `127.0.0.1` unless you set `HOST`. |

---

## 📦 Installation

Requires **Python 3.10+** and **Google Chrome** (or set `BROWSER_CHANNEL=chromium` and run `playwright install chromium`).

```bash
git clone https://github.com/Pawangunjkar/web-browser-mcp.git
cd web-browser-mcp
uv sync                       # or: pip install -e .
```

Check it starts (Ctrl+C to stop):

```bash
uv run server.py
```

---

## 🔌 Configuring Your AI Client

### Claude Code

```bash
claude mcp add web-browser -- uv --directory /absolute/path/to/web-browser-mcp run server.py
```

### Claude Desktop / Cursor / Windsurf / Cline

```json
{
  "mcpServers": {
    "web-browser": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/web-browser-mcp", "run", "server.py"],
      "env": {
        "BROWSER_CHANNEL": "chrome"
      }
    }
  }
}
```

On Windows use a path like `C:\\AI_Workspaces\\web-browser-mcp`.

---

## 🔑 First Run: Sign In to LinkedIn

1. Ask your assistant: *"Open LinkedIn login."* → it calls `linkedin_open_login` and a Chrome window opens.
2. **Sign in yourself** in that window (email, password, any verification code). Tick "Keep me signed in".
3. Ask: *"Check my LinkedIn login."* → `linkedin_check_login` confirms and shows your profile URL.

You stay signed in for future sessions because the browser profile is saved. To sign out completely, use `browser_clear_profile` (confirmation required).

---

## 💬 Example Prompts

- *"Read my LinkedIn profile and suggest a stronger headline for a senior DevOps / platform engineer. Show me options first."*
- *"Rewrite my About section to highlight Kubernetes, CI/CD and MCP servers I've built. Keep it under 2,000 characters and show the preview."*
- *"Add the skills Terraform, Argo CD and GitHub Actions to my profile if they're missing."*
- *"Research the current best practices for LinkedIn headlines for DevOps engineers and summarise them with sources."*
- *"Open Naukri, search for Senior DevOps Engineer jobs in Pune with 8+ years, and list the top 10 with company and salary."*
- *"Open my Gmail and summarise the unread emails from today."* (sign in yourself the first time)
- *"Open the Playwright Python docs, find how persistent contexts work, and give me an example."*
- *"Go to my GitHub profile page and take a screenshot."*

---

## 🧰 Complete Tool Reference (33 tools)

Every tool takes an optional `session_name` (default `"default"`); each name is its own persistent browser profile.

### Category 1: Browser Sessions (5)

| Tool | Description |
|---|---|
| `browser_launch` | Start a session with options (visible/headless, chrome/msedge/chromium, start URL). Sessions also start automatically. |
| `browser_close` | Close a session (profile kept on disk). |
| `browser_close_all` | Close every session. |
| `browser_status` | Running sessions, tabs, active page, profile folders. |
| `browser_clear_profile` | Delete a saved profile — signs out everywhere. **Confirmation-gated.** |

### Category 2: Navigation (5)

| Tool | Description |
|---|---|
| `browser_navigate` | Open a URL in the active tab. |
| `browser_back` / `browser_forward` / `browser_reload` | History and reload. |
| `browser_wait_for` | Wait for text, an element, or a URL change (e.g. after you sign in). |

### Category 3: Reading Pages (4)

| Tool | Description |
|---|---|
| `browser_get_text` | Visible text of the page or one element, paged with `offset` / `next_offset`; optional main-content-only mode. |
| `browser_snapshot` | Interactive elements with refs (`e1`, `e2`, …); `scope="dialog"` lists only an open pop-up. |
| `browser_get_links` | Links on the page, optionally filtered. |
| `browser_screenshot` | Screenshot of the viewport, full page, or one element (returned as an image). |

### Category 4: Interacting (5)

| Tool | Description |
|---|---|
| `browser_click` | Click by ref, selector, or text. Save/submit/post/delete-style clicks need `confirm=true`. |
| `browser_fill` | Type into a field (optionally press Enter). Password/card/OTP fields are refused. |
| `browser_select_option` | Choose a dropdown option. |
| `browser_press_key` | Press a key or shortcut (`Enter`, `Escape`, `Control+A`, …). |
| `browser_scroll` | Scroll up/down/top/bottom or bring an element into view. |

### Category 5: Tabs (4)

| Tool | Description |
|---|---|
| `browser_list_tabs` / `browser_new_tab` / `browser_switch_tab` / `browser_close_tab` | Manage tabs; links that open new tabs switch to them automatically. |

### Category 6: Web Research (3)

| Tool | Description |
|---|---|
| `web_research` | Search + read the top N results in parallel, in one call. |
| `web_search` | Titles, real URLs (redirects unwrapped), and snippets. `engine="auto"` tries DuckDuckGo, then Bing. |
| `web_read_page` | Main readable text of a URL, read in a background tab. Bot-check / sign-in walls are flagged. |

### Category 7: LinkedIn Profile (7)

| Tool | Description |
|---|---|
| `linkedin_open_login` | Open the sign-in page in a visible window for you to sign in. |
| `linkedin_check_login` | Signed in? Whose profile? Edits used today. |
| `linkedin_get_my_profile` | Name, headline, location, and chosen sections; `include_details=true` reads the full "See all" lists. |
| `linkedin_update_headline` | Preview, then save a new headline (≤ 220 chars) and verify it on the profile. |
| `linkedin_update_about` | Preview, then replace the About section (≤ 2,600 chars). |
| `linkedin_add_skills` | Add up to 10 skills per call; skills already on the profile are skipped. |
| `linkedin_open_section_editor` | Open the form for intro/about/skills/experience/education/certifications/projects and list its fields, for edits without a dedicated tool. |

---

## ⚙️ Environment Variables

All optional — see [`.env.example`](.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `BROWSER_CHANNEL` | `chrome` | `chrome`, `msedge`, or `chromium` (bundled). Falls back to bundled Chromium if the channel is missing. |
| `BROWSER_HEADLESS` | visible on desktops | `true` hides the window. Signing in needs a visible window. |
| `WEB_BROWSER_MCP_CACHE_DIR` | `~/.web-browser-mcp` | Browser profiles and state. |
| `BROWSER_TIMEOUT_MS` | `30000` | Page load timeout. |
| `BROWSER_VIEWPORT_WIDTH` / `_HEIGHT` | `1366` / `900` | Window size. |
| `BROWSER_CONFIRM_SUBMITS` | `true` | Gate save/submit/delete-style clicks behind `confirm=true`. |
| `BROWSER_ALLOWED_DOMAINS` | *(all)* | Comma-separated allowlist (subdomains included). |
| `BROWSER_BLOCKED_DOMAINS` | *(none)* | Comma-separated blocklist. |
| `BROWSER_ALLOW_FILE_URLS` | `false` | Allow opening local `file://` pages. |
| `LINKEDIN_MAX_WRITES_PER_DAY` | `20` | Daily cap on LinkedIn edits. |
| `LINKEDIN_MIN_SECONDS_BETWEEN_WRITES` | `10` | Minimum gap between LinkedIn edits. |
| `WEB_BROWSER_MCP_DISABLE_LINKEDIN` | `false` | Turn off the LinkedIn tools (set in hosted containers). |
| `MCP_TRANSPORT`, `HOST`, `PORT` | `stdio`, `127.0.0.1`, `8080` | HTTP mode for hosted use (`--http` also works). |

---

## 💼 LinkedIn Notes

- **Use it for your own profile, at human pace.** LinkedIn's User Agreement restricts automated access. This server only acts on your own signed-in account, never scrapes other people, never logs in for you, saves only what you approve, and rate-limits edits — but any automation carries some risk to the account. Use it at your own discretion.
- **Layouts change.** LinkedIn updates its pages often. Each editor is opened through several known entry points; if all fail, the tool says so and lists the open form's fields so the generic `browser_*` tools can finish the edit. Please open an issue if a LinkedIn tool stops working.
- **Hosted mode** (Docker / MCPize) runs headless and has the LinkedIn tools disabled, because signing in needs a window on your own computer. Use LinkedIn features with a local install.

---

## 🩺 Troubleshooting

| Problem | Fix |
|---|---|
| *"Could not start a browser … Executable doesn't exist"* | Install Google Chrome, or set `BROWSER_CHANNEL=chromium` and run `playwright install chromium`. |
| *"profile folder is in use"* | Another window is using that profile (e.g. a previous run). Close it or use a different `session_name`. |
| Search says *CAPTCHA* | Headless browsers often get bot checks. Use the default `engine="auto"`, run with a visible window, or solve it yourself in the window. |
| *"Ref … run browser_snapshot again"* | The page changed after the snapshot; take a new one. |
| LinkedIn: *"Not signed in"* | Run `linkedin_open_login` and sign in in the window, then `linkedin_check_login`. |
| Windows: *"An Application Control policy has blocked this file"* | Smart App Control blocked a freshly downloaded Python extension. Install with the versions in `uv.lock` (`uv sync`) or allow the file. |

---

## 🧪 Development & Tests

```bash
uv sync
uv run python -m unittest discover -s tests -v
```

The suite (27 tests) drives a **real headless browser** against a local fake website — including a fake search engine and a fake LinkedIn with edit dialogs — so it needs no internet access and no LinkedIn account. Set `BROWSER_CHANNEL=chromium` to test with the bundled browser.

---

## 👤 Author & License

**Pawan Gunjkar** — pawangunjkar@gmail.com · [GitHub](https://github.com/Pawangunjkar)

MIT License — see [LICENSE](LICENSE).
