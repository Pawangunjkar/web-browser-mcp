"""
Web Browser MCP Server — Main Entry Point.

Author: Pawan Gunjkar (pawangunjkar@gmail.com)

A Playwright-powered MCP server that lets AI assistants drive a real Chrome
browser: research the web, read and interact with pages, and keep your
LinkedIn profile polished — with you signed in yourself and every save
confirmed by you.

Usage:
    uv run web-browser-mcp          # Run via stdio (for Claude Code / Cursor / Claude Desktop)
    uv run web-browser-mcp --http   # Run via streamable HTTP on PORT (default 8080)
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import shutil
import sys
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import ToolAnnotations

from web_browser_mcp import __version__

from web_browser_mcp import web_research as research
from web_browser_mcp.browser_manager import MAX_TEXT_CHARS, BrowserError, BrowserManager, settle
from web_browser_mcp.config import BrowserSettings, WriteLimiter
from web_browser_mcp.linkedin import (
    ABOUT_LIMIT,
    EDITOR_URLS,
    HEADLINE_LIMIT,
    PROFILE_SECTIONS,
    SKILL_LIMIT,
    LinkedInHelper,
    base_url as linkedin_base_url,
    validate_text,
)

# ============================================================
# Initialize MCP Server
# ============================================================

mcp = FastMCP(
    "web-browser",
    instructions=(
        "Web Browser MCP Server — drive a real Chrome browser with Playwright. "
        "Research: web_research (search + read top pages in one call), web_search, web_read_page. "
        "Pages: browser_navigate, then browser_snapshot to get element refs (e1, e2, ...) for "
        "browser_click / browser_fill / browser_select_option / browser_hover. browser_get_text reads the page. "
        "LinkedIn: linkedin_open_login (the user signs in themselves), linkedin_get_my_profile, "
        "linkedin_update_headline / linkedin_update_about / linkedin_add_skills. "
        "Every save/submit/post/delete click, every Enter that would submit a form or send a message, "
        "and every LinkedIn edit returns a preview first and "
        "only acts when called again with confirm=true AFTER the user approves the preview. "
        "This server never types passwords or card numbers. Each session_name is a separate "
        "persistent browser profile (default: 'default')."
    ),
)
mcp._mcp_server.version = __version__

_settings = BrowserSettings.from_env()
_browser = BrowserManager(_settings)
_limiter = WriteLimiter(_settings)
_linkedin = LinkedInHelper(_browser, _limiter)


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
_NON_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
_MAY_CHANGE_DATA = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)


def _tool(annotations: ToolAnnotations):
    """Register a tool. Replies are plain text/images only (structured output would duplicate every reply)."""
    return mcp.tool(annotations=annotations, structured_output=False)


def _tool_errors(func):
    """Return BrowserError messages (and unexpected errors) as normal tool text instead of crashing."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except BrowserError as exc:
            return str(exc)
        except Exception as exc:
            first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
            return f"❌ {type(exc).__name__}: {first_line}"

    return wrapper


async def _page_header(page) -> str:
    try:
        title = await page.title()
    except Exception:
        title = ""
    return f"🌐 {title or '(untitled)'}\n   {page.url}"


def _dialog_notes(sess) -> str:
    """Describe (and forget) the alert/confirm/prompt pop-ups recorded since the last call."""
    seen = list(sess.dialogs)
    sess.dialogs.clear()
    if not seen:
        return ""
    lines = [f"\n   💬 The page showed a pop-up: {d}" for d in seen]
    if any(d.endswith("dismissed") for d in seen):
        lines.append("\n   (dismissed for safety; if the user wants to go ahead, repeat the click with confirm=true)")
    return "".join(lines)


def _format_snapshot(snap: dict, limit_note: bool = True) -> str:
    lines = []
    if snap.get("scoped_to_dialog"):
        lines.append("🪟 A dialog is open — showing only elements inside it.")
    elif snap.get("dialog_open"):
        lines.append("🪟 A dialog is open (elements inside it are marked [dialog]).")
    for el in snap.get("elements", []):
        parts = [f"[{el['ref']}]", el["role"]]
        if el.get("name"):
            parts.append(repr(el["name"]))
        if "value" in el and el["value"] != "":
            parts.append(f"value={el['value']!r}")
        if "checked" in el:
            parts.append("checked" if el["checked"] else "unchecked")
        if el.get("href") and el["role"] == "link":
            parts.append(f"→ {el['href']}")
        if el.get("disabled"):
            parts.append("(disabled)")
        if el.get("in_dialog") and not snap.get("scoped_to_dialog"):
            parts.append("[dialog]")
        lines.append("  " + " ".join(parts))
    shown = len(snap.get("elements", []))
    total = snap.get("total", shown)
    if limit_note and total > shown:
        lines.append(f"  … {total - shown} more elements not shown (use scope='dialog' or browser_get_text).")
    if not shown:
        lines.append("  (no interactive elements visible)")
    return "\n".join(lines)


# ============================================================
# CATEGORY 1: Browser Sessions (5 tools)
# ============================================================

