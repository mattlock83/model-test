# Trailhead: a larger model-driven example

Trailhead is a synthetic travel centre with **20 states, 148 journeys and three business data sets**. Browse two adventures, compare equipment and policies, manage a member profile, review and confirm a group booking, and cancel it for a full refund. There are no real payments, messages or customer records.

The exported [GraphWalker model](../../models/trailhead.json) is the maintained business specification. It contains no selectors, imports, scripts, browser actions or page objects. Author it independently in GraphWalker; this framework only consumes the exported file.

Read the [plain-English testing guide](../../docs/testing.md) for the workflow diagram, how property inputs are counted, setup/reset behaviour, and PASS/FAIL/INCONCLUSIVE/SKIPPED meanings. From a source checkout, prefix the `testwalker` commands below with `uv run`.

## Start with the short scenario

After installing the package, with GraphWalker and your Jev key in `testwalker.properties`:

```bash
testwalker demo --site trailhead --demo-hooks --headed \
  --generator predefined_path --edge-coverage 0 --state-coverage 0 \
  --input-mode none --max-steps 1000 --max-calls 250
```

The Python CLI starts or reuses isolated Chrome and serves the bundled example. GraphWalker is the executable configured in your properties file; dependencies are installed with the package. The predefined route visits **14 distinct journeys and 14 states** in 29 graph elements:

1. Begin planning an adventure and select Marina pier using the custom pickup hook.
2. Reject an oversized party, correct it, review, and finally confirm the booking.
3. Reject a short cancellation reason, correct it, preview the full refund, and finally cancel.
4. Reject an invalid member email, correct it, save the profile, and return home.

This is a deliberately scoped smoke test: the zero coverage targets permit that short path, and `--input-mode none` disables the separate Hegel campaigns. Nominal valid and invalid data journeys still execute. A PASS covers the selected route and its applicable requirements; it does not claim 148-edge coverage. Every global rule must still be established. Jev uncertainty or an exhausted call/action allowance remains INCONCLUSIVE. The full walk also exercises controls below the viewport; the walker observes their meaning and scrolls to them without adding selectors to the graph.

## Explore the complete graph and input space

```bash
# Every graph journey, without separate property campaigns.
testwalker demo --site trailhead --demo-hooks --headed \
  --generator new_york_street_sweeper --input-mode none \
  --max-steps 1000 --max-calls 2500

# Full graph exploration plus small generated campaigns.
testwalker demo --site trailhead --demo-hooks --headed \
  --input-mode generated --cases 1 --max-input-attempts 200 \
  --max-steps 1000 --max-calls 4000

# Full graph exploration plus just two selected property inputs.
testwalker demo --site trailhead --demo-hooks --headed \
  --generator new_york_street_sweeper \
  --edge-coverage 100 --state-coverage 100 --max-steps 1000 \
  --input-mode generated --cases 1 --max-input-attempts 2 \
  --no-shrink --max-calls 4000

# Recommended: full graph plus focused boundaries (the default input mode).
testwalker demo --site trailhead --demo-hooks --headed \
  --generator new_york_street_sweeper \
  --edge-coverage 100 --state-coverage 100 --max-steps 1000 \
  --max-calls 10000

# Broader randomized property scope (opt-in); shrinking is enabled by default.
testwalker demo --site trailhead --demo-hooks --headed \
  --generator new_york_street_sweeper \
  --edge-coverage 100 --state-coverage 100 --max-steps 1000 \
  --input-mode all --cases 20 --max-input-attempts 2000 \
  --max-calls 10000
```

The default focused mode selects 186 Hegel phases across seven campaigns, with one generated example per partition. Reproduction and shrinking can add attempts. Increase `--cases` or use `--input-strategies examples/trailhead/input-strategies.json` for per-field tuning. The same constraints are tested separately from different form states; identical reference inputs within a campaign are deduplicated. See [input strategies](../../docs/input-strategies.md).

The broader command selects all seven campaigns, with 186 explicit boundary cases and up to 640 generated inputs. `--cases 20` applies to each field phase and each combined phase. The 2,000-attempt limit selects all 826 planned inputs and leaves room for reproduction and shrinking; finite domains may use fewer. The Jev allowance is separate, and the runner stops at the first defect or unverifiable outcome.

These call limits are caps, not predicted usage or prices. The larger scope is substantially more expensive than the small booking demo. With `--input-mode generated --cases 1 --no-shrink`, the current Trailhead model offers 32 generation phases across seven campaigns (one phase per field plus a combined phase). `--max-input-attempts 2` selects the first two phases in model order before execution. Both may pass and produce an overall PASS when the graph checks and global rules also pass; the other 30 phases are outside this run, not skipped or inconclusive. This small sample does not cover every campaign or field. Use `--max-input-attempts 32` to select all 32 phases, or `--input-mode none` for graph-only exploration. Shrinking and failure reproduction share the total input-attempt limit; an observed failure remains FAIL if there is no allowance left to minimize it. Add `--walks 2 --seed 42` for two separately reset graph walks using successive seeds. Inputs, resets and shrinking also consume the shared Jev call allowance.

