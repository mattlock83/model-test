"""Bounded breadth-first discovery; observations are drafts, never pass/fail oracles."""

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from ..config import RuntimeConfig
from .evidence import EvidenceStore
from .explorer import explore
from .model import build_model, prose
from .scope import Scope, canonical_url, sitemap_urls


@dataclass(frozen=True)
class DiscoveryOptions:
    depth: int = 1
    submit_forms: bool = True
    max_pages: int = 50
    max_actions: int = 200
    timeout_ms: int = 10000
    settle_ms: int = 300
    headed: bool = False
    screenshots: bool = True
    dom: bool = True
    network: bool = True
    max_json_bytes: int = 1048576
    max_network_requests: int = 1000

    def __post_init__(self):
        for name, low, high in (
            ("max_json_bytes", 1, 10485760),
            ("max_network_requests", 1, 10000),
            ("depth", 0, 10),
            ("max_pages", 1, 100),
            ("max_actions", 1, 10000),
            ("timeout_ms", 100, 120000),
            ("settle_ms", 0, 10000),
        ):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer from {low} through {high}")


@dataclass
class DiscoveryResult:
    model: dict
    inventory: dict
    evidence: EvidenceStore

    def close(self):
        """Release staged evidence after writing or consuming it."""
        self.evidence.close()

    def write(self, output):
        output, inventory = output_paths(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        self.evidence.copy_to(output.with_suffix(".discovery"))
        saved_inventory = {**self.inventory, "artifact_root": output.with_suffix(".discovery").name}
        output.write_text(json.dumps(self.model, indent=2, ensure_ascii=False) + "\n")
        inventory.write_text(json.dumps(saved_inventory, indent=2, ensure_ascii=False) + "\n")
        return output, inventory


def output_paths(output):
    output = Path(output)
    inventory = output.with_suffix(".discovery.json")
    if output.name.endswith((".discovery.json", ".discovery")):
        raise ValueError("Model output must not end in .discovery.json or .discovery")
    if output.exists() or inventory.exists() or output.with_suffix(".discovery").exists():
        raise ValueError("Discovery output exists; choose a new output filename")
    return output, inventory


def validate_values(values):
    if not isinstance(values, dict) or set(values) - {"defaults", "pages"}:
        raise ValueError("Value overrides need defaults and/or pages objects")
    defaults, pages = values.get("defaults", {}), values.get("pages", {})
    if (
        not isinstance(defaults, dict)
        or not isinstance(pages, dict)
        or not all(isinstance(v, dict) for v in pages.values())
    ):
        raise ValueError("Value overrides must map field labels or names to literal values")
    for fields in [defaults, *pages.values()]:
        for key, value in fields.items():
            if not isinstance(key, str) or not (
                type(value) in (str, int, float, bool)
                or isinstance(value, list)
                and all(isinstance(v, str) for v in value)
            ):
                raise ValueError(
                    "Field overrides must be strings, numbers, booleans or lists of choice values"
                )
    return {"defaults": defaults, "pages": {canonical_url(k): v for k, v in pages.items()}}


def discover_sitemap(sitemap, *, output=None, config=None, options=None, values=None, hooks=None):
    """Inspect every listed URL, without submitting forms or following unlisted URLs."""
    from dataclasses import replace

    urls = sitemap_urls(sitemap)
    options = replace(options or DiscoveryOptions(), depth=0, submit_forms=False)
    return discover_urls(
        urls, allowed_urls=urls, output=output, config=config, options=options, values=values, hooks=hooks
    )


def discover_url(url, **kwargs):
    """Explore a URL's links and forms one interaction deep by default."""
    return discover_urls([url], **kwargs)


async def discover_urls_async(
    urls, *, allowed_urls=None, output=None, config=None, options=None, values=None, hooks=None
):
    """Merge multiple seeds into one graph. An explicit allowlist bounds recursive discovery.

    Hooks are objects/modules with before/after_visit and before/after_submit.
    Use options.depth to bound each seed's exploration; default is one link or submission.
    """
    if output is not None:
        output_paths(output)
    options = options or DiscoveryOptions()
    seeds = list(dict.fromkeys(canonical_url(u) for u in urls))
    if not seeds or len(seeds) > options.max_pages:
        raise ValueError("Supply 1–max_pages seed URLs; increase max_pages up to 100 or split the sitemap")
    scope = Scope(seeds, allowed_urls)
    if not all(scope.allows(url) for url in seeds):
        raise ValueError("Every seed URL must be in the allowed URL scope")
    # A business model has one application base URL.
    if len(scope.origins) != 1:
        raise ValueError("Use one origin per model; split cross-origin sitemaps")
    values = validate_values(values or {})
    config = config or RuntimeConfig(Path("."), None, "")
    inventory = {
        "version": 2,
        "seeds": seeds,
        "scope": sorted(scope.allowed_urls) if scope.allowed_urls is not None else "same origin",
        "depth": options.depth,
        "pages": {},
        "transitions": [],
        "failed": [],
        "blocked": [],
        "pending": [],
        "review": [
            "Observed labels and HTML constraints are provisional. Add independent business rules, "
            "data sets and distinct acceptance/rejection states before property testing.",
            "Buttons and custom widgets are inventoried, not blindly clicked. Use discovery hooks "
            "for custom interactions. Iframe contents and shadow roots are not inspected.",
        ],
    }
    pages, transitions, review = inventory["pages"], inventory["transitions"], inventory["review"]
    evidence = EvidenceStore()
    try:
        await explore(seeds, scope, options, config, hooks, values, inventory, evidence)
    except BaseException:
        evidence.close()
        raise
    if not pages:
        evidence.close()
        raise ValueError(
            "No pages could be inspected: " + json.dumps(inventory["failed"] + inventory["blocked"])
        )
    # Sitemap mode visits URLs directly. Infer only hyperlinks actually present in
    # a rendered page and pointing to another observed state; never invent returns.
    by_url = {}
    for key, page in pages.items():
        by_url.setdefault(canonical_url(page["url"]), key)
    for source, page in pages.items():
        for link in page["links"]:
            if not link["download"] and scope.allows(link["url"]):
                target = by_url.get(canonical_url(link["url"]))
                if target:
                    transitions.append(
                        {
                            "source": source,
                            "target": target,
                            "intent": f"Follow {prose(link['label'])}.",
                            "evidence": "observed hyperlink; outcome not independently verified",
                        }
                    )
    if inventory["pending"]:
        review.append("Discovery budget reached; pending visits are listed in this inventory.")
    result = DiscoveryResult(build_model(pages, transitions, review), inventory, evidence)
    if output is not None:
        result.write(output)
    return result


def discover_urls(urls, **kwargs):
    """Synchronous entry point; async applications should await discover_urls_async."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise ValueError("An event loop is running; await discover_urls_async instead")
    return asyncio.run(discover_urls_async(urls, **kwargs))
