"""Rendered inspection and native form interaction in an isolated browser context."""

import importlib.util
import json
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urljoin

from .scope import canonical_url

SNAPSHOT = Path(__file__).with_name("snapshot.js").read_text()
EVENTS = ("before_visit", "after_visit", "before_submit", "after_submit")


@dataclass
class DiscoveryContext:
    page: object
    url: str
    form: dict | None = None
    values: dict = field(default_factory=dict)
    snapshot: dict | None = None
    scratch: dict = field(default_factory=dict)
    skip: bool = False
    handled: bool = False


def load_hooks(path):
    if path is None:
        return None
    spec = importlib.util.spec_from_file_location("testwalker_discovery_hooks", Path(path).resolve())
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def callback(hooks, event, ctx):
    function = getattr(hooks, event, None)
    if function:
        import inspect

        if not callable(function):
            raise ValueError(f"Discovery hook {event} must be callable")
        result = function(ctx)
        if inspect.isawaitable(result):
            await result


def default_value(field):
    kind = field["type"]
    if kind == "checkbox":
        return field["checked"] or field["required"]
    if kind == "radio":
        return field["value"]
    if field["value"]:
        return field["value"]
    if field["options"]:
        return next((o["value"] for o in field["options"] if not o["disabled"] and o["value"]), "")
    if kind in {"number", "range"}:
        try:
            base = Decimal(field["min"] or "0")
            step = Decimal(field["step"] or "1") if field["step"] != "any" else Decimal("1")
            value = max(base, Decimal("1"))
            if step > 0:
                value = base + ((value - base) / step).to_integral_value(rounding=ROUND_CEILING) * step
            if field["max"] and value > Decimal(field["max"]):
                value = base
            return str(value)
        except InvalidOperation:
            return "1"
    value = {
        "email": "tester@example.com",
        "url": "https://example.com",
        "tel": "0400000000",
        "password": "Example-Test-42!",
        "date": "2030-01-15",
        "datetime-local": "2030-01-15T10:00",
        "month": "2030-01",
        "week": "2030-W03",
        "time": "10:00",
        "color": "#336699",
    }.get(kind, "Test example")
    if kind in {"date", "datetime-local", "month", "week", "time"}:
        return field["min"] or field["max"] or value
    minimum, maximum = int(field["minLength"] or 0), int(field["maxLength"] or 2000)
    return (value + "x" * max(0, min(minimum, 2000) - len(value)))[: max(0, maximum)]


def form_values(form, overrides, url):
    defaults = overrides.get("defaults", {})
    page_values = overrides.get("pages", {}).get(canonical_url(url), {})
    values = {}
    for control in form["fields"]:
        if control["type"] in {"hidden", "submit", "button", "reset", "image", "file"}:
            continue
        if not control["visible"] or control["disabled"] or control["readonly"]:
            continue
        key = control["name"] or control["label"]
        if key not in values:
            values[key] = page_values.get(
                control["label"],
                page_values.get(
                    key, defaults.get(control["label"], defaults.get(key, default_value(control)))
                ),
            )
    return values


