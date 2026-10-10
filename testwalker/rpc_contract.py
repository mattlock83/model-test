"""Declarative JSON-RPC bindings and deterministic checks, without expression evaluation.

Pointers use RFC 6901. A template reference is exactly ``{"$ref": "/input/Name"}``;
all other JSON is literal. Resolution preserves JSON types and returns independent
copies so an adapter cannot mutate the model or its captured fixtures.
"""

import copy
import math
import re


class ContractError(ValueError):
    """An invalid binding or a reference which cannot be resolved."""


_MISSING = object()
_SCOPES = {"input", "fixtures", "variables", "request", "response", "callback"}
_OPERATIONS = {"equals", "not_equals", "type", "exists", "length", "contains", "subset"}
_TYPES = {"null", "boolean", "number", "integer", "string", "array", "object"}


def _require(condition, message):
    if not condition:
        raise ContractError(f"Invalid RPC contract: {message}")


def _shape(value, allowed, required=(), location="object"):
    _require(isinstance(value, dict), f"{location} must be an object")
    _require(all(isinstance(key, str) for key in value), f"{location} keys must be strings")
    _require(
        not set(value) - set(allowed), f"{location} has unknown fields: {sorted(set(value) - set(allowed))}"
    )
    _require(
        not set(required) - set(value), f"{location} is missing fields: {sorted(set(required) - set(value))}"
    )


