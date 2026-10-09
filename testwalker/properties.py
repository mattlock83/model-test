"""Native Python property testing using Hypothesis, the engine behind Hegel."""

from contextlib import nullcontext
from copy import deepcopy

from hypothesis import HealthCheck, Phase, Verbosity, given, seed, settings
from hypothesis import strategies as st

from .errors import Defect, Inconclusive
from .model import violations


def boundaries(field):
    values = [field["example"], ""]
    kind = field["type"]
    if kind in {"whole number", "number"}:
        for limit in (field["minimum"], field["maximum"]):
            values.extend([limit - 1, limit, limit + 1])
        values.extend(["1.5", "not a number"])
    elif kind in {"text", "email"}:
        for length in (field.get("minimum length", 0), field.get("maximum length", 100)):
            for offset in (-1, 0, 1):
                size = max(0, length + offset)
                values.append("x" * (size - 4) + "@e.t" if kind == "email" and size >= 5 else "x" * size)
        values.append(f"  {field['example']}  ")
        if kind == "email":
            values.extend(["a@example.test", "missing-at", "a@localhost", "a @example.test"])
    else:
        values.extend(field["options"] + ["unlisted choice"])
    # Keep numeric 2 and string "2" distinct: textual representations can expose bugs.
    return list({(type(value), value): value for value in values}.values())


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


def plan_cases(fields, *, cases=3, mode="all"):
    """Exact boundary inputs and generated phases; never predict Hypothesis draws."""
    if type(cases) is not int or not 1 <= cases <= 200:
        raise ValueError("cases must be between 1 and 200")
    if mode not in {"all", "generated", "boundaries", "none"}:
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
    if mode in {"all", "boundaries"}:
        for index, (name, field) in enumerate(fields.items()):
            for sample, value in enumerate(boundaries(field)):
                data = {**baseline, name: value}
                planned.append(
                    {
                        "id": f"boundary/{index}/{sample}",
                        "kind": "boundary",
                        "field": name,
                        "source": f"boundary: {name}",
                        "input": data,
                        "violations": violations(fields, data),
                    }
                )
    return planned


def exercise(fields, execute, record, *, random_seed=42, cases=3, mode="all", shrink=True, phase_scope=None):
    """Isolate dimensions, explore combinations, then cover each boundary explicitly.

    Uncertain infrastructure is latched: Hypothesis can never shrink it into a defect
    or spend more API calls retrying it. Only stable Defect exceptions are reduced.
    """
    planned = plan_cases(fields, cases=cases, mode=mode)
    baseline = {name: field["example"] for name, field in fields.items()}
    infrastructure = None
    last_failure = None

    def verify(data, source):
        nonlocal infrastructure, last_failure
        if infrastructure:
            raise infrastructure
        try:
            return evaluate_case(fields, data, execute, record, source=source)
        except Defect as error:
            last_failure = error.case
            raise
        except Exception as error:
            infrastructure = error
            raise

    def generated(domain, source):
        @seed(random_seed)
        @settings(
            max_examples=cases,
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
        except Exception as error:
            if infrastructure:
                raise infrastructure
            if isinstance(error, Defect):
                error.case = last_failure
                raise
            raise Inconclusive(
                f"Property checks did not reproduce consistently: {type(error).__name__}"
            ) from error

    for phase in planned:
        with phase_scope(phase) if phase_scope else nullcontext():
            if phase["kind"] == "boundary":
                verify(phase["input"], phase["source"])
            elif "field" in phase:
                name = phase["field"]
                generated(
                    strategy(fields[name]).map(lambda value, key=name: {**baseline, key: value}),
                    phase["source"],
                )
            else:
                generated(
                    st.fixed_dictionaries({name: strategy(field) for name, field in fields.items()}),
                    phase["source"],
                )
