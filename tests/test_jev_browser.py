"""Real rendered navigation evidence without calls to a remote decision service."""

import asyncio
import os
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from testwalker.browser_runtime import chrome_executable
from testwalker.config import RuntimeConfig
from testwalker.jev import SEMANTICS


@pytest.mark.skipif(not os.getenv("TESTWALKER_BROWSER_TESTS"), reason="Opt-in real Chrome evidence test")
def test_navigation_evidence_distinguishes_offscreen_controls_and_hidden_controls():
    async def run():
        config = RuntimeConfig(Path("."), None, "")
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(executable_path=str(chrome_executable(config)))
            try:
                page = await browser.new_page(viewport={"width": 800, "height": 300})
                await page.set_content("""<a href="#home">Home</a><a href="#hidden" hidden>Hidden</a>
                    <div style="height:1000px"></div><a href="#revise">Clear errors and revise</a>""")
                before = await page.evaluate(SEMANTICS)
                links = {link["label"]: link for link in before["available_links"]}
                assert "Hidden" not in links
                assert links["Home"]["in_viewport"]
                assert links["Clear errors and revise"]["scroll_direction"] == "down"
                await page.get_by_role("link", name="Clear errors and revise").scroll_into_view_if_needed()
                after = await page.evaluate(SEMANTICS)
                links = {link["label"]: link for link in after["available_links"]}
                assert links["Clear errors and revise"]["in_viewport"]
                assert links["Clear errors and revise"]["scroll_direction"] is None
                await page.get_by_role("link", name="Clear errors and revise").click()
                assert page.url.endswith("#revise")
            finally:
                await browser.close()

    asyncio.run(run())
