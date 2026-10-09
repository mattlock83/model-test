import json

import pytest

from model_test import cli
from model_test.graphwalker import DEFAULT_BINARY, generate_path
from model_test.hooks import Hooks
from model_test.policy import exploration_model
from model_test.resources import asset


def test_coverage_overrides_are_copied_and_change_native_stop_policy(model):
    changed = exploration_model(model, generator="random", edge_coverage=50, state_coverage=70)
    assert changed.graph["generator"] == "random(edge_coverage(50) and vertex_coverage(70))"
    assert changed.business["coverage"] == {"edges": 50, "states": 70}
    assert model.business["coverage"] == {"edges": 100, "states": 100}
    assert changed.digest != model.digest
    assert exploration_model(model) is model


@pytest.mark.skipif(not DEFAULT_BINARY.exists(), reason="GraphWalker not installed")
@pytest.mark.parametrize("generator", ["random", "quick_random", "weighted_random"])
def test_native_graphwalker_accepts_runtime_coverage_policy(model, tmp_path, generator):
    configured = exploration_model(model, generator=generator, edge_coverage=50, state_coverage=60)
    path = tmp_path / "model.json"
    path.write_text(json.dumps(configured.document))
    steps = generate_path(configured, path)
    assert len({s["id"] for s in steps if s["kind"] == "edge"}) >= 2
    assert len({s["id"] for s in steps if s["kind"] == "state"}) >= 2


def test_validate_consumes_external_model_without_key_or_hooks(monkeypatch, capsys):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert cli.main(["validate", "--model", str(asset("models/feedback.json"))]) == 0
    assert "Valid:" in capsys.readouterr().out
    commands = cli.parser()._subparsers._group_actions[0].choices
    assert not {"mcp", "mcp-config", "example", "author"} & commands.keys()


def test_hooks_load_only_an_explicit_file_and_support_normal_python(tmp_path):
    path = tmp_path / "hooks.py"
    path.write_text("""from dataclasses import dataclass
@dataclass
class Record:
    value: int = 1
def before_run(ctx):
    ctx.scratch["value"] = Record().value
""")
    hooks = Hooks.load(path)
    from model_test.hooks import HookContext

    context = HookContext(None, "http://example.test")
    hooks.fire("before_run", context)
    assert context.scratch["value"] == 1
    assert len(hooks.digest) == 64 and hooks.path == str(path)
    assert Hooks.load(None).events == []


def test_async_hooks_are_rejected_before_a_run(tmp_path):
    path = tmp_path / "hooks.py"
    path.write_text("async def before_run(ctx):\n    pass\n")
    with pytest.raises(ValueError, match="synchronous"):
        Hooks.load(path)


def test_packaged_resources_and_external_tool_home(tmp_path, monkeypatch):
    from model_test import resources

    monkeypatch.setenv("MODEL_TEST_HOME", str(tmp_path))
    assert resources.native_binary().is_relative_to(tmp_path)
    assert asset("dist/index.html").exists()
    assert asset("models/booking.json").exists()
    assert json.loads(asset("graphwalker.lock.json").read_text())["revision"]


@pytest.mark.skipif(not DEFAULT_BINARY.exists(), reason="GraphWalker not installed")
@pytest.mark.parametrize("generator", ["shortest_all_paths", "new_york_street_sweeper", "predefined_path"])
def test_native_deterministic_modes_on_eulerian_model(model, tmp_path, generator):
    model.graph["predefinedPathEdgeIds"] = ["submit", "another", "submit_invalid", "correct"]
    configured = exploration_model(model, generator=generator)
    path = tmp_path / "model.json"
    path.write_text(json.dumps(configured.document))
    steps = generate_path(configured, path)
    assert {step["id"] for step in steps if step["kind"] == "edge"} == set(model.edges)


@pytest.mark.skipif(not DEFAULT_BINARY.exists(), reason="GraphWalker not installed")
@pytest.mark.parametrize(
    "expression",
    [
        "a_star(reached_vertex(accepted))",
        "random(length(4)) a_star(reached_vertex(accepted))",
    ],
)
def test_native_targeted_and_combined_modes(model, tmp_path, expression):
    configured = exploration_model(model, generator=expression, edge_coverage=0, state_coverage=0)
    path = tmp_path / "model.json"
    path.write_text(json.dumps(configured.document))
    steps = generate_path(configured, path)
    assert steps[-1]["id"] == "accepted"
    assert configured.graph["generator"] == expression


def test_target_and_predefined_modes_explain_required_configuration(model):
    with pytest.raises(ValueError, match="target"):
        exploration_model(model, generator="a_star")
    with pytest.raises(ValueError, match="predefinedPathEdgeIds"):
        exploration_model(model, generator="predefined_path")


def test_generator_expression_keeps_independent_coverage_gate(model, tmp_path):
    from model_test.graphwalker import parse_path

    configured = exploration_model(model, generator="a_star(reached_vertex(accepted))")
    output = "\n".join(
        json.dumps({"currentElementName": identifier}) for identifier in ("form", "submit", "accepted")
    )
    with pytest.raises(ValueError, match="coverage"):
        parse_path(configured, output)
