"""Shared native execution, evidence collection and reports for Python adapters."""

import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

from .collector import Collector
from .config import parse_http_url
from .core import CoreClient, CoreDecisions
from .errors import Defect, Inconclusive
from .hooks import Hooks
from .input_strategies import StrategyProvider, validate_policy
from .report import write_report


class _NoDecisions:
    """Usage evidence for adapters whose assertions require no decision provider."""

    def __init__(self):
        self.stats = {"calls": 0}
        self.decisions = []

    def close(self):
        pass


def console(message):
    print(message, flush=True)


def run(
    model,
    base_url,
    *,
    output="artifacts",
    seed=42,
    cases=1,
    input_mode="focused",
    input_strategies=None,
    strategy_provider=None,
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
    target_adapter=None,
    client=None,
    oracle=None,
    path_generator=None,
    core=None,
    core_binary=None,
    api_key=None,
    decision_model="jev-latest",
    log=console,
):
    """Collect adapter evidence while Rust owns planning and test execution.

    Supply target_adapter for a non-web target; it handles native callbacks and
    provides trace, context and close methods. Otherwise browser/oracle bindings
    use the web adapter. path_generator imports preplanned routes; normal runs
    use native GraphWalker directly. A supplied core remains owned by its caller.
    """
    input_strategies = validate_policy(input_strategies, model.data_sets)
    strategy_provider = (
        strategy_provider or getattr(target_adapter, "strategy_provider", None) or StrategyProvider()
    )
    if input_mode != "focused" and input_strategies:
        raise ValueError("Input strategy configuration and providers require --input-mode focused")
    if target_adapter is None:
        if keep_browser_open and not headed:
            raise ValueError("Keeping the test browser open requires headed mode")
        parse_http_url(base_url, "application URL")
        url = urljoin(base_url.rstrip("/") + "/", model.business.get("entry path", "/"))
        if start_query:
            parsed = urlparse(url)
            url = urlunparse(parsed._replace(query="&".join(filter(None, [parsed.query, start_query]))))
    else:
        url = base_url
        browser = target_adapter
        client = client or _NoDecisions()
        screenshots = bool(screenshots and callable(getattr(target_adapter, "screenshot", None)))
    directory = Path(output).resolve() / datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    directory.mkdir(parents=True)
    saved_model = directory / "model.json"
    saved_model.write_text(json.dumps(model.document, indent=2, ensure_ascii=False) + "\n")
    owns_core = core is None
    core = core or CoreClient(core_binary)
    previous_handler = core.handler
    if target_adapter is None and (browser is None or client is None or oracle is None):
        # Browser Harness must already be connected before this import.
        from .jev import JevBrowser, Oracle

        try:
            client = client or CoreDecisions(
                core, api_key=api_key, model=decision_model, max_calls=max_calls, threshold=threshold
            )
            browser = browser or JevBrowser(client, max_actions=max_actions, headed=headed)
            oracle = oracle or Oracle(client, model)
        except BaseException:
            if owns_core:
                core.close()
            raise
    hooks = hooks or getattr(target_adapter, "hooks", None) or Hooks()
    report = {
        "status": "RUNNING",
        "model": model.graph["name"],
        "model_hash": model.digest,
        "url": url,
        "adapter": (
            getattr(target_adapter, "name", type(target_adapter).__name__)
            if target_adapter is not None
            else "web"
        ),
        "seed": seed,
        "directory": str(directory),
        "steps": [],
        "cases": [],
        "started": datetime.now(timezone.utc).isoformat(),
        "replay": bool(replay),
        "limits": {
            "model_calls": max_calls,
            "actions_per_journey": max_actions,
            "generated_cases": cases,
            "input_mode": input_mode,
            "input_strategies": input_strategies,
            "strategy_provider": strategy_provider.metadata,
            "property_engine": "hegel",
            "shrink": shrink,
            "input_attempts": max_input_attempts,
            "walks": walks,
            "graph_steps": max_steps,
            "confidence": threshold,
            "screenshots": screenshots,
        },
        "hooks": {"path": hooks.path, "sha256": hooks.digest, "events": hooks.events},
        "exploration": {"generator": model.graph["generator"], "required": model.business["coverage"]},
        "scope": {"input_mode": input_mode, "available_campaigns": 0, "selected_campaigns": 0},
        "coverage": {
            "edges": {"verified": 0, "total": len(model.edges)},
            "states": {"verified": 0, "total": len(model.states)},
            "properties": {"completed": 0, "total": 0},
        },
    }
    collector = Collector(report, browser, client, log)
    if target_adapter is None:
        from .web_adapter import WebAdapter

        adapter = WebAdapter(
            model, browser, oracle, hooks=hooks, collector=collector, strategy_provider=strategy_provider
        )
    else:
        adapter = target_adapter
        adapter.collector = collector
        adapter.hooks = hooks
        adapter.strategy_provider = strategy_provider
    try:
        core.debug = debug
        core.handler = adapter
        options = {
            "seed": seed,
            "walks": walks,
            "max_steps": max_steps,
            "cases": cases,
            "input_mode": input_mode,
            "input_strategies": input_strategies,
            "custom_strategies": bool(strategy_provider.factory),
            "shrink": shrink,
            "max_input_attempts": max_input_attempts,
            "keep_target_open": keep_browser_open,
            "evaluator": "adapter",
        }
        if path_generator:
            routes = []
            for walk in range(walks):
                try:
                    routes.append(path_generator(model, saved_model, seed + walk, max_steps))
                except Exception:
                    if routes:
                        partial = core.request(
                            "run.plan",
                            model=model.document,
                            options={**options, "walks": len(routes), "paths": routes},
                        )
                        partial["planning"]["complete"] = False
                        partial["scope"]["walks_requested"] = walks
                        collector.event("planned", partial)
                    report["stop"] = {"phase": "graph planning", "walk": walk + 1, "seed": seed + walk}
                    raise
            options["paths"] = routes
        case = json.loads(Path(replay).read_text()) if replay else None
        result = core.request(
            "case.replay" if case else "run.start",
            model=model.document,
            options=options,
            target={"url": url},
            case=case,
        )
        # Core events have already captured available evidence and detailed trace ranges.
        # Preserve their attachment fields when merging the authoritative result.
        for test in result["tests"]:
            if local := collector.by_id.get(test["id"]):
                for key in (
                    "screenshot",
                    "screenshot_at",
                    "screenshot_error",
                    "trace_range",
                    "decision_range",
                    "replay_file",
                ):
                    if key in local:
                        test[key] = local[key]
        report.update(result)
        collector.plan.tests = report["tests"]
        if core.exit_error:
            raise core.exit_error
        if core.interrupted:
            raise KeyboardInterrupt
        if report["status"] != "PASS":
            log(f"{report['status']}: {report.get('error', 'The run could not be completed')}")
    except BaseException as error:
        report.update(
            status="FAIL" if isinstance(error, Defect) else "INCONCLUSIVE",
            error="Interrupted by the user" if isinstance(error, KeyboardInterrupt) else str(error),
        )
        collector.plan.fail(error)
        if isinstance(error, Inconclusive) and error.result:
            report["failure"] = error.result
            if partial := error.result.get("partial_report"):
                collector.event("planned", partial)
                report["stop"] = error.result["stop"]
                report["status"] = "INCONCLUSIVE"
        report.setdefault("stop", {"phase": "core connection"})
        report["stop"].update(reason=report["error"], exception=type(error).__name__)
        if debug:
            report["stop"] = {
                "phase": "core connection",
                "reason": report["error"],
                "exception": type(error).__name__,
                "traceback": traceback.format_exc(),
            }
        if not isinstance(error, Exception):
            raise
        log(f"{report['status']}: {report['error']}")
    finally:
        if not adapter.after_run:
            try:
                context = adapter.context({"result": report})
                hooks.fire("after_run", context)
            except Exception as error:
                report.setdefault("cleanup_errors", []).append(str(error))
                if report["status"] == "PASS":
                    report.update(
                        status="FAIL" if isinstance(error, Defect) else "INCONCLUSIVE", error=str(error)
                    )
        if debug and core:
            report["core_diagnostics"] = list(core.diagnostics)
            if core.adapter_tracebacks:
                report.setdefault("stop", {})["traceback"] = core.adapter_tracebacks[0]
        for resource in (adapter, client):
            try:
                resource.close(keep_open=keep_browser_open) if resource is adapter else resource.close()
            except Exception as error:
                report.setdefault("cleanup_errors", []).append(str(error))
                if report["status"] == "PASS":
                    report.update(status="INCONCLUSIVE", error=f"Cleanup failed: {error}")
        report["trace"] = browser.trace
        # Injected adapters can provide their own audit/usage. Native counters
        # arrive with the results; transcripts use bounded RPC pages.
        report["decisions"] = report.get("decisions") or getattr(client, "decisions", [])
        try:
            if isinstance(client, CoreDecisions):
                report["decisions"] = client.audit()
            report["usage"] = report.get("usage") or client.stats.copy()
        except Inconclusive:
            report.setdefault("usage", {"calls": 0})
        if core:
            core.handler = previous_handler
            if owns_core:
                core.close()
        report.setdefault("input_attempts", len(report["cases"]))
        if not collector.saved:
            collector.plan.save(directory)
        if report.get("counterexample"):
            entry = report["counterexample"]
            recipe = entry.get("replay_recipe") or {
                "model_hash": model.digest,
                "journey": entry["journey"],
                "input": entry["input"],
                "seed": seed,
            }
            (directory / "replay.json").write_text(json.dumps(recipe, indent=2, ensure_ascii=False) + "\n")
        report["finished"] = datetime.now(timezone.utc).isoformat()
        collector.plan.finalize()
        write_report(directory, report)
        log(
            f"{report['status']} · "
            f"{report['coverage']['edges']['verified']}/{len(model.edges)} verified edges · "
            f"{report['input_attempts']} input attempts · {report['usage']['calls']} model calls"
        )
        log(f"Report: {directory / 'report.html'}")
        log(f"JUnit: {directory / 'junit.xml'}")

    return report
