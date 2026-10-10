import argparse
import json
import sys
import traceback
from contextlib import nullcontext
from pathlib import Path

from .browser_runtime import connect
from .config import RuntimeConfig, parse_http_url
from .hooks import Hooks
from .input_strategies import StrategyProvider, load_policy
from .model import load_model
from .policy import exploration_model
from .resources import asset
from .server import serve


def positive(value):
    number = int(value)
    if not 1 <= number <= 10000:
        raise argparse.ArgumentTypeError("use an integer from 1 through 10000")
    return number


def percentage(value):
    number = float(value)
    if not 0 <= number <= 100:
        raise argparse.ArgumentTypeError("use a percentage from 0 through 100")
    return number


def exploration_arguments(command):
    command.add_argument("--seed", type=positive, default=42)
    command.add_argument("--max-steps", type=positive, default=200, help="Maximum elements per graph walk")
    command.add_argument("--walks", type=positive, default=1, help="Independent walks, with successive seeds")
    command.add_argument(
        "--generator",
        help="Native name (random, quick_random, weighted_random, shortest_all_paths, "
        "predefined_path, new_york_street_sweeper) or expression such as a_star(reached_vertex(studio))",
    )
    command.add_argument("--edge-coverage", type=percentage, help="Required verified edge coverage percent")
    command.add_argument("--state-coverage", type=percentage, help="Required verified state coverage percent")


def input_arguments(command):
    command.add_argument(
        "--cases",
        type=positive,
        default=1,
        help="Property examples per phase (default 1); focused mode plans each constraint separately",
    )
    command.add_argument(
        "--input-mode",
        choices=("focused", "boundaries", "generated", "all", "none"),
        default="focused",
        help="Property coverage: focused (default), exact boundaries, broad generation, both, or none",
    )
    command.add_argument(
        "--input-strategies",
        type=Path,
        help="JSON strategy settings and per-field overrides for focused mode",
    )
    command.add_argument(
        "--strategy-provider",
        type=Path,
        help="Explicitly load a trusted Python input_strategy(context, default) extension",
    )
    command.add_argument(
        "--max-input-attempts",
        type=positive,
        default=1000,
        help="Select up to N property inputs in model order; also caps shrinking and reproduction",
    )
    command.add_argument("--no-shrink", action="store_true", help="Disable Hegel failure shrinking")