def _tokens(path):
    _require(isinstance(path, str) and (path == "" or path.startswith("/")), "path must be a JSON Pointer")
    _require(not re.search(r"~(?![01])", path), f"invalid JSON Pointer escape in {path!r}")
    return [] if not path else [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def pointer(value, path, *, default=_MISSING):
    """Read an exact JSON Pointer, rejecting negative and noncanonical array indexes."""
    current = value
    for token in _tokens(path):
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif (
            isinstance(current, list)
            and re.fullmatch(r"0|[1-9][0-9]*", token)
            and len(token) <= len(str(len(current)))
            and int(token) < len(current)
        ):
            current = current[int(token)]
        elif default is not _MISSING:
            return default
        else:
            raise ContractError(f"Unresolved JSON Pointer: {path}")
    return current


def _json(value, *, templates=False, depth=0):
    _require(depth <= 64, "JSON nesting exceeds 64 levels")
    if isinstance(value, dict):
        _require(all(isinstance(key, str) for key in value), "JSON object keys must be strings")
        if templates and "$ref" in value:
            _shape(value, {"$ref"}, {"$ref"}, "reference")
            tokens = _tokens(value["$ref"])
            _require(tokens and tokens[0] in _SCOPES, "references must name an observation scope")
        else:
            for child in value.values():
                _json(child, templates=templates, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            _json(child, templates=templates, depth=depth + 1)
    else:
        _require(value is None or type(value) in (str, int, float, bool), "expected a JSON value")
        _require(type(value) is not float or math.isfinite(value), "JSON numbers must be finite")


def resolve(value, context):
    """Resolve typed references in a request or expected value; missing references raise."""
    _json(value, templates=True)

    def visit(item):
        if isinstance(item, dict):
            if "$ref" in item:
                return copy.deepcopy(pointer(context, item["$ref"]))
            return {key: visit(child) for key, child in item.items()}
        if isinstance(item, list):
            return [visit(child) for child in item]
        return copy.deepcopy(item)

    return visit(value)


def validate_predicates(predicates):
    """Validate a conjunction of deterministic checks before any worker is started."""
    _require(isinstance(predicates, list) and 0 < len(predicates) <= 100, "checks need 1–100 predicates")
    for predicate in predicates:
        _shape(predicate, {"path", *_OPERATIONS}, {"path"}, "predicate")
        _tokens(predicate["path"])
        operations = set(predicate) & _OPERATIONS
        _require(len(operations) == 1, "predicate must have exactly one operation")
        operation = next(iter(operations))
        expected = predicate[operation]
        if operation == "type":
            _require(isinstance(expected, str) and expected in _TYPES, "unsupported JSON type")
        elif operation == "exists":
            _require(type(expected) is bool, "exists must be boolean")
        elif operation == "length":
            _shape(expected, {"min", "max", "exact"}, location="length")
            _require(bool(expected), "length must specify min, max or exact")
            _require(
                all(type(item) is int and item >= 0 for item in expected.values()),
                "length limits must be nonnegative integers",
            )
            _require(
                "exact" not in expected or len(expected) == 1, "exact length cannot be combined with bounds"
            )
            _require(expected.get("min", 0) <= expected.get("max", math.inf), "length bounds are inverted")
        else:
            _json(expected, templates=True)
    return predicates


def _equal(left, right):
    # Python considers True == 1; JSON boolean and numeric assertions must not.
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _subset(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _subset(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and all(
            any(_equal(value, item) for item in actual) for value in expected
        )
    return _equal(actual, expected)


def _matches_type(value, kind):
    return {
        "null": value is None,
        "boolean": type(value) is bool,
        "number": type(value) in (int, float),
        "integer": type(value) is int,
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }[kind]


def _compare(operation, actual, expected):
    if operation == "equals":
        return _equal(actual, expected)
    if operation == "not_equals":
        return not _equal(actual, expected)
    if operation == "type":
        return _matches_type(actual, expected)
    if operation == "length":
        return isinstance(actual, (str, list, dict)) and (
            len(actual) == expected["exact"]
            if "exact" in expected
            else expected.get("min", 0) <= len(actual) <= expected.get("max", math.inf)
        )
    if operation == "contains":
        if isinstance(actual, list):
            return any(_equal(item, expected) for item in actual)
        return isinstance(actual, (str, dict)) and isinstance(expected, str) and expected in actual
    if operation == "subset":
        return _subset(actual, expected)
    raise ContractError(f"Unsupported operation: {operation}")


def check(predicates, context):
    """Return all predicate outcomes with actual/expected values for triage.

    Missing actual values fail every operation except ``exists: false``. Missing
    expected references are reported as failed checks, never accepted as null.
    """
    validate_predicates(predicates)
    results = []
    for predicate in predicates:
        operation = next(key for key in predicate if key != "path")
        result = {
            "path": predicate["path"],
            "operation": operation,
            "expected": copy.deepcopy(predicate[operation]),
            "actual": None,
            "present": False,
            "passed": False,
        }
        try:
            try:
                actual = pointer(context, predicate["path"])
                result.update(actual=copy.deepcopy(actual), present=True)
            except ContractError:
                actual = _MISSING
            expected = resolve(predicate[operation], context)
            result["expected"] = expected
            if operation == "exists":
                result["passed"] = (actual is not _MISSING) == expected
            elif actual is _MISSING:
                result["error"] = "Actual JSON Pointer does not exist"
            else:
                result["passed"] = _compare(operation, actual, expected)
        except ContractError as error:
            result["error"] = str(error)
        results.append(result)
    return results


def _graph(model):
    if hasattr(model, "graph"):
        return model.graph
    if isinstance(model, dict) and "models" in model:
        models = model["models"]
        _require(isinstance(models, list) and len(models) == 1, "supply exactly one graph")
        return models[0]
    _require(isinstance(model, dict), "model must be an object")
    return model


def _request(request, location, *, method_only=False):
    _require(isinstance(request, dict), f"{location} must be an object")
    metadata = {"restart", "wait_for_exit"}
    for name in metadata:
        _require(name not in request or type(request[name]) is bool, f"{name} must be boolean")
    if "method" in request:
        _shape(request, {"method", "params", *metadata}, {"method"}, location)
        _require(
            isinstance(request["method"], str) and bool(request["method"].strip()),
            f"{location} method must be nonempty text",
        )
        _json(request.get("params", {}), templates=True)
    elif not method_only and "raw" in request:
        _shape(request, {"raw", *metadata}, {"raw"}, location)
        _require(
            isinstance(request["raw"], str) and "\n" not in request["raw"] and "\r" not in request["raw"],
            "raw request must be a single line of text",
        )
    elif not method_only and "envelope" in request:
        _shape(request, {"envelope", *metadata}, {"envelope"}, location)
        _json(request["envelope"], templates=True)
    else:
        raise ContractError(
            f"Invalid RPC contract: {location} needs a method"
            + ("" if method_only else ", raw line or envelope")
        )


def _rules(rpc, business, location):
    bindings = rpc.get("rules")
    _require(isinstance(bindings, dict), f"{location} rules must map business statements to predicates")
    _require(
        set(bindings) == set(business.get("rules", [])),
        f"{location} must bind every business rule exactly once",
    )
    for predicates in bindings.values():
        validate_predicates(predicates)


def _callbacks(value):
    _require(isinstance(value, dict), "callbacks must be an object")
    _json(value, templates=True)


def validate_rpc_model(model):
    """Validate RPC extensions on an otherwise validated business model.

    Returns the graph configuration. Fixtures are literal JSON. Callback handler
    profiles are adapter-defined JSON; their nested references are validated here.
    """
    graph = _graph(model)
    rpc = graph.get("properties", {}).get("rpc")
    _shape(
        rpc,
        {"version", "fixtures", "variables", "reset", "rules", "callbacks"},
        {"version", "reset", "rules"},
        "graph RPC",
    )
    _require(type(rpc["version"]) is int and rpc["version"] == 1, "RPC version must be 1")
    _require(isinstance(rpc.get("fixtures", {}), dict), "fixtures must be an object")
    _json(rpc.get("fixtures", {}))
    _require(isinstance(rpc.get("variables", {}), dict), "variables must be an object")
    _json(rpc.get("variables", {}))
    _request(rpc["reset"], "reset", method_only=True)
    _rules(rpc, graph.get("properties", {}).get("business", {}), "graph")
    if "callbacks" in rpc:
        _callbacks(rpc["callbacks"])
    for state in graph.get("vertices", []):
        spec = state.get("properties", {}).get("rpc")
        _shape(spec, {"match", "rules"}, {"match", "rules"}, f"state {state['id']}")
        validate_predicates(spec["match"])
        _rules(spec, state.get("properties", {}).get("business", {}), f"state {state['id']}")
    for edge in graph.get("edges", []):
        spec = edge.get("properties", {}).get("rpc")
        _shape(spec, {"request", "capture", "callbacks"}, {"request"}, f"edge {edge['id']}")
        _request(spec["request"], f"edge {edge['id']} request")
        if "capture" in spec:
            _require(isinstance(spec["capture"], dict), "capture must map variable names to JSON Pointers")
            for name, path in spec["capture"].items():
                _require(
                    isinstance(name, str) and bool(name.strip()),
                    "capture variable names must be nonempty strings",
                )
                _tokens(path)
        if "callbacks" in spec:
            _callbacks(spec["callbacks"])
    return rpc


def matching_states(model, context):
    """Classify independently of the intended destination; return every matching ID."""
    return [
        state["id"]
        for state in _graph(model)["vertices"]
        if all(item["passed"] for item in check(state["properties"]["rpc"]["match"], context))
    ]


def evaluate_rules(bindings, context):
    """Evaluate named business rules, preserving predicate evidence for reports."""
    result = {}
    for rule, predicates in bindings.items():
        outcomes = check(predicates, context)
        result[rule] = {"passed": all(item["passed"] for item in outcomes), "predicates": outcomes}
    return result
