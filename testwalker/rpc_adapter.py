"""A declarative stdio JSON-RPC target with independent, deterministic assertions."""

import copy
import subprocess

from .errors import Defect, Inconclusive
from .hooks import HookContext, Hooks
from .rpc_contract import (
    ContractError,
    check,
    evaluate_rules,
    matching_states,
    pointer,
    resolve,
    validate_rpc_model,
)
from .rpc_transport import RpcTimeoutError, RpcTransport, RpcTransportError


class RpcAdapter:
    name = "json-rpc"

    def __init__(self, model, argv, *, timeout=30, cwd=None, hooks=None):
        self.model = model
        self.spec = validate_rpc_model(model)
        RpcTransport._timeout(timeout)
        if not argv or isinstance(argv, (str, bytes)):
            raise ValueError("Supply the target executable and arguments as a nonempty list")
        self.argv, self.timeout, self.cwd = argv, timeout, cwd
        self.hooks = hooks or Hooks()
        self.collector = self.strategy_provider = None
        self.trace, self.callback_methods = [], []
        self.variables = copy.deepcopy(self.spec.get("variables", {}))
        self.transport = None
        self.observation = {}
        self.input = {}
        self.callbacks = self.spec.get("callbacks", {})
        self.closed, self.after_run = False, False
        self.url = "stdio://" + str(argv[0])
        self.sequence = 0
        self._validate_callbacks(self.callbacks)
        for edge in model.edges.values():
            self._validate_callbacks(edge["properties"]["rpc"].get("callbacks", {}))

    @staticmethod
    def _validate_callbacks(callbacks):
        for method, definition in callbacks.items():
            if not method or not isinstance(definition, dict):
                raise ContractError("Callbacks must map method names to response objects")
            if set(definition) - {"result", "set", "request", "capture"} or "result" not in definition:
                raise ContractError(f"Callback {method} needs a result and supported callback options")
            for key in ("set", "capture"):
                if key in definition and not isinstance(definition[key], dict):
                    raise ContractError(f"Callback {key} must be an object")
            if "request" in definition:
                request = definition["request"]
                if (
                    not isinstance(request, dict)
                    or set(request) - {"method", "params"}
                    or not isinstance(request.get("method"), str)
                ):
                    raise ContractError("Nested callback requests need a method and optional params")

    def _context(self, observation=None):
        current = observation is None
        observation = self.observation if current else observation
        return {
            **observation,
            "input": self.input if current else observation.get("input", {}),
            "variables": self.variables if current else observation.get("variables", {}),
            "fixtures": self.spec.get("fixtures", {}),
        }

    def _callback(self, method, params):
        self.callback_methods.append(method)
        definition = self.callbacks.get(method)
        if definition is None:
            raise ContractError(f"No response is declared for target callback {method}")
        context = {**self._context(), "callback": {"method": method, "params": params}}
        self.variables.update(resolve(definition.get("set", {}), context))
        context["variables"] = self.variables
        if request := definition.get("request"):
            request = resolve(request, context)
            context["callback"]["response"] = self.transport.request(request["method"], request.get("params"))
        for name, path in definition.get("capture", {}).items():
            self.variables[name] = copy.deepcopy(pointer(context, path))
        return resolve(definition["result"], context)

    def _restart(self, *, reset_variables=False):
        if self.transport:
            self.transport.close()
        if reset_variables:
            self.variables = copy.deepcopy(self.spec.get("variables", {}))
        try:
            self.transport = RpcTransport(
                self.argv, handler=self._callback, timeout=self.timeout, cwd=self.cwd
            )
        except RpcTransportError as error:
            raise Inconclusive(str(error)) from error
        self.closed = False

    def _invoke(self, definition):
        definition = resolve(definition, self._context())
        if definition.get("restart"):
            self._restart()
        if self.transport is None:
            raise Inconclusive("JSON-RPC target has not been initialized")
        self.sequence += 1
        self.callback_methods = []
        if "raw" in definition:
            message = {"raw": definition["raw"]}
            expected_id = None
        elif "envelope" in definition:
            message = definition["envelope"]
            identifier = message.get("id") if isinstance(message, dict) else None
            expected_id = identifier if identifier is None or type(identifier) in (str, int, float) else None
        else:
            message = {"jsonrpc": "2.0", "id": f"api/{self.sequence}", "method": definition["method"]}
            if "params" in definition:
                message["params"] = definition["params"]
            expected_id = message["id"]
        self.observation = {"request": copy.deepcopy(message), "input": copy.deepcopy(self.input)}
        begin = len(self.transport.transcript)
        try:
            response = (
                self.transport.exchange_raw(definition["raw"], expected_id=expected_id)
                if "raw" in definition
                else self.transport.exchange(message, expected_id=expected_id)
            )
            self.observation["response"] = response
            if definition.get("wait_for_exit"):
                try:
                    self.transport.process.wait(timeout=self.timeout)
                except subprocess.TimeoutExpired as error:
                    raise Defect("The target acknowledged shutdown but did not exit") from error
        except RpcTimeoutError as error:
            raise Inconclusive(str(error), result={"request": message}) from error
        except RpcTransportError as error:
            raise Defect(f"JSON-RPC protocol violation: {error}", result={"request": message}) from error
        finally:
            self.observation["callback_methods"] = self.callback_methods.copy()
            self.observation["variables"] = copy.deepcopy(self.variables)
            self.observation["process"] = {
                "running": self.transport.process.poll() is None,
                "exit_code": self.transport.process.poll(),
            }
            self.trace.append(
                {
                    "operation": "json-rpc",
                    **copy.deepcopy(self.observation),
                    "messages": copy.deepcopy(self.transport.transcript[begin:]),
                    "stderr": self.transport.stderr,
                }
            )
        return response

    def _evaluate(self, observation):
        context = self._context(observation)
        matches = matching_states(self.model, context)
        observed = matches[0] if len(matches) == 1 else None
        checks = []
        bindings = [("global", self.spec["rules"])]
        if observed:
            bindings.append(("state", self.model.states[observed]["properties"]["rpc"]["rules"]))
        for scope, rules in bindings:
            for rule, result in evaluate_rules(rules, context).items():
                checks.append(
                    {
                        "rule": rule,
                        "scope": scope,
                        "status": "met" if result["passed"] else "broken",
                        "predicates": result["predicates"],
                    }
                )
        result = {"observed": observed, "checks": checks, "matching_states": matches}
        if observed is None:
            result["reason"] = (
                "RPC response matches no state" if not matches else "RPC response matches multiple states"
            )
            result["state_diagnostics"] = {
                identifier: check(state["properties"]["rpc"]["match"], context)
                for identifier, state in self.model.states.items()
            }
        return result

    def __call__(self, method, params):
        if method == "run.event":
            return self.collector.event(params["type"], params["data"]) if self.collector else {}
        if method == "target.strategy":
            return self.strategy_provider.build(params["context"], params["default"])
        if method == "target.initialize":
            if params.get("protocol_version") != "1":
                raise Inconclusive("Unsupported target protocol version")
            return {
                "capabilities": {"evaluator": "adapter", "isolated_checkpoints": False, "screenshots": False}
            }
        if method == "target.reset":
            self.input = {}
            self.callbacks = self.spec.get("callbacks", {})
            self._restart(reset_variables=True)
            self._invoke(self.spec["reset"])
            return {}
        if method == "target.execute":
            self.input = copy.deepcopy(params["input"])
            binding = params["edge"]["properties"]["rpc"]
            self.callbacks = {**self.spec.get("callbacks", {}), **binding.get("callbacks", {})}
            self._invoke(binding["request"])
            for name, path in binding.get("capture", {}).items():
                self.variables[name] = copy.deepcopy(pointer(self._context(), path))
            self.observation["variables"] = copy.deepcopy(self.variables)
            self.trace[-1]["variables"] = copy.deepcopy(self.variables)
            return {"executed": True}
        if method == "target.observe":
            return copy.deepcopy(self.observation)
        if method == "target.evaluate":
            return self._evaluate(params["observation"])
        if method == "target.audit":
            # Ordinary deterministic globals are checked at every observation.
            checks = []
            for rule in params["rules"]:
                results = [
                    check(self.spec["rules"][rule], self._context(record["evidence"]))
                    for record in params["records"]
                    if record.get("evidence")
                ]
                checks.append(
                    {
                        "rule": rule,
                        "scope": "global",
                        "status": "met"
                        if results and all(all(c["passed"] for c in result) for result in results)
                        else "broken",
                        "predicates": results,
                    }
                )
            return checks
        if method == "target.hook":
            if params["event"] == "after_run":
                self.after_run = True
                if self.collector and isinstance(params["context"].get("result"), dict):
                    self.collector.report.update(params["context"]["result"])
            self.hooks.fire(params["event"], self.context(params["context"]))
            return {}
        if method == "target.close":
            self.close()
            return {}
        raise Inconclusive(f"The RPC adapter does not implement {method}")

    def context(self, values):
        context = HookContext(
            model=self.model,
            url=self.url,
            target=self,
            phase=values.get("phase", "graph"),
            walk=values.get("walk"),
            element=copy.deepcopy(values.get("element")),
            data=copy.deepcopy(values.get("data") or {}),
            invalid=copy.deepcopy(values.get("invalid") or []),
            result=copy.deepcopy(values.get("result")),
            report=self.collector.report if self.collector else None,
            scratch=self.hooks.scratch,
        )
        if error := values.get("error"):
            kind = Defect if error["status"] == "FAIL" else Inconclusive
            context.error = kind(error["message"], result=error.get("result"))
        return context

    def close(self, *, keep_open=False):
        if self.transport:
            self.transport.close()
        self.closed = True
