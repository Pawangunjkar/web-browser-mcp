"""
Multi-Session Browser Manager for Web Browser MCP Server.

Author: Pawan Gunjkar (pawangunjkar@gmail.com)

Drives Google Chrome (or Playwright's bundled Chromium) with Playwright's async API.
Each named session is a separate persistent browser profile, so logins survive
restarts and sessions never share cookies (e.g. 'linkedin' vs 'research').

Design notes
- Async Playwright only: MCP tools run on the server's event loop, so nothing blocks it.
- Every action on a session is serialised with a lock (one browser, one action at a time).
- Element references ("e12") come from snapshot(): they are stamped onto the page as
  data-mcp-ref attributes and stay valid until the page navigates or re-renders.
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from playwright.async_api import (
    BrowserContext,
    Error as PlaywrightError,
    Locator,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
    async_playwright,
)

from web_browser_mcp.config import BrowserSettings, check_url_allowed, normalize_url

MAX_TEXT_CHARS = 15_000
MAX_SNAPSHOT_ELEMENTS = 150

# Words on a button/link that usually mean "this changes something".
_SUBMIT_WORDS = re.compile(
    r"\b(save|submit|send|post|publish|delete|remove|apply|pay|purchase|buy|order|checkout|confirm|"
    r"sign up|register|unfollow|follow|connect|accept|decline|withdraw|share|repost|upload|update|"
    r"done|discard|deactivate|close account|transfer|endorse|invite|message)\b",
    re.I,
)

# Fields this server never fills: the user types secrets themselves.
_SECRET_AUTOCOMPLETE = re.compile(r"(password|cc-number|cc-csc|cc-exp|one-time-code)", re.I)


# ------------------------------------------------------------------
# JavaScript run inside the page
# ------------------------------------------------------------------

_SNAPSHOT_JS = r"""
({scope, maxElements}) => {
  const SELECTOR = [
    'a[href]', 'button', 'input:not([type=hidden])', 'textarea', 'select', 'summary',
    '[role=button]', '[role=link]', '[role=textbox]', '[role=combobox]', '[role=checkbox]',
    '[role=radio]', '[role=switch]', '[role=tab]', '[role=menuitem]', '[role=option]',
    '[contenteditable=""]', '[contenteditable=true]'
  ].join(',');
  // Walk the page including open shadow roots (web components), in document order.
  const walk = (root, fn) => {
    for (const el of root.querySelectorAll('*')) {
      fn(el);
      if (el.shadowRoot) walk(el.shadowRoot, fn);
    }
  };
  walk(document, el => { if (el.hasAttribute('data-mcp-ref')) el.removeAttribute('data-mcp-ref'); });

  const dialogs = Array.from(document.querySelectorAll('[role=dialog],[role=alertdialog],dialog[open]'))
    .filter(d => d.getClientRects().length > 0);
  const root = (scope === 'dialog' && dialogs.length) ? dialogs[dialogs.length - 1] : document;
  const candidates = [];
  walk(root, el => { if (el.matches(SELECTOR)) candidates.push(el); });

  const visible = el => {
    if (!el.getClientRects().length) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && st.opacity !== '0';
  };
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const labelOf = el => {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria;
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const scopeRoot = el.getRootNode();
      const t = by.split(/\s+/).map(id => scopeRoot.getElementById(id)).filter(Boolean)
        .map(n => n.innerText).join(' ');
      if (clean(t)) return t;
    }
    if (el.labels && el.labels.length) return Array.from(el.labels).map(l => l.innerText).join(' ');
    return el.getAttribute('placeholder') || el.innerText || el.getAttribute('title') ||
           el.getAttribute('alt') || (el.tagName === 'INPUT' ? el.value : '') || el.getAttribute('name') || '';
  };

  const out = [];
  let n = 0;
  for (const el of candidates) {
    if (!visible(el)) continue;
    if (out.length >= maxElements) { n++; continue; }
    const ref = 'e' + (++n);
    el.setAttribute('data-mcp-ref', ref);
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    let role = el.getAttribute('role') || (tag === 'a' ? 'link' : tag === 'select' ? 'select' :
               tag === 'textarea' ? 'textbox' : tag === 'input' ? (type || 'text') : tag);
    if (el.isContentEditable && !el.getAttribute('role')) role = 'editable';
    const item = {ref, role, name: clean(labelOf(el)).slice(0, 80)};
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      if (type === 'password') item.value = '••••';
      else if (type === 'checkbox' || type === 'radio') item.checked = el.checked;
      else item.value = clean(el.value).slice(0, 80);
    }
    if (el.isContentEditable) item.value = clean(el.innerText).slice(0, 80);
    if (tag === 'a') item.href = el.href.slice(0, 160);
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') item.disabled = true;
    if (dialogs.some(d => d.contains(el))) item.in_dialog = true;
    out.push(item);
  }
  return {elements: out, total: n, dialog_open: dialogs.length > 0,
          scoped_to_dialog: root !== document};
}
"""

_TEXT_JS = r"""
({selector, mainOnly}) => {
  let root = document.body;
  if (selector) {
    root = document.querySelector(selector);
    if (!root) return null;
  } else if (mainOnly) {
    const candidates = ['article', 'main', '[role=main]', '#content', '.content'];
    for (const sel of candidates) {
      const el = document.querySelector(sel);
      if (el && el.innerText && el.innerText.trim().length > 400) { root = el; break; }
    }
  }
  if (!root) return '';
  const lines = (root.innerText || '').split('\n').map(l => l.replace(/[ \t]+/g, ' ').trim());
  const out = [];
  for (const line of lines) {
    if (!line && (!out.length || out[out.length - 1] === '')) continue;
    if (line && out.length && out[out.length - 1] === line) continue;  // screen-reader duplicates
    out.push(line);
  }
  return out.join('\n').trim();
}
"""

_LINKS_JS = r"""
({contains, maxLinks}) => {
  const seen = new Set();
  const out = [];
  const needle = (contains || '').toLowerCase();
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.href;
    if (!href || href.startsWith('javascript:') || seen.has(href)) continue;
    const text = (a.innerText || a.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
    if (needle && !href.toLowerCase().includes(needle) && !text.toLowerCase().includes(needle)) continue;
    seen.add(href);
    out.push({text: text.slice(0, 120), href});
    if (out.length >= maxLinks) break;
  }
  return out;
}
"""

_ELEMENT_INFO_JS = r"""
el => {
  const form = el.closest('form');
  const formHints = form ? [form.getAttribute('role'), form.getAttribute('action'), form.id,
                            form.getAttribute('class'), form.getAttribute('aria-label')].join(' ') : '';
  const type = (el.getAttribute('type') || '').toLowerCase();
  const name = (el.getAttribute('name') || '').toLowerCase();
  return {
    tag: el.tagName.toLowerCase(),
    type,
    role: el.getAttribute('role') || '',
    text: (el.innerText || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 120),
    label: (el.getAttribute('aria-label') || el.getAttribute('title') || '').trim(),
    autocomplete: (el.getAttribute('autocomplete') || '').toLowerCase(),
    name,
    inForm: !!form,
    searchForm: !!form && (/search/i.test(formHints) || !!form.querySelector('input[type=search]')),
    searchField: type === 'search' || el.getAttribute('role') === 'searchbox' || !!el.closest('[role=search]') ||
                 /^(q|query|search|keywords?)$/.test(name),
    multiline: el.tagName === 'TEXTAREA' || el.isContentEditable,
  };
}
"""

_SELECT_OPTIONS_JS = r"""
el => el.tagName === 'SELECT'
  ? Array.from(el.options).map(o => ({label: o.label.replace(/\s+/g, ' ').trim(), value: o.value}))
  : null
