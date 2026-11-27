"""
Tests for Web Browser MCP Server.

Author: Pawan Gunjkar (pawangunjkar@gmail.com)

Unit tests cover URL policy, safety heuristics, and the LinkedIn rate limiter.
Integration tests drive a real headless browser against a local fake website
(ordinary pages, a fake search engine, and a fake LinkedIn), so no internet
access or real LinkedIn account is needed.

Set BROWSER_CHANNEL=chromium to test with Playwright's bundled Chromium
instead of installed Google Chrome.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = Path(__file__).resolve().parent
for p in (str(SRC), str(TESTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ["WEB_BROWSER_MCP_CACHE_DIR"] = tempfile.mkdtemp(prefix="wbm-test-")
os.environ["BROWSER_HEADLESS"] = "true"
os.environ.setdefault("BROWSER_CHANNEL", "chrome")
os.environ["LINKEDIN_MIN_SECONDS_BETWEEN_WRITES"] = "0"
os.environ["LINKEDIN_MAX_WRITES_PER_DAY"] = "50"

import fake_site  # noqa: E402
from web_browser_mcp import server, web_research  # noqa: E402
from web_browser_mcp.browser_manager import BrowserManager  # noqa: E402
from web_browser_mcp.config import BrowserSettings, WriteLimiter, check_url_allowed, normalize_url  # noqa: E402


# ============================================================
# Unit tests (no browser)
# ============================================================

class UrlPolicyTests(unittest.TestCase):
    def settings(self, **overrides) -> BrowserSettings:
        s = BrowserSettings.from_env()
        for k, v in overrides.items():
            setattr(s, k, v)
        return s

    def test_normalize(self):
        self.assertEqual(normalize_url("example.com/a"), "https://example.com/a")
        self.assertEqual(normalize_url("http://x.org"), "http://x.org")

    def test_dangerous_schemes_blocked(self):
        s = self.settings()
        self.assertIn("file://", check_url_allowed("file:///etc/passwd", s))
        self.assertIn("Only http(s)", check_url_allowed("javascript:alert(1)", s))
        self.assertIn("Only http(s)", check_url_allowed("chrome://settings", s))
        self.assertIsNone(check_url_allowed("https://example.com", s))

    def test_allow_and_block_lists(self):
        s = self.settings(allowed_domains=["linkedin.com"], blocked_domains=["evil.com"])
        self.assertIsNone(check_url_allowed("https://www.linkedin.com/feed/", s))
        self.assertIn("not in BROWSER_ALLOWED_DOMAINS", check_url_allowed("https://example.com", s))
        self.assertIn("not in BROWSER_ALLOWED_DOMAINS", check_url_allowed("https://notlinkedin.com", s))
        s2 = self.settings(blocked_domains=["evil.com"])
        self.assertIn("blocked", check_url_allowed("https://sub.evil.com/x", s2))


class HeuristicTests(unittest.TestCase):
    def test_submit_detection(self):
        lk = BrowserManager.looks_like_submit
        self.assertTrue(lk({"text": "Save", "label": ""}))
        self.assertTrue(lk({"text": "", "label": "Delete post"}))
        self.assertTrue(lk({"type": "submit", "inForm": True, "text": "Go"}))
        self.assertFalse(lk({"text": "Show more", "label": ""}))
        self.assertFalse(lk({"text": "Updates", "label": ""}))
        self.assertFalse(lk({"text": "Posts", "label": ""}))

    def test_enter_submit_detection(self):
        enter = BrowserManager.enter_may_submit
        self.assertTrue(enter({"tag": "input", "inForm": True}))
        self.assertTrue(enter({"tag": "div", "multiline": True}))  # chat boxes send on Enter
        self.assertTrue(enter({"tag": "button", "text": "Delete"}))
        self.assertFalse(enter({"tag": "input", "inForm": True, "searchField": True}))
        self.assertFalse(enter({"tag": "input", "inForm": True, "searchForm": True}))
        self.assertFalse(enter({"tag": "input", "inForm": False}))
        self.assertFalse(enter({"tag": "button", "text": "Show more"}))

    def test_secret_fields(self):
        secret = BrowserManager.is_secret_field
        self.assertTrue(secret({"type": "password"}))
        self.assertTrue(secret({"type": "text", "autocomplete": "cc-number"}))
        self.assertTrue(secret({"type": "text", "autocomplete": "one-time-code"}))
        self.assertFalse(secret({"type": "text", "autocomplete": "name"}))

    def test_unwrap_redirect(self):
        url = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fb%3D1&rut=x"
        self.assertEqual(web_research.unwrap_redirect(url), "https://example.com/a?b=1")
        self.assertEqual(web_research.unwrap_redirect("https://x.org/"), "https://x.org/")
        bing = "https://www.bing.com/ck/a?!&&p=abc&u=a1aHR0cHM6Ly9wbGF5d3JpZ2h0LmRldi8&ntb=1"
        self.assertEqual(web_research.unwrap_redirect(bing), "https://playwright.dev/")


class WriteLimiterTests(unittest.TestCase):
    def make(self, per_day: int, gap: int) -> WriteLimiter:
        s = BrowserSettings.from_env()
        s.cache_dir = Path(tempfile.mkdtemp())
        s.linkedin_max_writes_per_day = per_day
        s.linkedin_min_seconds_between_writes = gap
        return WriteLimiter(s)

    def test_daily_cap_persists(self):
        limiter = self.make(per_day=2, gap=0)
        limiter.record()
        limiter.record()
        self.assertIn("Daily LinkedIn edit limit", limiter.check())
        again = WriteLimiter(limiter.settings)  # a restarted server still sees the count
        self.assertEqual(again.used_today(), 2)

    def test_minimum_gap(self):
        limiter = self.make(per_day=10, gap=60)
        self.assertIsNone(limiter.check())
        limiter.record()
        self.assertIn("Please wait", limiter.check())


# ============================================================
# Integration tests (real headless browser + local fake site)
# ============================================================

class BrowserTestCase(unittest.TestCase):
    loop: asyncio.AbstractEventLoop
    site: fake_site.FakeSite

    @classmethod
    def setUpClass(cls):
        cls.site = fake_site.FakeSite().__enter__()
        cls.loop = asyncio.new_event_loop()
        os.environ["LINKEDIN_BASE_URL"] = cls.site.base + "/li"
        cls._engines = dict(web_research.SEARCH_ENGINES)
        web_research.SEARCH_ENGINES["duckduckgo"] = cls.site.base + "/ddg/html/?q={q}"

    @classmethod
    def tearDownClass(cls):
        cls.loop.run_until_complete(server._browser.close_all())
        cls.loop.close()
        cls.site.__exit__(None, None, None)
        web_research.SEARCH_ENGINES.clear()
        web_research.SEARCH_ENGINES.update(cls._engines)

    def run_async(self, coro):
        return self.loop.run_until_complete(coro)

    def url(self, path: str) -> str:
        return self.site.base + path


class PageToolTests(BrowserTestCase):
    def test_navigate_and_read_text_in_pages(self):
        out = self.run_async(server.browser_navigate(self.url("/page1")))
        self.assertIn("Test Page One", out)
        self.assertIn("HTTP 200", out)
        first = self.run_async(server.browser_get_text(max_chars=200))
        self.assertIn("next_offset=200", first)
        rest = self.run_async(server.browser_get_text(offset=200, max_chars=15000))
        self.assertIn("canary deployments", rest)
        main = self.run_async(server.browser_get_text(main_content_only=True))
        self.assertNotIn("Home | Products", main)

    def test_snapshot_click_gate_fill_select(self):
        self.run_async(server.browser_navigate(self.url("/page1")))
        snap = self.run_async(server.browser_snapshot())
        self.assertIn("'Full name'", snap)
        self.assertIn("••••", snap)  # password field values are never shown

        refs = {line.split("]")[0].strip(" ["): line for line in snap.splitlines() if line.strip().startswith("[e")}
        name_ref = next(r for r, l in refs.items() if "'Full name'" in l)
        pw_ref = next(r for r, l in refs.items() if "'Password'" in l)
        color_ref = next(r for r, l in refs.items() if "'Color'" in l)
        more_ref = next(r for r, l in refs.items() if "'Show more'" in l)
        save_ref = next(r for r, l in refs.items() if "'Save changes'" in l)

        refused = self.run_async(server.browser_fill("hunter2", ref=pw_ref))
        self.assertIn("never types secrets", refused)

        self.assertIn("✅ Filled", self.run_async(server.browser_fill("Jane", ref=name_ref)))
        self.assertIn("✅ Selected", self.run_async(server.browser_select_option("Green", ref=color_ref)))

        self.assertIn("✅ Clicked", self.run_async(server.browser_click(ref=more_ref)))
        self.assertIn("Expanded", self.run_async(server.browser_get_text(selector="#status")))

        gated = self.run_async(server.browser_click(ref=save_ref))
        self.assertIn("Confirmation required", gated)
        self.assertIn("Expanded", self.run_async(server.browser_get_text(selector="#status")))

        self.assertIn("✅ Clicked", self.run_async(server.browser_click(ref=save_ref, confirm=True)))
        self.assertIn("Saved: Jane", self.run_async(server.browser_get_text(selector="#status")))

    def test_stale_ref_and_missing_element(self):
        self.run_async(server.browser_navigate(self.url("/page2")))
        out = self.run_async(server.browser_click(ref="e999"))
        self.assertIn("run browser_snapshot again", out)
        self.assertIn("No element", self.run_async(server.browser_click(selector="#nope")))

    def test_links_tabs_and_navigation(self):
        self.run_async(server.browser_navigate(self.url("/page1")))
        links = self.run_async(server.browser_get_links(contains="page two"))
        self.assertIn("/page2", links)
        out = self.run_async(server.browser_click(text="Open in new tab"))
        self.assertIn("new tab", out)
        tabs = self.run_async(server.browser_list_tabs())
        self.assertIn("👉 [1]", tabs)
        self.assertIn("✅ Closed tab", self.run_async(server.browser_close_tab()))
        self.run_async(server.browser_click(text="Go to page two"))
        self.assertIn("Page Two", self.run_async(server.browser_back()) + self.run_async(server.browser_forward()))

    def refs(self, snap: str) -> dict:
        return {line.split("]")[0].strip(" ["): line for line in snap.splitlines() if line.strip().startswith("[e")}

    def test_confirm_popup_dismissed_until_user_confirms(self):
        self.run_async(server.browser_navigate(self.url("/page3")))
        out = self.run_async(server.browser_click(text="Trash"))
        self.assertIn("Really delete this item?", out)
        self.assertIn("dismissed", out)
        self.assertIn("Kept", self.run_async(server.browser_get_text(selector="#status")))
        out = self.run_async(server.browser_click(text="Trash", confirm=True))
        self.assertIn("accepted", out)
        self.assertIn("Deleted", self.run_async(server.browser_get_text(selector="#status")))

    def test_snapshot_sees_shadow_dom_and_hover(self):
        self.run_async(server.browser_navigate(self.url("/page3")))
        refs = self.refs(self.run_async(server.browser_snapshot()))
        shadow_ref = next(r for r, l in refs.items() if "'Shadow button'" in l)
        self.assertIn("✅ Clicked", self.run_async(server.browser_click(ref=shadow_ref)))
        self.assertIn("Shadow clicked", self.run_async(server.browser_get_text(selector="#status")))
        self.assertIn("✅ Hovering", self.run_async(server.browser_hover(selector="#menu")))
        self.assertIn("Menu opened", self.run_async(server.browser_get_text(selector="#status")))

    def test_enter_in_form_is_gated_but_search_is_not(self):
        self.run_async(server.browser_navigate(self.url("/page1")))
        gated = self.run_async(server.browser_fill("Bob", selector="#name", press_enter=True))
        self.assertIn("Enter was NOT pressed", gated)
        self.assertIn("Idle", self.run_async(server.browser_get_text(selector="#status")))
        self.assertIn("Confirmation required", self.run_async(server.browser_press_key("Enter")))
        self.assertIn("Idle", self.run_async(server.browser_get_text(selector="#status")))
        self.run_async(server.browser_fill("Bob", selector="#name", press_enter=True, confirm=True))
        self.assertIn("Saved: Bob", self.run_async(server.browser_get_text(selector="#status")))

        self.run_async(server.browser_navigate(self.url("/page3")))
        out = self.run_async(server.browser_fill("rollouts", label="Search the site", press_enter=True))
        self.assertNotIn("NOT pressed", out)
        self.assertIn("Searched: rollouts", self.run_async(server.browser_get_text(selector="#status")))

    def test_select_unknown_option_fails_fast_with_choices(self):
        import time
        self.run_async(server.browser_navigate(self.url("/page1")))
        start = time.monotonic()
        out = self.run_async(server.browser_select_option("Purple", selector="#color"))
        self.assertLess(time.monotonic() - start, 5)
        self.assertIn("'Red', 'Green'", out)
        self.assertIn("✅ Selected 'Green'", self.run_async(server.browser_select_option("green", selector="#color")))

    def test_text_prefers_exact_match(self):
        self.run_async(server.browser_navigate(self.url("/page3")))
        self.run_async(server.browser_click(text="Open"))
        self.assertIn("Opened", self.run_async(server.browser_get_text(selector="#status")))

    def test_closing_earlier_tab_keeps_active_tab(self):
        self.run_async(server.browser_navigate(self.url("/page1")))
        first = server._browser.get_existing("default").active_index
        self.run_async(server.browser_new_tab(self.url("/page2")))
        self.run_async(server.browser_new_tab(self.url("/page3")))
        self.run_async(server.browser_switch_tab(first + 1))
        self.run_async(server.browser_close_tab(first))
        tabs = self.run_async(server.browser_list_tabs())
        active = next(line for line in tabs.splitlines() if "👉" in line)
        self.assertIn("/page2", active)
        self.run_async(server.browser_close_tab())
        self.run_async(server.browser_close_tab())

    def test_blocked_urls(self):
        self.assertIn("file://", self.run_async(server.browser_navigate("file:///C:/Windows/win.ini")))
        self.assertIn("Only http(s)", self.run_async(server.browser_navigate("javascript:alert(1)")))

    def test_status_lists_session(self):
        self.run_async(server.browser_navigate(self.url("/page2")))
        self.assertIn("🟢 default", self.run_async(server.browser_status()))


class ResearchTests(BrowserTestCase):
    def test_search_parses_and_unwraps(self):
        out = self.run_async(server.web_search("kubernetes rollout"))
        self.assertIn("Kubernetes rollouts guide", out)
        self.assertIn(self.url("/page1"), out)
        self.assertNotIn("ads.example", out)
        self.assertNotIn("duckduckgo.com/l/", out)

    def test_captcha_is_reported_not_bypassed(self):
        web_research.SEARCH_ENGINES["bing"] = self.url("/captcha?q={q}")
        try:
            out = self.run_async(server.web_search("x", engine="bing"))
        finally:
            web_research.SEARCH_ENGINES["bing"] = self._engines["bing"]
        self.assertIn("CAPTCHA", out)

    def test_auto_engine_falls_back_when_blocked(self):
        web_research.SEARCH_ENGINES["duckduckgo"] = self.url("/captcha?q={q}")
        web_research.SEARCH_ENGINES["bing"] = self.url("/bing?q={q}")
        try:
            fallback = self.run_async(server.web_search("rollouts"))
            web_research.SEARCH_ENGINES["bing"] = self.url("/captcha?q={q}")
            blocked = self.run_async(server.web_search("rollouts"))
        finally:
            web_research.SEARCH_ENGINES["duckduckgo"] = self.url("/ddg/html/?q={q}")
            web_research.SEARCH_ENGINES["bing"] = self._engines["bing"]
        self.assertIn("Kubernetes rollouts guide", fallback)
        self.assertIn("Every search engine asked for human verification", blocked)

    def test_read_page_and_research(self):
        page = self.run_async(server.web_read_page(self.url("/page2")))
        self.assertIn("Reached the second page", page)
        report = self.run_async(server.web_research("kubernetes", num_pages=2))
        self.assertIn("read 2 of 2 results", report)
        self.assertIn("canary deployments", report)
        self.assertIn("Reached the second page", report)


class McpProtocolTests(BrowserTestCase):
    def test_tools_over_mcp_including_screenshot_image(self):
        from mcp.shared.memory import create_connected_server_and_client_session

        async def scenario():
            async with create_connected_server_and_client_session(server.mcp._mcp_server) as client:
                tools = await client.list_tools()
                nav = await client.call_tool("browser_navigate", {"url": self.url("/page2")})
                shot = await client.call_tool("browser_screenshot", {})
                return {t.name: t for t in tools.tools}, nav, shot

        tools, nav, shot = self.run_async(scenario())
        self.assertEqual(server.mcp._mcp_server.version, server.__version__)
        self.assertEqual(len(tools), 34)
        self.assertIn("browser_hover", tools)
        self.assertTrue(tools["web_research"].annotations.readOnlyHint)
        self.assertFalse(tools["browser_navigate"].annotations.destructiveHint)
        self.assertTrue(tools["browser_click"].annotations.destructiveHint)
        self.assertTrue(all(t.outputSchema is None for t in tools.values()))
        self.assertIsNone(nav.structuredContent)  # replies are not duplicated as JSON
        self.assertIn("Page Two", nav.content[0].text)
        self.assertEqual(shot.content[0].type, "image")
        self.assertEqual(shot.content[0].mimeType, "image/jpeg")


class LinkedInTests(BrowserTestCase):
    def setUp(self):
        fake_site.reset_state()

    def sign_in(self):
        async def add_cookie():
            sess = await server._browser.session("default")
            await sess.context.add_cookies([{"name": "li_at", "value": "1", "url": self.site.base}])
        self.run_async(add_cookie())

    def sign_out(self):
        async def clear():
            sess = await server._browser.session("default")
            await sess.context.clear_cookies()
        self.run_async(clear())

    def test_requires_sign_in(self):
        self.sign_out()
        self.assertIn("Not signed in", self.run_async(server.linkedin_check_login()))
        self.assertIn("Not signed in", self.run_async(server.linkedin_get_my_profile()))
        self.assertIn("Not signed in", self.run_async(server.linkedin_update_headline("x", confirm=True)))

    def test_read_profile(self):
        self.sign_in()
        self.assertIn("/in/jane-doe/", self.run_async(server.linkedin_check_login()))
        out = self.run_async(server.linkedin_get_my_profile(sections="about,experience,skills"))
        self.assertIn("Jane Doe", out)
        self.assertIn("Headline: DevOps Engineer", out)
        self.assertIn("I build CI/CD pipelines.", out)
        self.assertIn("Acme Corp", out)
        self.assertEqual(out.count("Docker"), 1)  # screen-reader duplicate removed

    def test_update_headline_preview_then_confirm(self):
        self.sign_in()
        preview = self.run_async(server.linkedin_update_headline("Senior DevOps | Kubernetes | MCP"))
        self.assertIn("Confirmation required", preview)
        self.assertIn("Current (15 chars): DevOps Engineer", preview)
        self.assertEqual(fake_site.STATE["headline"], "DevOps Engineer")
        self.assertEqual(fake_site.STATE["saves"], [])

        done = self.run_async(server.linkedin_update_headline("Senior DevOps | Kubernetes | MCP", confirm=True))
        self.assertIn("verified on your profile", done)
        self.assertEqual(fake_site.STATE["headline"], "Senior DevOps | Kubernetes | MCP")

    def test_update_headline_validation(self):
        self.assertIn("at most 220", self.run_async(server.linkedin_update_headline("x" * 221)))
        self.assertIn("must not be empty", self.run_async(server.linkedin_update_headline("   ")))

    def test_update_about(self):
        self.sign_in()
        text = "I design release pipelines & observability.\n\nOpen to platform roles."
        self.assertIn("Confirmation required", self.run_async(server.linkedin_update_about(text)))
        done = self.run_async(server.linkedin_update_about(text, confirm=True))
        self.assertIn("✅ About section saved", done)
        self.assertEqual(fake_site.STATE["about"], text)

    def test_add_skills_skips_existing(self):
        self.sign_in()
        preview = self.run_async(server.linkedin_add_skills("Docker, Terraform"))
        self.assertIn("Will add: Terraform", preview)
        self.assertIn("Already present (skipped): Docker", preview)
        done = self.run_async(server.linkedin_add_skills("Docker, Terraform", confirm=True))
        self.assertIn("Added 1 skill(s): Terraform", done)
        self.assertEqual(fake_site.STATE["skills"], ["Docker", "Jenkins", "Terraform"])

    def test_section_editor_lists_fields(self):
        self.sign_in()
        out = self.run_async(server.linkedin_open_section_editor("about"))
        self.assertIn("editor is open", out)
        self.assertIn("'About'", out)
        self.assertIn("'Save'", out)
        self.run_async(server.browser_press_key("Escape"))

    def test_daily_limit_blocks_writes(self):
        self.sign_in()
        old = server._settings.linkedin_max_writes_per_day
        server._settings.linkedin_max_writes_per_day = server._limiter.used_today()
        try:
            out = self.run_async(server.linkedin_update_headline("New headline", confirm=True))
        finally:
            server._settings.linkedin_max_writes_per_day = old
        self.assertIn("Daily LinkedIn edit limit", out)
        self.assertEqual(fake_site.STATE["headline"], "DevOps Engineer")


if __name__ == "__main__":
    unittest.main()
