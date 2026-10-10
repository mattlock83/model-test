"""Opt-in real Chrome tests: TESTWALKER_BROWSER_TESTS=1 pytest -q tests/test_discovery_browser.py."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from testwalker.discovery import DiscoveryOptions, discover_sitemap, discover_url
from testwalker.model import validate_model

pytestmark = pytest.mark.skipif(
    not os.getenv("TESTWALKER_BROWSER_TESTS"), reason="Opt-in real Chrome discovery test"
)


@pytest.fixture
def website():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            calls.append(("GET", self.path))
            path = urlsplit(self.path).path
            if path in {"/redirect-out", "/redirect-in"}:
                self.send_response(302)
                self.send_header("Location", "/outside" if path.endswith("out") else "/a")
                self.end_headers()
                return
            if path.startswith("/api/"):
                self.send_response(422 if path.endswith("error") else 200)
                self.send_header("Content-Type", "application/problem+json")
                self.end_headers()
                self.wfile.write(
                    b'{"message":"synthetic error"}' if path.endswith("error") else b'{"items":[1,2]}'
                )
                return
            pages = {
                "/network": """<h1>Network demo</h1><label>Name<input name="name" value="initial"></label>
                <script>document.querySelector('input').value='live value';fetch('/api/items');
                fetch('/api/error');</script>
                <form aria-label="Send JSON" onsubmit="event.preventDefault();
                fetch('/api/save', {method:'POST',headers:{'Content-Type':'application/json'},
                body:JSON.stringify({name:document.querySelector('input').value})})
                .then(r=>r.json()).then(r=>document.querySelector('#result').textContent=r.message)">
                <button>Save</button></form><p id="result"></p>""",
                "/": """<h1>Home</h1><a href="/a">About</a><a href="/redirect-in">Redirect</a>
                <a href="http://localhost:PORT/outside">Outside</a>
                <form aria-label="Feedback" onsubmit="event.preventDefault();
                  document.getElementById('result').innerText='Thanks '+this.email.value">
                <label>Email<input name="email" type="email" required></label><button>Send</button></form>
                <p id="result"></p>
                <form aria-label="Order" action="/order" method="post">
                <label>Count<input type="number" name="count" min="2" max="5" required></label>
                <button>Order</button></form>
                <form action="http://localhost:PORT/outside" method="post">
                <button>External</button></form>""",
                "/custom": """<h1>Delivery</h1><form aria-label="Delivery" onsubmit="event.preventDefault();
                document.getElementById('result').innerText='Selected '+this.delivery.value">
                <input type="hidden" name="delivery" value="">
                <button type="button" role="combobox" aria-label="Delivery method"
                  onclick="document.getElementById('options').hidden=false">Choose</button>
                <div id="options" hidden><button type="button" role="option"
                  onclick="document.querySelector('[name=delivery]').value='Express';
                  document.getElementById('options').hidden=true">Express</button></div>
                <button>Confirm</button></form><p id="result"></p>""",
                "/a": '<h1>About</h1><a href="/">Home</a><a href="/deep">Deep</a>',
                "/deep": '<h1>Deep</h1><a href="/">Home</a>',
                "/thanks": '<h1>Order received</h1><a href="/">Home</a>',
                "/outside": "<h1>Must never be requested</h1>",
            }
            html = pages.get(path, "<h1>Missing</h1>").replace("PORT", str(self.server.server_port))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode())

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
            calls.append(("POST", self.path, body))
            if self.path == "/api/save":
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"message":"Saved successfully"}')
                return
            self.send_response(303)
            self.send_header("Location", "/thanks")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_real_sitemap_never_leaves_allowlist_or_submits(website, tmp_path):
    base, calls = website
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text(
        "<urlset>"
        + "".join(f"<url><loc>{base}{p}</loc></url>" for p in ("/", "/a", "/redirect-out"))
        + "</urlset>"
    )
    result = discover_sitemap(sitemap, options=DiscoveryOptions(settle_ms=50, timeout_ms=3000))
    assert all(c[0] == "GET" and c[1] in {"/", "/a", "/redirect-out"} for c in calls), calls
    assert result.inventory["blocked"]
    assert result.inventory["failed"]
    assert len(result.inventory["pages"]) == 2
    validate_model(result.model)


def test_real_url_forms_redirects_hooks_and_depth(website):
    base, calls = website
    events = []

    def before_submit(ctx):
        events.append(ctx.form["label"])
        if ctx.form["label"] == "Feedback":
            ctx.values["email"] = "hook@example.com"

    result = discover_url(
        base,
        options=DiscoveryOptions(settle_ms=50, timeout_ms=5000),
        hooks=SimpleNamespace(before_submit=before_submit),
    )
    assert not result.inventory["failed"], result.inventory["failed"]
    assert not any("/outside" in c[1] or "/deep" in c[1] for c in calls), calls
    assert ("POST", "/order", "count=2") in calls
    assert set(events) == {"Feedback", "Order"}
    pages = result.inventory["pages"].values()
    assert any("Thanks hook@example.com" in p["text"] for p in pages)
    assert any("Order received" in p["text"] for p in pages)
    submitted = [t for t in result.inventory["transitions"] if t["evidence"] == "form submission"]
    assert any(t["values"].get("email") == "hook@example.com" for t in submitted)
    assert any("hook@example.com" in t["intent"] for t in submitted)
    assert any(
        "Thanks hook@example.com" in str(v["properties"]) for v in result.model["models"][0]["vertices"]
    )
    validate_model(result.model)


def test_custom_select_hook_keeps_selectors_out_of_model(website):
    base, _ = website

    async def before_submit(ctx):
        await ctx.page.get_by_role("combobox", name="Delivery method").click()
        await ctx.page.get_by_role("option", name="Express").click()
        ctx.values["Delivery method"] = "Express"

    result = discover_url(
        base + "/custom",
        options=DiscoveryOptions(settle_ms=30),
        hooks=SimpleNamespace(before_submit=before_submit),
    )
    assert not result.inventory["failed"]
    assert any("Selected Express" in p["text"] for p in result.inventory["pages"].values())
    model = validate_model(result.model)
    assert any(
        "Delivery method: Express" in e["properties"]["business"]["intent"] for e in model.edges.values()
    )


def test_submission_failure_is_not_retried(website):
    base, calls = website

    async def after_submit(ctx):
        if ctx.form["label"] == "Order":
            raise ValueError("Failure after order reached the server")

    result = discover_url(
        base, options=DiscoveryOptions(settle_ms=30), hooks=SimpleNamespace(after_submit=after_submit)
    )
    failure = next(v for v in result.inventory["visits"] if "error" in v)
    assert failure["artifacts"]["network.json"]
    assert not failure["capture_errors"]
    assert result.inventory["failed"][0]["visit"] == failure["id"]
    assert sum(c[0] == "POST" and c[1] == "/order" for c in calls) == 1
    assert any("Failure after order reached the server" in f["error"] for f in result.inventory["failed"])


def test_action_budget_stops_before_extra_navigation_or_submission(website):
    base, calls = website
    result = discover_url(base, options=DiscoveryOptions(max_actions=1, settle_ms=30))
    assert calls == [("GET", "/")]
    assert result.inventory["operations"] == 1
    assert len(result.inventory["pending"]) == 4
    assert not result.inventory["failed"]


def test_cdp_discovery_preserves_external_browser_and_tabs(website, tmp_path):
    import json
    import socket
    from dataclasses import replace
    from urllib.request import urlopen

    from testwalker.browser_runtime import connect, ready
    from testwalker.config import RuntimeConfig

    base, _ = website
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = RuntimeConfig(tmp_path, None, "", chrome_port=port, chrome_profile=tmp_path / "chrome")
    with connect(config) as endpoint:
        with urlopen(endpoint + "/json/list") as response:
            original_tabs = {tab["id"] for tab in json.load(response) if tab["type"] == "page"}
        result = discover_url(
            base,
            config=replace(config, cdp_url=endpoint),
            options=DiscoveryOptions(depth=0, settle_ms=30),
        )
        assert result.inventory["pages"]
        assert ready(endpoint)
        with urlopen(endpoint + "/json/list") as response:
            targets = json.load(response)
            assert {tab["id"] for tab in targets if tab["type"] == "page"} == original_tabs, targets


def test_discovery_captures_dom_screenshots_and_json_per_visit(website, tmp_path):
    base, _ = website
    output = tmp_path / "draft.json"
    result = discover_url(base + "/network", output=output, options=DiscoveryOptions(settle_ms=100))
    inventory = json.loads(output.with_suffix(".discovery.json").read_text())
    root = tmp_path / inventory["artifact_root"]
    assert len(inventory["visits"]) == 2
    for visit in inventory["visits"]:
        assert not visit["capture_errors"]
        assert visit["id"] in inventory["pages"][visit["state"]]["visits"]
        artifacts = {k: root / v for k, v in visit["artifacts"].items()}
        assert artifacts["screenshot.png"].read_bytes().startswith(b"\x89PNG")
        assert "Network demo" in artifacts["page.html"].read_text()
        dom = json.loads(artifacts["dom.json"].read_text())
        assert "live value" in dom["strings"]
        values = dom["documents"][0]["nodes"]["inputValue"]["value"]
        assert "live value" in [dom["strings"][v] for v in values]
        network = json.loads(artifacts["network.json"].read_text())
        assert network["omitted_requests"] == 0
        requests = {r["url"].removeprefix(base): r for r in network["requests"]}
        assert requests["/api/items"]["response"]["body"]["value"] == {"items": [1, 2]}
        assert requests["/api/error"]["response"]["status"] == 422
        if "/api/save" in requests:
            saved = requests["/api/save"]
            assert saved["method"] == "POST"
            assert saved["request_body"]["value"] == {"name": "live value"}
            assert saved["response"]["body"]["value"] == {"message": "Saved successfully"}
            assert saved["status"] == "finished"
            assert visit["source"] and "Saved successfully" in inventory["pages"][visit["state"]]["text"]
    assert "/api/save" in requests
    # Evidence remains usable when the caller releases the temporary staging files.
    staged = result.evidence.path
    result.close()
    assert not staged.exists() and root.exists()


def test_discovery_evidence_opt_out_and_body_limit(website):
    base, _ = website
    result = discover_url(
        base + "/network",
        options=DiscoveryOptions(depth=0, screenshots=False, dom=False, max_json_bytes=4, settle_ms=100),
    )
    visit = result.inventory["visits"][0]
    assert set(visit["artifacts"]) == {"network.json"}
    network = json.loads((result.evidence.path / visit["artifacts"]["network.json"]).read_text())
    api = [r for r in network["requests"] if "/api/" in r["url"]]
    assert api and all(r["response"]["body"]["status"] == "too_large" for r in api)
    result.close()
