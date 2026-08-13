"""Unit coverage for the canonical pipeline CLI and registry."""

import pytest

from pipeline.cli import discover_checks, main
from pipeline.registry import CheckResult, Registry


def _registry_with(*entries):
    registry = Registry()
    for name, result in entries:
        registry.register(name, f"{name} description", lambda result=result: result)
    return registry


def test_list_enumerates_without_running():
    calls = []

    def never():
        calls.append(1)
        return CheckResult.ok()

    registry = Registry()
    registry.register("one", "first", never)
    assert main(["--list"], registry) == 0
    assert main(["list"], registry) == 0
    assert calls == []


def test_no_arguments_runs_every_check_and_reports_failure():
    registry = _registry_with(("good", CheckResult.ok("fine")), ("bad", CheckResult.fail("broken")))
    assert main([], registry) == 1


def test_no_arguments_all_green_exits_zero():
    registry = _registry_with(("good", CheckResult.ok()), ("also-good", CheckResult.ok()))
    assert main([], registry) == 0


def test_single_check_by_name():
    registry = _registry_with(("good", CheckResult.ok()), ("bad", CheckResult.fail()))
    assert main(["good"], registry) == 0
    assert main(["bad"], registry) == 1


def test_unknown_name_is_rejected_by_parsing():
    registry = _registry_with(("good", CheckResult.ok()))
    with pytest.raises(SystemExit) as excinfo:
        main(["nonexistent"], registry)
    assert excinfo.value.code != 0


def test_check_exception_becomes_failure_not_crash():
    registry = Registry()

    def boom():
        raise RuntimeError("live server exploded")

    registry.register("boom", "raises", boom)
    assert main(["boom"], registry) == 1


def test_discovery_imports_real_check_modules():
    failed = discover_checks()
    assert failed == []
    from pipeline.registry import get_registry
    names = get_registry().names()
    assert "unit-tests" in names
    assert "live-health" in names
    assert "live-catalog-coverage" in names