@_tool(_NON_DESTRUCTIVE)
@_tool_errors
async def browser_launch(
    session_name: str = "default",
    headless: Optional[bool] = None,
    channel: str = "",
    start_url: str = "",
) -> str:
    """
    Start a browser session (a persistent Chrome profile) and optionally open a URL.

    Sessions also start automatically on first use, so this is only needed to pick
    options. Logins, cookies, and settings are kept in the profile between runs.

    Args:
        session_name: Profile name, e.g. 'default', 'linkedin', 'research'
        headless: True = no window, False = visible window (needed to sign in). Empty = BROWSER_HEADLESS default
        channel: 'chrome' (installed Google Chrome), 'msedge', or 'chromium' (Playwright's bundled browser)
        start_url: Page to open after starting (optional)
    """
    existing = _browser.get_existing(session_name)
    if existing is not None and headless is not None and existing.headless != headless:
        await _browser.close(session_name)
    sess = await _browser.launch(session_name, headless=headless, channel=channel)
    page = sess.active_page()
    if start_url and page is not None:
        await _browser.goto(page, start_url)
    header = await _page_header(page) if page else ""
    return (
        f"✅ Browser session '{session_name}' ready\n"
        f"  • Browser: {sess.channel} ({'headless' if sess.headless else 'visible window'})\n"
        f"  • Profile: {sess.profile_dir}\n"
        f"{header}"
    )


@_tool(_NON_DESTRUCTIVE)
@_tool_errors
async def browser_close(session_name: str = "default") -> str:
    """
    Close a browser session. The profile (logins, cookies) is kept on disk for next time.

    Args:
        session_name: Session to close (default: 'default')
    """
    closed = await _browser.close(session_name)
    return f"✅ Closed browser session '{session_name}'." if closed else f"⚠️ No running session '{session_name}'."


@_tool(_NON_DESTRUCTIVE)
@_tool_errors
async def browser_close_all() -> str:
    """Close every browser session and stop the browser driver. Profiles stay on disk."""
    names = await _browser.close_all()
    return f"✅ Closed {len(names)} session(s): {', '.join(names)}" if names else "⚠️ No running sessions."


@_tool(_READ_ONLY)
@_tool_errors
async def browser_status() -> str:
    """List running browser sessions with their tabs, active page, and profile folder."""
    rows = _browser.list_sessions()
    if not rows:
        return (
            "📡 No running browser sessions. Any browser_* or web_* tool starts one automatically, "
            "or call browser_launch."
        )
    lines = ["📡 Browser sessions:\n"]
    for r in rows:
        lines.append(
            f"  🟢 {r['session_name']}: {r['channel']}, {'headless' if r['headless'] else 'window'}, "
            f"{r['tabs']} tab(s), actions: {r['actions']}\n     active: {r['active_url']}\n     profile: {r['profile_dir']}"
        )
    return "\n".join(lines)


@_tool(_MAY_CHANGE_DATA)
@_tool_errors
async def browser_clear_profile(session_name: str = "default", confirm: bool = False) -> str:
    """
    Delete a session's saved browser profile: signs you out of every site in it.

    ⚠️ Confirmation-gated: returns a preview unless confirm=true.

    Args:
        session_name: Profile to delete
        confirm: Must be true to actually delete (default: False)
    """
    profile = _settings.profile_dir(session_name)
    if not confirm:
        return (
            f"🔒 Confirmation required: delete browser profile '{session_name}'\n"
            f"  • Folder: {profile}\n"
            "  • Effect: signs out of every site in this profile and removes its cookies and history\n"
            "Re-call with confirm=true after the user approves."
        )
    await _browser.close(session_name)
    if not profile.exists():
        return f"⚠️ No saved profile for '{session_name}'."
    shutil.rmtree(profile, ignore_errors=True)
    if profile.exists():
        return (
            f"⚠️ Could not fully delete profile '{session_name}' ({profile}); some files are in use. "
            "Close any browser window using it and try again."
        )
    return f"✅ Deleted browser profile '{session_name}'."


# ============================================================
# CATEGORY 2: Navigation (5 tools)
# ============================================================

@_tool(_NON_DESTRUCTIVE)
@_tool_errors
async def browser_navigate(url: str, session_name: str = "default") -> str:
    """
    Open a URL in the active tab.

    Args:
        url: Address to open ('example.com' is treated as https://example.com)
        session_name: Browser session to use (default: 'default')
    """
    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await _browser.page(session_name)
        sess.dialogs.clear()
        status = await _browser.goto(page, url)
        sess.action_count += 1
        return f"{await _page_header(page)}\n   HTTP {status if status is not None else '—'}{_dialog_notes(sess)}"


@_tool(_NON_DESTRUCTIVE)
@_tool_errors
async def browser_back(session_name: str = "default") -> str:
    """
    Go back one page in the active tab's history.

    Args:
        session_name: Browser session to use
    """
    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await _browser.page(session_name)
        response = await page.go_back()
        await settle(page)
        note = "" if response is not None or page.url else "\n   (no previous page)"
        return f"{await _page_header(page)}{note}"


@_tool(_NON_DESTRUCTIVE)
@_tool_errors
async def browser_forward(session_name: str = "default") -> str:
    """
    Go forward one page in the active tab's history.

    Args:
        session_name: Browser session to use
    """
    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await _browser.page(session_name)
        await page.go_forward()
        await settle(page)
        return await _page_header(page)


@_tool(_NON_DESTRUCTIVE)
@_tool_errors
async def browser_reload(session_name: str = "default") -> str:
    """
    Reload the active tab.

    Args:
        session_name: Browser session to use
    """
    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await _browser.page(session_name)
        await page.reload()
        await settle(page)
        return await _page_header(page)


