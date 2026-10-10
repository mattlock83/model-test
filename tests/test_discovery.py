import copy
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest
from crawlee import ConcurrencySettings, Request
from crawlee.crawlers import BasicCrawler, BasicCrawlingContext
from crawlee.crawlers._basic._context_pipeline import ContextPipeline

from testwalker import cli
from testwalker.config import RuntimeConfig
from testwalker.discovery import DiscoveryOptions, discover_sitemap, discover_url, discover_urls, explorer
from testwalker.discovery.browser import default_value, form_values
from testwalker.discovery.scope import Scope, canonical_url, sitemap_urls
from testwalker.model import validate_model

BASE = "https://site.test"


def control(**kwargs):
    return dict(
        type="text",
        name="email",
        label="Email",
        value="",
        visible=True,
        disabled=False,
        readonly=False,
        required=True,
        checked=False,
        options=[],
        min=None,
        max=None,
        step=None,
        minLength=None,
        maxLength=None,
        **kwargs,
    )


def snapshot(path, links=(), forms=(), text=None):
    return {
        "url": BASE + path,
        "title": path,
        "headings": [path],
        "text": text or path,
        "links": [{"url": BASE + target, "label": target, "download": False} for target in links],
        "forms": list(forms),
        "buttons": [],
        "frames": [],
    }


@pytest.fixture
def fake_browser(monkeypatch):
    pages = {"/": snapshot("/", ["/a"]), "/a": snapshot("/a", ["/", "/b"]), "/b": snapshot("/b")}
    calls = []

    @dataclass(frozen=True)
    class Context(BasicCrawlingContext):
        page: object
        extract_links: object

    from types import SimpleNamespace

    session = SimpleNamespace(
        send=AsyncMock(return_value={"documents": [], "strings": []}), detach=AsyncMock()
    )
    shared_context = SimpleNamespace(new_cdp_session=AsyncMock(return_value=session))

    # Inspector uses context identity for one-time route attachment.
    class BrowserContext:
        new_cdp_session = shared_context.new_cdp_session

    shared_context = BrowserContext()

    class Page:
        context = shared_context

        def __init__(self):
            self.close = AsyncMock()

        def on(self, *_):
            pass

        def remove_listener(self, *_):
            pass

        async def content(self):
            return "<html><body>Captured</body></html>"

        async def screenshot(self, *, path, **kwargs):
            from pathlib import Path

            Path(path).write_bytes(b"fake screenshot")

    class SimulatedCrawler(BasicCrawler):
        def __init__(self, options, **kwargs):
            super().__init__(
                max_request_retries=0,
                use_session_pool=False,
                configure_logging=False,
                max_crawl_depth=options.depth,
                concurrency_settings=ConcurrencySettings(max_concurrency=1, desired_concurrency=1),
                _context_pipeline=ContextPipeline().compose(self.open_page),
                **kwargs,
            )

        def pre_navigation_hook(self, function):
            self.before = function
            return function

        async def open_page(self, context):
            page = Page()

            async def extract_links(**_):
                return [Request.from_url(link["url"]) for link in page.snapshot["links"]]

            upgraded = Context(**vars(context), page=page, extract_links=extract_links)
            await self.before(upgraded)
            calls.append(context.request.url)
            page.snapshot = copy.deepcopy(pages[context.request.url.removeprefix(BASE)])
            yield upgraded

    class Inspector:
        def __init__(self, scope, *_):
            self.scope = scope

        async def attach(self, context):
            pass

        async def dismiss_dialog(self, dialog):
            pass

        async def before_visit(self, page, url):
            assert self.scope.allows(url)

        async def after_visit(self, page, url):
            pass

        async def snapshot(self, page):
            return page.snapshot

        async def submit(self, page, form, values):
            calls.append((form["label"], values))
            page.snapshot = snapshot("/", text="Thank you")
            return values

    monkeypatch.setattr(explorer, "Inspector", Inspector)
    monkeypatch.setattr(
        explorer, "create_crawler", lambda options, config, **kw: SimulatedCrawler(options, **kw)
    )
    return pages, calls


