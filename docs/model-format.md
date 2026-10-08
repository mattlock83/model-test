# Model format

The input is a standard GraphWalker JSON document containing **one model**, extended with `properties.test` metadata. Native graph IDs, names, edges, guards, actions, requirements and generator syntax belong to GraphWalker. The framework's application-specific test contract lives in the metadata described here.

See [booking.json](../models/booking.json) for an end-to-end example and [feedback.json](../models/feedback.json) for a second site with different states and fields. Neither requires application-specific test JavaScript.

## Graph and execution

The model needs nonempty `vertices` and `edges`, unique element IDs and names, a `startElementId` naming a vertex, and connected edge endpoints. IDs and names can differ. All native elements together are limited to 10,000.

The Rust CLI generates the path before browser execution. Its `generator`, native `guard` expressions and native `actions` remain active during planning. The browser runner does not reinterpret them as browser operations. Declare browser operations separately under each edge's `properties.test`.

GraphWalker verbose data is available as the opaque value `{{graph.raw}}`. The framework does not parse it into typed variables. Browser form input is held independently in `input`; computed expectations are held in `derived`.

## Model metadata

Place these fields under `model.properties.test`:

| Field | Contract |
| --- | --- |
| `version` | Required; currently `1`. |
| `startUrl` | Required HTTP(S) URL after template resolution, usually `"{{baseUrl}}/"`. |
| `checkTimeoutMs` | Optional integer from 0 to 30000; defaults to 2000. Bounds polling for deterministic checkpoint assertions before declaring failure. Zero checks once. |
| `fields` | Optional map of form-field definitions. |
| `derived` | Optional ordered map of computed values. |
| `properties` | Optional map of Hegel property suites. Every suite must be referenced by an edge. |
| `coverage` | Optional `{ "edges": 100, "vertices": 100, "requirements": ["REQ-1"] }`. Percentages default to 100; requirement IDs are optional. |
| `demo` | Optional `{ "bugQuery": "bug=seats" }`, used only by the local demo launcher's `--bug` switch. |

A run must reach the requested verified edge/vertex percentages, any explicitly required requirement IDs, and every declared property suite. The graph's generator must plan enough traversal to satisfy those requirements. `--max-steps` limits the emitted path, including verification checkpoints; it does not silently truncate a longer plan.

## Vertex metadata

Each vertex needs `properties.test`:

| Field | Contract |
| --- | --- |
| `description` | Required nonempty static state description supplied as a choice to Decisions at every live checkpoint. Templates are rejected; use stable, distinguishing evidence. |
| `expectation` | Required nonempty statement of expected behavior; may interpolate submitted and derived values. |
| `match` | Optional array of deterministic assertions identifying this state. Required for offline identification and property outcome states. |
| `assertions` | Optional additional assertions that must hold in this state. |

```json
{
  "description": "The review page shows a summary and a Confirm booking button.",
  "expectation": "The review shows {{derived.customer}} and a total of ${{derived.total}}.",
  "match": [
    { "type": "visible", "target": { "role": "heading", "name": "Review your booking" } }
  ],
  "assertions": [
    { "type": "visible", "target": { "role": "button", "name": "Confirm booking" } },
    { "type": "text", "contains": "${{derived.total}}" }
  ]
}
```

A deterministic state match requires at least one executed assertion and every executed match assertion to pass. Exactly one state must match. Empty, fully skipped or overlapping matches cannot identify a state offline. In live graph checkpoints, Decisions can resolve otherwise unknown identification; it cannot override a failing deterministic assertion.

Checkpoint checks poll fresh accessibility evidence until the deterministic assertions pass or `checkTimeoutMs` expires. This allows an asynchronous page update to settle before a deterministic failure is reported. Polling does not call the decision provider; the semantic judgment runs once after polling, when deterministic checks permit it.

## Edge metadata and browser actions

Each edge needs `properties.test`:

| Field | Contract |
| --- | --- |
| `actions` | Nonempty list of browser primitives, or use `property` instead. |
| `input` | Optional map of declared field keys to values/expressions. Replaces the current input map before actions. |
| `clearInput` | Optional boolean; clears prior input and derived values before applying any new `input`. |
| `assertions` | Optional deterministic postconditions checked after executing this edge. |
| `property` | Name of a declared property suite. Requires a self-loop; cannot be combined with `actions`. |

A target uses an accessible `name`, an `intent`, or both; `role` optionally narrows candidates:

```json
{
  "input": { "name": "Ada Lovelace", "seats": "2" },
  "actions": [
    { "type": "fill", "target": { "role": "textbox", "name": "Full name" }, "value": "{{input.name}}" },
    { "type": "fill", "target": { "role": "textbox", "name": "Seats" }, "value": "{{input.seats}}" },
    { "type": "click", "target": { "role": "button", "name": "Review booking", "intent": "Review the entered booking details" } }
  ]
}
```

