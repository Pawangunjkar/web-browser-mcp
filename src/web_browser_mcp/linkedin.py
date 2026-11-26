"""
LinkedIn profile helpers for Web Browser MCP Server.

Author: Pawan Gunjkar (pawangunjkar@gmail.com)

Works on YOUR OWN profile, in a browser profile where YOU signed in by hand.
This module never types passwords, never solves CAPTCHAs, and saves only after
the caller passes confirm=True. Writes are rate-limited (see config.WriteLimiter).

LinkedIn changes its page structure often. Every editor is opened by trying the
current form URLs first, then older URLs, then the section's own edit button.
When something cannot be found, the error includes a snapshot of the open form
so the generic browser_* tools can finish the job by hand.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

from playwright.async_api import Error as PlaywrightError, Locator, Page

from web_browser_mcp.browser_manager import BrowserError, BrowserManager, settle
from web_browser_mcp.config import WriteLimiter

HEADLINE_LIMIT = 220
ABOUT_LIMIT = 2600
SKILL_LIMIT = 80

_LOGGED_OUT_MARKERS = ("/login", "/authwall", "/checkpoint", "/uas/", "/signup", "/start/join")

# Section anchors on the profile page (id of the element inside each <section>).
PROFILE_SECTIONS = {
    "about": "about",
    "experience": "experience",
    "education": "education",
    "certifications": "licenses_and_certifications",
    "skills": "skills",
    "projects": "projects",
    "volunteering": "volunteering_experience",
    "honors": "honors_and_awards",
    "languages": "languages",
    "courses": "courses",
    "publications": "publications",
    "recommendations": "recommendations",
}

# "See all" detail pages that hold the complete list for a section.
DETAIL_PAGES = {
    "experience": "details/experience/",
    "education": "details/education/",
    "certifications": "details/certifications/",
    "skills": "details/skills/",
    "projects": "details/projects/",
}

# Editor entry points, newest first. add-edit URLs take a profileFormEntryPoint query.
EDITOR_URLS = {
    "intro": ["add-edit/INTRO/", "edit/intro/"],
    "about": ["add-edit/SUMMARY/", "edit/about/", "edit/forms/summary/new/"],
    "skills": ["add-edit/SKILL_AND_ASSOCIATION/", "edit/forms/skills/new/"],
    "experience": ["add-edit/POSITION/", "edit/forms/position/new/"],
    "education": ["add-edit/EDUCATION/", "edit/forms/education/new/"],
    "certifications": ["add-edit/CERTIFICATION/", "edit/forms/certification/new/"],
    "projects": ["add-edit/PROJECT/", "edit/forms/project/new/"],
}

# Fallback: the pencil / plus button inside a section on the profile page.
EDIT_BUTTONS = {
    "intro": ('main section:has(h1)', r"edit intro|edit profile|^edit$"),
    "about": ('section:has(#about)', r"edit about|^edit$"),
    "skills": ('section:has(#skills)', r"add skill|add new skill|^add$"),
    "experience": ('section:has(#experience)', r"add position|add experience|^add$"),
    "education": ('section:has(#education)', r"add education|^add$"),
    "certifications": ('section:has(#licenses_and_certifications)', r"add license|add certification|^add$"),
    "projects": ('section:has(#projects)', r"add project|^add$"),
}

_DIALOG = '[role="dialog"]:visible, [role="alertdialog"]:visible, dialog[open]'

_TOP_CARD_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const h1 = document.querySelector('main h1') || document.querySelector('h1');
  if (!h1) return null;
  const card = h1.closest('section') || h1.parentElement;
  let headline = '';
  const preferred = card.querySelector('.text-body-medium');
  if (preferred) headline = clean(preferred.innerText);
  if (!headline) {
    const lines = (card.innerText || '').split('\n').map(clean).filter(Boolean);
    const i = lines.indexOf(clean(h1.innerText));
    if (i >= 0 && lines[i + 1]) headline = lines[i + 1];
  }
  const loc = card.querySelector('.text-body-small.inline, [data-test-location]');
  return {name: clean(h1.innerText), headline, location: loc ? clean(loc.innerText) : ''};
}
"""

_SECTION_TEXT_JS = r"""
(anchorId) => {
  const anchor = document.getElementById(anchorId);
  if (!anchor) return null;
  const section = anchor.closest('section') || anchor.parentElement;
  const lines = (section.innerText || '').split('\n').map(l => l.replace(/\s+/g, ' ').trim());
  const out = [];
  for (const line of lines) {
    if (!line) continue;
    if (out.length && out[out.length - 1] === line) continue;
    out.push(line);
  }
  return out.join('\n');
}
"""


