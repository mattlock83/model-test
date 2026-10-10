"""Crawlee owns browser/page lifetime; the CDP adapter preserves external Chrome."""

from datetime import timedelta

from crawlee import ConcurrencySettings
from crawlee.browsers import BrowserPool, PlaywrightBrowserController, PlaywrightBrowserPlugin
from crawlee.crawlers import PlaywrightCrawler

from ..browser_runtime import chrome_executable


class ConnectedController(PlaywrightBrowserController):
    async def close(self, *, force=False):
        # Close only the context created by Crawlee. Plugin teardown disconnects
        # Playwright; the externally managed browser and its existing tabs survive.
        if self._browser_context:
            await self._browser_context.close()


class ConnectedPlugin(PlaywrightBrowserPlugin):
    def __init__(self, endpoint, **kwargs):
        super().__init__(**kwargs)
        self.endpoint = endpoint

    async def new_browser(self):
        browser = await self._playwright.chromium.connect_over_cdp(self.endpoint)
        return ConnectedController(browser, max_open_pages_per_browser=1, header_generator=None)


def create_crawler(options, config, **kwargs):
    plugin_options = {
        "max_open_pages_per_browser": 1,
        "browser_new_context_options": {"service_workers": "block", "accept_downloads": False},
    }
    plugin = (
        ConnectedPlugin(config.cdp_url, **plugin_options)
        if config.cdp_url
        else PlaywrightBrowserPlugin(
            browser_launch_options={
                "executable_path": str(chrome_executable(config)),
                "headless": not options.headed,
            },
            **plugin_options,
        )
    )
    pool = BrowserPool(
        plugins=[plugin],
        retire_browser_after_page_count=options.max_actions + 1,
        browser_inactive_threshold=timedelta(hours=1),
    )
    return PlaywrightCrawler(
        browser_pool=pool,
        concurrency_settings=ConcurrencySettings(min_concurrency=1, max_concurrency=1, desired_concurrency=1),
        max_crawl_depth=options.depth,
        max_requests_per_crawl=options.max_actions,
        # A failed request may already have submitted a form. Never retry it.
        max_request_retries=0,
        max_session_rotations=0,
        retry_on_blocked=False,
        use_session_pool=False,
        respect_robots_txt_file=False,
        navigation_timeout=timedelta(milliseconds=options.timeout_ms),
        request_handler_timeout=timedelta(
            milliseconds=(options.timeout_ms + 3 * options.settle_ms) * (options.depth + 2)
        ),
        goto_options={"wait_until": "domcontentloaded"},
        configure_logging=False,
        **kwargs,
    )