@_tool(_READ_ONLY)
@_tool_errors
async def browser_wait_for(
    text: str = "",
    selector: str = "",
    url_contains: str = "",
    timeout_seconds: int = 10,
    session_name: str = "default",
) -> str:
    """
    Wait until text or an element appears, or the URL changes (e.g. after a sign-in).

    Args:
        text: Visible text to wait for
        selector: CSS/Playwright selector to wait for
        url_contains: Wait until the page URL contains this string
        timeout_seconds: Maximum wait (1–120, default 10)
        session_name: Browser session to use
    """
    timeout_ms = max(1, min(int(timeout_seconds), 120)) * 1000
    if not (text or selector or url_contains):
        return "❌ Pass text, selector, or url_contains."
    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await _browser.page(session_name)
        try:
            if url_contains:
                await page.wait_for_url(lambda u: url_contains in u, timeout=timeout_ms)
            if selector:
                await page.locator(selector).first.wait_for(state="visible", timeout=timeout_ms)
            if text:
                await page.get_by_text(text).first.wait_for(state="visible", timeout=timeout_ms)
        except Exception:
            return f"⏳ Not seen within {timeout_ms // 1000}s.\n{await _page_header(page)}"
        return f"✅ Condition met.\n{await _page_header(page)}"


# ============================================================
# CATEGORY 3: Reading Pages (4 tools)
# ============================================================

@_tool(_READ_ONLY)
@_tool_errors
async def browser_get_text(
    selector: str = "",
    offset: int = 0,
    max_chars: int = 8000,
    main_content_only: bool = False,
    session_name: str = "default",
) -> str:
    """
    Read the visible text of the active page (or of one element), in pages of max_chars.

    Args:
        selector: Only read inside this CSS selector (empty = whole page)
        offset: Character offset to continue from (use next_offset from a previous call)
        max_chars: Characters to return (100–15000, default 8000)
        main_content_only: Prefer <article>/<main> content, skipping menus and footers
        session_name: Browser session to use
    """
    max_chars = max(100, min(int(max_chars), MAX_TEXT_CHARS))
    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await _browser.page(session_name)
        text = await _browser.text(page, selector=selector, main_only=main_content_only)
        if text is None:
            return f"❌ No element matches selector {selector!r}."
        start = max(0, int(offset))
        chunk = text[start:start + max_chars]
        end = start + len(chunk)
        footer = (
            f"\n\n… showing characters {start}–{end} of {len(text)}. next_offset={end}"
            if end < len(text) else f"\n\n(end of text, {len(text)} characters)"
        )
        return f"{await _page_header(page)}\n\n{chunk}{footer}"


@_tool(_READ_ONLY)
@_tool_errors
async def browser_snapshot(scope: str = "all", max_elements: int = 150, session_name: str = "default") -> str:
    """
    List the clickable and fillable elements on the page, each with a ref like 'e12'.

    Use the refs with browser_click, browser_fill, and browser_select_option. Refs stay
    valid until the page navigates or re-renders; take a new snapshot after big changes.

    Args:
        scope: 'all' (whole page) or 'dialog' (only the open pop-up/modal, if any)
        max_elements: Maximum elements to list (10–400, default 150)
        session_name: Browser session to use
    """
    if scope not in ("all", "dialog"):
        return "❌ scope must be 'all' or 'dialog'."
    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await _browser.page(session_name)
        snap = await _browser.snapshot(page, scope=scope, max_elements=max(10, min(int(max_elements), 400)))
        return f"{await _page_header(page)}\n\n{_format_snapshot(snap)}"


@_tool(_READ_ONLY)
@_tool_errors
async def browser_get_links(contains: str = "", max_links: int = 100, session_name: str = "default") -> str:
    """
    List the links on the active page (text and URL), optionally filtered.

    Args:
        contains: Only links whose text or URL contains this (case-insensitive)
        max_links: Maximum links (1–500, default 100)
        session_name: Browser session to use
    """
    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await _browser.page(session_name)
        links = await _browser.links(page, contains=contains, max_links=max(1, min(int(max_links), 500)))
        if not links:
            return f"{await _page_header(page)}\n\n(no matching links)"
        body = "\n".join(f"  • {l['text'] or '(no text)'} → {l['href']}" for l in links)
        return f"{await _page_header(page)}\n\n🔗 {len(links)} link(s):\n{body}"


@_tool(_READ_ONLY)
@_tool_errors
async def browser_screenshot(full_page: bool = False, ref: str = "", session_name: str = "default") -> Image:
    """
    Take a screenshot of the active tab (or of one element) and return the image.

    Args:
        full_page: Capture the whole scrollable page instead of the visible area
        ref: Capture only this element (ref from browser_snapshot)
        session_name: Browser session to use
    """
    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await _browser.page(session_name)
        if ref:
            target = await _browser.locate(page, ref=ref)
            data = await target.screenshot(type="jpeg", quality=70)
        else:
            data = await page.screenshot(full_page=full_page, type="jpeg", quality=70)
        return Image(data=data, format="jpeg")


# ============================================================
# CATEGORY 4: Interacting (6 tools)
# ============================================================

@_tool(_MAY_CHANGE_DATA)
@_tool_errors
async def browser_click(
    ref: str = "",
    selector: str = "",
    text: str = "",
    confirm: bool = False,
    session_name: str = "default",
) -> str:
    """
    Click an element, found by snapshot ref (preferred), selector, or visible text.

    Buttons that look like they save, submit, post, send, delete, buy, follow, or
    connect are confirmation-gated: the first call returns a preview and nothing
    is clicked. Call again with confirm=true only after the user approves.

    Args:
        ref: Element ref from browser_snapshot, e.g. 'e12'
        selector: CSS/Playwright selector (alternative to ref)
        text: Visible text of the element (alternative to ref)
        confirm: Required for save/submit/post/delete-style clicks (default: False)
        session_name: Browser session to use
    """
    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await _browser.page(session_name)
        target = await _browser.locate(page, ref=ref, selector=selector, text=text)
        info = await _browser.element_info(target)
        label = info.get("text") or info.get("label") or info.get("tag")
        if _settings.confirm_submits and _browser.looks_like_submit(info) and not confirm:
            return (
                f"🔒 Confirmation required: this click looks like it changes something.\n"
                f"  • Element: {info.get('tag')} {label!r}\n"
                f"  • Page: {page.url}\n"
                "Show this to the user and call browser_click again with confirm=true after they approve."
            )
        opened: list = []

        def on_page(new_page) -> None:
            opened.append(new_page)

        sess.context.on("page", on_page)
        sess.dialogs.clear()
        sess.accept_dialogs = confirm
        try:
            await target.click()
            await settle(page, idle_ms=2000)
            if not opened:
                await asyncio.sleep(0.3)  # new tabs can appear just after the click returns
        finally:
            sess.accept_dialogs = False
            sess.context.remove_listener("page", on_page)
        sess.action_count += 1
        note = ""
        if opened:
            new_tab = opened[-1]
            await settle(new_tab)
            if new_tab in sess.pages:
                sess.active_index = sess.pages.index(new_tab)
            note = "\n   (the click opened a new tab; it is now the active tab)"
        return f"✅ Clicked {label!r}\n{await _page_header(sess.active_page())}{note}{_dialog_notes(sess)}"