def parser():
    root = argparse.ArgumentParser(description="Test business journeys with GraphWalker, Jev and Hegel")
    commands = root.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Check the business specification without API calls")
    validate.add_argument("--model", type=Path, required=True)
    hosting = commands.add_parser("serve", help="Serve the demo website")
    hosting.add_argument("--port", type=int, default=4173)
    for mode in ("sitemap", "url"):
        discovery = commands.add_parser(
            f"discover-{mode}", help=f"Generate a draft model from a {mode}, without AI"
        )
        discovery.add_argument(f"--{mode}", type=Path if mode == "sitemap" else str, required=True)
        discovery.add_argument("--output", type=Path, required=True, help="New model JSON filename")
        discovery.add_argument(
            "--config", type=Path, help="Optional browser properties; no API key or GraphWalker required"
        )
        discovery.add_argument("--headed", action="store_true")
        discovery.add_argument(
            "--hooks", type=Path, help="Trusted Python discovery hooks (separate from test lifecycle hooks)"
        )
        discovery.add_argument("--max-pages", type=positive, default=50)
        discovery.add_argument("--max-actions", type=positive, default=200)
        discovery.add_argument("--timeout-ms", type=int, default=10000)
        discovery.add_argument("--settle-ms", type=int, default=300)
        discovery.add_argument("--no-screenshots", action="store_true")
        discovery.add_argument("--no-dom", action="store_true")
        discovery.add_argument("--no-network", action="store_true")
        discovery.add_argument("--max-json-bytes", type=positive, default=1048576)
        discovery.add_argument("--max-network-requests", type=positive, default=1000)
        discovery.add_argument("--debug", action="store_true")
        if mode == "url":
            discovery.add_argument(
                "--depth", type=int, default=1, help="Link/submission depth from the seed (0–10)"
            )
            discovery.add_argument(
                "--no-submit-forms", action="store_true", help="Inspect forms without submitting them"
            )
            discovery.add_argument("--values", type=Path, help="JSON field defaults and per-page overrides")
    plan = commands.add_parser("plan", help="Validate and traverse a model without a browser or API calls")
    plan.add_argument("--model", type=Path)
    plan.add_argument("--site", choices=("booking", "feedback", "trailhead"), default="booking")
    plan.add_argument("--config", type=Path, default=Path("testwalker.properties"))
    exploration_arguments(plan)
    for name in ("run", "demo"):
        command = commands.add_parser(
            name, help="Run against your site" if name == "run" else "Run a bundled demo"
        )
        command.add_argument("--model", type=Path, required=name == "run")
        if name == "run":
            command.add_argument("--url", required=True, help="Application base URL")
        else:
            command.add_argument("--site", choices=("booking", "feedback", "trailhead"), default="booking")
            command.add_argument(
                "--bug", action="store_true", help="Enable the booking demo's seat-limit defect"
            )
        exploration_arguments(command)
        input_arguments(command)
        command.add_argument("--max-actions", type=positive, default=25)
        command.add_argument("--max-calls", type=positive, default=1000)
        command.add_argument("--threshold", type=float, default=0.85)
        command.add_argument(
            "--headed",
            action="store_true",
            help="Use visible Chrome and focus test tabs",
        )
        command.add_argument(
            "--keep-browser-open",
            action="store_true",
            help="Keep the last test tab for debugging; requires --headed",
        )
        command.add_argument(
            "--debug", action="store_true", help="Include error tracebacks in output and reports"
        )
        command.add_argument("--cdp-url", help="Connect Browser Harness to this Chrome debugging URL")
        command.add_argument(
            "--no-screenshots",
            dest="screenshots",
            action="store_false",
            help="Disable local screenshots of executed graph checks and property attempts",
        )
        command.add_argument(
            "--config",
            type=Path,
            default=Path("testwalker.properties"),
            help="Properties file containing TYPESAFE_API_KEY and optional TESTWALKER_CORE_BIN",
        )
        hooks = command.add_mutually_exclusive_group()
        hooks.add_argument("--hooks", type=Path, help="Explicitly load a trusted Python lifecycle hooks file")
        if name == "demo":
            hooks.add_argument(
                "--demo-hooks", action="store_true", help="Enable bundled Trailhead lifecycle hooks"
            )
        command.add_argument("--output", type=Path, default=Path("artifacts"))
        command.add_argument("--replay", type=Path)
    from .rpc_cli import add_commands

    add_commands(commands, exploration_arguments, input_arguments)
    return root


def plan_run(model, args):
    from .core import CoreClient

    config = RuntimeConfig.load(args.config)
    print(f"Generator: {model.graph['generator']}")
    with CoreClient(config.core_binary) as core:
        for walk in range(args.walks):
            print(f"Walk {walk + 1}, seed {args.seed + walk}:")
            route = core.request(
                "graph.plan",
                model=model.document,
                options={"seed": args.seed + walk, "max_steps": args.max_steps},
            )
            for element in route:
                spec = element["properties"]["business"]
                print(element["name"], "—", spec.get("intent", spec.get("description")))
    print(f"Validated: {len(model.edges)} journeys, {len(model.data_sets)} business data sets")
    return 0


def live_run(model, args, config):
    from .core import CoreClient, CoreDecisions

    policy = load_policy(args.input_strategies, model.data_sets)
    if args.input_mode != "focused" and args.input_strategies:
        raise ValueError("Input strategy configuration and providers require --input-mode focused")
    if args.replay and (args.input_strategies or args.strategy_provider):
        raise ValueError("Replay uses its saved input; do not supply input strategy overrides")
    provider = StrategyProvider.load(args.strategy_provider)
    options = {
        key: getattr(args, key)
        for key in (
            "output",
            "seed",
            "cases",
            "max_steps",
            "max_calls",
            "max_actions",
            "threshold",
            "headed",
            "keep_browser_open",
            "debug",
            "screenshots",
            "replay",
            "input_mode",
            "max_input_attempts",
            "walks",
        )
    }
    options.update(input_strategies=policy, strategy_provider=provider)
    with (
        CoreClient(config.core_binary) as core,
        connect(config, headed=args.headed, cdp_url=args.cdp_url, keep_browser_open=args.keep_browser_open),
    ):
        # Browser Harness captures the named connection at import time.
        # Establish it before importing Jev, the engine or application hooks.
        from .engine import run
        from .jev import JevBrowser

        options.update(hooks=Hooks.load(args.hooks), shrink=not args.no_shrink, core=core)
        client = CoreDecisions(
            core,
            api_key=config.api_key,
            model=config.model,
            max_calls=args.max_calls,
            threshold=args.threshold,
        )
        browser = JevBrowser(
            client, max_actions=args.max_actions, headed=args.headed, text_config=config.text
        )
        try:
            hosting = serve() if args.command == "demo" else nullcontext(args.url)
            with hosting as url:
                report = run(
                    model,
                    url,
                    start_query="bug=seats" if getattr(args, "bug", False) else "",
                    client=client,
                    browser=browser,
                    **options,
                )
        finally:
            client.close()
    return {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}[report["status"]]


