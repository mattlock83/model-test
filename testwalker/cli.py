import argparse
import json
import sys
import tempfile
import traceback
from contextlib import nullcontext
from functools import partial
from pathlib import Path

from .browser_runtime import connect
from .config import RuntimeConfig, parse_http_url
from .graphwalker import generate_path
from .hooks import Hooks
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


def parser():
    root = argparse.ArgumentParser(description="Test business journeys with GraphWalker, Jev and Hypothesis")
    commands = root.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Check the business specification without API calls")
    validate.add_argument("--model", type=Path, required=True)
    hosting = commands.add_parser("serve", help="Serve the demo website")
    hosting.add_argument("--port", type=int, default=4173)
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
        command.add_argument(
            "--cases", type=positive, default=3, help="Generated cases per field and combined phase"
        )
        command.add_argument(
            "--input-mode", choices=("all", "boundaries", "generated", "none"), default="all"
        )
        command.add_argument(
            "--max-input-attempts",
            type=positive,
            default=1000,
            help="Total input attempts across campaigns, including shrinking",
        )
        command.add_argument("--no-shrink", action="store_true", help="Disable Hypothesis failure shrinking")
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
            help="Properties file containing GRAPHWALKER_BIN and TYPESAFE_API_KEY",
        )
        hooks = command.add_mutually_exclusive_group()
        hooks.add_argument("--hooks", type=Path, help="Explicitly load a trusted Python lifecycle hooks file")
        if name == "demo":
            hooks.add_argument(
                "--demo-hooks", action="store_true", help="Enable bundled Trailhead lifecycle hooks"
            )
        command.add_argument("--output", type=Path, default=Path("artifacts"))
        command.add_argument("--replay", type=Path)
    return root


def plan_run(model, args, path_generator):
    with tempfile.TemporaryDirectory(prefix="testwalker-plan-") as directory:
        path = Path(directory) / "model.json"
        path.write_text(json.dumps(model.document))
        print(f"Generator: {model.graph['generator']}")
        for walk in range(args.walks):
            print(f"Walk {walk + 1}, seed {args.seed + walk}:")
            for element in path_generator(model, path, args.seed + walk, args.max_steps):
                spec = element["properties"]["business"]
                print(element["name"], "—", spec.get("intent", spec.get("description")))
        print(f"Validated: {len(model.edges)} journeys, {len(model.data_sets)} business data sets")
    return 0


def live_run(model, args, config, path_generator):
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
    with connect(config, headed=args.headed, cdp_url=args.cdp_url, keep_browser_open=args.keep_browser_open):
        # Browser Harness captures the named connection at import time.
        # Establish it before importing Jev, the engine or application hooks.
        from .engine import run
        from .jev import JevBrowser, JevClient

        options.update(hooks=Hooks.load(args.hooks), shrink=not args.no_shrink, path_generator=path_generator)
        client = JevClient(
            api_key=config.api_key, model=config.model, max_calls=args.max_calls, threshold=args.threshold
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


def main(argv=None):
    args = parser().parse_args(argv)
    try:
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
        path_generator = partial(generate_path, binary=config.graphwalker)
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
            return plan_run(model, args, path_generator)
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
        return live_run(model, args, config, path_generator)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"INCONCLUSIVE: {error}", file=sys.stderr)
        if getattr(args, "debug", False):
            traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
