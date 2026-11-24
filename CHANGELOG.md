# Changelog

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
