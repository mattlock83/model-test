"""Native Python property testing using Hypothesis, the engine behind Hegel."""

from contextlib import nullcontext
from copy import deepcopy
from decimal import Decimal

from hypothesis import HealthCheck, Phase, Verbosity, given, seed, settings
from hypothesis import strategies as st
from hypothesis.errors import FailedHealthCheck, Unsatisfiable

from .errors import Defect, Inconclusive
from .input_strategies import StrategyProvider, field_options
from .model import violations


class _InputLimitReached(BaseException):
    """Stop Hypothesis reproduction/shrinking without recording an unrun input."""


def boundary_samples(field):
    """Named, deterministic checks of declared constraints, with valid neighbors."""
    samples = [("valid example", field["example"]), ("blank", "")]
    if field.get("required", False):
        samples.append(("whitespace only", "   "))
    kind = field["type"]
    if kind in {"whole number", "number"}:
        for name in ("minimum", "maximum"):
            limit = Decimal(str(field[name]))
            for offset in (-1, 0, 1):
                value = limit + offset
                samples.append((f"{name} {offset:+d}", int(value) if value == int(value) else float(value)))
        if kind == "whole number":
            samples.append(
                ("fraction instead of whole number", str(Decimal(str(field["minimum"])) + Decimal("0.5")))
            )
        samples.append(("non-numeric text", "not a number"))
    elif kind in {"text", "email"}:
        for name in ("minimum length", "maximum length"):
            if name not in field:
                continue
            for offset in (-1, 0, 1):
                size = field[name] + offset
                if size < 0:
                    continue
                value = "x" * (size - 4) + "@e.t" if kind == "email" and size >= 5 else "x" * size
                samples.append((f"{name} {offset:+d}", value))
        samples.append(("surrounding whitespace", f"  {field['example']}  "))
        if kind == "email":
            samples.extend(
                [
                    ("email missing @", "missing-at"),
                    ("email missing domain suffix", "a@localhost"),
                    ("email contains whitespace", "a @example.test"),
                ]
            )
    else:
        samples.extend(("permitted choice", value) for value in field["options"])
        unknown = "unlisted choice"
        while unknown in field["options"]:
            unknown += "!"
        samples.append(("unlisted choice", unknown))
    seen = set()
    for reason, value in samples:
        # Equal numeric values produce the same form submission; text stays distinct.
        key = ("text" if isinstance(value, str) else "number", value)
        if key not in seen:
            seen.add(key)
            yield reason, value


def boundaries(field):
    return [value for _, value in boundary_samples(field)]


def strategy(field):
    partitions = st.sampled_from(boundaries(field))
    if field["type"] == "whole number":
        return st.one_of(st.integers(field["minimum"] - 1, field["maximum"] + 1), partitions)
    if field["type"] == "number":
        return st.one_of(
            st.floats(
                min_value=field["minimum"] - 1,
                max_value=field["maximum"] + 1,
                allow_nan=False,
                allow_infinity=False,
            ),
            partitions,
        )
    if field["type"] == "text":
        return st.one_of(
            partitions,
            st.text(
                alphabet=st.characters(categories=("L", "N", "P", "Zs")),
                max_size=field.get("maximum length", 100) + 1,
            ),
        )
    return partitions


