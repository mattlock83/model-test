# Native adapter protocol (version 1)

`testwalker-core` speaks bidirectional JSON-RPC 2.0 using one UTF-8 JSON message per line on stdin/stdout. Stdout contains protocol messages only; diagnostics use stderr. Message limit: 64 MiB. Python's `CoreClient` launches this worker, handles adapter callbacks and permits nested decision requests while servicing a callback.

This profile accepts single request objects, not batches. Malformed JSON returns `-32700`, invalid request envelopes return `-32600`, unknown methods return `-32601`, and scalar or null parameters return `-32602`. Valid notifications have no response. An unusable or absent ID on an invalid request is returned as null; valid IDs retain their JSON type. Domain validation errors use `-32001` with Testwalker's structured outcome data.

Run `testwalker self-test` to exercise this API through a separate driver and subject worker. The [RPC testing guide](rpc-testing.md) also describes testing other stdio APIs with declarative bindings and deterministic assertions, without Jev.

One worker runs one synchronous execution at a time. `run.start` returns its final report, rather than an asynchronous run handle. Start independent workers for parallel runs. `run.cancel` is cooperative at the next target or decision boundary; it cannot interrupt an already running browser operation or HTTP request.

## Client → core

| Method | Parameters / result |
| --- | --- |
| `core.info` | Returns version, protocol version and native dependency revisions. |
| `model.validate` | `{model}` → model hash and counts. |
| `graph.plan` | `{model, options}` → one connected GraphWalker route. No target or provider required. |
| `run.plan` | `{model, options}` → selected inventory, routes and property phases. No execution. |
| `run.start` | `{model, options, target}` → final results, coverage, cases and stop reason. `target` is adapter-specific configuration. |
| `case.replay` | Same as `run.start`, plus `{case}` containing a saved replay recipe. Validates the model hash. |
| `run.cancel` | Requests cancellation of the active run. |
| `run.results` | Returns the most recently completed report. |
| `decision.configure` | `{provider:"jev", api_key, model?, threshold?, max_calls?, endpoint?}`. Configure before starting. |
| `decision.ask` | `{evidence, questions}` → provider response. Core owns HTTP, cache, call budget and audit. |
| `decision.answer` | `{result, name, choices, threshold?}` → validated choice or uncertainty. Choice probabilities and confidence must meet the threshold. |
| `decision.stats` | Provider call, cache-hit and token usage. |
| `decision.audit` | `{offset?:0, limit?:128}` → `{entries, total, next_offset}`. Fetch the transcript in order until `next_offset` is null. Limit is 1–1024; pages also have a byte bound. |
| `decision.post` | `{url, key, body, cache?}` for an optional auxiliary text helper; shares the call budget. |
| `property.plan` | `{fields, data_set?, options?}` → native property phases. |
| `property.samples` | `{fields}` with one field → named native boundary samples. |
| `property.exercise` | Callback-based Hegel execution over `{fields, options?, phase_ids?}`; issues `property.evaluate` to the adapter. Used for testing domains independently of a graph. |
| `core.shutdown` | Graceful exit when no run is active. |

Options include `seed`, `walks`, `max_steps`, `cases`, `input_mode`, `input_strategies`, `custom_strategies`, `shrink`, `max_input_attempts`, `keep_target_open`, and `evaluator` (`core` or `adapter`). Unknown fields are rejected. Graph steps count both states and edges. Input limits select phases before execution and bound extra reproduction/shrink attempts. Hegel is always the property engine; there is no backend selector.

`run.plan` does not execute trusted strategy-provider callbacks. `run.start` binds custom domains before target initialization and emits the resulting authoritative inventory through `run.event`.

Run results contain `usage` and a `decision_audit` reference, rather than embedding the entire provider transcript in a single RPC message. Fetch it with `decision.audit`; Python's `CoreDecisions.audit()` handles pagination. Reports retain the full transcript locally, and the HTML viewer loads its evidence chunks on demand. Deferred web requirements likewise use bounded provider batches without imposing a limit on total campaign evidence.

## Core → adapter

Respond to each request with the same ID. The adapter must not choose the next graph edge or silently change supplied data.