def base_url() -> str:
    return os.environ.get("LINKEDIN_BASE_URL", "https://www.linkedin.com").rstrip("/")


def is_logged_out_url(url: str) -> bool:
    lowered = url.lower()
    return any(marker in lowered for marker in _LOGGED_OUT_MARKERS)


class LinkedInHelper:
    def __init__(self, manager: BrowserManager, limiter: WriteLimiter):
        self.manager = manager
        self.limiter = limiter

    # ------------------------------------------------------------------
    # Login and profile
    # ------------------------------------------------------------------

    async def logged_in(self, page: Page) -> bool:
        await self.manager.goto(page, f"{base_url()}/feed/")
        return not is_logged_out_url(page.url) and "/feed" in page.url

    async def require_login(self, page: Page) -> str:
        """Return the signed-in member's profile slug, or raise with sign-in instructions."""
        await self.manager.goto(page, f"{base_url()}/in/me/")
        match = re.search(r"/in/([^/?#]+)", page.url)
        if is_logged_out_url(page.url) or not match or match.group(1) == "me":
            raise BrowserError(
                "❌ Not signed in to LinkedIn in this browser session. Call linkedin_open_login, sign in "
                "yourself in the browser window, then try again."
            )
        return match.group(1)

    async def read_profile(self, page: Page, slug: str, sections: list[str], include_details: bool) -> dict:
        await self.manager.goto(page, f"{base_url()}/in/{slug}/")
        for _ in range(4):  # LinkedIn lazy-loads sections as you scroll
            await page.mouse.wheel(0, 2500)
            await page.wait_for_timeout(400)
        top = await page.evaluate(_TOP_CARD_JS) or {}
        result: dict[str, Any] = {"profile_url": page.url, **top, "sections": {}}
        for name in sections:
            anchor = PROFILE_SECTIONS.get(name)
            if not anchor:
                continue
            text = await page.evaluate(_SECTION_TEXT_JS, anchor)
            result["sections"][name] = text if text is not None else "(section not on profile)"

        if include_details:
            for name in sections:
                suffix = DETAIL_PAGES.get(name)
                if not suffix:
                    continue
                try:
                    await self.manager.goto(page, f"{base_url()}/in/{slug}/{suffix}")
                    text = await self.manager.text(page, selector="main") or ""
                    result["sections"][name] = text[:12_000]
                except BrowserError as exc:
                    result["sections"][f"{name}_details_error"] = str(exc)
        return result

    async def read_headline(self, page: Page, slug: str) -> str:
        await self.manager.goto(page, f"{base_url()}/in/{slug}/")
        top = await page.evaluate(_TOP_CARD_JS) or {}
        return top.get("headline", "")

    async def read_about(self, page: Page, slug: str) -> str:
        await self.manager.goto(page, f"{base_url()}/in/{slug}/")
        text = await page.evaluate(_SECTION_TEXT_JS, "about") or ""
        lines = text.split("\n")
        if lines and lines[0].strip().lower() == "about":
            lines = lines[1:]
        return "\n".join(lines).strip()

    # ------------------------------------------------------------------
    # Editors
    # ------------------------------------------------------------------

    async def open_editor(self, page: Page, slug: str, kind: str) -> Locator:
        """Open the edit/add form for a section and return its dialog."""
        if kind not in EDITOR_URLS:
            raise BrowserError(f"❌ Unknown section '{kind}'. Use one of: {', '.join(EDITOR_URLS)}.")
        for suffix in EDITOR_URLS[kind]:
            query = "?profileFormEntryPoint=PROFILE_SECTION" if suffix.startswith("add-edit/") else ""
            try:
                await self.manager.goto(page, f"{base_url()}/in/{slug}/{suffix}{query}")
            except BrowserError:
                continue
            dialog = await self._wait_dialog(page)
            if dialog is not None:
                return dialog

        # Fallback: click the section's own edit/add button on the profile page.
        await self.manager.goto(page, f"{base_url()}/in/{slug}/")
        container, pattern = EDIT_BUTTONS[kind]
        section = page.locator(container).first
        try:
            if await section.count():
                button = section.get_by_role("button", name=re.compile(pattern, re.I)).or_(
                    section.get_by_role("link", name=re.compile(pattern, re.I))
                ).first
                if await button.count():
                    await button.click()
                    dialog = await self._wait_dialog(page)
                    if dialog is not None:
                        return dialog
        except PlaywrightError:
            pass
        raise BrowserError(
            f"❌ Could not open the LinkedIn '{kind}' editor (LinkedIn may have changed its layout). "
            "Use browser_snapshot to find the edit button, then browser_click / browser_fill to finish."
        )

    @staticmethod
    async def _wait_dialog(page: Page, timeout_ms: int = 8000) -> Optional[Locator]:
        dialog = page.locator(_DIALOG).last
        try:
            await dialog.wait_for(state="visible", timeout=timeout_ms)
            return dialog
        except PlaywrightError:
            return None

    @staticmethod
    async def _first_present(*candidates: Locator) -> Optional[Locator]:
        for candidate in candidates:
            try:
                if await candidate.count() and await candidate.first.is_visible():
                    return candidate.first
            except PlaywrightError:
                continue
        return None

    async def headline_field(self, dialog: Locator) -> Locator:
        field = await self._first_present(
            dialog.get_by_label(re.compile(r"^\s*headline", re.I)),
            dialog.locator('input[id*="headline" i], textarea[id*="headline" i], textarea[name*="headline" i]'),
        )
        if field is None:
            raise BrowserError("❌ Headline field not found in the intro editor.")
        return field

    async def about_field(self, dialog: Locator) -> Locator:
        field = await self._first_present(
            dialog.locator("textarea"),
            dialog.get_by_label(re.compile(r"about|summary|description", re.I)),
            dialog.locator('[contenteditable="true"]'),
        )
        if field is None:
            raise BrowserError("❌ About text box not found in the editor.")
        return field

    async def skill_field(self, dialog: Locator) -> Locator:
        field = await self._first_present(
            dialog.get_by_label(re.compile(r"skill", re.I)),
            dialog.locator('input[role="combobox"]'),
            dialog.locator('input[type="text"]'),
        )
        if field is None:
            raise BrowserError("❌ Skill input not found in the editor.")
        return field

    async def save(self, page: Page, dialog: Locator) -> None:
        """Click Save in the dialog and wait for it to close. Raise with the form's errors if it stays open."""
        button = dialog.get_by_role("button", name=re.compile(r"^\s*save\s*$", re.I)).first
        if not await button.count():
            raise BrowserError("❌ Save button not found in the editor.")
        if await button.is_disabled():
            raise BrowserError("❌ LinkedIn disabled the Save button (nothing changed, or the form is invalid).")
        await button.click()
        try:
            await dialog.wait_for(state="hidden", timeout=15_000)
        except PlaywrightError:
            errors = await self._form_errors(dialog)
            raise BrowserError(
                "❌ LinkedIn did not accept the change; the editor is still open."
                + (f"\nForm says: {errors}" if errors else "")
            )
        await settle(page)

    @staticmethod
    async def _form_errors(dialog: Locator) -> str:
        try:
            alerts = dialog.locator('[role="alert"], .artdeco-inline-feedback--error, [aria-live="assertive"]')
            texts = [t.strip() for t in await alerts.all_inner_texts() if t.strip()]
            return " | ".join(texts)[:500]
        except PlaywrightError:
            return ""

    @staticmethod
    async def dismiss(page: Page) -> None:
        """Close an editor without saving (also answers a 'Discard changes?' prompt)."""
        try:
            await page.keyboard.press("Escape")
            discard = page.get_by_role("button", name=re.compile(r"^\s*discard\s*$", re.I))
            if await discard.count():
                await discard.first.click()
        except PlaywrightError:
            pass

    async def pick_typeahead(self, page: Page, value: str) -> bool:
        """After typing a skill, choose the matching suggestion if LinkedIn shows one."""
        try:
            option = page.get_by_role("option", name=re.compile(rf"^\s*{re.escape(value)}\s*$", re.I)).first
            await option.wait_for(state="visible", timeout=2500)
            await option.click()
            return True
        except PlaywrightError:
            return False


def validate_text(value: str, limit: int, what: str) -> str:
    text = (value or "").strip()
    if not text:
        raise BrowserError(f"❌ {what} must not be empty.")
    if len(text) > limit:
        raise BrowserError(f"❌ {what} is {len(text)} characters; LinkedIn allows at most {limit}.")
    return text
