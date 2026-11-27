# Changelog

## [1.2.0] — 2026-11-27

### Added
- Enter is confirmation-gated like save/submit clicks: `browser_fill(press_enter=true)` and
  `browser_press_key("Enter")` need `confirm=true` when Enter would submit a form, send a message
  (text areas, rich-text chat boxes), or activate a save/delete-style button. Search boxes are not gated.
- MCP tool annotations: read-only tools (reading, searching, screenshots, status) are marked
  `readOnlyHint`, navigation/tab tools `destructiveHint=false`, so clients can auto-approve safe calls.

### Changed
- Replies are sent once as text instead of text plus a duplicate structured-JSON copy, halving the
  payload every tool call puts into the client's context. Requires `mcp>=1.10`.
- `browser_select_option` matches labels and values case-insensitively (then a unique partial label),
  fails immediately with the list of available options, and explains when the element is not a
  `<select>` — previously a wrong option waited 60 s for two timeouts.
- `text=` and `label=` element lookups prefer an exact match, so `text="Open"` clicks the "Open"
  button rather than an earlier "Open settings" label.

## [1.1.0] — 2026-11-27

### Added
- `browser_hover` tool (34 tools total) for hover-only menus and tooltips.
- `browser_snapshot` now finds elements inside open shadow roots (web components); their refs work
  with click/fill/select like any other element.
- JavaScript `alert` / `confirm` / `prompt` pop-ups are reported in the output of `browser_navigate`,
  `browser_click`, and `browser_press_key`. They are dismissed by default and accepted only during a
  `browser_click(confirm=true)`, so a site's own "Are you sure?" step follows the same approval gate.
- `/health` reports the server version.
- GitHub Actions workflow running the full browser test suite on every push and pull request.

### Fixed
- `browser_close_tab` kept the wrong tab active when closing a tab to the left of the active one.
- `browser_clear_profile` reported success even when files in the profile were locked and not deleted.
- `browser_scroll` used `document.body` height only, giving wrong positions on pages that scroll `<html>`.

## [1.0.0] — 2026-11-24

### Added
- First release: 33 tools in 7 categories, built on Playwright's async API.
- **Browser sessions:** persistent per-session browser profiles (Google Chrome, Edge, or bundled Chromium),
  automatic start, visible or headless, profile reset with confirmation.
- **Navigation and reading:** navigate/back/forward/reload/wait, paged page text with main-content mode,
  element snapshots with refs, link listing, screenshots returned as images.
- **Interaction:** click, fill, select, key press, scroll, and tab management; links that open new tabs
  switch to them automatically; self-redirecting pages are followed instead of failing.
- **Web research:** `web_research` (search + parallel reading of top results), `web_search` with
  DuckDuckGo → Bing automatic fallback and unwrapped result URLs, `web_read_page` with bot-wall detection.
- **LinkedIn:** sign-in helper (the user signs in), login check, full profile reading, headline / About /
  skills updates with current-vs-new previews and post-save verification, and a generic section editor.
- **Safety:** confirmation gate for save/submit/post/delete-style clicks, refusal to type passwords, card
  numbers or one-time codes, LinkedIn rate limits (daily cap + minimum gap), domain allow/block lists,
  blocked `file://` / `javascript:` URLs, disabled downloads, CAPTCHAs reported and never bypassed.
- 27 tests running a real headless browser against a local fake site, fake search engine, and fake LinkedIn.
