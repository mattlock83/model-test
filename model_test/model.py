"""A small business vocabulary, with no browser operations or expression language."""

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(f"Invalid business model: {message}")


def text(value):
    require(isinstance(value, str) and 0 < len(value.strip()) <= 4000, "expected nonempty text")
    require(
        not any(token in value for token in ("{{", "querySelector", "xpath=", "css=", "document.", "=>")),
        "describe business intent, without selectors, templates or executable expressions",
    )


def shape(value, allowed, required=()):
    require(isinstance(value, dict), "expected an object")
    require(set(value) <= set(allowed), f"unsupported fields: {set(value) - set(allowed)}")
    require(set(required) <= set(value), f"missing fields: {set(required) - set(value)}")


def rules(value):
    require(isinstance(value, list) and 0 < len(value) <= 30, "rules must contain 1–30 business statements")
    for rule in value:
        text(rule)


def violations(fields, data):
    """Evaluate the independent business data dictionary, never the website's validators."""
    result = []
    for name, field in fields.items():
        raw = data.get(name)
        value = raw.strip() if isinstance(raw, str) and field.get("trim spaces", True) else raw
        broken = []
        if value is None or value == "":
            if field.get("required", False):
                broken.append("required")
        elif field["type"] in {"text", "email"}:
            if not isinstance(value, str):
                broken.append("text required")
            else:
                if len(value) < field.get("minimum length", 0):
                    broken.append("too short")
                if len(value) > field.get("maximum length", math.inf):
                    broken.append("too long")
                if field["type"] == "email" and not re.fullmatch(r"[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+", value):
                    broken.append("valid email required")
        elif field["type"] == "choice":
            if value not in field["options"]:
                broken.append("must be one of the permitted choices")
        else:
            decimal = re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", str(value))
            if not decimal or isinstance(value, bool):
                broken.append("number required")
            else:
                number = float(value)
                if not math.isfinite(number):
                    broken.append("finite number required")
                elif field["type"] == "whole number" and not re.fullmatch(r"[+-]?\d+", str(value)):
                    broken.append("whole number required")
                if number < field.get("minimum", -math.inf):
                    broken.append("below minimum")
                if number > field.get("maximum", math.inf):
                    broken.append("above maximum")
        if broken:
            result.append({"field": name, "violations": broken})
    return result


@dataclass
class BusinessModel:
    document: dict
    graph: dict
    business: dict
    states: dict
    edges: dict
    digest: str

    @property
    def data_sets(self):
        return self.business.get("data sets", {})

    def example(self, edge):
        spec = edge["properties"]["business"]
        if "data set" not in spec:
            return {}
        return spec.get(
            "example", {name: field["example"] for name, field in self.data_sets[spec["data set"]].items()}
        )


