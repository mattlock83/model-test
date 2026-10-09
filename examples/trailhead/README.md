# Trailhead: a larger model-driven example

Trailhead is a synthetic travel centre with **20 states, 148 journeys and three business data sets**. Browse two adventures, compare equipment and policies, manage a member profile, review and confirm a group booking, and cancel it for a full refund. There are no real payments, messages or customer records.

The exported [GraphWalker model](../../models/trailhead.json) is the maintained business specification. It contains no selectors, imports, scripts, browser actions or page objects. Author it independently in GraphWalker; this framework only consumes the exported file.

## Start with the short scenario

From the repository root, with your Jev key in `.env`:

```bash
./run-demo.sh --site trailhead --hooks examples/trailhead/hooks.py \
  --generator predefined_path --edge-coverage 0 --state-coverage 0 \
  --input-mode none --max-steps 1000 --max-calls 250
```

The visible Chrome launcher performs the existing dependency, GraphWalker and isolated-browser setup. The predefined route visits **14 distinct journeys and 14 states** in 29 graph elements:

1. Begin planning an adventure and select Marina pier using the custom pickup hook.
2. Reject an oversized party, correct it, review, and finally confirm the booking.
3. Reject a short cancellation reason, correct it, preview the full refund, and finally cancel.
4. Reject an invalid member email, correct it, save the profile, and return home.

This is a deliberately scoped smoke test: the zero coverage targets permit that short path, and `--input-mode none` disables the separate Hypothesis campaigns. Nominal valid and invalid data journeys still execute. A PASS covers the selected route and its applicable requirements; it does not claim 148-edge coverage. Every global rule must still be established. Jev uncertainty or an exhausted budget remains INCONCLUSIVE.

## Explore the complete graph and input space

```bash
# Every graph journey, without separate property campaigns.
./run-demo.sh --site trailhead --hooks examples/trailhead/hooks.py \
  --generator new_york_street_sweeper --input-mode none \
  --max-steps 1000 --max-calls 2500

# Full graph exploration plus small generated campaigns.
./run-demo.sh --site trailhead --hooks examples/trailhead/hooks.py \
  --input-mode generated --cases 1 --max-input-attempts 200 \
  --max-steps 1000 --max-calls 4000

# Add explicit boundaries and more generated cases; shrinking is enabled by default.
./run-demo.sh --site trailhead --hooks examples/trailhead/hooks.py \
  --input-mode all --cases 5 --max-input-attempts 1000 \
  --max-steps 1000 --max-calls 8000
```

These call limits are caps, not predicted usage or prices. The larger scope is substantially more expensive than the small booking demo. A small input-attempt cap can stop a campaign before completion; the framework will report that honestly. Add `--walks 2 --seed 42` for two separately reset graph walks using successive seeds. Inputs, resets and shrinking also use the shared run budget.

Inspect routes without an API key or browser:

