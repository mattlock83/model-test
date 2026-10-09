# model-test

A Python CLI framework that tests a website from a **GraphWalker business model**. A business analyst describes states, journeys, expected outcomes and input rules. The framework discovers the controls at runtime using [Jev Ultrafast](https://github.com/browser-use/jev-ultrafast).

There are **no UI selectors, page objects, browser action lists or assertion expressions in the models**. The same runner handles the booking and enquiry demos without site-specific test code.

- **GraphWalker Rust** generates seeded paths through the graph.
- **Jev Ultrafast + Browser Harness** discover and operate observed browser controls.
- **TypeSafe Jev** independently identifies the current business state and judges its requirements.
- **Hypothesis** generates input combinations, covers boundaries and shrinks reproducible failures.

The property-testing layer now uses Python's Hypothesis directly. [Hegel is built on Hypothesis](https://hegel.dev/); its TypeScript wrapper was removed with the JavaScript runner. This version does not invoke the Hegel protocol or claim to retain the Hegel package.

## Use the framework

Author and export your model in GraphWalker independently, using its editor or MCP with your preferred assistant. **This package does not create or edit graphs and does not expose an authoring MCP.** It consumes one exported GraphWalker JSON model with the [business specification](docs/model-format.md): state descriptions, journey intents, expected outcomes and input constraints. A bare navigation graph cannot supply missing business expectations.

Install from this checkout (Python 3.12+, Chrome, and Rust 1.88+ for the native GraphWalker build):

```bash
uv tool install .
model-test setup
model-test validate --model /path/to/exported-model.json
model-test plan --model /path/to/exported-model.json
```

You can also build a wheel with `uv build --out-dir build`, then install that wheel. Models, demo HTML and the native dependency lock ship inside the package. Installed native tools live under `~/.cache/model-test`; set `MODEL_TEST_HOME` to override this. Source checkouts retain `.tools`. You can supply an existing compatible native CLI using `GRAPHWALKER_BIN`.

Put `TYPESAFE_API_KEY=your_key` in your working directory's `.env`, or export it in the environment. Existing environment values take precedence. Run against your own application:

```bash
model-test run \
  --model /path/to/exported-model.json \
  --url http://localhost:3000 \
  --env-file .env \
  --generator quick_random --edge-coverage 100 --state-coverage 100 \
  --walks 2 --max-steps 300 \
  --input-mode all --cases 5 --max-input-attempts 300 \
  --max-calls 1000 --headed
```

Chrome connects through Browser Harness. For a dedicated Chrome debugging endpoint, add `--cdp-url http://127.0.0.1:9222`; the macOS profile instructions below show how to start it. `--headed` focuses the owned tabs and retains the last one; it does not itself launch Chrome or enable debugging. No API credential is stored in the model or report by the framework.

The execution components are independent of model authoring:

```text
GraphWalker export + runtime options + Jev credentials + optional hooks
                              ↓
                 Business model validation
                              ↓
             Seeded GraphWalker paths → verified coverage
                              ↓
             Hypothesis input campaigns → counterexamples
                              ↓
           Jev browser navigation + independent outcome checks
                              ↓
                    HTML / JSON / replay reports
```

Browser navigation and outcome checks run at each graph checkpoint and input attempt. They are not a final check performed only after exploration.

| Control | Meaning |
| --- | --- |
| `--generator NAME_OR_EXPRESSION` | Choose a native walk mode or complete generator expression, including chains. Without overrides, preserve the exported generator. |
| `--edge-coverage`, `--state-coverage` | Required verified percentages. The other target retains its model value. Named coverage modes use both targets as their stop condition; an explicit native expression keeps its own stop condition. |
| `--walks N` | Repeat graph traversal from the start with seeds `seed`, `seed+1`, etc. (1–100). Each walk must plan the targets. Verified coverage is the union. |
| `--max-steps N` | Maximum graph elements per walk, including state checkpoints. A route exceeding this budget is inconclusive before browser execution. |
| `--input-mode all` | Generated examples and explicit boundaries (default). |
| `--input-mode generated` | Hypothesis-generated examples only. |
| `--input-mode boundaries` | Explicit boundary partitions only. |
| `--input-mode none` | Graph journeys only; reports explicitly say input campaigns were disabled. Nominal data submission journeys still execute. |
| `--cases N` | Generated examples per field and combined phase, per campaign (1–200, default 3). Shrinking can add attempts. |
| `--max-input-attempts N` | Total attempted property cases across campaigns, including shrinking and replay (default 1000). Exhaustion is inconclusive. |
| `--no-shrink` | Disable the shrinking phase. Hypothesis may still replay a failing example to confirm it. |
| `--max-calls N` | Hard cap on Jev requests across the whole run (default 1000). |
| `--max-actions N` | Maximum browser decision cycles per journey (default 25). |
| `--threshold N` | Required Jev confidence (default 0.85). |
| `--hooks hooks.py` | Explicitly load optional trusted Python lifecycle extensions. |

A small smoke run can use one walk and `--input-mode none`; deeper testing can add walks and `--input-mode all --cases 20`. Lower coverage targets change the selected scope, not the meaning of a failed business requirement. Global requirements must still be evidenced, so a short route can be inconclusive if it never exercises one. Runtime overrides never rewrite your input file; the report stores the effective model, targets and seeds.

Hooks can reset a backend, seed test data, call APIs, or make deterministic assertions around individual states and transitions. They are optional application code outside the graph. See [lifecycle hooks](docs/hooks.md) for events, ordering and failure behavior. Pure browser tests need no hooks; stateful applications often need a repeatable reset to make property testing meaningful.

## GraphWalker walk modes

All seven native modes are available through `--generator`, or through the generator already in the exported model. The framework passes expressions to the pinned native GraphWalker CLI. See [GraphWalker’s generator reference](https://graphwalker.github.io/graphwalker-rs/generators.html).

| Mode | Usage and constraints |
| --- | --- |
| `quick_random` | Steers toward unvisited elements; default for this demo. |
| `random` | Uniform random exploration; may require many more steps. |
| `weighted_random` | Uses the exported graph’s edge weights. |
| `shortest_all_paths` | Requires an Eulerian or semi-Eulerian graph. The expanded booking graph is not compatible. |
| `new_york_street_sweeper` | Covers all edges in a strongly connected graph; takes no stop condition. Works with the expanded demo. |
| `predefined_path` | Requires the exported model’s `predefinedPathEdgeIds` sequence. |
| `a_star(...)` | Requires a target expression, such as `a_star(reached_vertex(visit))`. |

Native stop conditions and chained generators are accepted. For example:

```bash
# Full exploration, then return to the home state.
uv run model-test plan --generator 'quick_random(edge_coverage(100)) a_star(reached_vertex(home))'

# Inspect a targeted route instead of demanding full coverage.
uv run model-test plan --generator 'a_star(reached_vertex(visit))' \
  --edge-coverage 0 --state-coverage 0

# Efficient complete edge coverage for this demo.
./run-demo.sh --generator new_york_street_sweeper --input-mode none
```

Coverage is an independent verification gate. A short A* or predefined route cannot claim full graph coverage; choose appropriate targets explicitly. During a live run, global business requirements must still be evidenced, even when the route is short. Native failures (such as a non-Eulerian graph) remain visible as inconclusive results. Guards, executable model actions and multiple linked models remain outside this framework’s business-model contract.

With seed 42, the expanded demo plans **149 elements with quick_random** and **143 with new_york_street_sweeper**, both covering 35 edges. Uniform random needed 2171 elements and weighted random needed 1503 in the same native check; raise `--max-steps` (for example to 3000) and the API-call budget for those modes. Other seeds can differ. Native planning counts are not evidence that browser tests passed.

## Run the demo

From this repository, with [uv](https://docs.astral.sh/uv/getting-started/installation/), Chrome and Rust 1.88+ installed:

```bash
uv sync
uv run model-test setup
# Create .env and add TYPESAFE_API_KEY=your_key.
uv run browser-harness --doctor
uv run model-test demo
```

On macOS, after setting `TYPESAFE_API_KEY` in `.env`, run everything with:

```bash
./run-demo.sh
# Demo options are forwarded:
./run-demo.sh --site feedback
./run-demo.sh --bug
```

The script synchronizes the locked Python dependencies, builds GraphWalker if needed, opens a separate visible Chrome profile, waits for its debugging endpoint and runs the demo with `--headed`. Each new property-test tab comes to the front. It can be invoked from any directory and keeps Chrome open afterward. Override its port with `MODEL_TEST_CHROME_PORT=9333 ./run-demo.sh`. API/provider setup remains in `.env`.

The demo starts its own local HTML server and opens an owned Chrome tab. Follow Browser Harness's instructions to connect Chrome and enable remote debugging if prompted. It uses Jev's browser integration; Playwright MCP and OpenAI Decisions are removed.

Setup builds the pinned official Rust CLI from [graphwalker.lock.json](graphwalker.lock.json), following the [GraphWalker installation guide](https://graphwalker.github.io/graphwalker-rs/getting-started.html). Skip setup if you set `GRAPHWALKER_BIN` to an existing compatible CLI. Jev's exact Git revision and Python dependencies are locked in `uv.lock`.

**A TypeSafe Jev key is required for browser testing. There is no local-only execution mode.** The bundled demos do not require an OpenAI key or a separate text-generation key: generated business values are typed literally. For other journeys that need free text without a declared data set, the optional `TEXT_MODEL_*` variables configure Jev's small text-helper pattern.

Other runs:

```bash
uv run model-test demo --site feedback
uv run model-test demo --bug
uv run model-test demo --seed 42 --cases 3 --max-calls 1000
```

The booking demo now has **Home, Workshops, Our studio and Visit** pages with a shared menu, plus the booking, review, rejection and confirmation states. The selector-free business model contains **8 states and 35 journeys**, including navigation away from each booking state, links between information pages, and booking from the catalogue. The enquiry demo remains a separate small fixture.

The booking demo's `--bug` switch deliberately permits five places although the model permits at most four. Its intended result is `FAIL` when Jev successfully navigates and verifies that case. The switch persists through the new menu links. A live run with `--generator new_york_street_sweeper --cases 1` passed all 35 journeys, all 8 states and 66 input attempts using 194 Jev calls at the default 0.85 confidence threshold. Both property campaigns completed and both global requirements were verified. This is one measured run; provider judgments and future runs can vary. Framework unit tests use mocked remote responses. Your key and an adequate call budget are required for live testing.

## Larger example: Trailhead

The [Trailhead example](examples/trailhead/README.md) adds **20 states, 148 journeys, three data sets and seven property campaigns**, including trip comparisons, equipment, policies, member profiles, bookings and full refunds. Its optional [hooks](examples/trailhead/hooks.py) reset a synthetic backend, verify inventory and refund transactions, and operate a bespoke departure-pickup list through its real widget events. Widget selectors remain outside the [business model](models/trailhead.json).

Start with its short predefined scenario in visible Chrome:

```bash
./run-demo.sh --site trailhead --hooks examples/trailhead/hooks.py \
  --generator predefined_path --edge-coverage 0 --state-coverage 0 \
  --input-mode none --max-steps 1000 --max-calls 250
```

This visits 14 journeys and 14 states, including explicit invalid examples and both backend hook scenarios. For full coverage, use `--generator new_york_street_sweeper --edge-coverage 100 --state-coverage 100 --max-steps 1000 --max-calls 2500`; enable property campaigns separately with `--input-mode generated` or `all`. The example guide covers budgets, weighted exploration, targeted A*, chained routes, and injected inventory/refund defects.

## macOS Chrome profile permission error

If Browser Harness reports `Operation not permitted` for Chrome's `DevToolsActivePort`, launch a separate visible Chrome with a dedicated profile and connect by URL. This uses Browser Harness's [documented isolated-profile connection](https://github.com/browser-use/browser-harness/blob/main/skills/browser-harness/references/install.md).

From the repository directory:

```bash
open -na "Google Chrome" --args \
  --user-data-dir="$PWD/.tools/chrome-profile" \
  --remote-debugging-port=9222 \
  --no-first-run --no-default-browser-check

BU_NAME=model-test BU_CDP_URL=http://127.0.0.1:9222 uv run model-test demo
```

Wait for the new Chrome window before running the second command. This test profile is ignored by Git. Use `uv run model-test demo --headed` to bring each new test tab to the front. Keep the separate Chrome instance open while testing. The named connection avoids reusing the harness daemon for your everyday browser.

## What the analyst maintains

See [models/booking.json](models/booking.json) and [models/feedback.json](models/feedback.json). They are native GraphWalker JSON graphs with `properties.business` metadata.

A journey describes the business operation:

```json
{
  "intent": "Submit the supplied booking details for review. Stop after the first acceptance or rejection.",
  "data set": "Booking details",
  "rejected at": "needs_correction"
}
```

A business field declares its meaning and permitted values:

```json
{
  "Number of places": {
    "description": "How many people will attend the workshop",
    "type": "whole number",
    "required": true,
    "minimum": 1,
    "maximum": 4,
    "example": 2
  }
}
```

A state describes what it means and its requirements, such as “The total in AUD equals the submitted number of places multiplied by 45.” These are prose requirements, not executable expressions. Business field names need not match visible labels: “Number of places” maps to the demo's “Seats” field through Jev's observed choices.

The analyst maintains this business specification; the framework supplies traversal, browser interaction, generation and reporting. A bare graph without meaningful state descriptions and requirements cannot define correct application behavior. The runtime accepts exported JSON. See the [input model contract](docs/model-format.md).

To test a different site:

```bash
uv run model-test run --model my-model.json --url http://localhost:3000
```

Start your application separately. The model's `entry path` is relative to the supplied site's origin. No application-specific Python functions are registered.

## How a run works

1. Validate the business vocabulary and have the actual GraphWalker CLI generate a seeded path.
2. For each edge, send its intent to the Jev navigator. Jev picks operations and targets from the **current observed action space**. Its browser executor checks freshness and occlusion before input.
3. At each destination, independently ask Jev which business state is visible, without providing the expected state or navigation goal. Separately judge the global and state requirements. A navigator's `DONE` answer is never a pass.
4. Credit an edge only after its destination passes verification. Defer global requirements until an applicable checkpoint or the final audit of accumulated scenario evidence. Every global requirement must be verified before a full-run PASS; an observed violation fails immediately.
5. For each distinct data journey, vary one field against valid examples, then vary combinations, then explicitly cover every declared boundary partition. The independent data dictionary determines whether acceptance or rejection is required.
6. Before each property attempt, open a new owned tab and discover a shortest setup path through nominal graph journeys. Optional reset hooks can restore backend fixtures before the start page opens. Setup paths and property attempts do not inflate graph coverage.
7. Let Hypothesis shrink reproducible generated failures; save the counterexample and evidence. Boundary failures are recorded directly.

The integration adapts Jev's `Browser`, `action_space`, choice validator and policy prompts rather than calling its public `Agent` unchanged. For data journeys, Jev maps business meanings to observed editable fields; Python supplies literal generated values, including blanks and invalid values. Before submission it verifies every value, allowing only whitespace trimming when the business dictionary permits it. It discovers and scrolls to fields outside the viewport. Field ordering is handled by the framework, and Jev identifies the submission control. The runner stops after the first submission so the agent cannot repair rejected input and hide a defect.

Generic accessibility observations distinguish alerts, validation messages and editable fields from ordinary page text. Jev classifies prose requirements internally so comparisons receive literal expected data while outcome checks receive relevant observed evidence. Unclear classification preserves the full comparison context; it never grants a pass. If several navigation controls appear equivalent, a bounded extra request checks their suitability individually. State and rule verdicts retain the configured confidence threshold. If a batched state-rule verdict is uncertain, the verifier isolates that same question once with the same observed evidence and threshold. Both answers are retained in the report. Known failures and provider refusals are not retried, and unresolved checks remain inconclusive. Last-submitted data is only a reference for explicit comparisons; empty or default-valued inputs can still establish that a form is available.

## Results and cost controls

| Result | Meaning | Exit code |
| --- | --- | --- |
| PASS | All checkpoints and input campaigns selected by the run configuration passed, and required verified coverage was reached. | 0 |
| FAIL | An observed state or requirement contradicts the business model. | 1 |
| INCONCLUSIVE | Navigation, data entry, provider confidence, setup, budget or infrastructure prevented verification. | 2 |

Semantic navigation and judging remain probabilistic, even though graph generation and input constraints are formal. These checks measure modeled coverage and sampled data; they do not prove the website correct. A changing or inconsistent judgment can prevent reliable shrinking and produce `INCONCLUSIVE`.

The default confidence threshold is **0.85**, maximum model calls **1000**, and maximum decision cycles per journey **25**. Operation and speculative target questions share a request; rule checks and field bindings are batched. Exact requests are cached within a run. No provider retries silently exceed the call cap. Reports count calls, cache hits and any provider-reported token usage. A request cap is not a dollar budget; actual cost and full-run call volume need measurement with your key.

`--cases` defaults to **3 generated cases per field plus 3 combined cases**, per distinct data journey. With the default `--input-mode all`, explicit boundaries, graph checkpoints, setup and shrinking add work. Use `--max-calls` to cap spending; hitting it is inconclusive, not a partial pass.

Every started run saves `artifacts/<timestamp>/report.html`, `report.json` and the exact `model.json`. Reports include verified coverage, observed browser evidence, rule judgments, raw Jev decision requests/responses, action trace and usage. Property failures also save `replay.json`. Unresolved state requirements name the exact rule and retain its answer in the report. In headed runs the last test tab remains open for inspection; the demo server stops when execution ends. Initial configuration/model errors can exit before a report is created. Screenshots are not recorded or sent; visible text and structured controls are sent to Jev. Artifacts are ignored by Git.

Replay one property counterexample:

```bash
uv run model-test demo --bug \
  --model artifacts/RUN/model.json --replay artifacts/RUN/replay.json

uv run model-test run --model artifacts/RUN/model.json \
  --url http://localhost:3000 --replay artifacts/RUN/replay.json
```

Replay requires the exact model hash and the same application setup. It checks one input and does not claim full coverage.

## Development and limits

```bash
uv run pytest
uv run ruff check model_test tests
uv run model-test plan
uv run model-test plan --model models/feedback.json
uv run model-test serve
```

`plan` validates and prints an actual GraphWalker traversal without starting a browser or calling an API. It is a planning command, not an offline browser test. Python tests cover contracts, Jev choices, literal input entry, confidence and budgets, Hypothesis shrinking, coverage and replay. Native GraphWalker tests run when its binary is installed.

The framework currently handles one graph, ordinary visible HTML controls and bounded data dictionaries. It rejects native action scripts, guards, browser targets and assertion DSLs. Cross-field constraints and arbitrary generator programs are outside this vocabulary; relational outcome requirements can be written as prose, but are not translated into constraint solvers. Rejection must be modeled as a distinguishable business state.

Jev's upstream limitations apply: shadow roots, frames, canvas, file uploads, pop-up tabs, nested scrolling and custom keyboard widgets are outside its MVP. If a control cannot accept an exact generated value (for example an unavailable dropdown choice), that attempt is inconclusive. Opening a new owned tab does not clear the existing Chrome profile, cookies or server data; use a test environment whose start state can be revisited. Account provisioning and backend resets are not inferred from prose; supply optional lifecycle hooks when needed.
