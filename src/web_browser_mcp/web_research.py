"""
Web research helpers for Web Browser MCP Server.

Author: Pawan Gunjkar (pawangunjkar@gmail.com)

Searches run in the real browser (DuckDuckGo, Bing, or Google) and results are
parsed from the rendered page. Pages are read in background tabs so the user's
active tab is never disturbed. CAPTCHA / bot checks are reported, never bypassed.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlsplit

from playwright.async_api import BrowserContext

from web_browser_mcp.browser_manager import BrowserError, BrowserManager

SEARCH_ENGINES = {
    "duckduckgo": "https://html.duckduckgo.com/html/?q={q}",
    "bing": "https://www.bing.com/search?q={q}",
    "google": "https://www.google.com/search?q={q}&hl=en",
}

# Extract organic results from each engine's result page.
_RESULTS_JS = r"""
(engine) => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  if (engine === 'duckduckgo') {
    for (const r of document.querySelectorAll('.result')) {
      const a = r.querySelector('a.result__a');
      if (!a || r.classList.contains('result--ad')) continue;
      out.push({title: clean(a.innerText), url: a.href,
                snippet: clean((r.querySelector('.result__snippet') || {}).innerText)});
    }
  } else if (engine === 'bing') {
    for (const r of document.querySelectorAll('li.b_algo')) {
      const a = r.querySelector('h2 a');
      if (!a) continue;
      out.push({title: clean(a.innerText), url: a.href,
                snippet: clean((r.querySelector('.b_caption p, .b_lineclamp2, .b_lineclamp3') || {}).innerText)});
    }
  } else {
    for (const h3 of document.querySelectorAll('a h3')) {
      const a = h3.closest('a');
      if (!a || !a.href.startsWith('http') || a.href.includes('google.com/')) continue;
      const box = a.closest('div[data-hveid], div.g') || a.parentElement;
      const snip = box ? box.querySelector('[data-sncf], .VwiC3b, [style*="-webkit-line-clamp"]') : null;
      out.push({title: clean(h3.innerText), url: a.href, snippet: clean(snip ? snip.innerText : '')});
    }
  }
  return out;
}
"""

_BOT_CHECK_MARKERS = (
    "/sorry/", "captcha", "unusual traffic", "are you a robot", "verify you are human",
    "bots use duckduckgo", "complete the following challenge", "confirm this search was made by a human",
)

# 'auto' tries these in order and moves on when an engine shows a bot check or returns nothing.
AUTO_ORDER = ("duckduckgo", "bing")


class BotCheckError(BrowserError):
    """The search engine asked for human verification."""


def unwrap_redirect(url: str) -> str:
    """DuckDuckGo and Bing wrap result links in redirects; return the real destination."""
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    if "duckduckgo.com" in parts.netloc and parts.path.startswith("/l/") and query.get("uddg"):
        return query["uddg"][0]
    if parts.netloc.endswith("bing.com") and parts.path.startswith("/ck/a") and query.get("u"):
        encoded = query["u"][0]
        if encoded.startswith("a1"):
            encoded = encoded[2:]
        try:
            decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
            if decoded.startswith(("http://", "https://")):
                return decoded
        except (ValueError, UnicodeDecodeError):
            pass
    return url


async def search(manager: BrowserManager, context: BrowserContext, query: str,
                 engine: str = "auto", max_results: int = 10) -> list[dict[str, Any]]:
    """
    Run a search in a background tab and return [{title, url, snippet, engine}].

    engine='auto' tries DuckDuckGo, then Bing if DuckDuckGo shows a bot check or
    returns nothing (common for headless browsers). A named engine is used as-is.
    """
    engine = engine.lower().strip()
    if engine == "auto":
        notes: list[str] = []
        for candidate in AUTO_ORDER:
            try:
                results = await search(manager, context, query, engine=candidate, max_results=max_results)
            except BotCheckError:
                notes.append(f"{candidate}: bot check")
                continue
            if results:
                return results
            notes.append(f"{candidate}: no results")
        if notes and all(n.endswith("bot check") for n in notes):
            raise BotCheckError(
                "❌ Every search engine asked for human verification (CAPTCHA). This server does not bypass "
                "those. Run with a visible window (BROWSER_HEADLESS=false) or solve it yourself in the browser."
            )
        return []
    if engine not in SEARCH_ENGINES:
        raise BrowserError(
            f"❌ Unknown search engine '{engine}'. Use 'auto' or one of: {', '.join(SEARCH_ENGINES)}."
        )
    if not query.strip():
        raise BrowserError("❌ query must not be empty.")

    url = SEARCH_ENGINES[engine].format(q=quote_plus(query.strip()))
    page = await context.new_page()
    try:
        await manager.goto(page, url)
        lowered_url = page.url.lower()
        body = ((await manager.text(page)) or "")[:3000].lower()
        if any(marker in lowered_url or marker in body for marker in _BOT_CHECK_MARKERS):
            raise BotCheckError(
                f"❌ {engine} is asking for a human verification (CAPTCHA). This server does not bypass those. "
                "Try another engine, or solve it yourself in the browser window and search again."
            )
        raw = await page.evaluate(_RESULTS_JS, engine)
    finally:
        await page.close()

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        real = unwrap_redirect(item.get("url", ""))
        if not real.startswith(("http://", "https://")) or real in seen:
            continue
        seen.add(real)
        results.append({"title": item.get("title", ""), "url": real, "snippet": item.get("snippet", ""),
                        "engine": engine})
        if len(results) >= max_results:
            break
    return results


async def read_page(manager: BrowserManager, context: BrowserContext, url: str,
                    max_chars: int = 8000) -> dict[str, Any]:
    """Open a URL in a background tab and return its main readable text."""
    page = await context.new_page()
    try:
        status = await manager.goto(page, url)
        text = (await manager.text(page, main_only=True)) or ""
        title = await page.title()
        final_url = page.url
    finally:
        await page.close()
    truncated = len(text) > max_chars
    head = text[:2000].lower()
    blocked = any(marker in head for marker in _PAGE_BLOCK_MARKERS) and len(text) < 3000
    return {
        "url": final_url,
        "title": title,
        "status": status,
        "text": text[:max_chars],
        "truncated": truncated,
        "total_chars": len(text),
        "blocked": blocked,
    }


# Short pages showing these are bot-check or sign-in walls, not real content.
_PAGE_BLOCK_MARKERS = (
    "performing security verification", "verify you are not a bot", "checking your browser",
    "verify you are human", "enable javascript and cookies to continue", "access denied",
    "sign in to continue", "join now to see",
)


async def read_many(manager: BrowserManager, context: BrowserContext, urls: list[str],
                    max_chars: int = 4000, concurrency: int = 3) -> list[dict[str, Any]]:
    """Read several pages in parallel background tabs. Failures are reported per page."""
    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(target: str) -> dict[str, Any]:
        async with gate:
            try:
                return await read_page(manager, context, target, max_chars=max_chars)
            except BrowserError as exc:
                return {"url": target, "error": str(exc)}
            except Exception as exc:  # pragma: no cover - defensive
                return {"url": target, "error": f"❌ {type(exc).__name__}: {exc}"}

    return list(await asyncio.gather(*(one(u) for u in urls)))
