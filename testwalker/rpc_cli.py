"""CLI entry points for declarative RPC models and the bundled self-test."""

from pathlib import Path

from .config import RuntimeConfig
from .core import CoreClient, core_executable
from .engine import run
from .hooks import Hooks
from .input_strategies import StrategyProvider, load_policy
from .model import load_model
from .policy import exploration_model
from .resources import asset
from .rpc_adapter import RpcAdapter
from .rpc_transport import RpcTransport


def add_commands(commands, exploration_arguments, input_arguments):
    for name in ("rpc", "self-test"):
        command = commands.add_parser(
            name,
            help="Test a stdio JSON-RPC API"
            if name == "rpc"
            else "Test Testwalker's own JSON-RPC API, without a browser or key",
        )
        command.add_argument("--model", type=Path, required=name == "rpc")
        command.add_argument(
            "--target",
            type=Path,
            required=name == "rpc",
            help="Target executable; self-test defaults to the native core",
        )
        command.add_argument(
            "--target-arg",
            action="append",
            default=[],
            help="Target argument; repeat as needed (use --target-arg=--flag)",
        )
        command.add_argument("--cwd", type=Path, help="Working directory of the target process")
        command.add_argument("--core", type=Path, help="Driver core executable override")
        command.add_argument(
            "--config", type=Path, help="Optional properties file for TESTWALKER_CORE_BIN; no key required"
        )
        command.add_argument(
            "--timeout", type=float, default=30, help="Seconds allowed for each target RPC request"
        )
        command.add_argument(
            "--hooks", type=Path, help="Trusted Python lifecycle hooks; target available as ctx.target"
        )
        command.add_argument("--output", type=Path, default=Path("artifacts"))
        command.add_argument("--replay", type=Path, help="Replay one saved graph or property case")
        command.add_argument(
            "--plan", action="store_true", help="Print the inventory without starting the target"
        )
        command.add_argument("--debug", action="store_true")
        exploration_arguments(command)
        input_arguments(command)
        command.set_defaults(max_steps=1000)


def execute(args):
    RpcTransport._timeout(args.timeout)
    if args.plan and (args.replay or args.strategy_provider):
        raise ValueError("--plan cannot execute replay or custom strategy providers")
    model = load_model(args.model or asset("models/rpc-selftest.json"))
    if args.replay and any(
        value is not None for value in (args.generator, args.edge_coverage, args.state_coverage)
    ):
        raise ValueError("Replay uses the saved model; do not override its exploration settings")
    if args.replay and (args.input_strategies or args.strategy_provider):
        raise ValueError("Replay uses its saved input; do not supply input strategy overrides")
    if args.cases > 200 or args.walks > 100:
        raise ValueError("cases must be 1–200; walks must be 1–100")
    model = exploration_model(
        model, generator=args.generator, edge_coverage=args.edge_coverage, state_coverage=args.state_coverage
    )
    binary = args.core
    if args.config:
        config = RuntimeConfig.load(args.config)
        binary = binary or config.core_binary
    binary = core_executable(binary)
    target = args.target or binary
    argv = [str(target.expanduser().resolve()), *args.target_arg]
    adapter = RpcAdapter(model, argv, timeout=args.timeout, cwd=args.cwd)
    policy = load_policy(args.input_strategies, model.data_sets)
    if args.input_mode != "focused" and (args.input_strategies or args.strategy_provider):
        raise ValueError("Input strategy configuration and providers require --input-mode focused")
    options = dict(
        seed=args.seed,
        walks=args.walks,
        max_steps=args.max_steps,
        cases=args.cases,
        input_mode=args.input_mode,
        input_strategies=policy,
        max_input_attempts=args.max_input_attempts,
        shrink=not args.no_shrink,
        evaluator="adapter",
    )
    with CoreClient(binary) as core:
        if args.plan:
            report = core.request("run.plan", model=model.document, options=options)
            phases = report["scope"]["property_selection"]["selected_phases"]
            print(f"Planned {len(report['tests'])} checks; {phases} property phases")
            for test in report["tests"]:
                print(test["id"], "—", test["name"])
            return 0
        options.pop("evaluator")
        hooks = Hooks.load(args.hooks)
        provider = StrategyProvider.load(args.strategy_provider)
        report = run(
            model,
            "stdio://" + argv[0],
            core=core,
            target_adapter=adapter,
            hooks=hooks,
            strategy_provider=provider,
            output=args.output,
            replay=args.replay,
            debug=args.debug,
            **options,
        )
    return {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}[report["status"]]
