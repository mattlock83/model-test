"""Keep observation evidence outside the selector-free business graph."""

import hashlib
import json
from collections import deque
from difflib import SequenceMatcher
from urllib.parse import urlsplit

from ..model import validate_model
from .scope import canonical_url


def prose(value):
    value = " ".join(str(value).split())
    for token in ("{{", "querySelector", "xpath=", "css=", "document.", "=>"):
        value = value.replace(token, " ")
    return value[:1500].strip() or "Unnamed page"


def identity(snapshot):
    # Ignore field values (passwords, CSRF tokens, generated IDs). Visible feedback
    # distinguishes form results that stay on the same URL.
    signature = {
        "url": canonical_url(snapshot["url"]),
        "title": snapshot["title"],
        "text": snapshot["text"],
        "headings": snapshot["headings"],
        "forms": [
            {"label": f["label"], "fields": [(c["label"], c["type"]) for c in f["fields"]]}
            for f in snapshot["forms"]
        ],
    }
    return "page_" + hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:16]


def build_model(pages, transitions, review):
    vertices = []
    for index, (key, page) in enumerate(pages.items()):
        label = prose(next(iter(page["headings"]), page["title"] or urlsplit(page["url"]).path))
        rules = [f"The visitor sees {label}."]
        for transition in transitions:
            if transition["target"] == key and transition["evidence"] == "form submission":
                before = pages[transition["source"]]["text"].splitlines()
                after = page["text"].splitlines()
                added = [
                    line
                    for tag, _, _, start, end in SequenceMatcher(a=before, b=after).get_opcodes()
                    if tag in {"insert", "replace"}
                    for line in after[start:end]
                    if line.strip()
                ]
                if added:
                    rules.append(prose("The page displays: " + " ".join(added)))
        for form in page["forms"]:
            fields = [
                prose(c["label"])
                for c in form["fields"]
                if c["visible"] and c["type"] not in {"submit", "hidden", "button", "reset"}
            ]
            if fields:
                rules.append(prose("The page offers a form with fields: " + ", ".join(fields)))
        buttons = list(dict.fromkeys(prose(b["label"]) for b in page["buttons"]))
        if buttons:
            rules.append(prose("The page offers these controls: " + ", ".join(buttons)))
        vertices.append(
            {
                "id": key,
                "name": f"Page {index + 1}: {label}",
                "properties": {
                    "business": {"description": f"The visitor is viewing {label}.", "rules": rules[:30]}
                },
            }
        )
    edges, seen = [], set()
    for transition in transitions:
        source, target, intent = transition["source"], transition["target"], prose(transition["intent"])
        key = source, target, intent
        if key in seen:
            continue
        seen.add(key)
        if len(edges) == 500:
            review.append("Graph edge limit reached; some observed transitions remain in the inventory only.")
            break
        edge_id = "journey_" + hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16]
        transition["edge_id"] = edge_id
        edges.append(
            {
                "id": edge_id,
                "name": f"Journey {len(edges) + 1}: {intent[:150]}",
                "sourceVertexId": source,
                "targetVertexId": target,
                "properties": {"business": {"intent": intent}},
            }
        )
    start = vertices[0]["id"]
    entry = urlsplit(next(iter(pages.values()))["url"])
    document = {
        "models": [
            {
                "id": "discovered_site",
                "name": "Discovered site — review required",
                "startElementId": start,
                "generator": "quick_random(edge_coverage(100))",
                "properties": {
                    "business": {
                        "version": 3,
                        "purpose": "Draft navigation model derived from observed UI; "
                        "review and add independent business rules before testing.",
                        "entry path": entry.path + ("?" + entry.query if entry.query else ""),
                        "rules": ["Each journey reaches its described destination."],
                        "data sets": {},
                    }
                },
                "vertices": vertices,
                "edges": edges,
            }
        ]
    }
    reachable, queue = set(), deque([start])
    while queue:
        state = queue.popleft()
        if state in reachable:
            continue
        reachable.add(state)
        queue.extend(e["targetVertexId"] for e in edges if e["sourceVertexId"] == state)
    unreachable = set(pages) - reachable
    if unreachable:
        review.append(
            "States unreachable from the entry: "
            + ", ".join(sorted(unreachable))
            + ". Add real journeys or split the model; no navigation was invented."
        )
    dead_ends = [v["id"] for v in vertices if not any(e["sourceVertexId"] == v["id"] for e in edges)]
    if dead_ends:
        review.append(
            "States with no outgoing journeys: "
            + ", ".join(dead_ends)
            + ". Review walk termination and coverage goals."
        )
    try:
        validate_model(document)
    except ValueError as error:
        review.append(str(error))
    return document