def test_sitemap_exact_scope_and_no_submission(tmp_path, fake_browser):
    pages, calls = fake_browser
    pages["/"]["forms"] = [{"label": "Enquiry", "action": BASE + "/outside", "fields": []}]
    path = tmp_path / "sitemap.xml"
    path.write_text(
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{BASE}/</loc></url><url><loc>{BASE}/a</loc></url></urlset>'
    )
    result = discover_sitemap(path, options=DiscoveryOptions(depth=8))
    assert calls == [BASE + "/", BASE + "/a"]
    assert len(result.model["models"][0]["edges"]) == 2
    assert result.inventory["depth"] == 0
    validate_model(result.model)


def test_depth_and_single_merged_graph(fake_browser):
    _, calls = fake_browser
    result = discover_url(BASE, options=DiscoveryOptions(depth=1))
    assert calls == [BASE + "/", BASE + "/a"]
    assert len(result.model["models"]) == 1
    calls.clear()
    result = discover_url(BASE, options=DiscoveryOptions(depth=2))
    assert calls == [BASE + "/", BASE + "/a", BASE + "/b"]
    assert len(result.inventory["pages"]) == 3


def test_recursive_explicit_allowlist(fake_browser):
    _, calls = fake_browser
    discover_urls([BASE], allowed_urls=[BASE, BASE + "/a"], options=DiscoveryOptions(depth=5))
    assert calls == [BASE + "/", BASE + "/a"]


def test_same_url_form_outcome_and_overrides(fake_browser):
    pages, calls = fake_browser
    pages["/"]["forms"] = [{"label": "Enquiry", "action": BASE, "fields": [control()]}]
    result = discover_url(BASE, values={"pages": {BASE: {"Email": "person@example.com"}}})
    assert ("Enquiry", {"email": "person@example.com"}) in calls
    assert len(result.inventory["pages"]) == 3
    transitions = [t for t in result.inventory["transitions"] if t["evidence"] == "form submission"]
    assert transitions[0]["source"] != transitions[0]["target"]
    assert result.model["models"][0]["properties"]["business"]["data sets"] == {}
    validate_model(result.model)


def test_budget_reports_pending_and_no_fabricated_edges(fake_browser):
    result = discover_url(BASE, options=DiscoveryOptions(max_actions=1))
    assert result.inventory["pending"]
    assert not result.model["models"][0]["edges"]
    assert any("supply 1–500 journeys" in note for note in result.inventory["review"])


def test_output_pair_and_no_overwrite(tmp_path, fake_browser):
    output = tmp_path / "draft.json"
    result = discover_url(BASE, output=output)
    assert output.is_file() and output.with_suffix(".discovery.json").is_file()
    with pytest.raises(ValueError, match="exists"):
        result.write(output)


@pytest.mark.parametrize(
    "url",
    [
        "https://elsewhere.test",
        BASE + "/a?x=1",
        "javascript:alert(1)",
        "file:///tmp/file",
        "https://user:pass@site.test/a",
    ],
)
def test_strict_scope(url):
    assert not Scope([BASE], [BASE, BASE + "/a"]).allows(url)


def test_normalization_preserves_query_and_path():
    assert canonical_url("https://SITE.test:443/a?x=1#part") == BASE + "/a?x=1"
    assert Scope([BASE], [BASE + "/a"]).allows(BASE + "/a#part")
    assert not Scope([BASE]).allows("http://site.test/a")
    assert not Scope([BASE], [BASE + "/a"]).allows(BASE + "/a/")


@pytest.mark.parametrize(
    "xml", ["<sitemapindex/>", "<urlset/>", "<!DOCTYPE urlset><urlset/>", "<urlset><url/></urlset>"]
)
def test_invalid_sitemap(xml, tmp_path):
    path = tmp_path / "sitemap.xml"
    path.write_text(xml)
    with pytest.raises(ValueError):
        sitemap_urls(path)


