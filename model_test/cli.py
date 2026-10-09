import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from .graphwalker import generate_path
from .hooks import Hooks
from .model import load_model
from .policy import exploration_model
from .resources import asset
from .server import serve
from .setup import setup


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
    commands.add_parser("setup", help="Build the pinned GraphWalker execution CLI")
    validate = commands.add_parser("validate", help="Check the business specification without API calls")
    validate.add_argument("--model", type=Path, required=True)
    hosting = commands.add_parser("serve", help="Serve the demo website")
    hosting.add_argument("--port", type=int, default=4173)
    plan = commands.add_parser("plan", help="Validate and traverse a model without a browser or API calls")
    plan.add_argument("--model", type=Path, default=asset("models/booking.json"))
    exploration_arguments(plan)
    for name in ("run", "demo"):
        command = commands.add_parser(
            name, help="Run against your site" if name == "run" else "Run a bundled demo"
        )
        command.add_argument("--model", type=Path, required=name == "run")
        if name == "run":
            command.add_argument("--url", required=True, help="Application base URL")
        else:
            command.add_argument("--site", choices=("booking", "feedback"), default="booking")
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
        command.add_argument("--headed", action="store_true", help="Bring every new test tab to the front")
        command.add_argument("--cdp-url", help="Connect Browser Harness to this Chrome debugging URL")
        command.add_argument(
            "--env-file", type=Path, default=Path(".env"), help="Credential file (default: ./.env)"
        )
        command.add_argument(
            "--hooks", type=Path, help="Explicitly load a trusted Python lifecycle hooks file"
        )
        command.add_argument("--output", type=Path, default=Path("artifacts"))
        command.add_argument("--replay", type=Path)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "setup":
            setup()
            return 0
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
            with tempfile.TemporaryDirectory(prefix="model-test-plan-") as directory:
                path = Path(directory) / "model.json"
                path.write_text(json.dumps(model.document))
                print(f"Generator: {model.graph['generator']}")
                for walk in range(args.walks):
                    print(f"Walk {walk + 1}, seed {args.seed + walk}:")
                    for element in generate_path(model, path, args.seed + walk, args.max_steps):
                        spec = element["properties"]["business"]
                        print(element["name"], "—", spec.get("intent", spec.get("description")))
                print(f"Validated: {len(model.edges)} journeys, {len(model.data_sets)} business data sets")
            return 0
        load_dotenv(args.env_file)
        if not os.environ.get("TYPESAFE_API_KEY"):
            raise ValueError(
                "Live navigation needs TYPESAFE_API_KEY in the environment or --env-file (default: ./.env). "
                "Use 'model-test plan' to inspect a model without API credentials."
            )
        if args.cases > 200 or not 0.5 < args.threshold <= 1:
            raise ValueError("cases must be 1–200; confidence must be above 0.5 and at most 1")
        if args.cdp_url:
            os.environ["BU_CDP_URL"] = args.cdp_url
            # Browser Harness daemons are named: isolate distinct endpoints.
            import hashlib

            os.environ["BU_NAME"] = "model-test-" + hashlib.sha256(args.cdp_url.encode()).hexdigest()[:12]
        from .engine import run

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
                "replay",
                "input_mode",
                "max_input_attempts",
                "walks",
            )
        }
        options.update(hooks=Hooks.load(args.hooks), shrink=not args.no_shrink)
        if args.command == "demo":
            if args.bug and args.site != "booking":
                raise ValueError("The injected defect belongs to the booking demo")
            with serve() as url:
                report = run(model, url, start_query="bug=seats" if args.bug else "", **options)
        else:
            report = run(model, args.url, **options)
        return {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}[report["status"]]
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"INCONCLUSIVE: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
