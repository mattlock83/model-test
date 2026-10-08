# model-test

A small framework for testing a website from a **GraphWalker JSON model**. The model contains the journeys, semantic browser actions, expected states, input rules and property tests. There are no website-specific test functions, selectors or page objects to maintain outside it.

GraphWalker plans traversal. Playwright MCP operates the browser. Hegel generates and shrinks form inputs. OpenAI Decisions resolves controls when an explicit intent needs interpretation and judges the observed state at graph checkpoints.

This is a prototype with a bounded declarative vocabulary: you still maintain the model's requirements and action descriptions. A bare graph does not supply enough information to infer correct application behavior. Coverage describes that model and the exercised inputs; it is not proof that the website has no bugs.

## Run the demos

Prerequisites: **Node.js 22+**, **Git**, and **Google Chrome**. Setup uses an installed Rust toolchain or downloads one into `.tools/` without modifying shell profiles.

```sh
npm install
npm run setup
npm run demo:offline
npm run demo:feedback
npm run demo:bug
```

The first two demos exercise separate plain HTML applications through the same framework. The bug demo deliberately accepts five seats where the booking model allows four; its expected outcome is `FAIL` with exit code `1`.

Setup builds the official Rust GraphWalker CLI at the revision in [graphwalker.lock.json](graphwalker.lock.json), following the [GraphWalker installation guide](https://graphwalker.github.io/graphwalker-rs/getting-started.html). To use an existing CLI, set `GRAPHWALKER_BIN`. Set `PLAYWRIGHT_EXECUTABLE_PATH` if Chrome needs an explicit executable path.

Offline mode needs no API key or LLM. The JavaScript framework calls the Playwright MCP server directly. Controls must have unambiguous accessible names and states must have sufficient deterministic match assertions.

For live Decisions checks:

```sh
cp .env.example .env
# Set OPENAI_API_KEY in .env.
npm run demo
```

The default endpoint is `https://api.openai.com/v1/decisions`, and the default configured model is `gpt-6-luna`. Live mode requires access to that endpoint; API errors do not trigger an offline fallback. Offline runs and mocked contract tests do not verify live API availability.

## Test your own site

Start with [models/booking.json](models/booking.json) or the smaller [models/feedback.json](models/feedback.json). Change the graph and its `properties.test` metadata, then run:

```sh
npm run test:model -- --model my-model.json --url http://localhost:3000

# Deterministic checks only:
npm run test:model -- --model my-model.json --url http://localhost:3000 --offline
```

`--url` supplies `baseUrl` for the model's `startUrl`, such as `"{{baseUrl}}/"`. The framework command does not start your application. The bundled demo commands start and stop their own local server.

A model edge declares an action instead of calling a handwritten function:

```json
{
  "id": "open_form",
  "name": "open_form",
  "sourceVertexId": "home",
  "targetVertexId": "form",
  "properties": {
    "test": {
      "actions": [
        {
          "type": "click",
          "target": {
            "role": "button",
            "name": "Book a place",
            "intent": "Open the workshop booking form"
          }
        }
      ]
    }
  }
}
```

The runner reads a fresh accessibility snapshot, matches the accessible role and name, and uses the current MCP element reference. If the name is missing or ambiguous, the optional `intent` allows Decisions to choose among eligible visible controls. A missing intent, uncertain decision or unresolved target produces `INCONCLUSIVE`.

See [the model format](docs/model-format.md) for the complete supported contract, examples and validation rules.

## How a run works

1. Validate the model before opening the browser. Unknown metadata, unsupported actions and malformed assertions are errors.
2. Ask the native Rust GraphWalker CLI to generate a seeded path, including its native guards, actions and generator stopping condition.
3. Execute each declared edge through Playwright MCP and verify the destination against the model. Credit an edge only after its destination passes.
4. At property-suite self-loops, use Hegel to vary each field against a valid baseline, then vary combinations. Independently evaluate the model's input constraints to determine the required outcome.
5. Reset the application before every property attempt and shrink. Check deterministic state assertions and the required error messages. Add model-derived boundary cases and any explicit examples.
6. In live mode, ask Decisions to identify the state and assess the expected behavior at graph checkpoints. Record evidence, verified coverage and the final result.

Hegel uses deterministic assertions for reproducible shrinking. Decisions can still help resolve browser controls during a property attempt. Property setup paths are explicit, unconditional model edges; their replays do not count toward graph coverage or replay GraphWalker's native machine state.

The runner supports accessible clicks, text entry, selection, checked state, key presses and HTTP(S) navigation. Custom widgets that cannot be operated through those primitives need a framework extension. It does not invent arbitrary multi-step journeys, execute JavaScript embedded in test metadata, or automatically discover a site's requirements.

## Cost and provider configuration

Exact accessible-name matches cost no API calls. Decisions receives bounded choices for ambiguous controls and separate state/pass questions at checkpoints. Requests are cached within the run; cache keys ignore changing MCP references but retain the page evidence. The default call limit is **80**, and the default confidence threshold is **0.85**.

```sh
npm run test:model -- --model my-model.json --url http://localhost:3000 \
  --max-calls 30 --threshold 0.9
```

Reports include API calls, cache hits and reported token usage. The call limit bounds requests, not a dollar amount. Model pricing and evidence size determine actual cost.

| Environment variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | OpenAI authentication. |
| `OPENAI_DECISION_MODEL` | Override the default model. |
| `DECISION_ENDPOINT` | Full URL of an alternative endpoint implementing the OpenAI Decisions request/response contract. |
| `DECISION_API_KEY` | Key for that endpoint; takes precedence over `OPENAI_API_KEY`. |
| `DECISION_MODEL` | Provider model name; takes precedence over `OPENAI_DECISION_MODEL`. |

`--provider openai` selects the Decisions protocol adapter, including when a compatible endpoint is configured. **A native Jev adapter is not implemented.** An ordinary chat-completions endpoint is not compatible merely because it accepts an OpenAI-style API key.

Live mode sends browser accessibility evidence and the model's relevant expectations to the configured provider. Screenshots are saved locally; the current runner does not submit them for judgment.

## Results and replay

| Result | Exit code | Meaning |
| --- | --- | --- |
| `PASS` | `0` | Required checks, property suites and verified coverage completed. |
| `FAIL` | `1` | Observed behavior contradicted a deterministic requirement or received a sufficiently confident negative semantic judgment. |
| `INCONCLUSIVE` | `2` | The framework could not establish the result: for example ambiguity, low confidence, API/browser failure, exhausted budget or incomplete coverage. |

Each run writes `artifacts/<timestamp>-<provider>/` with `report.html`, `report.json`, the exact `model.json`, browser screenshots and `browser/mcp-transcript.json`. A captured property failure also writes `replay.json` containing its input and model hash. Initial model-validation errors exit before creating a run report.

Replay a property case using the saved model and the same application setup:

```sh
npm run test:model -- \
  --model artifacts/RUN/model.json \
  --url http://localhost:3000 \
  --offline \
  --replay artifacts/RUN/replay.json
```

To reproduce the bundled defect, keep its bug switch enabled:

```sh
npm run demo:bug -- --model artifacts/RUN/model.json --replay artifacts/RUN/replay.json
```

Replay checks the model hash and executes that property case; it does not claim full graph coverage. Hegel-generated failures may be reduced by shrinking. Explicit boundary/example failures are recorded directly. A recorded browser or provider problem is not evidence of an application bug.

Other commands:

```sh
npm start                                # Serve the bundled sites manually.
npm test                                 # Framework unit and contract tests.
npm run demo:offline -- --headed          # Watch the browser.
npm run demo:offline -- --seed 42 --cases 12
npm run test:model -- --help
```

`--cases` changes the mixed-input Hegel budget. Per-field budgets come from each model's `casesPerField`; boundaries, examples and shrinking can add attempts. Keeping the seed, saved model and dependency versions fixed helps reproduce generated runs, but browser timing and remote semantic judgments can still vary.