def discovery_run(args):
    from .discovery import DiscoveryOptions, discover_sitemap, discover_url
    from .discovery.browser import load_hooks
    from .discovery.crawler import output_paths

    output, _ = output_paths(args.output)
    options = DiscoveryOptions(
        depth=getattr(args, "depth", 0),
        submit_forms=not getattr(args, "no_submit_forms", False),
        max_pages=args.max_pages,
        max_actions=args.max_actions,
        timeout_ms=args.timeout_ms,
        settle_ms=args.settle_ms,
        headed=args.headed,
        screenshots=not args.no_screenshots,
        dom=not args.no_dom,
        network=not args.no_network,
        max_json_bytes=args.max_json_bytes,
        max_network_requests=args.max_network_requests,
    )
    config = RuntimeConfig.load(args.config, discovery=True) if args.config else None
    values_path = getattr(args, "values", None)
    values = json.loads(values_path.read_text()) if values_path else None
    function = discover_sitemap if args.command == "discover-sitemap" else discover_url
    result = function(
        args.sitemap if args.command == "discover-sitemap" else args.url,
        output=output,
        config=config,
        options=options,
        values=values,
        hooks=load_hooks(args.hooks),
    )
    graph = result.model["models"][0]
    print(f"Draft: {output} ({len(graph['vertices'])} states, {len(graph['edges'])} journeys)")
    print(f"Discovery inventory: {output.with_suffix('.discovery.json')}")
    for note in dict.fromkeys(result.inventory["review"]):
        print(f"Review: {note}")
    capture_errors = sum(len(v["capture_errors"]) for v in result.inventory["visits"])
    print(f"Discovery artifacts: {output.with_suffix('.discovery')}")
    if capture_errors:
        print(f"Evidence: {capture_errors} capture errors; see visits in the inventory")
    result.close()
    incomplete = len(result.inventory["failed"]) + len(result.inventory["pending"]) + capture_errors
    if incomplete:
        print(f"Partial discovery: {incomplete} failed/pending visits or capture errors; see the inventory")
    return 2 if incomplete else 0


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in {"rpc", "self-test"}:
            from .rpc_cli import execute

            return execute(args)
        if args.command.startswith("discover-"):
            return discovery_run(args)
        if args.command == "serve":
            with serve(args.port) as url:
                print(f"Demo site: {url}", flush=True)
                import threading

                threading.Event().wait()
        model = load_model(args.model or asset(f"models/{args.site}.json"))
        if args.command == "validate":
            print(
                f"Valid: {len(model.states)} states, {len(model.edges)} journeys, "
                f"{len(model.data_sets)} data sets; {model.digest}"
            )
            return 0
        config = RuntimeConfig.load(args.config, live=args.command != "plan")
        if args.walks > 100:
            raise ValueError("walks must be 1–100")
        if getattr(args, "replay", None) and any(
            value is not None for value in (args.generator, args.edge_coverage, args.state_coverage)
        ):
            raise ValueError("Replay uses the saved model; do not override its exploration settings")
        model = exploration_model(
            model,
            generator=args.generator,
            edge_coverage=args.edge_coverage,
            state_coverage=args.state_coverage,
        )
        if args.command == "plan":
            return plan_run(model, args)
        if args.cases > 200 or not 0.5 < args.threshold <= 1:
            raise ValueError("cases must be 1–200; confidence must be above 0.5 and at most 1")
        if args.keep_browser_open and not args.headed:
            raise ValueError("--keep-browser-open requires --headed")
        if args.command == "run":
            parse_http_url(args.url, "application URL")
        if args.command == "demo" and args.bug and args.site != "booking":
            raise ValueError("The injected defect belongs to the booking demo")
        if getattr(args, "demo_hooks", False):
            if args.site != "trailhead":
                raise ValueError("Bundled lifecycle hooks are provided for --site trailhead")
            args.hooks = asset("examples/trailhead/hooks.py")
        return live_run(model, args, config)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"INCONCLUSIVE: {error}", file=sys.stderr)
        if getattr(args, "debug", False):
            traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
