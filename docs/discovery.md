# Discovering a starter model

Deterministic model discovery is separate from test execution. It needs Chrome, but no Jev key, AI service or GraphWalker executable. It uses Crawlee’s `PlaywrightCrawler`; no Playwright MCP server is involved. The generated model is a draft to review and refine with independent business expectations.

## Commands

From this checkout, run `uv sync` to install the new dependency, then:

```bash
uv run testwalker discover-sitemap --sitemap sitemap.xml --output models/discovered.json
uv run testwalker discover-url --url http://127.0.0.1:4173/ --output models/explored.json --headed
```

Installed packages use `testwalker` without the `uv run` prefix. Reinstall the package after updating it. Neither command needs `testwalker.properties`. Supply `--config browser.properties` to use `CHROME_EXECUTABLE` or `CDP_URL`; GraphWalker and API credentials are ignored for discovery. Discovery creates an isolated browser context and closes it when finished. It does not reuse your normal Chrome profile or the test runner's profile-directory/port settings.

`discover-sitemap` accepts a local UTF-8 XML `urlset`, including standard sitemap namespaces. It visits each listed URL directly and inspects rendered links, forms, field constraints and buttons. It does not submit forms. Page navigations, including redirects, frames and popups, are restricted to the exact sitemap URLs. Queries are significant; fragments are treated as locations within one page. Sitemap indexes must be expanded separately. Use one origin per model and at most 100 seed URLs.

`discover-url` stays on the starting origin (scheme, host and port). Its default `--depth 1` visits linked pages and submits each discovered form from the seed once. It inspects resulting pages but does not interact with their links/forms until you increase depth. Each additional link or form submission costs a level. `--depth 0` only inspects the initial page. `--no-submit-forms` inspects forms without submitting them. Forms really submit, so use a test environment and hooks to reset fixtures as needed.

```bash
uv run testwalker discover-url \
  --url http://127.0.0.1:4173/ \
  --depth 2 --max-pages 50 --max-actions 200 \
  --values discovery-values.json --hooks discovery-hooks.py \
  --output models/discovered.json
```

`--max-pages` limits distinct observed states (default 50, maximum 100); `--max-actions` bounds visits and form submissions, including replays (default 200). Form paths are replayed when needed to explore deeper outcomes; this can submit an earlier form more than once. Cookies and server state persist within the discovery session. A changed source page stops replay instead of submitting a form by a stale index.

`--timeout-ms` defaults to 10000. `--settle-ms` defaults to 300 and allows asynchronous UI updates to arrive; increase it or wait for application readiness in a hook. Discovery is bounded sampling, not exhaustive interaction coverage.

## Values and hooks

Native inputs get deterministic examples: email, text, number bounds/steps, date/time, required checkboxes and the first nonempty enabled choice. Existing nonempty values are retained. Arbitrary regex patterns, file uploads and custom controls need overrides or hooks. Native validation stays active, and a submission with no observable change is flagged for review.

`discovery-values.json`:

```json
{
  "defaults": {
    "Email": "tester@example.com",
    "Seats": 2,
    "Accept terms": true
  },
  "pages": {
    "http://127.0.0.1:4173/book.html": {
      "Email": "booking@example.com"
    }
  }
}
```

Keys match visible field labels or HTML names. Prefer labels; a label wins over a name within the resolved overrides. Page values override defaults. Radio/select values use underlying option values; multi-select values are arrays. Checkboxes use JSON booleans. Unsupported controls and their HTML constraints remain in the inventory for review.

Discovery hooks are **separate from test execution lifecycle hooks**. An explicitly supplied Python module can define `before_visit(ctx)`, `after_visit(ctx)`, `before_submit(ctx)` and `after_submit(ctx)` functions. `ctx.page` is an **async Playwright page**, `ctx.url` is the source URL, `ctx.scratch` persists across callbacks, and `ctx.snapshot` is available after visits/submissions. Submission contexts also provide the observed `ctx.form` and mutable `ctx.values`. Hooks can be synchronous when only changing values, or async when operating the browser. Existing discovery hooks that use browser methods must change to `async def` and `await` those methods. Test execution lifecycle hooks are unchanged.

