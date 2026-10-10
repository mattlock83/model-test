import asyncio
import json
import os
import re
from pathlib import Path

import pytest

from testwalker.report import write_viewer


def sample_report():
    return {
        "model": "Boundary report",
        "status": "INCONCLUSIVE",
        "tests": [
            {
                "id": "graph/1",
                "name": "Uncertain rule",
                "kind": "graph",
                "status": "INCONCLUSIVE",
                "reason": "Human verification needed",
                "decision_range": [127, 130],
            }
        ],
        "decisions": [{"request": f"request {i}", "response": "uncertain"} for i in range(260)],
    }


def test_viewer_externalizes_lossless_safely_escaped_decisions(tmp_path):
    report = sample_report()
    attack = "</script><script>window.untrustedExecuted=true</script>\u2028 &"
    report["decisions"][128]["response"] = attack
    write_viewer(tmp_path, report)
    html = (tmp_path / "report.html").read_text()
    embedded = re.search(r'<script id="run-data" type="application/json">(.*?)</script>', html, re.S)[1]
    viewer = json.loads(embedded)
    assert "decisions" not in viewer
    assert viewer["tests"] == report["tests"]
    restored = []
    for index, filename in enumerate(viewer["decision_chunks"]["files"]):
        script = (tmp_path / filename).read_text()
        prefix = f"window.testwalkerDecisionChunks[{index}] = "
        assert script.startswith(prefix) and "</script>" not in script
        restored.extend(json.loads(script.removeprefix(prefix).removesuffix(";\n")))
    assert restored == report["decisions"]
    assert attack not in html


@pytest.mark.skipif(not os.getenv("TESTWALKER_BROWSER_TESTS"), reason="Opt-in real Chrome report test")
def test_local_viewer_loads_only_expanded_decision_ranges_and_handles_missing_evidence(tmp_path):
    from playwright.async_api import async_playwright, expect

    from testwalker.browser_runtime import chrome_executable
    from testwalker.config import RuntimeConfig

    report = sample_report()
    report["decisions"][128]["response"] = "</script><script>window.untrustedExecuted=true</script>"
    write_viewer(tmp_path, report)

    async def check():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                executable_path=str(chrome_executable(RuntimeConfig(Path("."), None, "")))
            )
            try:
                page = await browser.new_page()
                errors, requests = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: requests.append(request.url))
                await page.goto((tmp_path / "report.html").as_uri())
                await expect(page.locator("#detail")).to_contain_text("Human verification needed")
                assert not any("decisions-" in url for url in requests)
                toggle = page.get_by_text("Actions & Jev decisions", exact=True)
                await toggle.click()
                content = toggle.locator("..").locator("pre")
                await expect(content).to_contain_text("request 129")
                data = json.loads(await content.text_content())
                assert data["decisions"] == report["decisions"][127:130]
                assert sum("decisions-" in url for url in requests) == 2
                assert not await page.evaluate("Boolean(window.untrustedExecuted)")
                assert errors == []
                (tmp_path / "evidence/decisions-00001.js").unlink()
                await page.reload()
                await toggle.click()
                await expect(content).to_contain_text("Decision evidence could not be loaded")
                write_viewer(tmp_path, report)
                await toggle.click()
                await toggle.click()
                await expect(content).to_contain_text("request 129")
            finally:
                await browser.close()

    asyncio.run(check())