def focused_strategy(field, partition, value, options):
    """Generate inside a constraint partition; the validator checks its meaning."""
    if options["strategy"] == "boundary":
        return st.just(value)
    radius, alphabet = options["radius"], options["alphabet"]
    if partition in {"minimum -1", "maximum +1"}:
        direction = -1 if partition == "minimum -1" else 1
        limit = Decimal(str(field["minimum" if direction == -1 else "maximum"]))
        return st.integers(1, radius).map(
            lambda distance: (
                float(limit + direction * distance)
                if field["type"] == "number"
                else int(limit + direction * distance)
            )
        )
    if partition.startswith(("minimum length", "maximum length")):
        length = len(value)
        if field["type"] == "email" and length >= 5:
            return st.text(alphabet=alphabet, min_size=length - 4, max_size=length - 4).map(
                lambda text: text + "@e.t"
            )
        return st.text(alphabet=alphabet, min_size=length, max_size=length)
    if partition == "whitespace only":
        return st.text(alphabet=" ", min_size=1, max_size=3 * radius)
    if partition in {"non-numeric text", "email missing @", "unlisted choice"}:
        return st.text(alphabet=alphabet, min_size=1, max_size=10 * radius)
    if partition == "email missing domain suffix":
        return st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=10 * radius).map(
            lambda text: text + "@localhost"
        )
    if partition == "email contains whitespace":
        return st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=10 * radius).map(
            lambda text: text + " @example.test"
        )
    if partition == "fraction instead of whole number":
        return st.integers(1, 9).map(lambda digit: str(Decimal(str(field["minimum"])) + Decimal(digit) / 10))
    return st.just(value)


def evaluate_case(fields, data, execute, record, *, source):
    """Record one attempted input, including raised hook errors and interruptions."""
    entry = {"input": deepcopy(data), "source": source, "violations": violations(fields, data)}
    try:
        result = execute(data, entry["violations"])
        entry.update(result)
        if result["status"] == "FAIL":
            raise Defect("The submitted business data violated a modeled requirement", result=result)
        if result["status"] != "PASS":
            raise Inconclusive("The property outcome could not be verified", result=result)
        return result
    except Defect as error:
        if isinstance(error.result, dict):
            entry.update(error.result)
        entry.update(status="FAIL", error=str(error))
        error.case = entry
        raise
    except BaseException as error:
        if isinstance(error, Inconclusive) and isinstance(error.result, dict):
            entry.update(error.result)
        entry.update(status="INCONCLUSIVE", error=str(error) or type(error).__name__)
        raise
    finally:
        record(entry)


def plan_cases(fields, *, cases=1, mode="focused", policy=None, data_set=None):
    """Exact boundary inputs and generated phases; never predict Hypothesis draws."""
    if type(cases) is not int or not 1 <= cases <= 200:
        raise ValueError("cases must be between 1 and 200")
    if mode not in {"all", "generated", "boundaries", "focused", "none"}:
        raise ValueError("Unknown input exploration mode")
    baseline = {name: field["example"] for name, field in fields.items()}
    planned = []
    if mode in {"all", "generated"}:
        for index, name in enumerate(fields):
            planned.append(
                {
                    "id": f"generated/{index}",
                    "kind": "generated",
                    "field": name,
                    "source": f"generated: {name}",
                    "max_examples": cases,
                }
            )
        planned.append(
            {
                "id": "generated/combined",
                "kind": "generated",
                "source": "generated: combined",
                "max_examples": cases,
            }
        )
    if mode in {"all", "boundaries", "focused"}:
        seen = set()
        for index, (name, field) in enumerate(fields.items()):
            for sample, (reason, value) in enumerate(boundary_samples(field)):
                data = {**baseline, name: value}
                key = tuple(("text" if isinstance(v, str) else "number", v) for v in data.values())
                if key in seen:
                    continue
                seen.add(key)
                planned.append(
                    {
                        "id": f"boundary/{index}/{sample}",
                        "kind": "boundary",
                        "field": name,
                        "source": f"boundary: {name} — {reason}",
                        "input": data,
                        "violations": violations(fields, data),
                    }
                )
                if mode == "focused":
                    options = field_options(policy or {}, data_set, name, cases)
                    phase = planned[-1]
                    phase.update(
                        id=f"focused/{index}/{sample}",
                        kind="generated",
                        source=f"focused: {name} — {reason}",
                        partition=reason,
                        strategy=options,
                        data_set=data_set,
                        max_examples=options["cases"],
                        boundary_input=phase.pop("input"),
                    )
    return planned


