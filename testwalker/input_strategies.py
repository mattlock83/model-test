"""Input-generation policy, separate from business constraints and browser execution."""

import hashlib
import importlib.util
import inspect
import json
import sys
from copy import deepcopy
from pathlib import Path

from hypothesis.strategies import SearchStrategy


def validate_policy(policy, data_sets):
    policy = deepcopy({} if policy is None else policy)
    if not isinstance(policy, dict) or set(policy) - {"defaults", "data sets"}:
        raise ValueError("Input strategies must contain only defaults and data sets")

    def options(value):
        if not isinstance(value, dict) or set(value) - {"strategy", "cases", "radius", "alphabet"}:
            raise ValueError("Strategy options: strategy, cases, radius, alphabet")
        if value.get("strategy", "nearby") not in {"nearby", "boundary"}:
            raise ValueError("Input strategy must be nearby or boundary")
        for key, maximum in (("cases", 200), ("radius", 1000)):
            if key in value and (type(value[key]) is not int or not 1 <= value[key] <= maximum):
                raise ValueError(f"Strategy {key} must be an integer from 1 to {maximum}")
        alphabet = value.get("alphabet", "abcdefghijklmnopqrstuvwxyz")
        if not isinstance(alphabet, str) or not alphabet or len(alphabet) > 1000:
            raise ValueError("Strategy alphabet must contain 1–1000 characters")
        if any(not char.isalpha() for char in alphabet):
            raise ValueError(
                "Strategy alphabet must contain letters only; use a provider for other alphabets"
            )

    options(policy.get("defaults", {}))
    datasets = policy.get("data sets", {})
    if not isinstance(datasets, dict):
        raise ValueError("Strategy data sets must be an object")
    for name, fields in datasets.items():
        if name not in data_sets or not isinstance(fields, dict):
            raise ValueError(f"Unknown strategy data set or invalid field mapping: {name}")
        for field, config in fields.items():
            if field not in data_sets[name]:
                raise ValueError(f"Unknown strategy field: {name}.{field}")
            options(config)
    return policy


def field_options(policy, data_set, field, cases):
    return {
        "strategy": "nearby",
        "cases": cases,
        "radius": 1,
        "alphabet": "abcdefghijklmnopqrstuvwxyz",
        **policy.get("defaults", {}),
        **policy.get("data sets", {}).get(data_set, {}).get(field, {}),
    }


def load_policy(path, data_sets):
    return validate_policy(json.loads(Path(path).read_text()) if path else {}, data_sets)


class StrategyProvider:
    """Explicitly opted-in, trusted Python extension returning Hypothesis strategies."""

    def __init__(self, factory=None, metadata=None):
        self.factory = factory
        self.metadata = metadata

    @classmethod
    def load(cls, path):
        if path is None:
            return cls()
        path = Path(path).expanduser().resolve()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        spec = importlib.util.spec_from_file_location(f"testwalker_inputs_{digest}", path)
        if spec is None or spec.loader is None:
            raise ValueError("Strategy provider must be a Python module")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(spec.name, None)
            raise
        factory = getattr(module, "input_strategy", None)
        if not callable(factory) or inspect.iscoroutinefunction(factory):
            raise ValueError("Strategy provider must export synchronous input_strategy(context, default)")
        return cls(factory, {"path": str(path), "sha256": digest})

    def build(self, context, default):
        domain = self.factory(deepcopy(context), default) if self.factory else default
        if not isinstance(domain, SearchStrategy):
            raise TypeError("input_strategy must return a Hypothesis SearchStrategy")
        return domain