def test_discovery_browser_config_needs_no_native_binary_or_key(tmp_path):
    path = tmp_path / "browser.properties"
    path.write_text("GRAPHWALKER_BIN=/missing/on/purpose\nCHROME_DEBUG_PORT=9333\n")
    config = RuntimeConfig.load(path, discovery=True)
    assert config.graphwalker is None and config.api_key == "" and config.chrome_port == 9333


def test_cli_discovery_without_config_or_ai(tmp_path, fake_browser, capsys):
    assert cli.main(["discover-url", "--url", BASE, "--output", str(tmp_path / "draft.json")]) == 0
    assert "Draft:" in capsys.readouterr().out


def test_form_defaults_and_page_overrides():
    email = control()
    email["type"] = "email"
    assert default_value(email) == "tester@example.com"
    number = dict(email, type="number", min="2", max="5", step="2")
    assert default_value(number) == "2"
    checkbox = dict(email, type="checkbox")
    assert default_value(checkbox) is True
    choice = dict(
        email,
        type="select-one",
        options=[{"value": "", "disabled": False}, {"value": "good", "disabled": False}],
    )
    assert default_value(choice) == "good"
    assert form_values(
        {"fields": [email]},
        {"defaults": {"Email": "global@example.com"}, "pages": {BASE + "/": {"Email": "page@example.com"}}},
        BASE,
    ) == {"email": "page@example.com"}


def test_invalid_options_values_and_multiple_origins_fail_before_browser(fake_browser):
    for values in ({"wat": {}}, {"defaults": []}, {"defaults": {"field": {"code": "no"}}}):
        with pytest.raises(ValueError):
            discover_url(BASE, values=values)
    with pytest.raises(ValueError, match="one origin"):
        discover_urls([BASE, "https://other.test"])
    with pytest.raises(ValueError, match="depth"):
        DiscoveryOptions(depth=-1)


def test_existing_output_fails_before_visiting_or_submitting(tmp_path, fake_browser):
    _, calls = fake_browser
    output = tmp_path / "reviewed.json"
    output.write_text("reviewed work")
    with pytest.raises(ValueError, match="exists"):
        discover_url(BASE, output=output)
    assert calls == []
    assert output.read_text() == "reviewed work"


def test_page_name_override_wins_over_global_label():
    supplied = form_values(
        {"fields": [control()]},
        {"defaults": {"Email": "global@example.com"}, "pages": {BASE + "/": {"email": "local@example.com"}}},
        BASE,
    )
    assert supplied == {"email": "local@example.com"}


def test_crawlee_deduplicates_links_and_preserves_pending_at_page_limit(fake_browser):
    pages, calls = fake_browser
    pages["/"]["links"] *= 3
    result = discover_url(BASE)
    assert calls.count(BASE + "/a") == 1
    assert result.inventory["engine"] == "crawlee"
    calls.clear()
    result = discover_url(BASE, options=DiscoveryOptions(max_pages=1))
    assert calls == [BASE + "/"]
    assert result.inventory["pending"][0]["url"] == BASE + "/a"


def test_async_discovery_and_sync_entry_point_inside_event_loop(fake_browser):
    import asyncio

    from testwalker.discovery import discover_urls_async

    async def run():
        first = await discover_urls_async([BASE])
        second = await discover_urls_async([BASE])
        assert first.model == second.model
        with pytest.raises(ValueError, match="await discover_urls_async"):
            discover_url(BASE)

    asyncio.run(run())


def test_evidence_can_be_written_later_and_all_captures_can_be_disabled(tmp_path, fake_browser):
    result = discover_url(
        BASE, options=DiscoveryOptions(depth=0, screenshots=False, dom=False, network=False)
    )
    assert result.inventory["visits"][0]["artifacts"] == {}
    assert result.inventory["visits"][0]["capture_errors"] == []
    result.write(tmp_path / "first.json")
    result.write(tmp_path / "second.json")
    import json

    for name in ("first", "second"):
        saved = json.loads((tmp_path / f"{name}.discovery.json").read_text())
        assert saved["artifact_root"] == f"{name}.discovery"
        assert (tmp_path / saved["artifact_root"]).is_dir()
    result.close()
