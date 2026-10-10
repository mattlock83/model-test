# Hypothesis input strategies

The default `--input-mode focused` runs every selected input check through Hypothesis. Testwalker translates the business model into separately planned constraint partitions; Hypothesis generates values inside each partition and shrinks reproducible failures. Each input still resets, navigates to the form, submits and verifies the result.

The defaults are deliberately small: **one example per partition**, one field changed at a time, and numeric out-of-range values one unit beyond the bound. String contents, malformed values and whitespace can vary. Exact checks such as blank, a permitted choice or a numeric limit use `st.just(...)`. Finite or singleton strategies can exhaust before reaching the requested example count; that is not inconclusive.

## Turn the amount of testing up or down

```bash
# Focused checks; one Hypothesis example per partition by default.
uv run testwalker demo --site trailhead --demo-hooks --headed \
  --generator new_york_street_sweeper --max-steps 1000 --max-calls 10000

# More examples within the same partitions.
uv run testwalker demo --site trailhead --demo-hooks --headed \
  --generator new_york_street_sweeper --max-steps 1000 --max-calls 10000 \
  --cases 3
```

Trailhead currently has 186 focused phases across seven campaigns. The default offers 186 exploration inputs before failure reproduction or shrinking. `--cases 3` offers up to 558; singleton phases use fewer. These are scope estimates, not promises that a run will reach every phase or fit within its Jev allowance.

Each partition is scheduled separately, so a small example count does not randomly omit a whole category. `--max-input-attempts` still selects work in model order and caps reproduction/shrinking. `--no-shrink` disables shrinking, but Hypothesis can still reproduce a failure. Uncertain browser or provider outcomes are latched and never shrunk into defects.

## Tune individual fields without Python

Pass `--input-strategies examples/trailhead/input-strategies.json`, or create a JSON file:

```json
{
  "defaults": { "strategy": "nearby", "radius": 1 },
  "data sets": {
    "Adventure party": {
      "Party size": { "cases": 3, "radius": 5 },
      "Traveller name": { "cases": 3, "alphabet": "abcdefghijklmnopqrstuvwxyz" }
    }
  }
}
```

Dataset and field names must exactly match the model. Configuration is checked before Chrome starts. Settings override built-in defaults and the CLI case count in this order: file `defaults`, then per-field settings. These options apply to focused mode only; supplying them with another mode is rejected.

| Setting | Effect |
| --- | --- |
| `strategy: "nearby"` | Default. Hypothesis generates within each selected constraint partition. |
| `strategy: "boundary"` | Use the partition's exact reference value through Hypothesis `st.just`. Useful to pin a field to exact boundary checks. |
| `cases` | Maximum generated examples per partition for this field, 1–200. Falls back to CLI `--cases`, default 1. |
| `radius` | 1–1000. Out-of-range numeric values are 1 through `radius` units beyond the limit. Also expands generated whitespace and malformed-text lengths. Declared length boundary checks remain at their specific lengths. |
| `alphabet` | Letters used for generated text-length cases, non-numeric strings and unlisted choices. Default: lowercase English letters. Built-in malformed email local parts use ASCII. Use a Python provider for other characters or domains. |

Raising radius broadens the invalid numeric domain. With radius above 1, a small sample is not a guarantee that the exact nearest invalid number is drawn. Numeric equality and valid-neighbor checks remain separate. Combined-field exploration remains available explicitly through `--input-mode generated` or `all`.

## Supply your own Hypothesis strategies

For cases not covered by JSON options, pass `--strategy-provider my_strategies.py`. This explicitly imports trusted local Python code. It is separate from lifecycle hooks and requires no runner or graph-model changes.

```python
from hypothesis import strategies as st


def input_strategy(context, default):
    if (context["data_set"] == "Adventure party"
            and context["field_name"] == "Traveller name"
            and context["partition"].startswith(("minimum length", "maximum length"))):
        size = len(context["reference"])
        return st.text(alphabet="éøñabc", min_size=size, max_size=size)
    return default
```

The function returns a Hypothesis `SearchStrategy` for **one field value**, not the whole form. The supplied `default` is the built-in strategy; it can be returned unchanged, transformed with `.map()`, or replaced. Hypothesis continues to own generation and shrinking. Returning a value, list or `None` is an error.

`context` contains `data_set`, `field_name`, the field's model definition (`field`), `partition`, the boundary `reference`, resolved `options`, and expected `violations`. It is a snapshot: mutating it does not change the model or scope. Partition names appear in `plan.json`, for example `minimum -1`, `maximum +1`, `minimum length -1`, `fraction instead of whole number`, `blank`, and `unlisted choice`.

Keep generation pure: do not call the browser, Jev or mutate backend state in a strategy. Use lifecycle hooks for those actions.

## Expected validity and reports

The independent model validator filters generated values to preserve the reference partition's complete violation list. Other fields remain at valid examples. This applies to custom strategies and shrinking too. Discarded draws are local; they do not become browser attempts or spend Jev calls. Overlapping constraints can mean one partition has several violations.

A strategy that cannot produce matching values stops inconclusively with a generation error. It does not silently skip the partition or claim a pass. For a valid partition, the application must accept the input; for an invalid partition, it must reject it as modeled. A generated invalid value is not itself a test failure.

`plan.json` records partition identities, reference inputs, expected violations, resolved strategy settings and selected example limits. References describe the intended partition; actual generated inputs are recorded during execution in JSON, JUnit and the viewer. Policy settings and a custom provider's path and SHA-256 are recorded for triage. Replay uses the saved concrete input without invoking a strategy provider.

`boundaries` remains an opt-in exact-value mode, now also executed by Hypothesis. `generated` runs broad per-field and combined strategies; `all` combines that broad exploration with exact boundary phases. Use `focused` for configurable per-constraint strategies.