This is a metadata fragment: supply every field required by your own form and declarations.

| Action `type` | Fields | Behavior |
| --- | --- | --- |
| `click` | `target` | Click an eligible visible, enabled control. |
| `fill` | `target`, `value` | Fill a textbox, searchbox, spinbutton or combobox. |
| `select` | `target`, `value` | Select one value, or an array of values, in a combobox/listbox. |
| `check` | `target`, boolean `value` | Set checked state if needed; radios cannot be directly unchecked. |
| `press` | `key`, optional `target` | Press a key; when a target is supplied, click it first. |
| `navigate` | `url` | Navigate to an HTTP(S) URL; relative URLs resolve against `baseUrl`. |

The framework reads fresh MCP snapshot references for each target resolution. Exact accessible names ignore repeated whitespace and case. Exactly one matching eligible control is used without a provider call. If resolution needs interpretation, `intent` enables a Decisions choice among the observed controls. Low confidence and unresolved ambiguity are inconclusive. A role alone is not enough for an action target.

Targets cannot contain CSS/XPath selectors, fixed MCP refs, source code or arbitrary Playwright methods. Framework action lists are limited to 200 entries. `select` lists are limited to 100 values.

## Assertions

Assertions use visible accessibility evidence. Deterministic element targets require `role`, with optional `name`; they do not accept `intent`.

| `type` | Required fields | Check |
| --- | --- | --- |
| `visible` | `target` | At least one matching node exists. |
| `absent` | `target` | No matching nodes exist. |
| `text` | Nonempty `contains` | Visible text contains the value. |
| `notText` | Nonempty `contains` | Visible text excludes the value. |
| `value` | `target`, `equals` | Exactly one node has the expected scalar field value. |
| `count` | `target`, `equals` | Number of matching nodes equals a nonnegative integer. |

All types allow `description` and an optional condition such as `"when": { "exists": "input.name" }`. An absent variable skips that assertion. No other condition language is supported. Unknown assertion types and fields are errors even if their condition would skip them.

Text comparisons collapse whitespace but preserve case. Value comparisons preserve case and compare scalar representations, so an input value `"2"` equals numeric expectation `2`. Missing values and ambiguous value targets fail. Text containment does not establish that content is in a particular summary field; choose assertions strong enough for the behavior you need to verify.

Assertion arrays are limited to 200 entries. Missing variables in executed assertions are errors; use an explicit `when` for truly optional context.

## Fields and independent input rules

Field definitions live under `model.properties.test.fields`:

```json
{
  "seats": {
    "target": { "role": "textbox", "name": "Seats" },
    "constraints": {
      "type": "integer",
      "required": true,
      "trim": true,
      "min": 1,
      "max": 4,
      "pattern": "[0-9]+"
    },
    "generator": { "type": "integer", "min": 0, "max": 6 },
    "error": "Seats must be a whole number from 1 to 4."
  }
}
```

`target`, `constraints.type` and nonempty `error` are required. `generator` is optional. The error is the text required when that field violates its model constraints. The rule is independent of the application's own validation, so a site accepting an invalid input fails even when it claims success.

| Constraint | Meaning |
| --- | --- |
| `type` | `string`, `email`, `integer` or `number`. |
| `required` | Reject missing, null or empty input after optional trimming. Defaults to false; declare it for required fields. |
| `trim` | Trim strings before validation. |
| `minLength`, `maxLength` | Inclusive Unicode code-point length limits for strings/email; at most 1000. |
| `min`, `max` | Inclusive finite numeric limits for numbers/integers. |
| `pattern` | Full-value JavaScript regular-expression match, at most 256 characters; common nested-repeat patterns are rejected. |
| `message` | Optional text attached to a reported constraint violation. Field `error` controls the expected browser error text. |

Empty optional input is valid. Numeric strings must be finite decimals; `integer` checks numerical integrality, so `"2.0"` is integral. Add a pattern, as above, when the textual format must be digits only. Email uses the demo framework's explicit simple rule: one `@`, no whitespace, nonempty local part and a dotted domain with no empty segments. It is not a full RFC email validator.

## Generators and property suites

| Generator `type` | Fields |
| --- | --- |
| `integer` | Inclusive safe-integer `min`, `max`. |
| `number` | Finite `min`, `max`. NaN and infinity excluded. |
| `sample` | Nonempty `values` array of literal JSON scalars. |
| `text` | `minLength`, `maxLength` controlling generated strings. |
| `oneOf` | Nonempty `generators` array containing supported generator specifications. |

Numeric ranges may span at most 1,000,000. Text lengths are bounded at 1000, samples at 1000 values, `oneOf` at 20 alternatives and nesting at eight levels. If no generator is supplied, Hegel samples from model-derived boundary values and the baseline. Generators define the explored input domain; constraints define correctness.

A suite under `properties` declares the reset path and expected outcomes:

