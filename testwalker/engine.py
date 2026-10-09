"""Formal graph traversal and input exploration around a goal-driven browser agent."""

import json
import traceback
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

from .config import parse_http_url
from .errors import Defect, Inconclusive
from .graphwalker import generate_path
from .hooks import HookContext, Hooks
from .jev import JevBrowser, JevClient, Oracle
from .model import setup_path, violations
from .planning import TestPlan
from .properties import evaluate_case, exercise
from .report import write_report


def console(message):
    print(message, flush=True)


def require_pass(result):
    if result["status"] == "FAIL":
        raise Defect("Observed behavior violates the business model", result=result)
    if result["status"] != "PASS":
        raise Inconclusive("The business outcome could not be established")


class Runner:
    def __init__(self, model, browser, oracle, url, *, hooks=None):
        self.model, self.browser, self.oracle, self.url = model, browser, oracle, url
        self.data = {}
        self.fields = {}
        self.verified_rules = set()
        self.hooks = hooks or Hooks()
        self.phase = "graph"
        self.walk = None

    def context(self, **kwargs):
        return HookContext(
            model=self.model,
            url=self.url,
            phase=self.phase,
            browser=self.browser,
            data=deepcopy(self.data),
            walk=self.walk,
            **kwargs,
        )

    def check(self, state, invalid=None):
        context = self.context(element=deepcopy(self.model.states[state]), invalid=deepcopy(invalid or []))
        with self.hooks.scope("state", context):
            result = self._check(state, invalid)
            context.result = deepcopy(result)
            return result

    def _check(self, state, invalid=None):
        if invalid:
            invalid = [
                {**item, "description": self.fields.get(item["field"], {}).get("description", "")}
                for item in invalid
            ]
        # Browser actions settle before observation. A verdict is final: retrying a
        # failure until the oracle agrees would hide defects and spend extra calls.
        page = self.browser.observe()
        observation = {
            "input": deepcopy(self.data),
            "violations": deepcopy(invalid or []),
            "evidence": {key: page.get(key) for key in ("url", "title", "text", "actions", "semantics")},
        }
        try:
            result = self.oracle.check(page, state, self.data, invalid)
        except Inconclusive as error:
            if error.result is None:
                error.result = {"status": "INCONCLUSIVE", "expected": state}
            error.result.update(observation)
            raise
        result.update(observation)
        self.verified_rules.update(
            check["rule"]
            for check in result.get("checks", [])
            if check.get("scope") == "global" and check["status"] == "met"
        )
        return result

    def reset(self):
        self.data = {}
        self.fields = {}
        with self.hooks.scope("reset", self.context()):
            self.browser.reset(self.url)

    def start(self):
        self.reset()
        require_pass(self.check(self.model.graph["startElementId"]))

    def transition(self, edge, *, data=None):
        spec = edge["properties"]["business"]
        fields = self.model.data_sets.get(spec.get("data set"), {})
        if fields:
            self.fields = fields
            self.data = self.model.example(edge) if data is None else data
        destination = self.model.states[edge["targetVertexId"]]["properties"]["business"]["description"]
        with self.hooks.scope("transition", self.context(element=deepcopy(edge))):
            self.browser.pursue(
                spec["intent"],
                fields=fields,
                data=self.data if fields else None,
                destination=destination if not fields else None,
                states=[
                    state["properties"]["business"]["description"] for state in self.model.states.values()
                ],
            )
        if edge["targetVertexId"] == self.model.graph["startElementId"] and not fields:
            self.data = {}
            self.fields = {}

    def case(self, edge, data, invalid):
        previous_phase = self.phase
        self.phase = "property"
        context = self.context(element=deepcopy(edge), invalid=deepcopy(invalid))
        context.data = deepcopy(data)
        try:
            with self.hooks.scope("case", context):
                result = self._case(edge, data, invalid)
                context.result = deepcopy(result)
                return result
        finally:
            self.phase = previous_phase

    def _case(self, edge, data, invalid):
        self.phase = "setup"
        try:
            self.start()
            for setup in setup_path(self.model.graph, self.model.edges, edge["sourceVertexId"]):
                self.transition(setup)
                require_pass(self.check(setup["targetVertexId"]))
        except Defect as error:
            raise Inconclusive("The property setup journey did not reach its required state") from error
        finally:
            self.phase = "property"
        self.transition(edge, data=data)
        spec = edge["properties"]["business"]
        expected = spec["rejected at"] if invalid else spec.get("accepted at", edge["targetVertexId"])
        return self.check(expected, invalid)