```python
# discovery-hooks.py
from urllib.request import Request, urlopen


def before_visit(ctx):
    # Optional fixture setup or other application-specific calls.
    if ctx.url.endswith("/checkout"):
        with urlopen(Request("http://127.0.0.1:4173/test/reset", method="POST")):
            pass


async def before_submit(ctx):
    if ctx.form["label"] == "Delivery":
        # Custom widget handling stays in this optional hook, not the graph.
        await ctx.page.get_by_role("combobox", name="Pickup location").click()
        await ctx.page.get_by_role("option", name="North studio").click()
        ctx.values["Pickup location"] = "North studio"
        ctx.values["email"] = "tester@example.com"
        # Native filling/submission continues afterwards.
```

For a completely custom submission, perform it in `before_submit` and set `ctx.handled = True`; update `ctx.values` to describe the values actually used. Set `ctx.skip = True` to prevent submission (recorded as an uncompleted discovery attempt). Hooks execute trusted Python: browser routing still applies, but calls made directly by hook code are the author's responsibility.

## Python composition

```python
from testwalker.discovery import DiscoveryOptions, discover_sitemap, discover_urls
from testwalker.discovery.scope import sitemap_urls
import discovery_hooks

# Inspection only: every sitemap page, one merged graph.
result = discover_sitemap("sitemap.xml", output="models/initial.json")

# Optional recursive exploration with the same explicit sitemap boundary.
urls = sitemap_urls("sitemap.xml")
result = discover_urls(
    urls,
    allowed_urls=urls,
    options=DiscoveryOptions(depth=2, submit_forms=True, max_pages=100),
    values={"defaults": {"Email": "tester@example.com"}},
    hooks=discovery_hooks,
)
result.write("models/refined-draft.json")
```

`discover_url(url, ...)` uses the same options and returns the same `DiscoveryResult`. In async applications, use `await discover_urls_async([url], ...)` instead of the synchronous wrappers. Omit `output` to work with `result.model` and `result.inventory` in memory. Multiple seed pages are merged by observed state identity. Ordinary linked pages are navigated by their href; JavaScript-only navigation needs a hook. Buttons are inventoried rather than automatically clicked.

## Reviewing the result

Each run writes:

- `discovered.json`: a single GraphWalker graph with selector-free business metadata, observed states, link journeys and exercised form journeys. Forms/buttons appear in state requirements; submissions include the sample values in their intent.
- `discovered.discovery.json`: URLs, observed text, full form/control inventories, HTML constraints, transition evidence, submitted values, blocked requests, failed/pending visits and review notes.

- `discovered.discovery/`: evidence files grouped by visit, with screenshots, rendered HTML, live DOM snapshots and network records enabled by default.

The model, inventory and evidence directory must all be new; discovery refuses to overwrite reviewed work. Exit code 0 means discovery completed, **not that the application passed tests**. Exit code 2 indicates an error or partial discovery, with usable partial output when pages were inspected.

The model deliberately has no generated property-test data sets: the page's constraints are not an independent oracle, and a successful submission does not establish the correct rejection outcome. Review the collected constraints, supply business data sets and connect acceptance/rejection states before running Hypothesis. Review same-URL states and custom controls especially carefully. Observed hyperlinks are distinguished from exercised form transitions in the inventory.

No invented navigation repairs disconnected components or dead ends. These appear as review notes. A page with no discovered transitions produces an edge-free draft; add real journeys before `testwalker validate` or `plan` can accept it. Use GraphWalker independently to refine the resulting graph.

External static assets may load so pages render correctly; external fetch/XHR and non-GET requests are blocked. HTTP navigation redirects are checked individually; GET redirects and the usual POST-to-GET redirects are supported, while POST-preserving 307/308 redirects are recorded as blocked. Service workers are disabled. This initial inspector does not descend into iframes or shadow roots, infer business meaning from arbitrary widgets, or model every fragment-driven SPA state. Live text can cause extra states; refine these during review.