@_tool(_MAY_CHANGE_DATA)
@_tool_errors
async def browser_fill(
    value: str,
    ref: str = "",
    selector: str = "",
    label: str = "",
    press_enter: bool = False,
    confirm: bool = False,
    session_name: str = "default",
) -> str:
    """
    Type into a text box, text area, or editable field (replacing its content).

    Password, card-number, and one-time-code fields are refused: the user types
    those directly in the browser window. When press_enter would submit a form or
    send a message (anything but a search box), the text is filled but Enter is
    only pressed when called again with confirm=true after the user approves.

    Args:
        value: Text to enter
        ref: Element ref from browser_snapshot (preferred)
        selector: CSS/Playwright selector (alternative)
        label: The field's label text (alternative)
        press_enter: Press Enter after typing (e.g. to run a search)
        confirm: Required for press_enter when Enter would submit or send (default: False)
        session_name: Browser session to use
    """
    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await _browser.page(session_name)
        target = await _browser.locate(page, ref=ref, selector=selector, label=label)
        info = await _browser.element_info(target)
        if _browser.is_secret_field(info):
            return (
                "🔐 This is a password / payment / one-time-code field. For your security this server never "
                "types secrets — please type it yourself in the browser window."
            )
        await target.fill(value)
        sess.action_count += 1
        shown = value if len(value) <= 80 else value[:77] + "..."
        filled = f"✅ Filled {info.get('label') or info.get('name') or info.get('tag')!r} with {shown!r}"
        if not press_enter:
            return filled
        if _settings.confirm_submits and _browser.enter_may_submit(info) and not confirm:
            return (
                f"{filled}\n🔒 Enter was NOT pressed: in this field it would probably submit the form or send "
                f"a message.\n  • Page: {page.url}\n"
                "Show this to the user and call browser_fill again with confirm=true after they approve."
            )
        sess.dialogs.clear()
        sess.accept_dialogs = confirm
        try:
            await target.press("Enter")
            await settle(page, idle_ms=2000)
        finally:
            sess.accept_dialogs = False
        return f"{filled}\n{await _page_header(page)}{_dialog_notes(sess)}"


@_tool(_NON_DESTRUCTIVE)
@_tool_errors
async def browser_select_option(
    option: str,
    ref: str = "",
    selector: str = "",
    label: str = "",
    session_name: str = "default",
) -> str:
    """
    Choose an option in a dropdown (<select>), by its visible label or value.

    Args:
        option: Option label (or value) to select
        ref: Element ref from browser_snapshot (preferred)
        selector: CSS/Playwright selector (alternative)
        label: The dropdown's label text (alternative)
        session_name: Browser session to use
    """
    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await _browser.page(session_name)
        target = await _browser.locate(page, ref=ref, selector=selector, label=label)
        chosen = await _browser.select(target, option)
        sess.action_count += 1
        return f"✅ Selected {chosen!r}"


@_tool(_MAY_CHANGE_DATA)
@_tool_errors
async def browser_press_key(key: str, confirm: bool = False, session_name: str = "default") -> str:
    """
    Press a key or shortcut in the active tab, e.g. 'Enter', 'Escape', 'Tab', 'PageDown', 'Control+A'.

    Enter (or a shortcut ending in Enter) in a field where it would submit a form or
    send a message is confirmation-gated, like save/submit clicks.

    Args:
        key: Key name (Playwright key syntax)
        confirm: Required when Enter would submit or send (default: False)
        session_name: Browser session to use
    """
    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await _browser.page(session_name)
        if key.split("+")[-1].strip().lower() == "enter" and _settings.confirm_submits and not confirm:
            info = await _browser.focused_info(page)
            if info is not None and _browser.enter_may_submit(info):
                name = info.get("label") or info.get("text") or info.get("name") or info.get("tag")
                return (
                    f"🔒 Confirmation required: pressing {key} in {info.get('tag')} {name!r} would probably "
                    f"submit the form or send a message.\n  • Page: {page.url}\n"
                    "Show this to the user and call browser_press_key again with confirm=true after they approve."
                )
        sess.dialogs.clear()
        sess.accept_dialogs = confirm
        try:
            await page.keyboard.press(key)
            await settle(page, idle_ms=1500)
        finally:
            sess.accept_dialogs = False
        sess.action_count += 1
        return f"✅ Pressed {key}\n{await _page_header(page)}{_dialog_notes(sess)}"


