import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from testwalker.discovery import DiscoveryOptions
from testwalker.discovery.crawler import output_paths
from testwalker.discovery.evidence import EvidenceStore, VisitEvidence, json_body


@pytest.mark.parametrize(
    "body,content_type,limit,expected",
    [
        (
            b'{"ok":true}',
            "application/json; charset=utf-8",
            100,
            {"status": "captured", "value": {"ok": True}},
        ),
        (b"null", "application/problem+json", 100, {"status": "captured", "value": None}),
        (b"no", "application/json", 100, {"status": "invalid_json"}),
        (b"12345", "application/json", 4, {"status": "too_large", "bytes": 5}),
        (None, "application/json", 100, {"status": "unavailable"}),
        (b"image", "image/png", 100, {"status": "not_json"}),
    ],
)
def test_json_body_capture_status(body, content_type, limit, expected):
    assert json_body(body, content_type, limit) == expected


def test_artifact_directory_prevents_overwrite_before_crawling(tmp_path):
    (tmp_path / "model.discovery").mkdir()
    with pytest.raises(ValueError, match="output exists"):
        output_paths(tmp_path / "model.json")


def test_network_limits_failed_requests_and_incomplete_bodies():
    class Request:
        url = "https://example.test/api"
        method = "POST"
        resource_type = "fetch"
        headers = {"content-type": "application/json"}
        post_data_buffer = b'{"test":true}'
        redirected_from = None
        failure = "net::ERR_FAILED"

    async def run():
        store = EvidenceStore()
        page = SimpleNamespace(on=lambda *_: None, remove_listener=lambda *_: None)
        record = {"id": "visit-0001", "artifacts": {}, "capture_errors": []}
        capture = VisitEvidence(
            page, store, DiscoveryOptions(dom=False, screenshots=False, max_network_requests=2), record
        )
        failed, streaming = Request(), Request()
        capture.on_request(failed)
        capture.on_failed(failed)
        capture.on_request(streaming)
        capture.on_request(Request())

        async def body():
            await asyncio.Event().wait()

        response = SimpleNamespace(
            request=streaming, status=200, headers={"content-type": "application/json"}, body=body
        )
        capture.on_response(response)
        await capture.finish()
        network = json.loads((store.path / record["artifacts"]["network.json"]).read_text())
        assert network["omitted_requests"] == 1
        assert network["requests"][0]["status"] == "failed"
        assert network["requests"][0]["failure"] == "net::ERR_FAILED"
        assert network["requests"][1]["status"] == "incomplete"
        assert network["requests"][1]["response"]["body"]["status"] == "incomplete"
        assert not capture.tasks
        store.close()

    asyncio.run(run())


def test_capture_failure_is_explicit_and_does_not_prevent_other_evidence():
    async def run():
        store = EvidenceStore()
        page = SimpleNamespace(
            screenshot=AsyncMock(side_effect=RuntimeError("Page closed")),
            content=AsyncMock(return_value="<html>Still available</html>"),
            context=SimpleNamespace(new_cdp_session=AsyncMock(side_effect=RuntimeError("No CDP"))),
        )
        record = {"id": "visit-0001", "artifacts": {}, "capture_errors": []}
        capture = VisitEvidence(page, store, DiscoveryOptions(network=False), record)
        await capture.finish()
        assert set(record["artifacts"]) == {"page.html"}
        assert {e["artifact"] for e in record["capture_errors"]} == {"screenshot.png", "dom.json"}
        store.close()

    asyncio.run(run())


@pytest.mark.parametrize("filename", ["model.discovery", "model.discovery.json"])
def test_artifact_names_cannot_collide_with_model(tmp_path, filename):
    with pytest.raises(ValueError, match="must not end"):
        output_paths(tmp_path / filename)
