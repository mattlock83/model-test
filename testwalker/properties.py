"""Property inspection and callback bindings to native Hegel. No Python generator."""

from copy import deepcopy

from .core import CoreClient
from .errors import Defect, Inconclusive
from .input_strategies import StrategyProvider


def boundary_samples(field):
    with CoreClient() as core:
        return core.request("property.samples", fields={"Field": field})


def boundaries(field):
    return [value for _, value in boundary_samples(field)]


def plan_cases(fields, *, cases=1, mode="focused", policy=None, data_set=None):
    if type(cases) is not int or not 1 <= cases <= 200:
        raise ValueError("cases must be between 1 and 200")
    if mode not in {"all", "generated", "boundaries", "focused", "none"}:
        raise ValueError("Unknown input exploration mode")
    with CoreClient() as core:
        return core.request(
            "property.plan",
            fields=fields,
            data_set=data_set,
            options={"cases": cases, "input_mode": mode, "input_strategies": policy or {}},
        )


def exercise(
    fields,
    execute,
    record,
    *,
    random_seed=42,
    cases=1,
    mode="focused",
    shrink=True,
    planned=None,
    policy=None,
    data_set=None,
    strategy_provider=None,
):
    """Run Hegel against a synchronous callback; graph execution uses the Rust runner."""
    provider = strategy_provider or StrategyProvider()
    if planned:
        mode = (
            "focused"
            if "partition" in planned[0]
            else ("boundaries" if planned[0]["kind"] == "boundary" else "generated")
        )
        cases = max(p.get("max_examples", 1) for p in planned)
        if mode == "focused" and policy is None:
            policy = {"defaults": planned[0]["strategy"]}

    def handle(method, params):
        if method == "target.strategy":
            return provider.build(params["context"], params["default"])
        if method != "property.evaluate":
            raise Inconclusive(f"Unsupported property callback: {method}")
        entry = {key: deepcopy(params[key]) for key in ("input", "violations", "source")}
        try:
            entry.update(execute(deepcopy(entry["input"]), deepcopy(entry["violations"])))
            return entry
        except BaseException as error:
            entry.update(status="FAIL" if isinstance(error, Defect) else "INCONCLUSIVE", error=str(error))
            raise
        finally:
            record(entry)

    with CoreClient(handler=handle) as core:
        try:
            return core.request(
                "property.exercise",
                fields=fields,
                data_set=data_set,
                phase_ids=[p["id"] for p in planned] if planned is not None else None,
                options={
                    "seed": random_seed,
                    "cases": cases,
                    "input_mode": mode,
                    "shrink": shrink,
                    "input_strategies": policy or {},
                    "custom_strategies": bool(provider.factory),
                },
            )
        except Defect as error:
            error.case = error.result["case"]
            raise
