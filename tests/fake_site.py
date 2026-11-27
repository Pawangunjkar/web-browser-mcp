"""
A tiny local website used by the tests: ordinary pages, a fake search engine,
and a fake LinkedIn that mimics the profile page and its edit dialogs.

Author: Pawan Gunjkar (pawangunjkar@gmail.com)
"""

from __future__ import annotations

import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

STATE = {
    "headline": "DevOps Engineer",
    "about": "I build CI/CD pipelines.",
    "skills": ["Docker", "Jenkins"],
    "saves": [],
}


def reset_state() -> None:
    STATE.update(headline="DevOps Engineer", about="I build CI/CD pipelines.", skills=["Docker", "Jenkins"], saves=[])


PAGE1 = """<!doctype html><html><head><title>Test Page One</title></head><body>
<nav>Home | Products | Contact</nav>
<main><article>
<h1>Welcome to the test page</h1>
<p>This article explains Kubernetes rollouts in detail. """ + ("Lorem ipsum dolor sit amet. " * 30) + """</p>
<p>Second paragraph about canary deployments.</p>
</article>
<form onsubmit="event.preventDefault(); document.getElementById('status').innerText='Saved: ' + document.getElementById('name').value;">
  <label for="name">Full name</label><input id="name" name="name" type="text">
  <label for="pw">Password</label><input id="pw" name="password" type="password">
  <label for="color">Color</label>
  <select id="color"><option value="r">Red</option><option value="g">Green</option></select>
  <button type="button" id="more" onclick="document.getElementById('status').innerText='Expanded'">Show more</button>
  <button type="submit">Save changes</button>
</form>
<div id="status">Idle</div>
<a href="/page2">Go to page two</a>
<a href="/page2?x=1" target="_blank">Open in new tab</a>
</main></body></html>"""

PAGE2 = """<!doctype html><html><head><title>Page Two</title></head><body><main>
<h1>Page two</h1><p>Reached the second page.</p></main></body></html>"""

PAGE3 = """<!doctype html><html><head><title>Widgets</title></head><body><main>
<div id="status">Idle</div>
<button id="trash" onclick="document.getElementById('status').innerText = confirm('Really delete this item?') ? 'Deleted' : 'Kept'">Trash</button>
<div id="menu" onmouseenter="document.getElementById('status').innerText='Menu opened'">Account menu</div>
<fancy-widget></fancy-widget>
<script>
customElements.define('fancy-widget', class extends HTMLElement {
  connectedCallback() {
    const root = this.attachShadow({mode: 'open'});
    root.innerHTML = '<button onclick="document.getElementById(\\'status\\').innerText=\\'Shadow clicked\\'">Shadow button</button>';
  }
});
</script>
</main></body></html>"""


def search_page(base: str, query: str) -> str:
    rows = []
    for i, (title, path) in enumerate([("Kubernetes rollouts guide", "/page1"), ("Second result", "/page2")]):
        target = quote(base + path, safe="")
        rows.append(
            f'<div class="result results_links"><a class="result__a" href="//duckduckgo.com/l/?uddg={target}&rut=x">'
            f'{title}</a><a class="result__snippet">Snippet {i} for {html.escape(query)}</a></div>'
        )
    rows.append('<div class="result result--ad"><a class="result__a" href="https://ads.example/">Ad</a></div>')
    return f"<html><head><title>{html.escape(query)} at DuckDuckGo</title></head><body>{''.join(rows)}</body></html>"


def linkedin_profile(dialog: str = "") -> str:
    skills = "".join(f"<li><span aria-hidden='true'>{html.escape(s)}</span><span class='visually-hidden'>{html.escape(s)}</span></li>" for s in STATE["skills"])
    return f"""<!doctype html><html><head><title>Jane Doe | LinkedIn</title>
<style>.visually-hidden{{position:absolute;clip:rect(1px,1px,1px,1px);}}</style></head><body><main>
<section class="top-card"><h1>Jane Doe</h1><div class="text-body-medium">{html.escape(STATE['headline'])}</div>
<span class="text-body-small inline">Pune, India</span></section>
<section><div id="about"></div><h2>About</h2><div>{html.escape(STATE['about'])}</div></section>
<section><div id="experience"></div><h2>Experience</h2><div>Senior DevOps Engineer</div><div>Acme Corp</div></section>
<section><div id="skills"></div><h2>Skills</h2><ul>{skills}</ul></section>
</main>{dialog}
<script>
async function save(kind) {{
  const field = document.querySelector('[data-field]');
  let value = field.value;
  if (kind === 'headline' && !value.trim()) {{
    document.getElementById('err').innerText = 'Headline is required';
    return;
  }}
  await fetch('/li/api/save', {{method: 'POST', body: JSON.stringify({{kind, value}})}});
  document.querySelector('[role=dialog]').remove();
}}
function typeahead(input) {{
  const box = document.getElementById('suggestions');
  box.innerHTML = input.value ? '<li role="option" onclick="pick(this)">' + input.value + '</li>' : '';
}}
function pick(li) {{ document.querySelector('[data-field]').value = li.innerText; li.parentElement.innerHTML = ''; }}
</script></body></html>"""


