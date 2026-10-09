"""Formal graph traversal and input exploration around a goal-driven browser agent."""

import json
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

from .errors import Defect, Inconclusive
from .graphwalker import generate_path
from .hooks import HookContext, Hooks
from .jev import JevBrowser, JevClient, Oracle
from .model import setup_path, violations
from .properties import exercise
from .report import write_report


def console(message):
    print(message, flush=True)


def require_pass(result):
    if result["status"] == "FAIL":
        raise Defect("Observed behavior violates the business model", result=result)
    if result["status"] != "PASS":
        raise Inconclusive("The business outcome could not be established")


class Runner:
    def __init__(self, model, browser, oracle, url, *, settle_seconds=2, hooks=None):
        self.model, self.browser, self.oracle, self.url = model, browser, oracle, url
        self.data = {}
        self.fields = {}
        self.settle_seconds = settle_seconds
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
        deadline = time.monotonic() + self.settle_seconds
        while True:
            page = self.browser.observe()
            try:
                result = self.oracle.check(page, state, self.data, invalid)
            except Inconclusive as error:
                if error.result:
                    error.result["evidence"] = {
                        key: page.get(key) for key in ("url", "title", "text", "actions", "semantics")
                    }
                raise
            result["evidence"] = {
                key: page.get(key) for key in ("url", "title", "text", "actions", "semantics")
            }
            self.verified_rules.update(
                check["rule"]
                for check in result.get("checks", [])
                if check.get("scope") == "global" and check["status"] == "met"
            )
            if result["status"] == "PASS" or time.monotonic() >= deadline:
                return result
            time.sleep(min(0.15, max(0, deadline - time.monotonic())))

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
    replay=None,
    start_query="",
    browser=None,
    client=None,
    oracle=None,
    path_generator=generate_path,
    settle_seconds=2,
    log=console,
):
    if type(walks) is not int or not 1 <= walks <= 100:
        raise ValueError("walks must be 1–100")
    if input_mode not in {"all", "generated", "boundaries", "none"}:
        raise ValueError("Unknown input exploration mode")
    if type(max_input_attempts) is not int or not 1 <= max_input_attempts <= 10000:
        raise ValueError("max_input_attempts must be 1–10000")
    if urlparse(base_url).scheme not in {"http", "https"} or not urlparse(base_url).netloc:
        raise ValueError("Supply an HTTP(S) application URL")
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
    runner = Runner(model, browser, oracle, url, settle_seconds=settle_seconds, hooks=hooks)
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

    def record(entry, edge):
        entry["journey"] = edge["id"]
        report["cases"].append(entry)
        log(f"{entry['status']} · {entry['source']} · {json.dumps(entry['input'], ensure_ascii=False)}")

    input_attempts = 0

    def attempt(edge, data, invalid):
        nonlocal input_attempts
        if input_attempts >= max_input_attempts:
            raise Inconclusive("The input-attempt budget was exhausted")
        input_attempts += 1
        return runner.case(edge, data, invalid)

    run_context = runner.context(report=report)
    run_context.scratch = hooks.scratch

    try:
        hooks.fire("before_run", run_context)
        if replay:
            case = json.loads(Path(replay).read_text())
            if case.get("model_hash") != model.digest:
                raise Inconclusive("Replay model hash differs; use the original saved model.json")
            edge = model.edges.get(case.get("journey"))
            if not edge or "data set" not in edge["properties"]["business"]:
                raise ValueError("Replay must reference a data journey")
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
            invalid = violations(fields, data)
            result = attempt(edge, data, invalid)
            record({**result, "input": data, "source": "replay", "violations": invalid}, edge)
            require_pass(result)
        else:
            report["walks"] = []
            for walk in range(walks):
                runner.walk = walk + 1
                with hooks.scope("walk", runner.context()):
                    path = path_generator(model, saved_model, seed + walk, max_steps)
                    planned = [{"id": item["id"], "kind": item["kind"]} for item in path]
                    report["walks"].append({"seed": seed + walk, "planned_path": planned})
                    if walk == 0:
                        report["planned_path"] = planned
                    log(
                        f"GraphWalker walk {walk + 1}/{walks}: {len(path)} elements. "
                        "Opening the start page..."
                    )
                    runner.reset()
                    pending = None
                    for item in path:
                        entry = {
                            "id": item["id"],
                            "name": item["name"],
                            "kind": item["kind"],
                            "walk": walk + 1,
                        }
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
                        log(f"{entry['status']} · {item['name']}")
            runner.walk = None
            for identity, edge in campaigns.items() if input_mode != "none" else []:
                fields = model.data_sets[edge["properties"]["business"]["data set"]]
                exercise(
                    fields,
                    lambda data, invalid, e=edge: attempt(e, data, invalid),
                    lambda entry, e=edge: record(entry, e),
                    random_seed=seed,
                    cases=cases,
                    mode=input_mode,
                    shrink=shrink,
                )
                suites.add(identity)
            coverage = model.business["coverage"]
            if len(seen_edges) / len(model.edges) * 100 < coverage["edges"]:
                raise Inconclusive("Required verified edge coverage was not achieved")
            if len(seen_states) / len(model.states) * 100 < coverage["states"]:
                raise Inconclusive("Required verified state coverage was not achieved")
            unverified = set(model.business["rules"]) - runner.verified_rules
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
        run_context.error = error
        raise
    except Exception as error:
        report["status"] = "FAIL" if isinstance(error, Defect) else "INCONCLUSIVE"
        run_context.error = error
        report["error"] = str(error)
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
    finally:
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
            if resource is browser and headed and getattr(browser, "browser", browser) is not None:
                report["browser_left_open"] = True
                log("The last test tab has been left open for inspection.")
                continue
            try:
                resource.close()
            except Exception as error:
                report.setdefault("cleanup_errors", []).append(str(error))
                if report["status"] != "FAIL":
                    report["status"] = "INCONCLUSIVE"
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
        write_report(directory, report)
        log(
            f"{report['status']} · {len(seen_edges)}/{len(model.edges)} verified edges · "
            f"{len(report['cases'])} input attempts · {client.stats['calls']} model calls"
        )
        log(f"Report: {directory / 'report.html'}")
    return report