class Inspector:
    def __init__(self, scope, options, hooks, blocked):
        self.scope, self.options, self.hooks = scope, options, hooks
        self.blocked = blocked
        self.scratch = {}
        self.navigation_count = 0

    async def attach(self, context):
        await context.route("**/*", self.route)
        context.on("page", lambda page: page.on("dialog", self.dismiss_dialog))

    async def dismiss_dialog(self, dialog):
        await dialog.dismiss()

    async def route(self, route):
        request = route.request
        navigation = request.is_navigation_request()
        allowed = self.scope.allows(request.url) if navigation else True
        if request.resource_type in {"fetch", "xhr", "websocket"} or request.method not in {"GET", "HEAD"}:
            allowed = allowed and self.scope.same_origin(request.url)
        if not self.options.submit_forms and request.method not in {"GET", "HEAD"}:
            allowed = False
        if navigation:
            self.navigation_count += 1
            allowed = allowed and self.navigation_count <= self.options.max_actions * 10
        if not allowed:
            self.blocked.append(
                {"url": request.url, "method": request.method, "reason": "outside scope or navigation budget"}
            )
            await route.abort()
            return
        if not navigation:
            await route.continue_()
            return
        # Do not let automatic HTTP redirect chains bypass the scope check. Reissue
        # GET redirects as new browser navigations, each routed independently.
        try:
            response = await route.fetch(max_redirects=0, timeout=self.options.timeout_ms)
            if 300 <= response.status < 400 and response.headers.get("location"):
                target = urljoin(request.url, response.headers["location"])
                if not self.scope.allows(target) or (
                    response.status in {307, 308} and request.method != "GET"
                ):
                    self.blocked.append(
                        {"url": target, "reason": "redirect outside scope or requires method replay"}
                    )
                    await route.abort()
                else:
                    script = json.dumps(target).replace("<", "\\u003c")
                    await route.fulfill(
                        status=200,
                        content_type="text/html",
                        body=f"<script>location.replace({script})</script>",
                    )
            else:
                await route.fulfill(response=response)
        except Exception as error:
            self.blocked.append({"url": request.url, "reason": str(error)})
            await route.abort()

    async def settle(self, page):
        await page.wait_for_load_state("domcontentloaded", timeout=self.options.timeout_ms)
        await page.wait_for_timeout(self.options.settle_ms)
        if not self.scope.allows(page.url):
            raise ValueError("Page left the discovery scope")

    async def snapshot(self, page):
        await self.settle(page)
        return await page.evaluate(SNAPSHOT)

    async def before_visit(self, page, url):
        if not self.scope.allows(url):
            raise ValueError("Visit outside discovery scope")
        page.set_default_timeout(self.options.timeout_ms)
        await callback(self.hooks, "before_visit", DiscoveryContext(page, url, scratch=self.scratch))

    async def after_visit(self, page, url):
        await self.settle(page)
        ctx = DiscoveryContext(page, url, scratch=self.scratch, snapshot=await page.evaluate(SNAPSHOT))
        await callback(self.hooks, "after_visit", ctx)

    async def submit(self, page, form, values):
        ctx = DiscoveryContext(page, page.url, form=form, values=dict(values), scratch=self.scratch)
        await callback(self.hooks, "before_submit", ctx)
        if ctx.skip:
            raise ValueError("Form submission skipped by hook")
        if not ctx.handled:
            form_handle = await page.evaluate_handle("i => document.forms[i]", form["index"])
            for control in form["fields"]:
                key = control["name"] or control["label"]
                if (
                    key not in ctx.values
                    or not control["visible"]
                    or control["disabled"]
                    or control["readonly"]
                ):
                    continue
                kind = control["type"]
                if kind in {"submit", "reset", "hidden", "button", "image", "file"}:
                    continue
                element = (
                    await form_handle.evaluate_handle("(f, i) => f.elements[i]", control["index"])
                ).as_element()
                value = ctx.values[key]
                if kind == "checkbox":
                    if type(value) is not bool:
                        raise ValueError("Checkbox overrides must be JSON booleans")
                    await element.set_checked(value)
                elif kind == "radio":
                    if str(value) == control["value"]:
                        await element.check()
                elif control["tag"] == "select":
                    await element.select_option(value=value if isinstance(value, list) else str(value))
                elif control["tag"] in {"input", "textarea"}:
                    await element.fill(str(value))
            submitter = next(
                (
                    c
                    for c in form["fields"]
                    if c["type"] in {"submit", "image"} and c["visible"] and not c["disabled"]
                ),
                None,
            )
            if submitter:
                action = submitter["action"] or form["action"]
                if not self.scope.allows(action):
                    raise ValueError("Submit button action is outside discovery scope")
                button = (
                    await form_handle.evaluate_handle("(f, i) => f.elements[i]", submitter["index"])
                ).as_element()
                await button.click()
            else:
                await form_handle.evaluate("f => f.requestSubmit()")
        await self.settle(page)
        ctx.snapshot = await page.evaluate(SNAPSHOT)
        await callback(self.hooks, "after_submit", ctx)
        return ctx.values