def validate_model(document):
    document = copy.deepcopy(document)
    require(
        isinstance(document, dict)
        and isinstance(document.get("models"), list)
        and len(document["models"]) == 1,
        "supply exactly one GraphWalker graph",
    )
    graph = document["models"][0]
    require(isinstance(graph, dict), "graph must be an object")
    for key in ("id", "name", "startElementId"):
        text(graph.get(key))
    require(not graph.get("actions"), "native action scripts are outside the business model vocabulary")
    shape(graph.get("properties"), {"business"}, {"business"})
    business = graph["properties"]["business"]
    shape(
        business,
        {"version", "purpose", "entry path", "rules", "data sets", "coverage"},
        {"version", "purpose", "rules"},
    )
    require(business["version"] == 3, "this runner accepts business model version 3")
    text(business["purpose"])
    rules(business["rules"])
    path = business.get("entry path", "/")
    require(
        isinstance(path, str)
        and path.startswith("/")
        and not path.startswith("//")
        and "\\" not in path
        and ".." not in path.split("/"),
        "entry path must be a site-relative path",
    )
    coverage = business.setdefault("coverage", {"edges": 100, "states": 100})
    shape(coverage, {"edges", "states"}, {"edges", "states"})
    for value in coverage.values():
        require(
            type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100,
            "coverage must be between 0 and 100",
        )
    data_sets = business.get("data sets", {})
    require(isinstance(data_sets, dict) and len(data_sets) <= 20, "too many data sets")
    for name, fields in data_sets.items():
        text(name)
        require(isinstance(fields, dict) and 0 < len(fields) <= 20, "data sets need 1–20 business fields")
        for label, field in fields.items():
            text(label)
            shape(
                field,
                {
                    "description",
                    "type",
                    "required",
                    "example",
                    "minimum",
                    "maximum",
                    "minimum length",
                    "maximum length",
                    "trim spaces",
                    "options",
                },
                {"description", "type", "example"},
            )
            text(field["description"])
            kind = field["type"]
            require(kind in {"text", "email", "whole number", "number", "choice"}, "unsupported data type")
            for flag in ("required", "trim spaces"):
                require(flag not in field or type(field[flag]) is bool, f"{flag} must be boolean")
            require(type(field["example"]) in (str, int, float), "examples must be literal text or numbers")
            for key in ("minimum", "maximum"):
                if key in field:
                    require(
                        kind in {"whole number", "number"}
                        and type(field[key]) in (int, float)
                        and math.isfinite(field[key])
                        and abs(field[key]) <= 1_000_000,
                        "numeric limits must be finite and bounded",
                    )
                    require(
                        kind != "whole number" or type(field[key]) is int,
                        "whole-number limits must be integers",
                    )
            if kind in {"whole number", "number"}:
                require(
                    "minimum" in field and "maximum" in field and field["minimum"] <= field["maximum"],
                    "numeric fields need ordered minimum and maximum business limits",
                )
            for key in ("minimum length", "maximum length"):
                if key in field:
                    require(
                        kind in {"text", "email"} and type(field[key]) is int and 0 <= field[key] <= 1000,
                        "text lengths must be integers from 0 to 1000",
                    )
            require(
                field.get("minimum length", 0) <= field.get("maximum length", 1000), "inverted text limits"
            )
            if kind == "choice":
                require(
                    isinstance(field.get("options"), list) and 0 < len(field["options"]) <= 50,
                    "choices need 1–50 options",
                )
                for option in field["options"]:
                    text(option)
                require(len(set(field["options"])) == len(field["options"]), "duplicate choice options")
            else:
                require("options" not in field, "options only apply to choice fields")
        example = {key: field["example"] for key, field in fields.items()}
        require(
            not violations(fields, example), f"examples must satisfy the independent data rules for {name}"
        )
    require(
        isinstance(graph.get("vertices"), list) and 0 < len(graph["vertices"]) <= 100, "supply 1–100 states"
    )
    require(isinstance(graph.get("edges"), list) and 0 < len(graph["edges"]) <= 500, "supply 1–500 journeys")
    states, edges, names = {}, {}, set()
    for collection, destination in ((graph["vertices"], states), (graph["edges"], edges)):
        for element in collection:
            require(isinstance(element, dict), "graph elements must be objects")
            text(element.get("id"))
            text(element.get("name"))
            require(element["id"] not in states and element["id"] not in edges, "duplicate graph ID")
            require(element["name"] not in names, "duplicate graph name")
            require(
                not element.get("actions") and not element.get("guard"),
                "express state transitions and rules as business intent, not scripts",
            )
            names.add(element["name"])
            destination[element["id"]] = element
            shape(element.get("properties"), {"business"}, {"business"})
            spec = element["properties"]["business"]
            if destination is states:
                shape(spec, {"description", "rules"}, {"description", "rules"})
                text(spec["description"])
                rules(spec["rules"])
            else:
                shape(spec, {"intent", "data set", "example", "accepted at", "rejected at"}, {"intent"})
                text(spec["intent"])
    require(graph["startElementId"] in states, "startElementId must name a state")
    for edge in edges.values():
        require(
            edge.get("sourceVertexId") in states and edge.get("targetVertexId") in states,
            "journey endpoints must exist",
        )
        spec = edge["properties"]["business"]
        if "data set" in spec:
            require(spec["data set"] in data_sets, "journey refers to an unknown data set")
            accepted = spec.get("accepted at", edge["targetVertexId"])
            require(
                accepted in states and spec.get("rejected at") in states and spec["rejected at"] != accepted,
                "a data journey needs a distinct rejection state",
            )
            fields = data_sets[spec["data set"]]
            example = spec.get("example", {key: field["example"] for key, field in fields.items()})
            require(
                isinstance(example, dict) and set(example) == set(fields),
                "a scenario example must supply every business field",
            )
            require(
                all(
                    type(value) in (str, int, float) and len(str(value)) <= 2000 for value in example.values()
                ),
                "scenario data must contain literal values",
            )
            expected = spec["rejected at"] if violations(fields, example) else accepted
            require(edge["targetVertexId"] == expected, "scenario data contradicts the journey's destination")
            # Setup is discovered from nominal journeys. No reset script is authored.
            setup_path(graph, edges, edge["sourceVertexId"])
        else:
            require(
                not {"example", "accepted at", "rejected at"} & set(spec), "data options require a data set"
            )
    used_sets = {e["properties"]["business"].get("data set") for e in edges.values()}
    require(set(data_sets) <= used_sets, "every data set must be used by a journey")
    graph.setdefault("generator", "quick_random(edge_coverage(100))")
    text(graph["generator"])
    digest = hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return BusinessModel(document, graph, business, states, edges, digest)


def setup_path(graph, edges, target):
    queue = [(graph["startElementId"], [])]
    seen = set()
    while queue:
        state, path = queue.pop(0)
        if state == target:
            return path
        if state in seen:
            continue
        seen.add(state)
        for edge in edges.values():
            if edge["sourceVertexId"] == state:
                queue.append((edge["targetVertexId"], path + [edge]))
    raise ValueError("Invalid business model: a data journey must be reachable from the start")


def load_model(path):
    return validate_model(json.loads(Path(path).read_text()))