```bash
uv run model-test validate --model models/trailhead.json
uv run model-test plan --model models/trailhead.json --max-steps 1000
uv run model-test plan --model models/trailhead.json --generator quick_random --max-steps 1000
uv run model-test plan --model models/trailhead.json --generator weighted_random \
  --edge-coverage 20 --state-coverage 50 --max-steps 1000
uv run model-test plan --model models/trailhead.json \
  --generator 'a_star(reached_vertex(cancellation_done))' --edge-coverage 0 --state-coverage 0
uv run model-test plan --model models/trailhead.json \
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

## Hook scenario 2: cancellation refund

Cancellation setup creates a real synthetic booking through the normal UI. Review and rejected reasons must leave the ledger unchanged. After final cancellation, the hook verifies the existing booking is cancelled, one refund returns the whole original charge (including the conservation gift), the supplied reason is recorded, and all reserved places are released. The backend uses confirmation tokens and booking IDs to make repeated requests idempotent.

`after_run` writes `trailhead-hook-audit.json` beside the normal report. It includes fixture resets, deterministic checkpoints, custom-picker selections, walk/case records and the final synthetic backend state. The normal report also records hook events and the loaded file hash.

## Custom select list: an explicit escape hatch

The booking form's **Departure pickup** widget is a bespoke list in a web component with an open shadow root. Its opener is visible to the generic browser action space; its choices are not native `<select>` options. This demonstrates a control requiring an integration when the generic navigator cannot operate it reliably.

The graph contains two business intents only: choose Marina pier, or choose City visitor centre. `before_transition` handles their stable edge IDs and evaluates a small widget-specific script. It opens the real list, clicks the actual requested option, and waits for the ordinary rendered pickup summary to reflect the choice. It does not assign internal application variables, mutate a hidden value, submit the form, bypass validation or override a verifier verdict. Jev still independently identifies and verifies the resulting state.

Selectors and shadow-DOM knowledge exist only in this optional hooks file. If the widget implementation changes, this integration may need maintenance; the business model does not. Without `--hooks`, the generic navigator may be unable to finish a pickup journey and should return INCONCLUSIVE.

Pickup is a pair of fixed navigation scenarios, not a generated form field. Hypothesis explores the three declared business data sets. The adventure and updates fields intentionally accept typed choices so invalid unlisted values can be submitted and rejected; a closed native or custom picker cannot necessarily represent such values. Do not substitute a permitted option for an impossible generated value or inject a hidden value just to force a pass.

## Demonstrate defects caught by hooks

Run the same smoke command with one of these prefixes:

```bash
# UI still says confirmed, but no inventory is reserved: the reservation hook must fail.
TRAILHEAD_DEMO_BUG=inventory ./run-demo.sh --site trailhead --hooks examples/trailhead/hooks.py \
  --generator predefined_path --edge-coverage 0 --state-coverage 0 \
  --input-mode none --max-steps 1000 --max-calls 250

# UI still says refunded, but the backend creates a duplicate refund: the refund hook must fail.
TRAILHEAD_DEMO_BUG=refund ./run-demo.sh --site trailhead --hooks examples/trailhead/hooks.py \
  --generator predefined_path --edge-coverage 0 --state-coverage 0 \
  --input-mode none --max-steps 1000 --max-calls 250
```

The environment flag is consumed by the fixture hook on each reset. It does not modify the graph. The existing booking demo's `--bug` flag remains separate. Remove the environment prefix to run the healthy backend.

## Preview or run against a separately hosted demo

```bash
uv run model-test serve --port 4184
# Open http://127.0.0.1:4184/trailhead/
```

In another terminal, with Chrome connected as described in the main README:

```bash
uv run model-test run --model models/trailhead.json --url http://127.0.0.1:4184 \
  --hooks examples/trailhead/hooks.py --headed --cdp-url http://127.0.0.1:9222 \
  --generator predefined_path --edge-coverage 0 --state-coverage 0 \
  --input-mode none --max-calls 250
```

The demo's local `/api/trailhead/` endpoints are fixtures for this synthetic application. They are not a proposed reset API for arbitrary sites. Hooks for another application should call that application's own test APIs with bounded timeouts. Each server instance has its own in-memory ledger; run one test job per server so resets do not interfere. The framework host binds to localhost.

## Validation evidence

The healthy predefined live run passed all **14 selected journeys, 14 states and five global requirements**, with both backend scenarios and the Marina picker verified, using **62 Jev calls** at the unchanged 0.85 threshold. Separate targeted live runs caught the missing inventory update (18 calls) and duplicate refund (30 calls) as FAIL through the hooks, despite successful UI acknowledgements.

The repository suite has **149 passing tests**, including native traversal, independent input boundaries, fixture HTTP endpoints, hook assertions, deliberate defects and the generic off-screen submission fix. Wheel and source builds include the site, model, hook file and guide. The full 148-journey browser run and the seven live property campaigns were not run in this validation; route planning and backend boundary tests are not substitutes for those live scopes.