"""


@dataclass
class BrowserSession:
    """One persistent browser profile with its tabs."""
    name: str
    context: BrowserContext
    headless: bool
    channel: str
    profile_dir: str
    active_index: int = 0
    started_at: float = field(default_factory=time.time)
    action_count: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    accept_dialogs: bool = False
    dialogs: list[str] = field(default_factory=list)

    @property
    def pages(self) -> list[Page]:
        return [p for p in self.context.pages if not p.is_closed()]

    def active_page(self) -> Optional[Page]:
        pages = self.pages
        if not pages:
            return None
        self.active_index = max(0, min(self.active_index, len(pages) - 1))
        return pages[self.active_index]


class BrowserError(Exception):
    """A user-facing error (message already formatted)."""


class BrowserManager:
    """Owns the Playwright driver and all browser sessions."""

    def __init__(self, settings: Optional[BrowserSettings] = None):
        self.settings = settings or BrowserSettings.from_env()
        self._playwright: Optional[Playwright] = None
        self._sessions: dict[str, BrowserSession] = {}
        self._start_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _driver(self) -> Playwright:
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        return self._playwright

    async def launch(
        self,
        session_name: str = "default",
        headless: Optional[bool] = None,
        channel: str = "",
    ) -> BrowserSession:
        """Start (or return) a session. Tries the requested channel, then bundled Chromium."""
        async with self._start_lock:
            existing = self._sessions.get(session_name)
            if existing is not None and self._is_alive(existing):
                return existing
            self._sessions.pop(session_name, None)

            driver = await self._driver()
            headless = self.settings.headless if headless is None else headless
            wanted = (channel or self.settings.channel or "chrome").lower()
            profile = self.settings.profile_dir(session_name)
            profile.mkdir(parents=True, exist_ok=True)

            attempts = [wanted] if wanted in ("chromium", "bundled") else [wanted, "chromium"]
            errors: list[str] = []
            for attempt in attempts:
                try:
                    context = await driver.chromium.launch_persistent_context(
                        str(profile),
                        headless=headless,
                        channel=None if attempt in ("chromium", "bundled") else attempt,
                        viewport={"width": self.settings.viewport_width, "height": self.settings.viewport_height},
                        accept_downloads=False,
                        args=["--no-first-run", "--no-default-browser-check"],
                    )
                except PlaywrightError as exc:
                    errors.append(f"{attempt}: {str(exc).splitlines()[0]}")
                    continue
                context.set_default_timeout(self.settings.timeout_ms)
                context.set_default_navigation_timeout(self.settings.timeout_ms)
                session = BrowserSession(
                    name=session_name,
                    context=context,
                    headless=headless,
                    channel=attempt,
                    profile_dir=str(profile),
                )
                context.on("page", lambda p, s=session: self._watch_dialogs(s, p))
                for existing_page in context.pages:
                    self._watch_dialogs(session, existing_page)
                if not context.pages:
                    await context.new_page()
                self._sessions[session_name] = session
                print(f"Browser session '{session_name}' started ({attempt}, headless={headless})", file=sys.stderr)
                return session

            hint = ""
            if any("is already in use" in e or "user data directory" in e.lower() for e in errors):
                hint = "\nThe profile folder is in use by another browser window; close it and retry."
            elif any("Executable doesn't exist" in e for e in errors):
                hint = ("\nInstall Google Chrome, or run `playwright install chromium` "
                        "(use BROWSER_CHANNEL=chromium for the bundled browser).")
            raise BrowserError("❌ Could not start a browser.\n  " + "\n  ".join(errors) + hint)

    @staticmethod
    def _watch_dialogs(session: BrowserSession, page: Page) -> None:
        """
        Handle alert/confirm/prompt pop-ups and record them so tools can report them.

        Dialogs are dismissed unless the current action was approved by the user
        (session.accept_dialogs), so a site's own "Are you sure?" never bypasses the gate.
        """

        async def on_dialog(dialog) -> None:
            accept = session.accept_dialogs
            message = " ".join((dialog.message or "").split())[:200]
            session.dialogs.append(f"{dialog.type} {message!r} → {'accepted' if accept else 'dismissed'}")
            try:
                if accept:
                    await dialog.accept()
                else:
                    await dialog.dismiss()
            except PlaywrightError:
                pass

        page.on("dialog", on_dialog)

    @staticmethod
    def _is_alive(session: BrowserSession) -> bool:
        try:
            browser = session.context.browser
            return browser is None or browser.is_connected()
        except Exception:
            return False

    async def session(self, session_name: str = "default") -> BrowserSession:
        """Return a running session, starting it with default settings if needed."""
        existing = self._sessions.get(session_name)
        if existing is not None and self._is_alive(existing) and existing.pages:
            return existing
        if existing is not None:
            await self.close(session_name)
        return await self.launch(session_name)

    async def page(self, session_name: str = "default") -> Page:
        sess = await self.session(session_name)
        page = sess.active_page()
        if page is None:
            page = await sess.context.new_page()
            sess.active_index = len(sess.pages) - 1
        return page

    async def close(self, session_name: str = "default") -> bool:
        sess = self._sessions.pop(session_name, None)
        if sess is None:
            return False
        try:
            await sess.context.close()
        except Exception:
            pass
        return True

    async def close_all(self) -> list[str]:
        names = list(self._sessions)
        for name in names:
            await self.close(name)
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        return names

    def list_sessions(self) -> list[dict[str, Any]]:
        rows = []
        for name, sess in self._sessions.items():
            page = sess.active_page()
            rows.append({
                "session_name": name,
                "channel": sess.channel,
                "headless": sess.headless,
                "tabs": len(sess.pages),
                "active_url": page.url if page else "",
                "actions": sess.action_count,
                "profile_dir": sess.profile_dir,
            })
        return rows

    def get_existing(self, session_name: str) -> Optional[BrowserSession]:
        return self._sessions.get(session_name)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def check_url(self, url: str) -> str:
        target = normalize_url(url)
        problem = check_url_allowed(target, self.settings)
        if problem:
            raise BrowserError(problem)
        return target

    async def goto(self, page: Page, url: str, wait_until: str = "domcontentloaded") -> Optional[int]:
        target = self.check_url(url)
        try:
            response = await page.goto(target, wait_until=wait_until)
        except PlaywrightTimeout:
            raise BrowserError(f"❌ Timed out loading {target} after {self.settings.timeout_ms // 1000}s.")
        except PlaywrightError as exc:
            message = str(exc).splitlines()[0]
            if "Download is starting" in message:
                raise BrowserError(f"❌ {target} is a file download, not a web page.")
            if "interrupted by another navigation" in message and "chrome-error://" not in message:
                # The site redirected itself while loading (very common on LinkedIn and other
                # single-page apps). Follow it instead of failing.
                try:
                    await page.wait_for_load_state("domcontentloaded")
                except PlaywrightError:
                    pass
                await settle(page)
                return None
            raise BrowserError(f"❌ Could not open {target}: {message}")
        await settle(page)
        return response.status if response else None

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    async def snapshot(self, page: Page, scope: str = "all", max_elements: int = MAX_SNAPSHOT_ELEMENTS) -> dict:
        return await page.evaluate(_SNAPSHOT_JS, {"scope": scope, "maxElements": max_elements})

    async def text(self, page: Page, selector: str = "", main_only: bool = False) -> Optional[str]:
        return await page.evaluate(_TEXT_JS, {"selector": selector or None, "mainOnly": main_only})

    async def links(self, page: Page, contains: str = "", max_links: int = 100) -> list[dict]:
        return await page.evaluate(_LINKS_JS, {"contains": contains, "maxLinks": max_links})

    # ------------------------------------------------------------------
    # Element lookup
    # ------------------------------------------------------------------

    async def locate(
        self,
        page: Page,
        ref: str = "",
        selector: str = "",
        text: str = "",
        label: str = "",
    ) -> Locator:
        """Find exactly one element by snapshot ref, CSS/Playwright selector, visible text, or label."""
        if ref:
            if not re.fullmatch(r"e\d{1,5}", ref.strip()):
                raise BrowserError(f"❌ '{ref}' is not a snapshot ref (refs look like 'e12').")
            locator = page.locator(f'[data-mcp-ref="{ref.strip()}"]')
            what = f"ref {ref}"
        elif selector:
            locator = page.locator(selector)
            what = f"selector {selector!r}"
        elif label:
            locator = await self._exact_first(page.get_by_label(label, exact=True), page.get_by_label(label))
            what = f"label {label!r}"
        elif text:
            locator = await self._exact_first(page.get_by_text(text, exact=True), page.get_by_text(text))
            what = f"text {text!r}"
        else:
            raise BrowserError("❌ Say which element: pass ref (from browser_snapshot), selector, label, or text.")

        try:
            count = await locator.count()
        except PlaywrightError as exc:
            raise BrowserError(f"❌ Invalid {what}: {str(exc).splitlines()[0]}")
        if count == 0:
            hint = " The page changed since the snapshot; run browser_snapshot again." if ref else ""
            raise BrowserError(f"❌ No element found for {what}.{hint}")
        if count > 1 and not ref:
            visible = [locator.nth(i) for i in range(min(count, 20))]
            for candidate in visible:
                if await candidate.is_visible():
                    return candidate
        return locator.first

    @staticmethod
    async def _exact_first(exact: Locator, partial: Locator) -> Locator:
        """Prefer exact matches when one of them is visible; otherwise fall back to substring matches."""
        try:
            for i in range(min(await exact.count(), 20)):
                if await exact.nth(i).is_visible():
                    return exact
        except PlaywrightError:
            pass
        return partial

    async def element_info(self, locator: Locator) -> dict:
        return await locator.evaluate(_ELEMENT_INFO_JS)

    @staticmethod
    def looks_like_submit(info: dict) -> bool:
        """Heuristic: does clicking this element probably submit, post, save, or delete something?"""
        if info.get("type") == "submit" and info.get("inForm"):
            return True
        words = " ".join([info.get("text", ""), info.get("label", "")])
        return bool(_SUBMIT_WORDS.search(words))

    @classmethod
    def enter_may_submit(cls, info: dict) -> bool:
        """Heuristic: would pressing Enter in this element submit a form or send a message?"""
        if info.get("searchField") or info.get("searchForm"):
            return False
        if info.get("tag") in ("button", "a") or info.get("role") in ("button", "link", "menuitem"):
            return cls.looks_like_submit(info)
        return bool(info.get("inForm") or info.get("multiline"))

    async def focused_info(self, page: Page) -> Optional[dict]:
        """element_info() for the focused element, or None when nothing is focused."""
        focused = page.locator("*:focus")
        try:
            if not await focused.count():
                return None
            return await self.element_info(focused.last)
        except PlaywrightError:
            return None

    async def select(self, locator: Locator, option: str) -> str:
        """Pick a <select> option by label or value (exact, then partial); return the chosen label."""
        options = await locator.evaluate(_SELECT_OPTIONS_JS)
        if options is None:
            raise BrowserError(
                "❌ That element is not a <select> dropdown. For custom dropdowns, click it, run "
                "browser_snapshot, and click the option."
            )
        wanted = option.strip().lower()
        match = (
            next((o for o in options if o["label"].lower() == wanted), None)
            or next((o for o in options if o["value"].lower() == wanted), None)
        )
        if match is None:
            partial = [o for o in options if wanted and wanted in o["label"].lower()]
            match = partial[0] if len(partial) == 1 else None
        if match is None:
            listed = ", ".join(repr(o["label"]) for o in options[:40])
            more = f" … (+{len(options) - 40} more)" if len(options) > 40 else ""
            raise BrowserError(f"❌ No option matches {option!r}. Options: {listed or '(none)'}{more}")
        await locator.select_option(value=match["value"])
        return match["label"]

    @staticmethod
    def is_secret_field(info: dict) -> bool:
        if info.get("type") == "password":
            return True
        haystack = " ".join([info.get("autocomplete", ""), info.get("name", "")])
        return bool(_SECRET_AUTOCOMPLETE.search(haystack))


async def settle(page: Page, idle_ms: int = 3000) -> None:
    """Give client-side apps a moment to render after navigation, without waiting forever."""
    try:
        await page.wait_for_load_state("networkidle", timeout=idle_ms)
    except PlaywrightError:
        pass
