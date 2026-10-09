"""Seeded paths from the actual Rust GraphWalker CLI, with verified graph identity."""

import json
import os
import subprocess
from pathlib import Path

from .resources import native_binary

DEFAULT_BINARY = native_binary()


def parse_path(model, output, max_steps=200):
    by_name = {
        item["name"]: {**item, "kind": kind}
        for kind, items in (("state", model.states.values()), ("edge", model.edges.values()))
        for item in items
    }
    path = []
    for line in output.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        item = by_name.get(record.get("currentElementName"))
        if not item or record.get("currentElementId", item["id"]) != item["id"]:
            raise ValueError("GraphWalker returned an unknown or mismatched element")
        if record.get("modelId", model.graph["id"]) != model.graph["id"]:
            raise ValueError("GraphWalker returned a different model")
        if len(path) >= max_steps:
            raise ValueError("GraphWalker path exceeds the step budget")
        if path:
            previous = path[-1]
            if (
                previous["kind"] == item["kind"]
                or (item["kind"] == "edge" and item["sourceVertexId"] != previous["id"])
                or (item["kind"] == "state" and previous["targetVertexId"] != item["id"])
            ):
                raise ValueError("GraphWalker returned a disconnected path")
        path.append(item)
    if not path or path[0]["id"] != model.graph["startElementId"]:
        raise ValueError("GraphWalker must begin at the declared start state")
    if path[-1]["kind"] == "edge":
        if len(path) == max_steps:
            raise ValueError("The final destination requires another verification checkpoint")
        path.append({**model.states[path[-1]["targetVertexId"]], "kind": "state"})
    visited = {item["id"] for item in path if item["kind"] == "edge"}
    if 100 * len(visited) / len(model.edges) < model.business["coverage"]["edges"]:
        raise ValueError("GraphWalker did not plan the requested edge coverage")
    visited_states = {item["id"] for item in path if item["kind"] == "state"}
    if 100 * len(visited_states) / len(model.states) < model.business["coverage"]["states"]:
        raise ValueError("GraphWalker did not plan the requested state coverage")
    return path


def generate_path(model, model_path, seed=42, max_steps=200):
    if type(seed) is not int or not 1 <= seed <= 2147483647 or not 1 <= max_steps <= 10000:
        raise ValueError("Use a positive seed and a step budget from 1 to 10000")
    binary = os.environ.get("GRAPHWALKER_BIN", str(native_binary()))
    try:
        result = subprocess.run(
            [binary, "offline", "-g", str(Path(model_path).resolve()), "-s", str(seed), "-o"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    except FileNotFoundError as error:
        raise ValueError("GraphWalker is missing. Run: uv run model-test setup") from error
    except subprocess.CalledProcessError as error:
        raise ValueError(f"GraphWalker rejected the model: {error.stderr[-2000:]}") from error
    if len(result.stdout) > 4_000_000:
        raise ValueError("GraphWalker path exceeds the output budget")
    return parse_path(model, result.stdout, max_steps)
