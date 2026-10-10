"""Use Crawlee's scheduler and request lifecycle for bounded UI exploration."""

import hashlib
import json

from crawlee import Request
from crawlee.configuration import Configuration
from crawlee.events import LocalEventManager
from crawlee.storage_clients import MemoryStorageClient
from crawlee.storages import RequestQueue

from .browser import Inspector, form_values
from .evidence import VisitEvidence
from .model import identity, prose
from .runtime import create_crawler
from .scope import canonical_url


class DiscoveryStorage(MemoryStorageClient):
    def get_storage_client_cache_key(self, configuration):
        # Crawlee otherwise caches stores by client class, even across separate
        # MemoryStorageClient instances. Each discovery must see fresh URLs.
        return self


def submission_intent(form, values):
    intent = f"Submit {prose(form['label'])}"
    if values:
        labels = {c["name"] or c["label"]: c["label"] for c in form["fields"]}
        intent += " with " + ", ".join(f"{prose(labels.get(k, k))}: {prose(v)}" for k, v in values.items())
    return intent + "."


def page_request(url, **data):
    url = canonical_url(url)
    return Request.from_url(url, unique_key="url:" + url, user_data=data)


def pending(request):
    return {"url": request.url, "intent": request.user_data.get("intent", ""), "depth": request.crawl_depth}


async def explore(seeds, scope, options, config, hooks, values, inventory, evidence):
    # Each invocation owns its stores and event manager. No default filesystem
    # storage, ambient queue, or state from a previous crawl is shared.
    settings = Configuration(purge_on_start=False)
    storage = DiscoveryStorage()
    queue = await RequestQueue.open(configuration=settings, storage_client=storage)
    crawler = create_crawler(
        options,
        config,
        request_manager=queue,
        configuration=settings,
        storage_client=storage,
        event_manager=LocalEventManager(),
    )
    browser = Inspector(scope, options, hooks, inventory["blocked"])
    contexts, expanded = set(), set()
    pages = inventory["pages"]
    inventory.update(engine="crawlee", operations=0, visits=[])
    visits = {}

    @crawler.pre_navigation_hook
    async def before_navigation(context):
        request, page = context.request, context.page

        # Crawlee opens a page before pre-navigation hooks. Register cleanup here
        # too, including for a setup hook or budget failure before goto runs.
        async def cleanup():
            try:
                if capture := visits.pop(request.unique_key, None):
                    await capture.finish()
            finally:
                await page.close()

        context.register_deferred_cleanup(cleanup)
        if page.context not in contexts:
            await browser.attach(page.context)
            contexts.add(page.context)
        page.on("dialog", browser.dismiss_dialog)
        needed = 1 + len(request.user_data.get("steps", []))
        if inventory["operations"] + needed > options.max_actions or len(pages) >= options.max_pages:
            request.user_data["budget_pending"] = True
            inventory["pending"].append(pending(request))
            crawler.stop("Discovery budget reached")
            raise ValueError("Discovery budget reached before navigation")
        record = {
            "id": f"visit-{len(inventory['visits']) + 1:04d}",
            **pending(request),
            "source": request.user_data.get("source"),
            "state": None,
            "artifacts": {},
            "capture_errors": [],
        }
        inventory["visits"].append(record)
        visits[request.unique_key] = VisitEvidence(page, evidence, options, record)
        inventory["operations"] += 1
        await browser.before_visit(page, request.url)

    @crawler.failed_request_handler
    async def failed(context, error):
        if not context.request.user_data.get("budget_pending"):
            capture = visits.get(context.request.unique_key)
            visit_id = capture.record["id"] if capture else None
            if capture:
                capture.record["error"] = str(error)
            inventory["failed"].append({**pending(context.request), "error": str(error), "visit": visit_id})

    @crawler.router.default_handler
    async def inspect_page(context):
        request, page = context.request, context.page
        data = request.user_data
        await browser.after_visit(page, request.url)
        steps = data.get("steps", [])
        for step in steps:
            current = await browser.snapshot(page)
            if identity(current) != step["source"]:
                raise ValueError("Source page changed during replay; form was not submitted")
            inventory["operations"] += 1
            step["values"] = await browser.submit(page, step["form"], step["values"])
            data["intent"] = submission_intent(step["form"], step["values"])
        snapshot = await browser.snapshot(page)
        key = identity(snapshot)
        pages.setdefault(key, snapshot)
        record = visits[request.unique_key].record
        record.update(state=key, intent=data.get("intent", ""))
        pages[key].setdefault("visits", []).append(record["id"])
        if source := data.get("source"):
            inventory["transitions"].append(
                {
                    "visit": record["id"],
                    "source": source,
                    "target": key,
                    "intent": data["intent"],
                    "evidence": data["kind"],
                    "values": steps[-1]["values"] if steps else {},
                }
            )
            if key == source and data["kind"] == "form submission":
                inventory["review"].append(
                    f"Form submission on {key} produced no distinguishable page change; "
                    "inspect validation and feedback."
                )
        if key not in expanded and request.crawl_depth < options.depth:
            expanded.add(key)
            # Crawlee resolves hrefs/base URLs and filters link strategies. Our
            # allowlist is stricter than its same-origin filter and stays explicit.
            labels = {
                canonical_url(link["url"]): link["label"]
                for link in snapshot["links"]
                if not link["download"] and scope.allows(link["url"])
            }
            links = await context.extract_links(
                selector="a[href]:visible:not([download])", strategy="same-origin"
            )
            destinations = [
                page_request(
                    link.url,
                    source=key,
                    intent=f"Follow {prose(labels[canonical_url(link.url)])}.",
                    kind="visited hyperlink",
                )
                for link in links
                if scope.allows(link.url) and canonical_url(link.url) in labels
            ]
            if options.submit_forms:
                for form in snapshot["forms"]:
                    if not scope.allows(form["action"]):
                        inventory["blocked"].append(
                            {"url": form["action"], "reason": "form action outside scope"}
                        )
                        continue
                    supplied = form_values(form, values, snapshot["url"])
                    step = {"source": key, "form": form, "values": supplied}
                    fingerprint = hashlib.sha256(json.dumps(step, sort_keys=True).encode()).hexdigest()
                    destinations.append(
                        Request.from_url(
                            request.url,
                            unique_key="form:" + fingerprint,
                            user_data={
                                "steps": [*steps, step],
                                "source": key,
                                "intent": submission_intent(form, supplied),
                                "kind": "form submission",
                            },
                        )
                    )
            # Crawlee increments depth and deduplicates queued requests, including
            # cycles and distinct form outcomes that share a navigation URL.
            await context.add_requests(destinations, strategy="same-origin")
        if inventory["operations"] >= options.max_actions or len(pages) >= options.max_pages:
            crawler.stop("Discovery budget reached")

    try:
        await crawler.run([page_request(url, kind="seed") for url in seeds])
        while request := await queue.fetch_next_request():
            inventory["pending"].append(pending(request))
            await queue.mark_request_as_handled(request)
    finally:
        await queue.drop()
        await (await crawler.get_key_value_store()).drop()
