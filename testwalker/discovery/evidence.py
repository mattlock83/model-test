"""Passive browser evidence, kept separate from the generated business model."""

import asyncio
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def json_body(body, content_type, limit):
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        return {"status": "not_json"}
    if body is None:
        return {"status": "unavailable"}
    if len(body) > limit:
        return {"status": "too_large", "bytes": len(body)}
    try:
        return {"status": "captured", "value": json.loads(body)}
    except (ValueError, UnicodeError):
        return {"status": "invalid_json"}


class EvidenceStore:
    """Disk-backed staging also supports discover(output=None), followed by write()."""

    def __init__(self):
        self._temporary = TemporaryDirectory(prefix="testwalker-discovery-")
        self.path = Path(self._temporary.name)

    def copy_to(self, destination):
        shutil.copytree(self.path, destination)

    def close(self):
        self._temporary.cleanup()


class VisitEvidence:
    def __init__(self, page, store, options, record):
        self.page, self.options, self.record = page, options, record
        self.directory = store.path / record["id"]
        self.directory.mkdir()
        self.requests = {}
        self.tasks = set()
        self.listeners = []
        self.network = {"requests": [], "omitted_requests": 0}
        if options.network:
            for event, callback in (
                ("request", self.on_request),
                ("response", self.on_response),
                ("requestfailed", self.on_failed),
                ("requestfinished", self.on_finished),
            ):
                page.on(event, callback)
                self.listeners.append((event, callback))

    def on_request(self, request):
        if len(self.requests) >= self.options.max_network_requests:
            self.network["omitted_requests"] += 1
            return
        entry = {
            "id": len(self.requests) + 1,
            "url": request.url,
            "method": request.method,
            "resource_type": request.resource_type,
            "status": "pending",
            "request_content_type": request.headers.get("content-type", ""),
        }
        try:
            content_type = entry["request_content_type"]
            entry["request_body"] = json_body(None, content_type, 0)
            if entry["request_body"]["status"] != "not_json":
                entry["request_body"] = json_body(
                    request.post_data_buffer, content_type, self.options.max_json_bytes
                )
        except Exception as error:
            entry["request_body"] = {"status": "unavailable", "error": str(error)}
        if previous := self.requests.get(request.redirected_from):
            entry["redirected_from"] = previous["id"]
        self.requests[request] = entry
        self.network["requests"].append(entry)

    def on_response(self, response):
        entry = self.requests.get(response.request)
        if entry is None:
            return
        entry["response"] = {
            "status": response.status,
            "content_type": response.headers.get("content-type", ""),
            "body": {"status": "pending"},
        }
        task = asyncio.create_task(self.read_response(response, entry["response"]))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def read_response(self, response, entry):
        content_type = entry["content_type"]
        if json_body(None, content_type, 0)["status"] == "not_json":
            entry["body"] = {"status": "not_json"}
            return
        try:
            length = response.headers.get("content-length", "")
            if length.isdigit() and int(length) > self.options.max_json_bytes:
                entry["body"] = {"status": "too_large", "bytes": int(length)}
                return
            body = await response.body()
            entry["body"] = json_body(body, content_type, self.options.max_json_bytes)
        except asyncio.CancelledError:
            entry["body"] = {"status": "incomplete", "reason": "capture window ended"}
            raise
        except Exception as error:
            entry["body"] = {"status": "unavailable", "error": str(error)}

    def on_failed(self, request):
        if entry := self.requests.get(request):
            entry.update(status="failed", failure=request.failure)

    def on_finished(self, request):
        if entry := self.requests.get(request):
            entry.update(status="finished", timing=request.timing)

    async def capture(self, name, operation):
        try:
            await asyncio.wait_for(operation(), timeout=5)
            self.record["artifacts"][name] = f"{self.record['id']}/{name}"
        except Exception as error:
            self.record["capture_errors"].append({"artifact": name, "error": str(error)})

    async def html(self):
        (self.directory / "page.html").write_text(await self.page.content(), encoding="utf-8")

    async def dom(self):
        # Unlike HTML serialization, DOMSnapshot retains live input values,
        # checked/selected properties, shadow DOM and frame/layout information.
        session = await self.page.context.new_cdp_session(self.page)
        try:
            snapshot = await session.send(
                "DOMSnapshot.captureSnapshot", {"computedStyles": [], "includeDOMRects": True}
            )
            write_json(self.directory / "dom.json", snapshot)
        finally:
            await session.detach()

    async def finish(self):
        self.record["captured_at"] = datetime.now(timezone.utc).isoformat()
        if self.options.screenshots:
            await self.capture(
                "screenshot.png",
                lambda: self.page.screenshot(
                    path=str(self.directory / "screenshot.png"), full_page=True, timeout=5000
                ),
            )
        if self.options.dom:
            await self.capture("page.html", self.html)
            await self.capture("dom.json", self.dom)
        if self.options.network:
            for event, callback in self.listeners:
                self.page.remove_listener(event, callback)
            # A bounded drain, not networkidle: polling/streaming apps must finish.
            if self.tasks:
                _, pending = await asyncio.wait(self.tasks, timeout=2)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            for entry in self.network["requests"]:
                if entry["status"] == "pending":
                    entry["status"] = "incomplete"
            write_json(self.directory / "network.json", self.network)
            self.record["artifacts"]["network.json"] = f"{self.record['id']}/network.json"