def exercise(
    fields,
    execute,
    record,
    *,
    random_seed=42,
    cases=1,
    mode="focused",
    shrink=True,
    phase_scope=None,
    planned=None,
    remaining_attempts=None,
    policy=None,
    data_set=None,
    strategy_provider=None,
):
    """Isolate dimensions, explore combinations, then cover each boundary explicitly.

    Uncertain infrastructure is latched: Hypothesis can never shrink it into a defect
    or spend more API calls retrying it. Only stable Defect exceptions are reduced.
    """
    planned = (
        plan_cases(fields, cases=cases, mode=mode, policy=policy, data_set=data_set)
        if planned is None
        else planned
    )
    strategy_provider = strategy_provider or StrategyProvider()
    baseline = {name: field["example"] for name, field in fields.items()}
    infrastructure = None
    last_failure = None

    def verify(data, source):
        nonlocal infrastructure, last_failure
        if infrastructure:
            raise infrastructure
        if remaining_attempts is not None and remaining_attempts() <= 0:
            raise _InputLimitReached()
        try:
            return evaluate_case(fields, data, execute, record, source=source)
        except Defect as error:
            last_failure = error
            raise
        except Exception as error:
            infrastructure = error
            raise

    def generated(domain, source, max_examples):
        @seed(random_seed)
        @settings(
            max_examples=max_examples,
            deadline=None,
            database=None,
            report_multiple_bugs=False,
            phases=[Phase.generate, Phase.shrink] if shrink else [Phase.generate],
            verbosity=Verbosity.quiet,
            suppress_health_check=[HealthCheck.too_slow],
        )
        @given(domain)
        def check(data):
            verify(data, source)

        try:
            check()
        except _InputLimitReached:
            if last_failure is None:
                # Passing exploration is bounded during planning. Only failure
                # reproduction/shrinking can need additional attempts here.
                raise Inconclusive("Property execution exceeded its selected input scope") from None
            last_failure.case["minimization_limited"] = True
            last_failure.case["minimization_note"] = (
                "The input limit stopped failure reproduction or shrinking; "
                "the recorded failing input is retained without a minimality guarantee."
            )
            raise last_failure from None
        except Exception as error:
            if infrastructure:
                raise infrastructure
            if isinstance(error, Defect):
                error.case = last_failure.case
                raise
            if isinstance(error, (Unsatisfiable, FailedHealthCheck)):
                raise Inconclusive(
                    f"Input strategy could not generate usable examples for {source}: {type(error).__name__}"
                ) from error
            raise Inconclusive(
                f"Property checks did not reproduce consistently: {type(error).__name__}"
            ) from error

    for phase in planned:
        with phase_scope(phase) if phase_scope else nullcontext():
            if phase["kind"] == "boundary":
                generated(st.just(phase["input"]), phase["source"], 1)
            elif "partition" in phase:
                name = phase["field"]
                reference = phase["boundary_input"][name]
                domain = focused_strategy(fields[name], phase["partition"], reference, phase["strategy"])
                context = {
                    "data_set": phase["data_set"],
                    "field_name": name,
                    "field": fields[name],
                    "partition": phase["partition"],
                    "reference": reference,
                    "options": phase["strategy"],
                    "violations": phase["violations"],
                }
                domain = strategy_provider.build(context, domain)
                # Keep the intended validity/violations stable, including while shrinking.
                # Rejected draws never reach the browser or spend Jev calls.
                domain = domain.filter(
                    lambda value, key=name, expected=phase["violations"]: (
                        violations(fields, {**baseline, key: value}) == expected
                    )
                )
                generated(
                    domain.map(lambda value, key=name: {**baseline, key: value}),
                    phase["source"],
                    phase["max_examples"],
                )
            elif "field" in phase:
                name = phase["field"]
                generated(
                    strategy(fields[name]).map(lambda value, key=name: {**baseline, key: value}),
                    phase["source"],
                    phase["max_examples"],
                )
            else:
                generated(
                    st.fixed_dictionaries({name: strategy(field) for name, field in fields.items()}),
                    phase["source"],
                    phase["max_examples"],
                )