## Screenshots, DOM and network evidence

Both discovery commands capture evidence automatically. No Jev key or accessibility scanner is needed. Each inventory `visits` entry includes its source/result state IDs, intent, timestamp, artifact paths and any capture errors. States list their visit IDs; exercised transitions and failed attempts reference their visit. Repeated visits to the same state keep separate evidence, including form submissions and replay traffic.

For `--output models/discovered.json`, a visit writes:

```text
models/discovered.discovery/visit-0001/
  screenshot.png
  page.html
  dom.json
  network.json
```

Resolve each visit's `artifacts` path relative to the inventory's `artifact_root`, itself relative to the inventory file. `screenshot.png` is a full-page screenshot. `page.html` is serialized rendered HTML. `dom.json` is Chrome's `DOMSnapshot.captureSnapshot` result, with a string table, DOM nodes, live input/checked/selected values and layout rectangles. It also includes frame/shadow DOM information Chrome exposes; the model generator still only inspects the main document. No accessibility checks are performed here; those belong in the walker. These are inspection snapshots, not a replayable browser session: scripts, event listeners, resources and computed styles are not bundled, so checks requiring a live page should use the walker’s browser.

`network.json` records requests observed in the visit's page (including frames), starting before navigation and covering hooks and replayed form submissions until evidence capture finishes. Entries include URL, HTTP method, resource type, completion/failure status, available timing, response status and content types. Request and response bodies declared as `application/json` or `*+json` are parsed and saved, including JSON error responses. Other payloads get metadata only. Headers/cookies are not dumped. Capturing traffic does not follow endpoints or issue additional requests, and existing crawl boundaries still apply.

Body status distinguishes `captured` (including JSON null), `not_json`, `invalid_json`, `too_large`, `unavailable` and `incomplete`. Defaults are **1 MiB per JSON body** and **1,000 requests per visit**. Omitted request counts are recorded. `--max-json-bytes` and `--max-network-requests` tune those limits. The body limit caps persisted payloads; unknown-length or compressed responses may need to be read before their decoded size can be checked. Responses still streaming after a bounded two-second drain are marked incomplete. Increase `--settle-ms` or wait in an `after_visit`/`after_submit` hook for later application calls.

Capture covers browser page traffic, not Python hook HTTP calls, WebSocket messages or separate popup tabs. Navigation guards may replace redirects with a checked client navigation; recorded status reflects what the browser received, not a complete upstream HTTP trace. No requests blocked by the guard are replayed to obtain their responses.

Disable individual captures with `--no-screenshots`, `--no-dom`, or `--no-network`. The Python equivalents are `DiscoveryOptions(screenshots=False, dom=False, network=False)`. Capture failures are listed in `visits[].capture_errors` and make the CLI exit with code 2 while retaining usable output.

When using the Python API without `output`, files are staged at `result.evidence.path`. `result.write(path)` copies them alongside the model and inventory; call `result.close()` after consuming/writing the result to release temporary files. Saved output remains intact. Staging is also cleaned when the result is garbage-collected.

## Crawlee integration

Crawlee manages request scheduling, URL deduplication, link extraction, depth limits, browser pooling, page navigation/cleanup and failed-request callbacks. Testwalker retains model generation, rendered control inventory, form defaults/replay and exact sitemap request guards. The small local XML parser remains because sitemap input is a local file and discovery must not fetch unlisted sitemap resources.

Discovery runs serially so forms and hooks share a predictable browser session. Request retries and session rotation are disabled: retrying a failed request could repeat a submitted form. Form requests have their own identities so distinct outcomes on the same URL are explored. The action budget counts navigation and replay submissions, separately from Crawlee's request count.

Each run uses isolated in-memory Crawlee storage, released after completion, and the inventory records `engine: "crawlee"`. No Crawlee storage directory, Apify account or API token is required. Connecting through `CDP_URL` uses a Crawlee browser plugin that closes only its own context and preserves existing Chrome tabs.
