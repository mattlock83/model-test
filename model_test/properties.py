"""Native Python property testing using Hypothesis, the engine behind Hegel."""

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


def exercise(fields, execute, record, *, random_seed=42, cases=3, mode="all", shrink=True):
    """Isolate dimensions, explore combinations, then cover each boundary explicitly.

    Uncertain infrastructure is latched: Hypothesis can never shrink it into a defect
    or spend more API calls retrying it. Only stable Defect exceptions are reduced.
    """
    if type(cases) is not int or not 1 <= cases <= 200:
        raise ValueError("cases must be between 1 and 200")
    if mode not in {"all", "generated", "boundaries", "none"}:
        raise ValueError("Unknown input exploration mode")
    baseline = {name: field["example"] for name, field in fields.items()}
    infrastructure = None
    last_failure = None

    def verify(data, source):
        nonlocal infrastructure, last_failure
        if infrastructure:
            raise infrastructure
        entry = {"input": data, "source": source, "violations": violations(fields, data)}
        try:
            result = execute(data, entry["violations"])
            entry.update(result)
            if result["status"] == "FAIL":
                last_failure = entry
                raise Defect(
                    "The submitted business data violated a modeled requirement", result=result, case=entry
                )
            if result["status"] != "PASS":
                raise Inconclusive("The property outcome could not be verified")
        except Defect as error:
            if "status" not in entry:
                entry.update(status="FAIL", error=str(error))
                last_failure = entry
                error.case = entry
            raise
        except Exception as error:
            infrastructure = error
            if isinstance(error, Inconclusive) and error.result:
                entry.update(error.result)
            entry.update(status="INCONCLUSIVE", error=str(error))
            raise
        finally:
            record(entry)

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

    if mode in {"all", "generated"}:
        for name, field in fields.items():
            generated(
                strategy(field).map(lambda value, key=name: {**baseline, key: value}), f"generated: {name}"
            )
        generated(
            st.fixed_dictionaries({name: strategy(field) for name, field in fields.items()}),
            "generated: combined",
        )
    if mode in {"all", "boundaries"}:
        for name, field in fields.items():
            for value in boundaries(field):
                verify({**baseline, name: value}, f"boundary: {name}")