def run(
    model,
    base_url,
    *,
    output="artifacts",
    seed=42,
    cases=3,
    input_mode="all",
    shrink=True,
    max_input_attempts=1000,
    walks=1,
    hooks=None,
    max_steps=200,
    max_calls=1000,
    max_actions=25,
    threshold=0.85,
    headed=False,
    keep_browser_open=False,
    debug=False,
    screenshots=True,
    replay=None,
    start_query="",
    browser=None,
    client=None,
    oracle=None,
    path_generator=generate_path,
    log=console,
):
    if type(walks) is not int or not 1 <= walks <= 100:
        raise ValueError("walks must be 1–100")
    if input_mode not in {"all", "generated", "boundaries", "none"}:
        raise ValueError("Unknown input exploration mode")
    if type(max_input_attempts) is not int or not 1 <= max_input_attempts <= 10000:
        raise ValueError("max_input_attempts must be 1–10000")
    if keep_browser_open and not headed:
        raise ValueError("Keeping the test browser open requires headed mode")
    parse_http_url(base_url, "application URL")
    url = urljoin(base_url.rstrip("/") + "/", model.business.get("entry path", "/"))
    if start_query:
        parsed = urlparse(url)
        url = urlunparse(parsed._replace(query="&".join(filter(None, [parsed.query, start_query]))))
    directory = Path(output).resolve() / datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    directory.mkdir(parents=True)
    saved_model = directory / "model.json"
    saved_model.write_text(json.dumps(model.document, indent=2, ensure_ascii=False) + "\n")
    client = client or JevClient(max_calls=max_calls, threshold=threshold)
    browser = browser or JevBrowser(client, max_actions=max_actions, headed=headed)
    oracle = oracle or Oracle(client, model)
    hooks = hooks or Hooks()
    runner = Runner(model, browser, oracle, url, hooks=hooks)
    report = {
        "status": "RUNNING",
        "model": model.graph["name"],
        "model_hash": model.digest,
        "url": url,
        "seed": seed,
        "limits": {
            "model_calls": max_calls,
            "actions_per_journey": max_actions,
            "generated_cases": cases,
            "input_mode": input_mode,
            "shrink": shrink,
            "input_attempts": max_input_attempts,
            "walks": walks,
            "graph_steps": max_steps,
            "confidence": threshold,
            "screenshots": screenshots,
        },
        "graph": {
            "start": model.graph["startElementId"],
            "states": [
                {"id": state["id"], "name": state["name"], **state["properties"]["business"]}
                for state in model.states.values()
            ],
            "edges": [
                {
                    "id": edge["id"],
                    "name": edge["name"],
                    "source": edge["sourceVertexId"],
                    "target": edge["targetVertexId"],
                    "intent": edge["properties"]["business"]["intent"],
                }
                for edge in model.edges.values()
            ],
        },
        "steps": [],
        "cases": [],
        "replay": bool(replay),
        "started": datetime.now(timezone.utc).isoformat(),
        "directory": str(directory),
        "hooks": {"path": hooks.path, "sha256": hooks.digest, "events": hooks.events},
        "exploration": {"generator": model.graph["generator"], "required": model.business["coverage"]},
    }
    seen_edges, seen_states, suites = set(), set(), set()
    campaigns = {}
    for edge in model.edges.values():
        spec = edge["properties"]["business"]
        if "data set" in spec:
            identity = (
                edge["sourceVertexId"],
                spec["data set"],
                spec.get("accepted at", edge["targetVertexId"]),
                spec["rejected at"],
            )
            campaigns.setdefault(identity, edge)
    report["scope"] = {
        "input_mode": input_mode,
        "available_campaigns": len(campaigns),
        "selected_campaigns": len(campaigns) if input_mode != "none" else 0,
        "walks_requested": walks,
    }
    plan = TestPlan(report, browser, client)
    plan_saved = False

    def record(entry, edge):
        entry["journey"] = edge["id"]
        if entry.get("attempted", True):
            plan.capture(entry)
        if plan.active is not None:
            entry["test_id"] = plan.active["id"]
            plan.active["attempt_indices"].append(len(report["cases"]))
        report["cases"].append(entry)
        log(f"{entry['status']} · {entry['source']} · {json.dumps(entry['input'], ensure_ascii=False)}")

    input_attempts = 0
    activity = {"phase": "run setup"}

    def stopped(error):
        report["stop"] = {**deepcopy(activity), "exception": type(error).__name__, "reason": report["error"]}
        element = activity.get("element", {})
        location = f" at {element['kind']} {element['id']}" if element else ""
        log(f"Stopped during {activity['phase']}{location}.")
        if debug:
            report["stop"]["traceback"] = traceback.format_exc()
            log(report["stop"]["traceback"])

    def attempt(edge, data, invalid):
        nonlocal input_attempts
        activity.update(phase="property case", input=deepcopy(data), violations=deepcopy(invalid))
        if input_attempts >= max_input_attempts:
            activity["phase"] = "property input budget"
            raise Inconclusive("The input-attempt budget was exhausted", result={"attempted": False})
        input_attempts += 1
        return runner.case(edge, data, invalid)

    run_context = runner.context(report=report)
    run_context.scratch = hooks.scratch

    try:
        if replay:
            activity = {"phase": "replay planning"}
            case = json.loads(Path(replay).read_text())
            replay_test = plan.add(
                "property/replay",
                "property",
                journey=case.get("journey"),
                input=case.get("input"),
                phase={"kind": "replay", "source": "replay"},
            )
        else:
            activity = {"phase": "property planning"}
            plan.add_campaigns(model, campaigns.values(), mode=input_mode, cases=cases)
            paths = []
            report["walks"] = []
            for walk in range(walks):
                activity = {"phase": "graph planning", "walk": walk + 1, "seed": seed + walk}
                path = path_generator(model, saved_model, seed + walk, max_steps)
                paths.append(path)
                planned = [{"id": item["id"], "kind": item["kind"]} for item in path]
                report["walks"].append({"seed": seed + walk, "planned_path": planned})
                plan.add_walk(model, path, walk + 1, seed + walk)
                if walk == 0:
                    report["planned_path"] = planned
            selected_edges = {item["id"] for path in paths for item in path if item["kind"] == "edge"}
            report["planning"]["unselected_edges"] = sorted(set(model.edges) - selected_edges)
        report["planning"]["complete"] = True
        plan.save(directory)
        plan_saved = True
        log(f"Planned {len(plan.tests)} tests. Inventory: {directory / 'plan.json'}")
        activity = {"phase": "run setup"}
        hooks.fire("before_run", run_context)
        if replay:
            activity = {"phase": "property replay"}
            plan.begin(replay_test)
            if case.get("model_hash") != model.digest:
                raise Inconclusive("Replay model hash differs; use the original saved model.json")
            edge = model.edges.get(case.get("journey"))
            if not edge or "data set" not in edge["properties"]["business"]:
                raise ValueError("Replay must reference a data journey")
            activity["element"] = {"id": edge["id"], "kind": "edge"}
            fields = model.data_sets[edge["properties"]["business"]["data set"]]
            data = case["input"]
            if (
                not isinstance(data, dict)
                or set(data) != set(fields)
                or not all(
                    type(value) in (str, int, float) and len(str(value)) <= 2000 for value in data.values()
                )
            ):
                raise ValueError("Replay must supply every declared business field")
            evaluate_case(
                fields,
                data,
                lambda values, invalid: attempt(edge, values, invalid),
                lambda entry: record(entry, edge),
                source="replay",
            )
            plan.finish("PASS")
        else:
            for walk, path in enumerate(paths):
                runner.walk = walk + 1
                activity = {"phase": "graph reset", "walk": walk + 1, "seed": seed + walk}
                with hooks.scope("walk", runner.context()):
                    log(
                        f"GraphWalker walk {walk + 1}/{walks}: {len(path)} elements. "
                        "Opening the start page..."
                    )
                    activity["phase"] = "graph reset"
                    runner.reset()
                    pending = None
                    for position, item in enumerate(path):
                        plan.begin(plan.graph[walk + 1, position])
                        activity = {
                            "phase": "graph execution",
                            "walk": walk + 1,
                            "element": {
                                key: item[key]
                                for key in ("id", "name", "kind", "sourceVertexId", "targetVertexId")
                                if key in item
                            },
                        }
                        entry = {
                            "id": item["id"],
                            "name": item["name"],
                            "kind": item["kind"],
                            "walk": walk + 1,
                            "test_id": plan.active["id"],
                        }
                        plan.active["step_indices"].append(len(report["steps"]))
                        report["steps"].append(entry)
                        if item["kind"] == "edge":
                            runner.transition(item)
                            pending = item
                            entry["status"] = "EXECUTED"
                        else:
                            invalid = None
                            if pending and "data set" in pending["properties"]["business"]:
                                fields = model.data_sets[pending["properties"]["business"]["data set"]]
                                invalid = violations(fields, runner.data)
                            log(f"Checking state: {item['name']}...")
                            result = runner.check(item["id"], invalid)
                            entry.update(result)
                            require_pass(result)
                            seen_states.add(item["id"])
                            if pending:
                                seen_edges.add(pending["id"])
                                pending = None
                            plan.finish("PASS")
                        log(f"{entry['status']} · {item['name']}")
            runner.walk = None
            for identity, edge in campaigns.items() if input_mode != "none" else []:
                activity = {
                    "phase": "property campaign",
                    "element": {"id": edge["id"], "kind": "edge"},
                    "data_set": edge["properties"]["business"]["data set"],
                }
                fields = model.data_sets[edge["properties"]["business"]["data set"]]
                exercise(
                    fields,
                    lambda data, invalid, e=edge: attempt(e, data, invalid),
                    lambda entry, e=edge: record(entry, e),
                    random_seed=seed,
                    cases=cases,
                    mode=input_mode,
                    shrink=shrink,
                    phase_scope=lambda phase, e=edge: plan.phase(e, phase),
                )
                suites.add(identity)
            activity = {"phase": "coverage check"}
            coverage = model.business["coverage"]
            if len(seen_edges) / len(model.edges) * 100 < coverage["edges"]:
                raise Inconclusive("Required verified edge coverage was not achieved")
            if len(seen_states) / len(model.states) * 100 < coverage["states"]:
                raise Inconclusive("Required verified state coverage was not achieved")
            unverified = set(model.business["rules"]) - runner.verified_rules
            activity = {"phase": "global requirements"}
            if unverified and hasattr(oracle, "audit_globals"):
                audit = oracle.audit_globals(report["steps"] + report["cases"], sorted(unverified))
                report["global_audit"] = audit
                if any(check["status"] == "broken" for check in audit):
                    raise Defect("Observed scenarios violate a global business requirement", result=audit)
                runner.verified_rules.update(check["rule"] for check in audit if check["status"] == "met")
                unverified -= runner.verified_rules
            if unverified:
                raise Inconclusive("Global requirements never verified: " + "; ".join(sorted(unverified)))
        report["status"] = "PASS"
    except KeyboardInterrupt as error:
        report["status"] = "INCONCLUSIVE"
        report["error"] = "Interrupted by the user"
        plan.fail(error)
        stopped(error)
        run_context.error = error
        raise
    except BaseException as error:
        report["status"] = "FAIL" if isinstance(error, Defect) else "INCONCLUSIVE"
        run_context.error = error
        report["error"] = str(error) or type(error).__name__
        plan.fail(error)
        stopped(error)
        if isinstance(error, Inconclusive) and error.result:
            report["failure"] = error.result
            if report["steps"] and "status" not in report["steps"][-1]:
                report["steps"][-1].update(error.result)
        if isinstance(error, Defect):
            report["failure"] = error.result
            if error.case:
                report["counterexample"] = error.case
                (directory / "replay.json").write_text(
                    json.dumps(
                        {
                            "model_hash": model.digest,
                            "journey": error.case["journey"],
                            "input": error.case["input"],
                            "seed": seed,
                        },
                        indent=2,
                    )
                    + "\n"
                )
        log(f"{report['status']}: {error}")
        if not isinstance(error, Exception):
            raise
    finally:
        if not plan_saved:
            plan.save(directory)
        report["input_attempts"] = input_attempts
        run_context.result = report
        try:
            hooks.fire("after_run", run_context)
        except Exception as error:
            report.setdefault("cleanup_errors", []).append(str(error))
            if report["status"] == "PASS":
                report["status"] = "FAIL" if isinstance(error, Defect) else "INCONCLUSIVE"
                report["error"] = str(error)
        report["trace"] = browser.trace
        report["decisions"] = getattr(client, "decisions", [])
        report["usage"] = client.stats.copy()
        for resource in (browser, client):
            if resource is browser and keep_browser_open and getattr(browser, "browser", browser) is not None:
                report["browser_left_open"] = True
                log("The last test tab has been left open for inspection.")
                continue
            try:
                resource.close()
            except Exception as error:
                report.setdefault("cleanup_errors", []).append(str(error))
                if report["status"] != "FAIL":
                    report["status"] = "INCONCLUSIVE"
                    report.setdefault("error", f"Cleanup failed: {error}")
        report["coverage"] = {
            "edges": {"verified": len(seen_edges), "total": len(model.edges), "ids": sorted(seen_edges)},
            "states": {"verified": len(seen_states), "total": len(model.states), "ids": sorted(seen_states)},
            "properties": {"completed": len(suites), "total": len(campaigns)},
            "global requirements": {
                "verified": len(runner.verified_rules),
                "total": len(set(model.business["rules"])),
            },
        }
        report["finished"] = datetime.now(timezone.utc).isoformat()
        plan.finalize()
        write_report(directory, report)
        log(
            f"{report['status']} · {len(seen_edges)}/{len(model.edges)} verified edges · "
            f"{input_attempts} input attempts · {client.stats['calls']} model calls"
        )
        log(f"Report: {directory / 'report.html'}")
        log(f"JUnit: {directory / 'junit.xml'}")
    return report
