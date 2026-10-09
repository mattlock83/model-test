# Exported business model contract

A model is the test specification. Describe **where the user can be, what they are trying to do, what must be true, and which data is permitted**. Do not describe selectors, clicks, typing sequences, page objects or code.

Start with [the smaller enquiry model](../models/feedback.json) or [the booking model](../models/booking.json). Both use the same Python framework.

## Graph structure

Supply one native GraphWalker JSON graph under `models`, with:

- A unique graph `id` and `name`.
- `vertices` describing business states, each with a unique `id` and `name`.
- `edges` describing journeys, each with a unique `id`, `name`, `sourceVertexId` and `targetVertexId`.
- `startElementId` naming the state visible at the entry URL.
- A native `generator`, normally `quick_random(edge_coverage(100))`. All seven native walk modes and chained expressions are supported, subject to their graph requirements and the framework’s verified coverage gate. `predefined_path` uses the graph-level `predefinedPathEdgeIds` array; weighted exploration uses native edge `weight` metadata.

Names must be unique across states and edges. The runner supports at most 100 states and 500 journeys. Native graph metadata such as layout can be retained, but native action scripts and guards are rejected: this business vocabulary does not execute application code.

The metadata below belongs under `properties.business` on the graph, state or journey. Unknown business keys are rejected so a misspelled rule cannot silently disappear. Version 2's `properties.test`, targets, action lists and assertion expressions are no longer supported.

## Graph-level business information

```json
{
  "version": 3,
  "purpose": "Visitors can reserve places for a workshop.",
  "entry path": "/",
  "coverage": {"edges": 100, "states": 100},
  "rules": [
    "Invalid details are rejected with useful explanations.",
    "Reviewing a booking does not confirm it."
  ],
  "data sets": {
    "Booking details": {
      "Number of places": {
        "description": "How many people will attend",
        "type": "whole number",
        "required": true,
        "minimum": 1,
        "maximum": 4,
        "example": 2
      }
    }
  }
}
```

`version`, `purpose` and nonempty `rules` are required. `entry path` defaults to `/`, `coverage` to 100% of edges and states, and `data sets` to none. Coverage percentages must be between 0 and 100. The native generator must plan enough edges for the requested coverage; execution must verify enough destinations. Raising the step budget alone does not change the native generator's stop condition.

Entry paths must begin with one slash and stay on the supplied origin. The target origin, API keys, budgets and test seed are runtime configuration, not model content. `--edge-coverage`, `--state-coverage` and `--generator` can override exploration policy without editing the file; effective settings are saved with the report.

## States

A state's metadata contains a `description` and nonempty list of `rules`:

```json
{
  "description": "A submitted booking is awaiting final confirmation, with a summary and total cost.",
  "rules": [
    "The summary matches the submitted business data after trimming surrounding spaces.",
    "The total in AUD equals the submitted number of places multiplied by 45.",
    "The customer can revise the details or confirm the booking."
  ]
}
```

Descriptions should distinguish states by meaning. For example, “Entering details without validation problems” differs from “Details rejected with explanations.” Two indistinguishable descriptions make verification uncertain.

Requirements should be observable. State-specific rules must be evidenced; only global rules can be judged not applicable. The state-identification request does not receive the expected destination. A wrong known state fails; unknown, ambiguous or low-confidence state identification is inconclusive. Rule verification is a separate request.

Global requirements can be deferred while their evidence is unavailable. Full-run PASS requires every global requirement to have been verified at an applicable checkpoint or in the final audit of accumulated observed scenarios. State requirements cannot be deferred. Requirements already known to be broken fail immediately.

Describe what can be established at the current checkpoint. For example, “A link to visitor information is available” checks the studio page; the journey to the visitor page verifies that the link reaches the required information. “The visitor can find information” ambiguously mixes those two checks.

An uncertain verdict from a batch gets at most one focused evaluation of the same requirement and observed evidence. This keeps the configured confidence threshold and never overrides a known failure. Reports retain both answers; persistent uncertainty stops the run.

Keep each statement atomic: prefer separate customer-name, email and quantity checks over one compound “all details match” rule. The framework inserts the literal submitted values into Jev’s questions automatically; no model templates are needed. Constraints already expressed by the data dictionary need not be duplicated as universal prose rules.

These are natural-language requirements interpreted by Jev. They are not JavaScript, Python, formulas, templates or a deterministic assertion language.

## Journeys

A navigation journey needs only business intent:

```json
{"intent": "Abandon this booking and return to the workshop introduction."}
```

Do not say “click the button with this label” or encode a sequence of browser operations. Jev discovers how to carry out the intent from the live page. The runner then verifies the edge's destination.