@_tool(_NON_DESTRUCTIVE)
@_tool_errors
async def browser_hover(ref: str = "", selector: str = "", text: str = "", session_name: str = "default") -> str:
    """
    Move the mouse over an element, e.g. to open a hover menu or show a tooltip.

    Take a new browser_snapshot afterwards to get refs for anything that appeared.

    Args:
        ref: Element ref from browser_snapshot (preferred)
        selector: CSS/Playwright selector (alternative)
        text: Visible text of the element (alternative)
        session_name: Browser session to use
    """
    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await _browser.page(session_name)
        target = await _browser.locate(page, ref=ref, selector=selector, text=text)
        info = await _browser.element_info(target)
        await target.hover()
        await page.wait_for_timeout(300)
        return f"✅ Hovering over {info.get('text') or info.get('label') or info.get('tag')!r}"


@_tool(_NON_DESTRUCTIVE)
@_tool_errors
async def browser_scroll(
    direction: str = "down",
    pages: float = 1.0,
    ref: str = "",
    session_name: str = "default",
) -> str:
    """
    Scroll the page (loads lazy content) or scroll an element into view.

    Args:
        direction: 'down', 'up', 'top', or 'bottom'
        pages: How many screen heights to scroll for up/down (0.1–20, default 1)
        ref: Scroll this element into view instead (ref from browser_snapshot)
        session_name: Browser session to use
    """
    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await _browser.page(session_name)
        if ref:
            target = await _browser.locate(page, ref=ref)
            await target.scroll_into_view_if_needed()
            return f"✅ Scrolled element {ref} into view."
        height = _settings.viewport_height
        amount = max(0.1, min(float(pages), 20.0))
        page_height = "Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
        if direction == "top":
            await page.evaluate("window.scrollTo(0, 0)")
        elif direction == "bottom":
            await page.evaluate(f"window.scrollTo(0, {page_height})")
        elif direction in ("down", "up"):
            await page.mouse.wheel(0, height * amount * (1 if direction == "down" else -1))
        else:
            return "❌ direction must be 'down', 'up', 'top', or 'bottom'."
        await page.wait_for_timeout(500)
        position = await page.evaluate(
            f"({{y: Math.round(scrollY), max: Math.max(0, {page_height} - innerHeight)}})"
        )
        return f"✅ Scrolled {direction}. Position {position['y']} / {position['max']} px."


# ============================================================
# CATEGORY 5: Tabs (4 tools)
# ============================================================

@_tool(_READ_ONLY)
@_tool_errors
async def browser_list_tabs(session_name: str = "default") -> str:
    """
    List open tabs in a session; the active one is marked.

    Args:
        session_name: Browser session to use
    """
    sess = await _browser.session(session_name)
    lines = [f"🗂️ Tabs in '{session_name}':"]
    for index, page in enumerate(sess.pages):
        marker = "👉" if index == sess.active_index else "  "
        try:
            title = await page.title()
        except Exception:
            title = ""
        lines.append(f"  {marker} [{index}] {title or '(untitled)'} — {page.url}")
    return "\n".join(lines)


@_tool(_NON_DESTRUCTIVE)
@_tool_errors
async def browser_new_tab(url: str = "", session_name: str = "default") -> str:
    """
    Open a new tab (optionally at a URL) and make it active.

    Args:
        url: Page to open (empty = blank tab)
        session_name: Browser session to use
    """
    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await sess.context.new_page()
        sess.active_index = len(sess.pages) - 1
        if url:
            await _browser.goto(page, url)
        return f"✅ New tab [{sess.active_index}]\n{await _page_header(page)}"


@_tool(_NON_DESTRUCTIVE)
@_tool_errors
async def browser_switch_tab(index: int, session_name: str = "default") -> str:
    """
    Make another tab active (see browser_list_tabs for indexes).

    Args:
        index: Tab index
        session_name: Browser session to use
    """
    sess = await _browser.session(session_name)
    pages = sess.pages
    if not 0 <= index < len(pages):
        return f"❌ No tab {index}. Open tabs: 0–{len(pages) - 1}."
    sess.active_index = index
    await pages[index].bring_to_front()
    return f"✅ Switched to tab [{index}]\n{await _page_header(pages[index])}"


@_tool(_NON_DESTRUCTIVE)
@_tool_errors
async def browser_close_tab(index: int = -1, session_name: str = "default") -> str:
    """
    Close a tab (default: the active tab). The last tab is replaced by a blank one.

    Args:
        index: Tab index to close (-1 = active tab)
        session_name: Browser session to use
    """
    sess = await _browser.session(session_name)
    async with sess.lock:
        pages = sess.pages
        target_index = sess.active_index if index < 0 else index
        if not 0 <= target_index < len(pages):
            return f"❌ No tab {index}."
        if len(pages) == 1:
            await sess.context.new_page()
        await pages[target_index].close()
        if target_index < sess.active_index:
            sess.active_index -= 1
        sess.active_index = max(0, min(sess.active_index, len(sess.pages) - 1))
        return f"✅ Closed tab [{target_index}]. Active: [{sess.active_index}] {sess.active_page().url}"


# ============================================================
# CATEGORY 6: Web Research (3 tools)
# ============================================================

@_tool(_READ_ONLY)
@_tool_errors
async def web_search(
    query: str,
    engine: str = "auto",
    max_results: int = 10,
    session_name: str = "default",
) -> str:
    """
    Search the web in the browser and return titles, URLs, and snippets.

    Runs in a background tab, so the active tab is untouched. If an engine asks for
    a CAPTCHA, the tool says so (it never bypasses verification) — try another engine.

    Args:
        query: Search terms (engine operators like site: and quotes work)
        engine: 'auto' (default: DuckDuckGo, then Bing if blocked), 'duckduckgo', 'bing', or 'google'
        max_results: Maximum results (1–30, default 10)
        session_name: Browser session to use
    """
    sess = await _browser.session(session_name)
    results = await research.search(
        _browser, sess.context, query, engine=engine, max_results=max(1, min(int(max_results), 30))
    )
    sess.action_count += 1
    if not results:
        return f"🔍 No results for {query!r} on {engine}."
    lines = [f"🔍 {len(results)} result(s) for {query!r} ({results[0].get('engine', engine)}):\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['url']}")
        if r["snippet"]:
            lines.append(f"   {r['snippet'][:300]}")
    return "\n".join(lines)


