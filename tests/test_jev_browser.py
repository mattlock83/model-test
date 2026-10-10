"""Real rendered navigation evidence without calls to a remote decision service."""

import asyncio
import os
from pathlib import Path

import pytest
from playwright.async_api import async_playwright, expect

from testwalker.browser_runtime import chrome_executable
from testwalker.config import RuntimeConfig
from testwalker.jev import SEMANTICS
from testwalker.server import serve


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


@pytest.mark.skipif(not os.getenv("TESTWALKER_BROWSER_TESTS"), reason="Opt-in real Chrome evidence test")
def test_trailhead_length_errors_are_linked_to_fields_and_clear_after_valid_submission():
    async def run(url):
        config = RuntimeConfig(Path("."), None, "")
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(executable_path=str(chrome_executable(config)))
            try:
                page = await browser.new_page(viewport={"width": 1120, "height": 780})
                await page.goto(url + "/trailhead/#booking_details")
                name = page.get_by_label("Lead traveller", exact=True)
                await page.get_by_label("Email address", exact=True).fill("alex@example.test")
                for value, message in [("", "is required"), ("a", "is too short"), ("a" * 61, "is too long")]:
                    await name.fill(value)
                    await page.get_by_role("button", name="Review adventure", exact=True).click()
                    await expect(page.get_by_role("alert")).to_contain_text(message)
                    await name.scroll_into_view_if_needed()
                    semantics = await page.evaluate(SEMANTICS)
                    invalid = next(
                        field for field in semantics["invalid_fields"] if field["label"] == "Lead traveller"
                    )
                    assert message in invalid["validation_messages"][0]
                    hint = await name.get_attribute("aria-describedby")
                    assert len(hint.split()) == 1
                    assert await page.locator(f'[id="{hint}"]').count() == 1
                await name.fill("Alex Morgan")
                await page.get_by_role("button", name="Review adventure", exact=True).click()
                await expect(
                    page.get_by_role("heading", name="Review your adventure", exact=True)
                ).to_be_visible()
                semantics = await page.evaluate(SEMANTICS)
                assert semantics["alerts"] == []
                assert semantics["invalid_fields"] == []
            finally:
                await browser.close()

    with serve() as url:
        asyncio.run(run(url))