Inspect routes without an API key or browser:

```bash
testwalker validate --model models/trailhead.json # Source checkout or your exported model
testwalker plan --site trailhead --max-steps 1000
testwalker plan --site trailhead --generator quick_random --max-steps 1000
testwalker plan --site trailhead --generator weighted_random \
  --edge-coverage 20 --state-coverage 50 --max-steps 1000
testwalker plan --site trailhead \
  --generator 'a_star(reached_vertex(cancellation_done))' --edge-coverage 0 --state-coverage 0
testwalker plan --site trailhead \
  --generator 'quick_random(edge_coverage(100)) a_star(reached_vertex(home))' --max-steps 1000
```

With seed 42, the native street-sweeper plans 705 elements, quick random 801, and the chained route 803. The weighted partial route above plans 91. Other seeds can differ. Weighted full coverage exceeded the maximum 10,000-element runner budget in the checked seed; use street-sweeper for full coverage here. This graph is strongly connected but not Eulerian, so it is not an example for `shortest_all_paths`. Native route planning is separate from evidence of browser success.

## Model features exercised

| Feature | Use in this example |
| --- | --- |
| States and directed journeys | Shared five-destination menu from every workflow, cross-links, abandonment, revision and repeat rejection loops. |
| State and global requirements | Advertised prices, included equipment, selected pickup, draft versus committed status, exact summary values, totals and refund acknowledgements. |
| Native weights | Higher probabilities for workflow progression than incidental navigation; positive outgoing weights sum to less than one. |
| Native predefined route | Stable booking, cancellation and profile scenario under `predefinedPathEdgeIds`. |
| Native generators and stop conditions | Full street-sweeper, coverage-driven quick/weighted random, targeted A*, and a chain returning home. |
| Native requirement identifiers | `TH-01` through `TH-20` annotate the exported vertices for external authoring. Framework verdicts come from `properties.business.rules`; IDs are not a second assertion engine. |
| Valid and rejected examples | Oversized parties, malformed member email and too-short cancellation reason are ordinary graph journeys. |
| Business data types | Required text and email, whole-number party size, decimal conservation contribution, enumerated adventure and update choices, optional accessibility notes. |
| Property campaigns | Seven deduplicated source/data/outcome combinations: three booking forms, two profile forms, two cancellation forms. |
| Setup and reset | Shortest nominal setup paths, including rejected inputs and confirmed bookings needed by cancellation campaigns. |
| Lifecycle extensions | Run, walk, reset, transition, state and input-case events, shared scratch state, deterministic assertions and a separate hook audit. |

GraphWalker guards, executable actions, linked graphs and model-authored JavaScript remain outside the framework contract. Branches are explicit valid/rejected business journeys rather than guards. Backend code belongs to the example application; optional integration code belongs to the hook file.

## Hook scenario 1: reservation inventory

[hooks.py](hooks.py) uses `before_reset` to seed a clean backend ledger for each graph walk and every property attempt, including shrink/replay attempts. Repeated visits within a walk retain state; a new property case gets its own clean fixture. The synthetic capacity of 1,000 places per adventure accommodates repeated confirmations during long graph walks; the business limit remains six travellers per booking.

Around booking submission, `before_transition` saves the backend ledger. At a successfully verified review or rejection state, `after_state` checks that no booking, inventory or refund changed. At final confirmation it verifies exactly one new booking, the literal submitted values, an independently calculated charge in cents, and the exact inventory decrement. Reviewing never calls the booking API.

The policy-page state checks that these policies are explained to the visitor. A displayed policy does not prove backend behaviour; the review, confirmation and cancellation hooks check the actual ledger at their respective checkpoints.

## Hook scenario 2: cancellation refund

Cancellation setup creates a real synthetic booking through the normal UI. Review and rejected reasons must leave the ledger unchanged. After final cancellation, the hook verifies the existing booking is cancelled, one refund returns the whole original charge (including the conservation gift), the supplied reason is recorded, and all reserved places are released. The backend uses confirmation tokens and booking IDs to make repeated requests idempotent.

`after_run` writes `trailhead-hook-audit.json` beside the normal report. It includes fixture resets, deterministic checkpoints, custom-picker selections, walk/case records and the final synthetic backend state. The normal report also records hook events and the loaded file hash.

## Custom select list: an explicit escape hatch

The booking form's **Departure pickup** widget is a bespoke list in a web component with an open shadow root. Its opener is visible to the generic browser action space; its choices are not native `<select>` options. This demonstrates a control requiring an integration when the generic navigator cannot operate it reliably.

The graph contains two business intents only: choose Marina pier, or choose City visitor centre. `before_transition` handles their stable edge IDs and evaluates a small widget-specific script. It opens the real list, clicks the actual requested option, and waits for the ordinary rendered pickup summary to reflect the choice. It does not assign internal application variables, mutate a hidden value, submit the form, bypass validation or override a verifier verdict. Jev still independently identifies and verifies the resulting state.