@_tool(_READ_ONLY)
@_tool_errors
async def web_read_page(url: str, max_chars: int = 8000, session_name: str = "default") -> str:
    """
    Open a URL in a background tab and return its main readable text.

    Args:
        url: Page to read
        max_chars: Maximum characters of text (500–15000, default 8000)
        session_name: Browser session to use
    """
    sess = await _browser.session(session_name)
    page = await research.read_page(
        _browser, sess.context, url, max_chars=max(500, min(int(max_chars), MAX_TEXT_CHARS))
    )
    sess.action_count += 1
    more = f"\n\n… truncated ({page['total_chars']} characters total)" if page["truncated"] else ""
    warning = (
        "⚠️ This looks like a bot check or sign-in wall, not the real content. Open it with "
        "browser_navigate in a visible window (and sign in yourself if needed).\n\n"
        if page.get("blocked") else ""
    )
    return (
        f"🌐 {page['title'] or '(untitled)'}\n   {page['url']}  (HTTP {page['status']})\n\n"
        f"{warning}{page['text']}{more}"
    )


@_tool(_READ_ONLY)
@_tool_errors
async def web_research(
    query: str,
    num_pages: int = 3,
    chars_per_page: int = 4000,
    engine: str = "auto",
    session_name: str = "default",
) -> str:
    """
    One-shot research: search the web, then read the top results in parallel.

    Returns the search results followed by the readable text of each page, ready to
    summarise with sources.

    Args:
        query: What to research
        num_pages: How many top results to read (1–8, default 3)
        chars_per_page: Text to keep per page (500–10000, default 4000)
        engine: 'auto' (default: DuckDuckGo, then Bing if blocked), 'duckduckgo', 'bing', or 'google'
        session_name: Browser session to use
    """
    sess = await _browser.session(session_name)
    count = max(1, min(int(num_pages), 8))
    results = await research.search(_browser, sess.context, query, engine=engine, max_results=count + 4)
    if not results:
        return f"🔍 No results for {query!r} on {engine}."
    allowed = [r for r in results if not _url_problem(r["url"])][:count]
    pages = await research.read_many(
        _browser, sess.context, [r["url"] for r in allowed],
        max_chars=max(500, min(int(chars_per_page), 10_000)),
    )
    sess.action_count += 1 + len(pages)
    lines = [
        f"🔍 Research: {query!r} ({results[0].get('engine', engine)}) — read {len(pages)} of {len(results)} results\n"
    ]
    for i, (hit, page) in enumerate(zip(allowed, pages), 1):
        lines.append(f"━━━ [{i}] {hit['title']}\n{page.get('url', hit['url'])}")
        if page.get("error"):
            lines.append(page["error"])
        elif page.get("blocked"):
            lines.append("⚠️ This site showed a bot check or sign-in wall instead of content; skip it or "
                         "open it with browser_navigate in a visible window.")
        else:
            lines.append(page["text"] + ("\n… (truncated)" if page.get("truncated") else ""))
        lines.append("")
    other = results[len(allowed):]
    if other:
        lines.append("More results not read:")
        lines.extend(f"  • {r['title']} — {r['url']}" for r in other)
    return "\n".join(lines).rstrip()


def _url_problem(url: str) -> Optional[str]:
    from web_browser_mcp.config import check_url_allowed
    return check_url_allowed(url, _settings)


# ============================================================
# CATEGORY 7: LinkedIn Profile (7 tools)
# ============================================================

def _linkedin_enabled() -> Optional[str]:
    if not _settings.linkedin_enabled:
        return "❌ LinkedIn tools are disabled on this server (WEB_BROWSER_MCP_DISABLE_LINKEDIN)."
    return None


@_tool(_NON_DESTRUCTIVE)
@_tool_errors
async def linkedin_open_login(session_name: str = "default") -> str:
    """
    Open the LinkedIn sign-in page in a visible browser window so the user can sign in.

    The user types their own email, password, and any 2-step code; this server never
    sees or stores them. The session's browser profile keeps them signed in afterwards.

    Args:
        session_name: Browser session (profile) to sign in with
    """
    if (problem := _linkedin_enabled()):
        return problem
    existing = _browser.get_existing(session_name)
    if existing is not None and existing.headless:
        await _browser.close(session_name)
    sess = await _browser.launch(session_name, headless=False)
    async with sess.lock:
        page = await _browser.page(session_name)
        if await _linkedin.logged_in(page):
            return "✅ Already signed in to LinkedIn in this browser profile."
        await _browser.goto(page, f"{linkedin_base_url()}/login")
        return (
            "🔑 The LinkedIn sign-in page is open in the browser window.\n"
            "Please sign in there yourself (email, password, and any verification code).\n"
            "When you're done, call linkedin_check_login."
        )


@_tool(_READ_ONLY)
@_tool_errors
async def linkedin_check_login(session_name: str = "default") -> str:
    """
    Check whether this browser profile is signed in to LinkedIn, and whose profile it is.

    Args:
        session_name: Browser session (profile) to check
    """
    if (problem := _linkedin_enabled()):
        return problem
    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await _browser.page(session_name)
        if not await _linkedin.logged_in(page):
            return "❌ Not signed in. Call linkedin_open_login and sign in in the browser window."
        slug = await _linkedin.require_login(page)
        return f"✅ Signed in to LinkedIn.\n  • Profile: {linkedin_base_url()}/in/{slug}/\n  • Edits used today: {_limiter.used_today()}/{_settings.linkedin_max_writes_per_day}"


