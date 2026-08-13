"""Live health check against the REAL deployed server.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com

Asserts the deployed server's ``health`` command strictly (verified on
0.2.19 / adapter 8.10.25): ``status`` is exactly ``"ok"``, ``version`` is the
adapter version string, the command registry is populated, and proxy
registration is enabled, REGISTERED, and carries the expected server name.
An unreachable server FAILS the check; there is no skip concept.
"""

from __future__ import annotations

from pipeline import registry
from pipeline.live.client import (
    LiveClient,
    data_of,
    is_success,
    require,
    run_case,
    run_live_check,
    summarize_cases,
)
from pipeline.registry import CheckResult

CHECK_NAME = "live-health"
CHECK_DESCRIPTION = (
    "Strict health assertions against the REAL deployed server: status ok, "
    "adapter version present, populated command registry, and proxy "
    "registration enabled+registered under the expected server name.")

EXPECTED_SERVER_NAME = "science-assistant-vvz"


def _body(client: LiveClient) -> CheckResult:
    envelope = client.call("health", {})
    data = data_of(envelope)

    def case_success_and_status() -> str:
        require(is_success(envelope), f"expected success envelope: {envelope!r}")
        require(data.get("status") == "ok", f"status={data.get('status')!r}, expected 'ok'")
        return "health.success is true and status == 'ok'"

    def case_version_and_uptime() -> str:
        version = data.get("version")
        require(isinstance(version, str) and version, f"version={version!r} must be a non-empty string")
        uptime = data.get("uptime")
        require(isinstance(uptime, (int, float)) and uptime >= 0, f"uptime={uptime!r} must be a number >= 0")
        return f"adapter version {version}, uptime {uptime:.0f}s"

    def case_command_registry_populated() -> str:
        commands = data.get("components", {}).get("commands", {})
        count = commands.get("registered_count")
        require(isinstance(count, int) and count > 0, f"registered_count={count!r} must be a positive integer")
        return f"{count} commands registered"

    def case_proxy_registration() -> str:
        reg = data.get("components", {}).get("proxy_registration", {})
        require(reg.get("enabled") is True, f"proxy_registration.enabled={reg.get('enabled')!r}")
        require(reg.get("registered") is True, f"proxy_registration.registered={reg.get('registered')!r}")
        name = reg.get("server_name")
        require(name == EXPECTED_SERVER_NAME, f"server_name={name!r}, expected {EXPECTED_SERVER_NAME!r}")
        return f"registered at {reg.get('proxy_url')!r} as {name!r}"

    results = [run_case(name, func) for name, func in (
        ("success_and_status", case_success_and_status),
        ("version_and_uptime", case_version_and_uptime),
        ("command_registry_populated", case_command_registry_populated),
        ("proxy_registration", case_proxy_registration),
    )]
    return summarize_cases("health", client.endpoint.describe(), results)


def check_live_health() -> CheckResult:
    return run_live_check(_body)


registry.register(CHECK_NAME, CHECK_DESCRIPTION, check_live_health)