def dialog(kind: str) -> str:
    if kind == "headline":
        field = (f'<label for="hl">Headline*</label><input id="hl" data-field value="{html.escape(STATE["headline"])}">')
    elif kind == "about":
        field = f'<label for="ab">About</label><textarea id="ab" data-field>{html.escape(STATE["about"])}</textarea>'
    else:
        field = ('<label for="sk">Skill*</label><input id="sk" role="combobox" data-field oninput="typeahead(this)">'
                 '<ul role="listbox" id="suggestions"></ul>')
    return (f'<div role="dialog" aria-label="Edit {kind}"><h2>Edit</h2>{field}<div id="err" role="alert"></div>'
            f'<button onclick="save(\'{kind}\')">Save</button></div>')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output quiet
        pass

    def _send(self, body: str, status: int = 200, headers: dict | None = None):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _logged_in(self) -> bool:
        return "li_at=1" in (self.headers.get("Cookie") or "")

    def do_GET(self):
        parts = urlsplit(self.path)
        path = parts.path
        base = f"http://{self.headers.get('Host')}"
        if path == "/page1":
            return self._send(PAGE1)
        if path == "/page2":
            return self._send(PAGE2)
        if path == "/page3":
            return self._send(PAGE3)
        if path == "/ddg/html/":
            q = parse_qs(parts.query).get("q", [""])[0]
            return self._send(search_page(base, q))
        if path == "/bing":
            item = (f'<li class="b_algo"><h2><a href="{base}/page1">Kubernetes rollouts guide</a></h2>'
                    '<div class="b_caption"><p>Bing snippet</p></div></li>')
            return self._send(f"<html><body><ol id='b_results'>{item}</ol></body></html>")
        if path == "/captcha":
            return self._send("<html><body>Our systems have detected unusual traffic. Are you a robot?</body></html>")
        if path.startswith("/li/"):
            return self._linkedin(path)
        return self._send("<html><body>not found</body></html>", 404)

    def _linkedin(self, path: str):
        if path == "/li/authwall":
            return self._send("<html><title>Sign Up | LinkedIn</title><body>Join LinkedIn</body></html>")
        if path == "/li/login":
            return self._send("<html><title>LinkedIn Login</title><body><form><input name=session_key>"
                              "<input type=password name=session_password><button>Sign in</button></form></body></html>")
        if not self._logged_in():
            return self._redirect("/li/authwall")
        if path == "/li/feed/":
            return self._send("<html><title>Feed | LinkedIn</title><body><main>Feed</main></body></html>")
        if path == "/li/in/me/":
            return self._redirect("/li/in/jane-doe/")
        if path == "/li/in/jane-doe/":
            return self._send(linkedin_profile())
        if path == "/li/in/jane-doe/add-edit/INTRO/":
            return self._send(linkedin_profile(dialog("headline")))
        if path == "/li/in/jane-doe/add-edit/SUMMARY/":
            return self._send(linkedin_profile(dialog("about")))
        if path == "/li/in/jane-doe/add-edit/SKILL_AND_ASSOCIATION/":
            return self._send(linkedin_profile(dialog("skill")))
        if path == "/li/in/jane-doe/details/skills/":
            items = "".join(f"<li>{html.escape(s)}</li>" for s in STATE["skills"])
            return self._send(f"<html><body><main><h2>Skills</h2><ul>{items}</ul></main></body></html>")
        return self._send("<html><body>not found</body></html>", 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/li/api/save" and self._logged_in():
            STATE["saves"].append(payload)
            if payload["kind"] == "headline":
                STATE["headline"] = payload["value"]
            elif payload["kind"] == "about":
                STATE["about"] = payload["value"]
            else:
                STATE["skills"].append(payload["value"])
            return self._send("{}")
        return self._send("{}", 403)


class FakeSite:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
