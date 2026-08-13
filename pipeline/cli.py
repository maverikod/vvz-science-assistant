"""Canonical pipeline console application.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com

The single entry point through which every registered check runs. It never
hard-codes a check list: discovery walks the real ``pipeline/checks/``
package and imports every module found there, so each check file registers
itself; a new file dropped into ``pipeline/checks/`` becomes a subcommand
with zero change here.

Invocation modes:

``pipeline``
    Run every registered check in registration order; print one PASS/FAIL
    line per check plus a summary; exit non-zero if any check failed.

``pipeline <check-name>``
    Run exactly the named check; exit non-zero only if it failed.

``pipeline list`` / ``pipeline --list``
    Enumerate every registered check's name and description; run nothing.
"""

from __future__ import annotations

import argparse
import importlib
import pkgutil
import sys
from typing import IO, List, Optional, Sequence

from pipeline.registry import Check, CheckNotFoundError, CheckResult, Registry, get_registry

PROG = "pipeline"
CHECKS_PACKAGE = "pipeline.checks"


def _print_result(check: Check, result: CheckResult, stream: IO[str]) -> None:
    status_word = "PASS" if result.passed else "FAIL"
    line = f"[{status_word}] {check.name}"
    if result.message:
        line += f" - {result.message}"
    print(line, file=stream)
    if not result.passed and result.output:
        print(result.output, file=stream)


def run_named_check(name: str, registry: Optional[Registry] = None, stream: IO[str] = sys.stdout) -> int:
    """Run exactly one registered check by name: 0 pass, 1 fail, 2 unknown."""
    registry = registry if registry is not None else get_registry()
    try:
        entry = registry.lookup_by_name(name)
    except CheckNotFoundError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return 2
    result = entry.run()
    _print_result(entry, result, stream)
    return 0 if result.passed else 1


def run_all(registry: Optional[Registry] = None, stream: IO[str] = sys.stdout) -> int:
    """Run every registered check in registration order; 0 only if all passed."""
    registry = registry if registry is not None else get_registry()
    entries = registry.list_checks()
    if not entries:
        print("no checks registered", file=stream)
        return 0
    failed = 0
    for entry in entries:
        result = entry.run()
        _print_result(entry, result, stream)
        if not result.passed:
            failed += 1
    passed = len(entries) - failed
    print(f"{passed}/{len(entries)} checks passed", file=stream)
    return 1 if failed else 0


def list_registered_checks(registry: Optional[Registry] = None, stream: IO[str] = sys.stdout) -> int:
    """Print every registered check's name and description; run nothing."""
    registry = registry if registry is not None else get_registry()
    entries = registry.list_checks()
    if not entries:
        print("no checks registered", file=stream)
        return 0
    width = max(len(entry.name) for entry in entries)
    for entry in entries:
        description = entry.description or "(no description)"
        print(f"{entry.name.ljust(width)}  {description}", file=stream)
    return 0


def discover_checks(package_name: str = CHECKS_PACKAGE, stream: IO[str] = sys.stderr) -> List[str]:
    """Import every module under ``package_name`` so each check registers itself.

    A module that fails to import must not take the CLI down: the failure is
    reported as one warning line and discovery continues. Dotted names of
    modules that failed to import are returned for tests to assert on.
    """
    failed: List[str] = []
    try:
        package = importlib.import_module(package_name)
    except ImportError as exc:
        print(f"{PROG}: warning: could not import package {package_name!r}: {exc}", file=stream)
        return failed
    package_path = getattr(package, "__path__", None)
    if package_path is None:
        return failed
    for module_info in pkgutil.iter_modules(package_path, prefix=f"{package_name}."):
        if module_info.ispkg:
            continue
        try:
            importlib.import_module(module_info.name)
        except Exception as exc:  # noqa: BLE001 - a bad check must not crash the CLI
            print(f"{PROG}: warning: failed to import check module {module_info.name!r}: {exc}", file=stream)
            failed.append(module_info.name)
    return failed


def _build_parser(registry: Registry) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Canonical pipeline check runner. With no subcommand, runs every registered check.",
    )
    parser.add_argument("--list", action="store_true", help="List registered check names and descriptions; run nothing.")
    subparsers = parser.add_subparsers(dest="command", metavar="{list,<check-name>}")
    subparsers.add_parser("list", help="List registered check names and descriptions; run nothing.")
    for entry in registry.list_checks():
        # argparse %-formats help text; escape literal percents so one check's
        # description cannot take the whole CLI down.
        help_text = (entry.description or "(no description)").replace("%", "%%")
        subparsers.add_parser(entry.name, help=help_text)
    return parser


def main(argv: Optional[Sequence[str]] = None, registry: Optional[Registry] = None) -> int:
    """Entry point: discover checks, parse argv against the registry, dispatch.

    Discovery runs only against the default singleton registry; a caller that
    supplies its own Registry is doing test isolation and populates it itself.
    """
    using_default_registry = registry is None
    registry = registry if registry is not None else get_registry()
    if using_default_registry:
        discover_checks()
    parser = _build_parser(registry)
    args = parser.parse_args(argv)
    if args.list or args.command == "list":
        return list_registered_checks(registry)
    if args.command is None:
        return run_all(registry)
    return run_named_check(args.command, registry)


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