| Method | Contract |
| --- | --- |
| `target.initialize` | `{protocol_version:"1", model, config}` → `{capabilities:{...}}`. |
| `target.reset` | Restore the target's entry state. For web, open a fresh owned tab; hooks restore backend fixtures. |
| `target.execute` | `{edge, input, fields, destination, states}`. Perform the edge's business intent using literal data. Return when observation can assess the outcome. |
| `target.observe` | Return arbitrary JSON evidence of the actual target state. |
| `target.evaluate` | Only with `evaluator:"adapter"`: `{observation, expected, input, violations, ...}` → `{observed, checks}`. Independently identify state before comparing expectations. Rust applies the final verdict. Web sends decision questions through nested core calls. |
| `target.audit` | Web/adapter evaluator: `{records, rules}` → deferred global rule checks from accumulated observations. |
| `target.hook` | `{event, context}`. Bind lifecycle events to the implementation language. |
| `target.strategy` | `{context, default}` → declarative Hegel domain, when `custom_strategies:true`. |
| `target.close` | `{keep_open}`. Release owned resources; runs after all terminal outcomes. |
| `run.event` | `{type, data}`. Types: `planned`, `test.begin`, `step`, `step.result`, `case`, `test.end`. Return `screenshot`, `screenshot_at`, `screenshot_error`, `trace_range`, `decision_range`, `replay_file` or `attachments` from `case` or `test.end` to enrich the report. Inputs and verdicts remain authoritative in Rust. |
| `target.checkpoint` | Optional `{state}` → opaque isolated checkpoint handle. |
| `target.restore` | Optional `{checkpoint}`. Restore the full fixture, including application/backend state. |

Checkpoint calls are enabled only when initialization returns `capabilities.isolated_checkpoints:true`. The runner prepares one source fixture per campaign, then restores and verifies it before each case. The web adapter advertises false. A DOM snapshot alone is insufficient. Restores do not fire reset hooks; the checkpoint adapter owns restoring its full fixture.

Checks are objects such as `{"scope":"state", "rule":"Invalid data is rejected.", "status":"met"}`. Statuses are `met`, `broken`, `uncertain`, and (for global requirements) `not_applicable`. Wrong identified state or broken requirements produce FAIL. Unknown state, unresolved state requirements, provider or target errors produce INCONCLUSIVE. Untouched selected tests are SKIPPED with the stopping reason. Rules that have not been demonstrated cannot be counted as verified coverage.

Hooks cover before/after run, walk, reset, state, transition and case. Context includes phase (`graph`, `setup`, `property`), walk, element, data, independent violations, result and error. After hooks run even when the wrapped operation fails; cleanup faults do not erase the primary defect.

## A browser-free adapter

```python
from testwalker.core import CoreClient, CoreDecisions

class ApiTarget:
    def __call__(self, method, params):
        if method == "target.initialize":
            self.config = params["config"]
            return {"capabilities": {"isolated_checkpoints": False}}
        if method == "target.reset":
            # Restore fixtures through your application's API.
            return self.reset_fixture()
        if method == "target.execute":
            # Bind the model's intent to your API operation.
            self.last_response = self.execute_intent(params["edge"], params["input"])
            return {}
        if method == "target.observe":
            return self.last_response  # e.g. status, headers, JSON body
        if method in {"target.hook", "run.event", "target.close"}:
            return {}
        raise RuntimeError(f"Unsupported operation: {method}")

# Supply implementations of reset_fixture/execute_intent for your API.
with CoreClient(handler=ApiTarget()) as core:
    CoreDecisions(core, api_key="your-jev-key")
    report = core.request("run.start", model=model_document,
                          target={"base_url": "http://localhost:3000"},
                          options={"evaluator": "core"})
```

With the core evaluator, Rust constructs state and requirement questions from JSON observations and calls Jev. Adapters for any language can use the same stdio contract; the bundled Python implementations cover web and stdio JSON-RPC targets.

A definite assertion or modeled defect can be returned as a JSON-RPC error whose `data` contains `{status:"FAIL", message, result}`. Transport failures and errors without a definite defect are INCONCLUSIVE. JSON-RPC errors preserve detailed evidence without converting uncertainty into a failed assertion.
