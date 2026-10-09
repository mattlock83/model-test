"""Explicitly loaded, trusted Python extensions; never execute code from a model."""

import hashlib
import importlib.util
import inspect
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from .errors import Defect, Inconclusive

EVENTS = (
    "before_run",
    "after_run",
    "before_walk",
    "after_walk",
    "before_reset",
    "after_reset",
    "before_state",
    "after_state",
    "before_transition",
    "after_transition",
    "before_case",
    "after_case",
)


@dataclass
class HookContext:
    """One lifecycle occurrence. scratch persists for the entire run."""

    model: Any
    url: str
    phase: str = "graph"
    element: dict | None = None
    data: dict = field(default_factory=dict)
    invalid: list = field(default_factory=list)
    result: dict | None = None
    error: BaseException | None = None
    browser: Any = None
    scratch: dict = field(default_factory=dict)
    report: dict | None = None
    walk: int | None = None

    @property
    def element_id(self):
        return self.element["id"] if self.element else None

    def check(self, condition, message):
        if not condition:
            raise Defect(message)


class Hooks:
    def __init__(self, module=None, *, path=None, digest=None):
        self.module = module or ModuleType("empty_hooks")
        self.path, self.digest = path, digest
        self.events = []
        self.scratch = {}
        for event in EVENTS:
            callback = getattr(self.module, event, None)
            if callback is not None and (not callable(callback) or inspect.iscoroutinefunction(callback)):
                raise ValueError(f"Hook {event} must be a synchronous function accepting one context")

    @classmethod
    def load(cls, path):
        if path is None:
            return cls()
        path = Path(path).expanduser().resolve()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        spec = importlib.util.spec_from_file_location("model_test_user_hooks", path)
        if spec is None or spec.loader is None:
            raise ValueError("--hooks must name a Python file")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return cls(module, path=str(path), digest=digest)

    def fire(self, event, context):
        callback = getattr(self.module, event, None)
        if callback is None:
            return
        entry = {"event": event, "phase": context.phase, "element": context.element_id}
        self.events.append(entry)
        try:
            value = callback(context)
            if inspect.isawaitable(value):
                value.close()
                raise TypeError("Async hook results are not supported")
            entry["status"] = "PASS"
        except AssertionError as error:
            entry.update(status="FAIL", error=str(error))
            raise Defect(f"Hook {event}: {error}") from error
        except Exception as error:
            entry.update(status="INCONCLUSIVE", error=str(error))
            raise Inconclusive(f"Hook {event}: {error}") from error

    @contextmanager
    def scope(self, name, context):
        context.scratch = self.scratch
        primary = None
        try:
            self.fire(f"before_{name}", context)
            yield context
        except BaseException as error:
            primary = context.error = error
            raise
        finally:
            # Teardown runs even when a before hook fails. A teardown failure must
            # never conceal the original failure or promote an uncertain result.
            try:
                self.fire(f"after_{name}", context)
            except Exception:
                if primary is None and not (
                    context.result and context.result.get("status") in {"FAIL", "INCONCLUSIVE"}
                ):
                    raise