Selectors and shadow-DOM knowledge exist only in this optional hooks file. If the widget implementation changes, this integration may need maintenance; the business model does not. Without `--hooks`, the generic navigator may be unable to finish a pickup journey and should return INCONCLUSIVE.

Pickup is a pair of fixed navigation scenarios, not a generated form field. Hegel explores the three declared business data sets. The adventure and updates fields intentionally accept typed choices so invalid unlisted values can be submitted and rejected; a closed native or custom picker cannot necessarily represent such values. Do not substitute a permitted option for an impossible generated value or inject a hidden value just to force a pass.

## Demonstrate defects caught by hooks

Run the same smoke command with one of these prefixes:

```bash
# UI still says confirmed, but no inventory is reserved: the reservation hook must fail.
TRAILHEAD_DEMO_BUG=inventory testwalker demo --site trailhead --demo-hooks --headed \
  --generator predefined_path --edge-coverage 0 --state-coverage 0 \
  --input-mode none --max-steps 1000 --max-calls 250

# UI still says refunded, but the backend creates a duplicate refund: the refund hook must fail.
TRAILHEAD_DEMO_BUG=refund testwalker demo --site trailhead --demo-hooks --headed \
  --generator predefined_path --edge-coverage 0 --state-coverage 0 \
  --input-mode none --max-steps 1000 --max-calls 250
```

The environment flag is consumed by the fixture hook on each reset. It does not modify the graph. The existing booking demo's `--bug` flag remains separate. Remove the environment prefix to run the healthy backend.

## Preview or run against a separately hosted demo

```bash
testwalker serve --port 4184
# Open http://127.0.0.1:4184/trailhead/
```

From a source checkout, in another terminal, with the properties file configured as described in the main README:

```bash
testwalker run --model models/trailhead.json --url http://127.0.0.1:4184 \
  --hooks examples/trailhead/hooks.py --headed \
  --generator predefined_path --edge-coverage 0 --state-coverage 0 \
  --input-mode none --max-calls 250
```

The demo's local `/api/trailhead/` endpoints are fixtures for this synthetic application. They are not a proposed reset API for arbitrary sites. Hooks for another application should call that application's own test APIs with bounded timeouts. Each server instance has its own in-memory ledger; run one test job per server so resets do not interfere. The framework host binds to localhost.

## Validation evidence

After the Rust-core migration, a full live run with `new_york_street_sweeper`, 100% edge/state targets, and the default focused Hegel mode passed **539 planned checks**: all **148 journeys, 20 states, seven property campaigns, 186 input attempts and five global requirements**. It used **946 Jev calls** at the unchanged **0.85** confidence threshold, with no failed, inconclusive or skipped checks. JUnit recorded zero failures/errors/skips, and the browser closed automatically. The hooks recorded 320 successful backend checkpoints and 47 custom pickup selections. This is one measured run; provider judgments may vary.

```bash
uv run testwalker demo --site trailhead --demo-hooks \
  --generator new_york_street_sweeper \
  --edge-coverage 100 --state-coverage 100 --max-steps 1000 \
  --input-mode focused --cases 1 --max-input-attempts 1000 \
  --max-calls 4000
```

The demo links validation errors to their input fields and distinguishes missing, short and long values in its feedback. Outcome assertions exclude irrelevant editable values; explicit comparisons retain them. Global audits split large campaigns into bounded batches, and native decision transcripts use pagination so report finalization does not exceed the JSON-RPC message limit.

The healthy predefined live run passed all **14 selected journeys, 14 states and five global requirements**, with both backend scenarios and the Marina picker verified, using **62 Jev calls** at the unchanged 0.85 threshold. Separate targeted live runs caught the missing inventory update (18 calls) and duplicate refund (30 calls) as FAIL through the hooks, despite successful UI acknowledgements.

The regression suite covers native traversal, independent input boundaries, fixture HTTP endpoints, hook assertions, deliberate defects, offscreen controls, and selected input scope. Wheel and source builds include the site, model, hook file and guide.

The full live street-sweeper run with generated inputs (`--cases 1 --max-input-attempts 32 --no-shrink`) passed all 148 journeys, 20 states, seven property campaigns and five global requirements, using 518 Jev calls at the unchanged 0.85 threshold. Its report contained 385 passed tests and no failures, inconclusive tests or skips. The backend audit recorded 136 successful checkpoints and 15 custom pickup selections. This validates one complete run; the formal dictionary and outcome metadata scope the final validation audit, while independent Jev verdicts still check the observed evidence.

A later live run with `--cases 1 --max-input-attempts 2 --no-shrink` passed all 148 journeys, 20 states, both selected property phases and five global requirements using 484 Jev calls. Its report contained 355 passed tests and no failures, inconclusive tests or skips. The other 30 generated phases were outside scope. These measured runs do not establish the outcome or cost of the larger comprehensive command.
