"""Process-wide registry for named checks used by the canonical pipeline CLI.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com

One dependency-free mechanism binds a check name to a description and a
zero-argument callable returning a pass/fail result with captured output.
Every check kind registers through the same API: unit, package, version,
and live-server checks alike; nothing here assumes a check is fast, pure,
or offline.

Importing this module has no side effects beyond defining the classes and
the module-level singleton. The registry never imports check modules;
discovery is the CLI's job. Each check module lives in its own file under
``pipeline/checks/`` and registers itself at import time via
``registry.register(name, description, func)``.
"""

from __future__ import annotations

import dataclasses
import enum
import re
import traceback
from typing import Callable, Iterator, List

# A check name doubles as the CLI subcommand token: letters first, then
# letters/digits/hyphens/underscores, no spaces.
_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


class CheckStatus(enum.Enum):
    """Outcome of running a single check."""

    PASS = "pass"
    FAIL = "fail"


@dataclasses.dataclass(frozen=True)
class CheckResult:
    """Structured outcome of a check: status, a message, and captured output."""

    status: CheckStatus
    message: str = ""
    output: str = ""

    @property
    def passed(self) -> bool:
        return self.status is CheckStatus.PASS

    @classmethod
    def ok(cls, message: str = "", output: str = "") -> "CheckResult":
        return cls(CheckStatus.PASS, message, output)

    @classmethod
    def fail(cls, message: str = "", output: str = "") -> "CheckResult":
        return cls(CheckStatus.FAIL, message, output)


CheckCallable = Callable[[], CheckResult]


class CheckNotFoundError(LookupError):
    """Raised by lookup_by_name() when no check is registered under a name."""

    def __init__(self, name: str, available: List[str]):
        if available:
            known = ", ".join(sorted(available))
            message = f"no check registered under name {name!r}; known checks: {known}"
        else:
            message = f"no check registered under name {name!r}; the registry is empty"
        super().__init__(message)
        self.name = name
        self.available = list(available)


@dataclasses.dataclass(frozen=True)
class Check:
    """A single registered check: its identity plus its zero-argument callable."""

    name: str
    description: str
    func: CheckCallable

    def run(self) -> CheckResult:
        """Invoke the callable; any exception becomes a failing CheckResult."""
        try:
            result = self.func()
        except Exception:  # noqa: BLE001 - convert any failure into a result
            return CheckResult(
                status=CheckStatus.FAIL,
                message=f"check {self.name!r} raised an exception",
                output=traceback.format_exc(),
            )
        if not isinstance(result, CheckResult):
            raise TypeError(
                f"check {self.name!r} must return a CheckResult, got {type(result).__name__}"
            )
        return result


class Registry:
    """Ordered collection of named checks, keyed by unique name."""

    def __init__(self) -> None:
        self._checks: dict[str, Check] = {}

    def register(self, name: str, description: str, func: CheckCallable, *, replace: bool = False) -> Check:
        """Register a check; the name must be a valid CLI subcommand token."""
        if not name:
            raise ValueError("check name must be a non-empty string")
        if not _NAME_PATTERN.match(name):
            raise ValueError(
                f"check name {name!r} is not a valid subcommand token; it must start with a "
                "letter and contain only letters, digits, hyphens, and underscores"
            )
        if not isinstance(description, str):
            raise TypeError(f"check {name!r}: description must be a string")
        if not callable(func):
            raise TypeError(f"check {name!r}: func must be callable")
        if name in self._checks and not replace:
            raise ValueError(f"a check named {name!r} is already registered (pass replace=True to override)")
        entry = Check(name=name, description=description, func=func)
        self._checks[name] = entry
        return entry

    def check(self, name: str, description: str = "") -> Callable[[CheckCallable], CheckCallable]:
        """Decorator form of register()."""

        def decorator(func: CheckCallable) -> CheckCallable:
            self.register(name, description, func)
            return func

        return decorator

    def lookup_by_name(self, name: str) -> Check:
        try:
            return self._checks[name]
        except KeyError:
            raise CheckNotFoundError(name, self.names()) from None

    def list_checks(self) -> List[Check]:
        return list(self._checks.values())

    def names(self) -> List[str]:
        return list(self._checks.keys())

    def __iter__(self) -> Iterator[Check]:
        return iter(self._checks.values())

    def __len__(self) -> int:
        return len(self._checks)

    def __contains__(self, name: object) -> bool:
        return name in self._checks

    def clear(self) -> None:
        """Remove all registered checks. Intended for test isolation only."""
        self._checks.clear()


_registry = Registry()


def get_registry() -> Registry:
    """Return the process-wide Registry singleton."""
    return _registry


def register(name: str, description: str, func: CheckCallable, *, replace: bool = False) -> Check:
    return _registry.register(name, description, func, replace=replace)


def check(name: str, description: str = "") -> Callable[[CheckCallable], CheckCallable]:
    return _registry.check(name, description)


def list_checks() -> List[Check]:
    return _registry.list_checks()


def lookup_by_name(name: str) -> Check:
    return _registry.lookup_by_name(name)