```json
{
  "booking-inputs": {
    "fields": ["name", "email", "seats"],
    "baseline": { "name": "Ada Lovelace", "email": "ada@example.com", "seats": "2" },
    "resetEdges": ["open_form"],
    "submit": [
      { "type": "click", "target": { "role": "button", "name": "Review booking" } }
    ],
    "validState": "review",
    "invalidState": "error",
    "casesPerField": 16,
    "cases": 12,
    "boundaries": true,
    "examples": [
      { "name": "Ada Lovelace", "email": "ada@example.com", "seats": "5" }
    ]
  }
}
```

Reference it from a self-loop edge using `"properties": { "test": { "property": "booking-inputs" } }`. The suite executes once per graph run even if the walk visits that self-loop again.

- `fields` is a nonempty list of distinct declared field keys. Baseline and examples must supply exactly those keys; other declared fields may belong to other suites.
- `baseline` must satisfy the suite's constraints. Per-field generation keeps all other fields at this baseline, preventing one invalid field from hiding another defect.
- `resetEdges` is an explicit connected path from the graph's initial vertex to the property self-loop. Empty is allowed when already at that vertex. Reset edges cannot contain guards or other property suites.
- `submit` contains browser actions after generic filling of the suite fields. Property filling currently uses the `fill` primitive; richer checkbox/select properties need a framework extension.
- `validState` and `invalidState` must be distinct vertices with deterministic match assertions, including at least one unconditional match assertion.
- `casesPerField` defaults to 16; mixed `cases` defaults to 12. Each is limited to 1–200. CLI `--cases` overrides only the mixed budget.
- `boundaries` defaults to true. Each field is tested independently at its derived boundaries after the generated phases. These include numeric limits and neighbors, string length limits and neighbors, emptiness and basic email partitions.
- `examples` optionally adds up to 200 complete literal inputs after boundaries.

Every attempt—including Hegel shrinking—reloads the starting URL and replays `resetEdges` before filling the form. Invalid inputs must reach the invalid state and show each violated field's declared error text. Deterministic assertions drive shrinking; uncertain infrastructure outcomes are not treated as property counterexamples. Guaranteed boundaries and explicit examples are direct checks and are not shrunk by Hegel.

Reset navigation is browser setup, not a second GraphWalker execution. Native graph data is cleared during it. These replays do not count as verified graph traversal. Keep property setup independent of native graph machine state. Reloading the URL does not clear cookies, browser storage or server-side records. The declared reset path must restore any application state the property depends on; a reload alone is sufficient only for applications whose relevant state resets on reload.

## Templates and expressions

Strings interpolate `{{input.name}}`, `{{derived.total}}`, `{{baseUrl}}` or `{{graph.raw}}`. An exact template preserves the original value type; embedding a value in a longer string converts a scalar to text.

Supported JSON expressions:

```json
{
  "customer": { "trim": { "var": "input.name" } },
  "total": { "multiply": [{ "number": { "var": "input.seats" } }, 45] },
  "totalWithFee": { "add": [{ "var": "derived.total" }, 5] }
}
```

This fragment belongs under `derived`. Definitions resolve in declaration order and can refer to earlier derived values. The only operators are `var`, `trim`, `number`, `multiply` and `add`; there is no JavaScript evaluation. Arithmetic is finite and accepts at most 64 operands. Expression depth is limited to 32.

Each derived value is calculated only when the input fields it references are present and satisfy their declared constraints, and any earlier derived values it references are available. Unrelated missing or invalid fields do not block it. Values depending on invalid or missing input remain unavailable; use an explicit existence condition for an assertion that legitimately depends on such optional derived context. Do not assume arithmetic derived from intentionally invalid input is available. Field and derived keys must start with a letter and contain only letters, digits, `_` or `-`; prototype-related names are forbidden. References to undeclared input or derived values fail validation. `graph.raw` is the only supported graph-data path.

## Decisions, confidence and failure behavior

The provider receives the observed accessibility snapshot as untrusted evidence. It chooses the current state independently of the expected-state assertion, and answers a separate pass predicate. A live checkpoint passes only when the expected state is selected, its confidence and selected-choice probability meet the threshold, and the pass probability also meets the threshold.

A sufficiently confident wrong state or low pass probability fails the test. Unknown states, intermediate probabilities, malformed responses, refusals, API failures and exhausted call budgets cannot become passes. Uncertain control selection stops as inconclusive.

Only `openai` and `offline` provider modes are implemented. `DECISION_ENDPOINT` can target an endpoint implementing the same Decisions protocol. Native Jev and generic chat-completions protocols need adapters; the framework does not claim they work automatically.

Strict validation rejects unknown fields throughout `properties.test`, unsupported operators, missing targets, invalid baselines and ignored action options before browser startup. GraphWalker still validates native graph syntax itself. Model changes remain the source of test maintenance: accessible names, distinguishing states and independently correct requirements must describe the intended application.
