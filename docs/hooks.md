# Lifecycle hooks

Most tests run from the business model alone. When needed, explicitly load a trusted local Python file:

```bash
model-test run --model booking.json --url http://localhost:3000 \
  --env-file .env --hooks hooks.py --headed
```

The model cannot name files to import or supply executable hook code. Loading the file executes normal Python with your process permissions. Credentials for backend APIs belong in environment variables, not the graph. Do not print secrets in hooks: their error messages can appear in the report.

Define any of these synchronous functions; omit events you do not need:

| Event pair | When it runs |
| --- | --- |
| `before_run` / `after_run` | Once, around graph traversal and property campaigns. |
| `before_walk` / `after_walk` | Around each seeded graph walk. |
| `before_reset` / `after_reset` | Around opening the start page, including every property setup and shrink attempt. Use `before_reset` for backend reset/seed operations. |
| `before_transition` / `after_transition` | Around executing an edge, including setup edges. The after event precedes destination verification. |
| `before_state` / `after_state` | Around independently observing and checking a vertex. Fired once per checkpoint, outside internal observation retries. |
| `before_case` / `after_case` | Around each complete generated, boundary or replay input attempt, including its reset and setup path. |

A graph walk runs `before_walk → before_reset → after_reset → before_state → after_state`, then repeats transition and destination-state checks. A property attempt runs `before_case → reset → setup transitions/checkpoints → tested transition → destination check → after_case`. All pairs are enclosed by the run events. Repeated visits fire hooks again; coverage still counts each graph element only once.

Each function receives a `HookContext`:

- `element_id` and `element`: the vertex or edge for state, transition and case events; otherwise `None`.
- `phase`: `graph`, `setup` or `property`. Property setup paths use `setup`; the case envelope and submitted input use `property`.
- `data`: a snapshot of business values. Case events contain the candidate before reset. Mutating this snapshot does not replace generated inputs.
- `invalid`: expected dictionary violations, supplied for case and state events.
- `result`: the verification result for after-state/after-case; the run report for after-run. Check `result["status"]` before making assertions that require a successful browser checkpoint.
- `error`: an exception if the enclosed operation raised. Failed browser verdicts may be returned as `result["status"] == "FAIL"` without an exception yet.
- `scratch`: a dictionary shared across all hooks for this run, for client handles or fixture identifiers.
- `walk`: one-based graph walk number, or `None` during property campaigns.
- `url`, `model`, `browser`: run URL, validated model, and the framework browser adapter. Browser access is advanced and can invalidate the modeled state; prefer external API operations.
- `report`: available to run hooks. At `after_run`, it has the provisional result; final cleanup, coverage summary and report writing follow.
- `check(condition, message)`: raise a test defect when a deterministic assertion is false.

For example, this hooks file records confirmed bookings in an additional local audit file. It is optional and uses the booking model's stable state ID:

```python
import json
from pathlib import Path


def before_run(ctx):
    ctx.scratch["confirmed"] = []


def after_state(ctx):
    if ctx.element_id == "confirmed" and ctx.result and ctx.result["status"] == "PASS":
        ctx.scratch["confirmed"].append(dict(ctx.data))


def after_run(ctx):
    destination = Path(ctx.report["directory"]) / "confirmed-bookings.json"
    destination.write_text(json.dumps(ctx.scratch.get("confirmed", []), indent=2))
```

A real backend integration can instead use `before_reset` to call your test-data reset endpoint, and `after_state` to query an API or database and call `ctx.check(...)`. The framework does not invent endpoint names, schemas or reset behavior. Provide timeouts for external calls. Hooks may run many times during generation, confirmation and shrinking, so make fixture setup repeatable and cleanup tolerant of partial setup.

An assertion (`ctx.check`, `assert`, or `Defect`) fails the test. Other hook exceptions make it inconclusive. Assertions during a property's setup path are inconclusive for that candidate because the required starting state could not be established. After hooks run even if their corresponding before hook fails. Secondary teardown errors are recorded without replacing the original outcome. An `after_run` failure prevents a successful run from passing, and browser/client cleanup still happens.

Hooks cannot grant graph coverage: an edge is credited only after its destination and state hooks pass. Reports include hook events, phase, element, outcome, the loaded file path and SHA-256. Replay must use the same hook file and backend setup; the replay model hash does not by itself prove that external state or hook code is unchanged. No hook file is loaded automatically from a report.

Async hooks are not supported directly. A synchronous hook can manage its own external clients or call `asyncio.run` if needed. There is no hook sandbox or automatic timeout for arbitrary user code.

## Complete example

The [Trailhead integration](../examples/trailhead/README.md) demonstrates repeated fixture resets, inventory and refund assertions, case auditing, and custom code for a bespoke pickup list. Its [hooks file](../examples/trailhead/hooks.py) uses `ctx.browser.browser.evaluate(...)` through the Jev Browser Harness connection to operate the actual widget. This is an explicitly loaded application integration; selectors never enter the graph, and browser state verification remains independent.
