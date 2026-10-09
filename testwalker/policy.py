"""Runtime exploration settings, independent of the authored specification."""

from .model import validate_model

COVERAGE_GENERATORS = {"quick_random", "random", "weighted_random", "shortest_all_paths"}
PATH_GENERATORS = {
    "new_york_street_sweeper": "new_york_street_sweeper()",
    "predefined_path": "predefined_path(predefined_path)",
}


def exploration_model(model, *, generator=None, edge_coverage=None, state_coverage=None):
    if generator is None and edge_coverage is None and state_coverage is None:
        return model
    # Validate a copy: CLI overrides must never rewrite the author's file.
    configured = validate_model(model.document)
    coverage = configured.business["coverage"]
    for key, value in (("edges", edge_coverage), ("states", state_coverage)):
        if value is not None:
            if not 0 <= value <= 100:
                raise ValueError("Coverage targets must be between 0 and 100")
            coverage[key] = value
    generator = generator or "quick_random"
    if generator in COVERAGE_GENERATORS:
        expression = (
            f"{generator}(edge_coverage({coverage['edges']:g}) and vertex_coverage({coverage['states']:g}))"
        )
    elif generator in PATH_GENERATORS:
        expression = PATH_GENERATORS[generator]
        if generator == "predefined_path" and not configured.graph.get("predefinedPathEdgeIds"):
            raise ValueError("predefined_path needs predefinedPathEdgeIds in the exported model")
    elif "(" in generator and generator.rstrip().endswith(")"):
        # Pass native expressions (including chains/stop conditions) to GraphWalker.
        # Shell execution is never used. Verified coverage remains an independent gate.
        expression = generator
    elif generator == "a_star":
        raise ValueError("A* needs a target: --generator 'a_star(reached_vertex(your_state_id))'")
    else:
        raise ValueError("Use a GraphWalker generator name or a complete native generator expression")
    configured.graph["generator"] = expression
    return validate_model(configured.document)