A data submission also names the data set and its rejection state:

```json
{
  "intent": "Submit the supplied booking details for review. Stop after the first acceptance or rejection.",
  "data set": "Booking details",
  "rejected at": "needs_correction"
}
```

`accepted at` defaults to the edge's destination. Acceptance and rejection must be different states. Every data journey automatically gets a property campaign; no explicit property-test loop or reset path is authored.

Nominal graph traversal uses each field's valid `example`. To model a specific rejection journey, supply a complete example and both outcomes:

```json
{
  "intent": "Submit the supplied booking details for review. Stop after the first acceptance or rejection.",
  "data set": "Booking details",
  "accepted at": "review",
  "rejected at": "needs_correction",
  "example": {
    "Customer name": "A",
    "Contact email": "missing-at",
    "Number of places": 0
  }
}
```

The edge must target `needs_correction` because these values violate the data dictionary. Model validation rejects scenarios whose data contradict their declared destination. Examples are literal values, not templates. Include every field in an override.

Campaigns with the same source state, data set and acceptance/rejection states are deduplicated. A correction journey from a different source gets its own campaign. For setup, the framework discovers a shortest path from the start using ordinary nominal journeys, including explicit rejected examples when necessary.

## Business data dictionary

Each field has a business name, `description`, `type` and valid baseline `example`. Field names and descriptions represent meaning, and need not match the site's visible wording. Jev chooses the observed control at runtime.

| Type | Business limits | Automatically explored |
| --- | --- | --- |
| `text` | Optional `minimum length`, `maximum length` | Empty, short/long boundaries, surrounding spaces, generated Unicode text |
| `email` | Optional text lengths; ordinary address with a dotted domain | Valid example, malformed addresses, empty and length partitions |
| `whole number` | Required integer `minimum` and `maximum` | Bounds, just outside bounds, integers, decimals, empty and nonnumeric strings |
| `number` | Required finite `minimum` and `maximum` | Bounds, neighboring values, generated finite floats, malformed strings |
| `choice` | Required nonempty `options` list | Permitted options, empty and an unlisted value |

Optional `required` defaults to false. Optional `trim spaces` defaults to true. Blank optional values are permitted. Number rules use decimal textual notation; whole numbers must contain integer digits, without a decimal point. Email rules are deliberately a small practical format check, not full RFC validation. Text length counts Unicode code points.

Numeric bounds are limited to ±1,000,000 and explicit text limits to 0–1000 characters. There are at most 20 data sets with 20 fields each; each data set must be used. Examples must satisfy the independent rules. These bounds constrain this prototype's input exploration.

Generation varies one field against valid baseline examples, then combines field strategies. With the default `--input-mode all`, explicit boundaries still run when `--cases` is small. Use `--input-mode generated`, `boundaries` or `none` to select a narrower scope; `--max-input-attempts` caps all attempts, including shrinking. Without a maximum text length, generation explores a bounded domain around 100 characters rather than an unbounded string space. This is sampled property testing, not exhaustive input enumeration.

The framework validates input against this dictionary, independently of the site's own validation messages. It requires acceptance for valid inputs, rejection for invalid ones, and visible explanations for the invalid fields. That lets it detect a site accepting five places where the business maximum is four.

Generated values are inserted literally. Before submission, the framework verifies all mapped controls, allowing only surrounding whitespace trimming when `trim spaces` permits it. Chrome's automatic trimming of email inputs therefore follows the declared business rule. Other substitutions, ambiguous mappings and impossible native dropdown values remain inconclusive. Fields outside the viewport are discovered by business meaning and reached by scrolling.

## External authoring and validation

Author models separately in GraphWalker, through its editor or MCP. This framework supplies no authoring commands. The exported JSON must carry this business metadata.

1. List the business states and their distinguishing evidence.
2. Connect them with goal-oriented journeys.
3. Write global and state-specific business rules in plain language.
4. Define form data meaning, permitted ranges and valid examples.
5. Connect submission journeys to acceptance and rejection states.
6. Run `uv run model-test plan --model your-model.json` to validate the model and inspect GraphWalker's route.
7. Run against the application with a Jev key; inspect inconclusive evidence and refine ambiguous business descriptions.

You still maintain a specification as requirements change. The model avoids application implementation details; the framework owns all browser interaction code. It cannot infer missing requirements or guarantee semantic judgments from an underspecified graph. Backend reset procedures and deterministic external checks can be supplied through optional [Python lifecycle hooks](hooks.md), explicitly loaded with `--hooks`. Hooks are never embedded in or loaded by the model.
