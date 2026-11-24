"""
Configuration and local state for Web Browser MCP Server.

Author: Pawan Gunjkar (pawangunjkar@gmail.com)

Everything is driven by environment variables so the same code runs locally
(visible Chrome window, your own logins) and in a container (headless).

State lives in one cache directory (default ~/.web-browser-mcp):
  profiles/<session_name>/   persistent browser profile per session (cookies, logins)
  linkedin_writes.json       daily counter for LinkedIn write actions (rate limiting)

No passwords are ever stored or handled by this server: you sign in yourself
in the browser window, and the browser profile keeps you signed in.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(value, high))


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def default_headless() -> bool:
    """Visible window on desktops (needed to sign in); headless on servers without a display."""
    if os.environ.get("BROWSER_HEADLESS", "").strip():
        return _env_bool("BROWSER_HEADLESS", False)
    if os.environ.get("K_SERVICE") or os.environ.get("MCP_TRANSPORT", "").lower() in ("http", "streamable-http", "sse"):
        return True
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return True
    return False


@dataclass
class BrowserSettings:
    """Runtime settings read from the environment."""

    cache_dir: Path
    headless: bool
    channel: str
    timeout_ms: int
    viewport_width: int
    viewport_height: int
    confirm_submits: bool
    allowed_domains: list[str] = field(default_factory=list)
    blocked_domains: list[str] = field(default_factory=list)
    allow_file_urls: bool = False
    linkedin_enabled: bool = True
    linkedin_max_writes_per_day: int = 20
    linkedin_min_seconds_between_writes: int = 10

    @classmethod
    def from_env(cls) -> "BrowserSettings":
        cache = os.environ.get("WEB_BROWSER_MCP_CACHE_DIR")
        cache_dir = Path(cache).expanduser() if cache else Path.home() / ".web-browser-mcp"
        return cls(
            cache_dir=cache_dir,
            headless=default_headless(),
            channel=os.environ.get("BROWSER_CHANNEL", "chrome").strip().lower() or "chrome",
            timeout_ms=_env_int("BROWSER_TIMEOUT_MS", 30_000, 1_000, 180_000),
            viewport_width=_env_int("BROWSER_VIEWPORT_WIDTH", 1366, 320, 3840),
            viewport_height=_env_int("BROWSER_VIEWPORT_HEIGHT", 900, 320, 2160),
            confirm_submits=_env_bool("BROWSER_CONFIRM_SUBMITS", True),
            allowed_domains=_env_list("BROWSER_ALLOWED_DOMAINS"),
            blocked_domains=_env_list("BROWSER_BLOCKED_DOMAINS"),
            allow_file_urls=_env_bool("BROWSER_ALLOW_FILE_URLS", False),
            linkedin_enabled=not _env_bool("WEB_BROWSER_MCP_DISABLE_LINKEDIN", False),
            linkedin_max_writes_per_day=_env_int("LINKEDIN_MAX_WRITES_PER_DAY", 20, 1, 500),
            linkedin_min_seconds_between_writes=_env_int("LINKEDIN_MIN_SECONDS_BETWEEN_WRITES", 10, 0, 3600),
        )

    def profile_dir(self, session_name: str) -> Path:
        """Persistent browser profile for a session. Names are sanitised into a safe folder name."""
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_name.strip() or "default")[:64]
        return self.cache_dir / "profiles" / safe


# ============================================================
# URL policy
# ============================================================

_SAFE_SCHEMES = ("http", "https")
_INTERNAL_PREFIXES = ("about:blank",)


def normalize_url(url: str) -> str:
    """Add https:// to bare hosts like 'example.com/page'."""
    value = (url or "").strip()
    if not value:
        raise ValueError("URL is empty.")
    if value in _INTERNAL_PREFIXES:
        return value
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value):
        return value
    return "https://" + value


def _host_matches(host: str, rule: str) -> bool:
    rule = rule.lstrip(".").lstrip("*.")
    return host == rule or host.endswith("." + rule)


def check_url_allowed(url: str, settings: BrowserSettings) -> Optional[str]:
    """Return an error message if the URL may not be opened, or None if it is allowed."""
    if url in _INTERNAL_PREFIXES:
        return None
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme == "file":
        if settings.allow_file_urls:
            return None
        return "❌ file:// URLs are disabled (set BROWSER_ALLOW_FILE_URLS=true to allow local files)."
    if scheme not in _SAFE_SCHEMES:
        return f"❌ Only http(s) URLs can be opened (got '{scheme}:')."
    host = (parts.hostname or "").lower()
    if not host:
        return f"❌ URL has no host: {url}"
    for rule in settings.blocked_domains:
        if _host_matches(host, rule):
            return f"❌ {host} is blocked by BROWSER_BLOCKED_DOMAINS."
    if settings.allowed_domains and not any(_host_matches(host, rule) for rule in settings.allowed_domains):
        return f"❌ {host} is not in BROWSER_ALLOWED_DOMAINS ({', '.join(settings.allowed_domains)})."
    return None


# ============================================================
# LinkedIn write rate limiter
# ============================================================

class WriteLimiter:
    """
    Keeps LinkedIn edits at a human pace: a minimum gap between writes and a daily cap.

    The daily count is stored on disk so restarting the server does not reset it.
    """

    def __init__(self, settings: BrowserSettings, filename: str = "linkedin_writes.json"):
        self.settings = settings
        self.path = settings.cache_dir / filename
        self._last_write = 0.0

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data), encoding="utf-8")
        except OSError as exc:
            print(f"Warning: could not save LinkedIn write counter: {exc}", file=sys.stderr)

    def used_today(self) -> int:
        data = self._load()
        return int(data.get(date.today().isoformat(), 0))

    def check(self) -> Optional[str]:
        """Return an error message if another write is not allowed right now."""
        if self.used_today() >= self.settings.linkedin_max_writes_per_day:
            return (
                f"❌ Daily LinkedIn edit limit reached ({self.settings.linkedin_max_writes_per_day}). "
                "This protects your account from looking automated. Try again tomorrow or raise "
                "LINKEDIN_MAX_WRITES_PER_DAY."
            )
        wait = self.settings.linkedin_min_seconds_between_writes - (time.monotonic() - self._last_write)
        if self._last_write and wait > 0:
            return f"⏳ Please wait {wait:.0f}s before the next LinkedIn edit (human-pace limit)."
        return None

    def record(self) -> None:
        self._last_write = time.monotonic()
        data = self._load()
        today = date.today().isoformat()
        data = {today: int(data.get(today, 0)) + 1}  # keep only today's counter
        self._save(data)