@_tool(_READ_ONLY)
@_tool_errors
async def linkedin_get_my_profile(
    sections: str = "about,experience,education,skills,certifications,projects",
    include_details: bool = False,
    session_name: str = "default",
) -> str:
    """
    Read your own LinkedIn profile: name, headline, location, and the chosen sections.

    Use this before suggesting improvements, so suggestions build on what is there.

    Args:
        sections: Comma-separated sections: about, experience, education, skills, certifications,
                  projects, volunteering, honors, languages, courses, publications, recommendations
        include_details: Also open the 'See all' pages for experience/education/skills/
                         certifications/projects to get complete lists (slower)
        session_name: Browser session (profile) signed in to LinkedIn
    """
    if (problem := _linkedin_enabled()):
        return problem
    wanted = [s.strip().lower() for s in sections.split(",") if s.strip()]
    unknown = [s for s in wanted if s not in PROFILE_SECTIONS]
    if unknown:
        return f"❌ Unknown section(s): {', '.join(unknown)}. Valid: {', '.join(PROFILE_SECTIONS)}"
    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await _browser.page(session_name)
        slug = await _linkedin.require_login(page)
        data = await _linkedin.read_profile(page, slug, wanted, include_details)
        lines = [
            f"👤 {data.get('name') or '(name not found)'}",
            f"   Headline: {data.get('headline') or '(not found)'}",
            f"   Location: {data.get('location') or '(not found)'}",
            f"   URL: {data.get('profile_url')}",
        ]
        for name, text in data["sections"].items():
            lines.append(f"\n━━━ {name.upper()}\n{text}")
        return "\n".join(lines)


async def _linkedin_write(
    session_name: str,
    kind: str,
    describe_preview,
    apply_change,
    verify,
    confirm: bool,
) -> str:
    """Shared preview → confirm → save → verify flow for LinkedIn edits."""
    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await _browser.page(session_name)
        slug = await _linkedin.require_login(page)
        if confirm:
            limit_problem = _limiter.check()
            if limit_problem:
                return limit_problem
        dialog = await _linkedin.open_editor(page, slug, kind)
        try:
            if not confirm:
                preview = await describe_preview(dialog)
                await _linkedin.dismiss(page)
                return preview + "\n\nNothing was changed. Call again with confirm=true after the user approves."
            await apply_change(page, dialog)
            await _linkedin.save(page, dialog)
        except BrowserError as exc:
            snap = await _browser.snapshot(page, scope="dialog")
            return f"{exc}\n\nOpen editor (finish with browser_fill / browser_click):\n{_format_snapshot(snap)}"
        _limiter.record()
        sess.action_count += 1
        return await verify(page, slug)


@_tool(_MAY_CHANGE_DATA)
@_tool_errors
async def linkedin_update_headline(headline: str, confirm: bool = False, session_name: str = "default") -> str:
    """
    Change your LinkedIn headline (the line under your name, max 220 characters).

    ⚠️ Confirmation-gated: the first call shows current vs. new and changes nothing.
    Call again with confirm=true only after the user approves the new text.

    Args:
        headline: New headline text
        confirm: Must be true to save (default: False)
        session_name: Browser session signed in to LinkedIn
    """
    if (problem := _linkedin_enabled()):
        return problem
    new = validate_text(headline, HEADLINE_LIMIT, "Headline")

    async def preview(dialog):
        current = await (await _linkedin.headline_field(dialog)).input_value()
        return (
            "🔒 Confirmation required: update LinkedIn headline\n"
            f"  • Current ({len(current)} chars): {current}\n"
            f"  • New     ({len(new)}/{HEADLINE_LIMIT} chars): {new}"
        )

    async def apply(page, dialog):
        await (await _linkedin.headline_field(dialog)).fill(new)

    async def verify(page, slug):
        shown = await _linkedin.read_headline(page, slug)
        ok = shown.strip() == new
        return (
            f"✅ Headline saved{' and verified on your profile' if ok else ''}.\n  • {new}"
            + ("" if ok else f"\n  ⚠️ Profile currently shows: {shown or '(could not read)'} — LinkedIn may take a moment.")
        )

    return await _linkedin_write(session_name, "intro", preview, apply, verify, confirm)


@_tool(_MAY_CHANGE_DATA)
@_tool_errors
async def linkedin_update_about(about: str, confirm: bool = False, session_name: str = "default") -> str:
    """
    Replace your LinkedIn About (summary) section (max 2,600 characters).

    ⚠️ Confirmation-gated: the first call shows current vs. new and changes nothing.
    Call again with confirm=true only after the user approves the new text.

    Args:
        about: New About text (plain text; blank lines separate paragraphs)
        confirm: Must be true to save (default: False)
        session_name: Browser session signed in to LinkedIn
    """
    if (problem := _linkedin_enabled()):
        return problem
    new = validate_text(about, ABOUT_LIMIT, "About text")

    async def preview(dialog):
        current = await (await _linkedin.about_field(dialog)).input_value()
        excerpt = current if len(current) <= 600 else current[:600] + " …"
        return (
            "🔒 Confirmation required: replace LinkedIn About section\n"
            f"  • Current ({len(current)} chars):\n{excerpt or '(empty)'}\n\n"
            f"  • New ({len(new)}/{ABOUT_LIMIT} chars):\n{new}"
        )

    async def apply(page, dialog):
        await (await _linkedin.about_field(dialog)).fill(new)

    async def verify(page, slug):
        shown = await _linkedin.read_about(page, slug)
        ok = new[:60] in " ".join(shown.split()) or " ".join(new.split())[:60] in " ".join(shown.split())
        return f"✅ About section saved{' and verified on your profile' if ok else ''} ({len(new)} characters)." + (
            "" if ok else "\n  ⚠️ Could not confirm the new text on the profile yet — check it with linkedin_get_my_profile."
        )

    return await _linkedin_write(session_name, "about", preview, apply, verify, confirm)


@_tool(_MAY_CHANGE_DATA)
@_tool_errors
async def linkedin_add_skills(skills: str, confirm: bool = False, session_name: str = "default") -> str:
    """
    Add one or more skills to your LinkedIn profile (comma-separated, max 10 per call).

    Skills already on the profile are skipped. ⚠️ Confirmation-gated: the first call
    lists what would be added and changes nothing.

    Args:
        skills: Comma-separated skills, e.g. 'Kubernetes, Terraform, GitHub Actions'
        confirm: Must be true to save (default: False)
        session_name: Browser session signed in to LinkedIn
    """
    if (problem := _linkedin_enabled()):
        return problem
    wanted = []
    for raw in skills.split(","):
        name = raw.strip()
        if name and name.lower() not in [w.lower() for w in wanted]:
            wanted.append(validate_text(name, SKILL_LIMIT, f"Skill '{name[:20]}'"))
    if not wanted:
        return "❌ Pass at least one skill."
    if len(wanted) > 10:
        return "❌ Add at most 10 skills per call (keeps edits at a human pace)."

    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await _browser.page(session_name)
        slug = await _linkedin.require_login(page)
        current = await _linkedin.read_profile(page, slug, ["skills"], include_details=True)
        existing_text = (current["sections"].get("skills") or "").lower()
        existing_lines = {line.strip().lower() for line in existing_text.split("\n")}
        new_skills = [s for s in wanted if s.lower() not in existing_lines]
        skipped = [s for s in wanted if s not in new_skills]

        if not new_skills:
            return f"ℹ️ All of these are already on your profile: {', '.join(skipped)}"
        if not confirm:
            return (
                "🔒 Confirmation required: add LinkedIn skills\n"
                f"  • Will add: {', '.join(new_skills)}\n"
                + (f"  • Already present (skipped): {', '.join(skipped)}\n" if skipped else "")
                + "\nNothing was changed. Call again with confirm=true after the user approves."
            )

        added, failed = [], []
        for skill in new_skills:
            limit_problem = _limiter.check()
            if limit_problem:
                failed.append(f"{skill}: {limit_problem}")
                break
            try:
                dialog = await _linkedin.open_editor(page, slug, "skills")
                field = await _linkedin.skill_field(dialog)
                await field.fill(skill)
                await _linkedin.pick_typeahead(page, skill)
                await _linkedin.save(page, dialog)
                _limiter.record()
                added.append(skill)
            except BrowserError as exc:
                failed.append(f"{skill}: {exc}")
                await _linkedin.dismiss(page)
        sess.action_count += len(added)
        lines = [f"✅ Added {len(added)} skill(s): {', '.join(added)}" if added else "⚠️ No skills were added."]
        if skipped:
            lines.append(f"  • Already present: {', '.join(skipped)}")
        if failed:
            lines.append("  • Not added:")
            lines.extend(f"    - {f}" for f in failed)
        return "\n".join(lines)


@_tool(_NON_DESTRUCTIVE)
@_tool_errors
async def linkedin_open_section_editor(section: str, session_name: str = "default") -> str:
    """
    Open the LinkedIn editor for a section and list its form fields (with refs).

    Use for edits without a dedicated tool (e.g. adding an experience, education,
    certification, or project): fill the fields with browser_fill /
    browser_select_option, then click Save with browser_click(confirm=true) after
    the user approves.

    Args:
        section: intro, about, skills, experience, education, certifications, or projects
        session_name: Browser session signed in to LinkedIn
    """
    if (problem := _linkedin_enabled()):
        return problem
    kind = section.strip().lower()
    if kind not in EDITOR_URLS:
        return f"❌ Unknown section '{section}'. Use one of: {', '.join(EDITOR_URLS)}."
    sess = await _browser.session(session_name)
    async with sess.lock:
        page = await _browser.page(session_name)
        slug = await _linkedin.require_login(page)
        await _linkedin.open_editor(page, slug, kind)
        snap = await _browser.snapshot(page, scope="dialog")
        return (
            f"📝 LinkedIn '{kind}' editor is open. Fields:\n{_format_snapshot(snap)}\n\n"
            "Fill fields with browser_fill(ref=...), then show the user what will be saved and "
            "click Save with browser_click(ref=..., confirm=true) once they approve."
        )


# ============================================================
# Entry Point
# ============================================================

@mcp.custom_route("/health", methods=["GET"])
async def health_check(_request):
    from starlette.responses import JSONResponse
    return JSONResponse({"status": "healthy", "server": "web-browser-mcp", "version": __version__})


def main() -> None:
    """Console entry point for PyPI / uvx installs and hosted HTTP containers."""
    transport = os.environ.get("MCP_TRANSPORT", "").strip().lower()
    if not transport:
        if "--http" in sys.argv:
            transport = "streamable-http"
        elif os.environ.get("K_SERVICE"):
            transport = "streamable-http"
        else:
            transport = "stdio"

    if transport in ("http", "streamable-http", "sse"):
        port = int(os.environ.get("PORT", "8080"))
        host = os.environ.get("HOST", "127.0.0.1")
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.settings.json_response = True
        resolved = "sse" if transport == "sse" else "streamable-http"
        mcp.run(transport=resolved)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
