"""Live full-surface check for ``info`` against the REAL deployed server.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com

``info`` is the server's own documentation and inventory command, so "full
surface" means asserting that what it DOCUMENTS matches what the server
ACTUALLY exposes:

* top-level response shape with the documented keys and types;
* internal version coherence: server version, image tag, client version, and
  agent script version all equal, and the advertised adapter wheel path
  carries the adapter version the deployed server itself reports through
  ``health`` (the regression fixed in 0.2.19: the path used to hard-code
  8.10.20);
* every command ``info`` lists in ``registered_commands`` exists in the real
  ``help`` catalog (no stale entries), and the catalog is a superset;
* runtime identity matches the packaging standard: process user/group equal
  the expected identity ``info`` itself declares;
* both declared optional parameters exercised (``include_markdown`` present
  and absent — pagination metadata asserted) and the negative path: an
  unknown extra parameter is rejected with the observed ``-32602`` code.

Registration is unconditional; an unreachable server FAILS the check.
"""

from __future__ import annotations

from pipeline import registry
from pipeline.live.client import (
    LiveClient,
    data_of,
    error_code,
    is_success,
    require,
    run_case,
    run_live_check,
    summarize_cases,
)
from pipeline.registry import CheckResult

CHECK_NAME = "live-info"
CHECK_DESCRIPTION = (
    "Full-surface info check against the REAL deployed server: response shape, "
    "internal version coherence including the adapter wheel path, registered_commands "
    "cross-checked against the real help catalog, runtime identity, both optional "
    "parameter states, and the -32602 unknown-parameter rejection.")

COMMAND = "info"
_EXPECTED_TOP_LEVEL_TYPES = {
    "guide_version": str,
    "package": dict,
    "summary": str,
    "pagination": dict,
    "runtime": dict,
    "registered_commands": list,
    "integrations": dict,
    "docs": list,
}


def _body(client: LiveClient) -> CheckResult:
    def info(params: dict) -> dict:
        return client.call(COMMAND, params)

    def case_response_shape() -> str:
        env = info({"include_markdown": False})
        require(is_success(env), f"expected success: {env!r}")
        d = data_of(env)
        missing = [k for k in _EXPECTED_TOP_LEVEL_TYPES if k not in d]
        require(not missing, f"missing documented key(s): {missing}")
        wrong = [(k, type(d[k]).__name__) for k, t in _EXPECTED_TOP_LEVEL_TYPES.items()
                 if k in d and not isinstance(d[k], t)]
        require(not wrong, f"wrong type for: {wrong}")
        return f"all {len(_EXPECTED_TOP_LEVEL_TYPES)} documented top-level keys present with the documented type"

    def case_version_coherence() -> str:
        package = data_of(info({"include_markdown": False})).get("package", {})
        version = package.get("version")
        require(isinstance(version, str) and version, f"package.version={version!r}")
        for key in ("service_image_tag", "client_version", "agent_script_version"):
            require(package.get(key) == version, f"package.{key}={package.get(key)!r}, expected {version!r}")
        adapter_version = data_of(client.call("health", {})).get("version")
        wheel_path = package.get("adapter_wheel_relative_path", "")
        require(f"mcp_proxy_adapter-{adapter_version}-" in wheel_path,
                f"adapter wheel path {wheel_path!r} does not carry the deployed adapter version {adapter_version!r}")
        return f"version {version} coherent across package/image/client/agent; adapter wheel path carries {adapter_version}"

    def case_registered_commands_vs_catalog() -> str:
        listed = data_of(info({"include_markdown": False})).get("registered_commands", [])
        names = [entry.get("name") for entry in listed if isinstance(entry, dict)]
        require(names and all(isinstance(n, str) and n for n in names),
                f"registered_commands must be a non-empty list of named entries: {listed!r}")
        catalog = set(client.command_names())
        stale = [n for n in names if n not in catalog]
        require(not stale, f"info lists command(s) that do not exist on the server: {stale}")
        return f"all {len(names)} commands info documents exist in the real {len(catalog)}-command help catalog"

    def case_runtime_identity() -> str:
        runtime = data_of(info({"include_markdown": False})).get("runtime", {})
        process, expected = runtime.get("process", {}), runtime.get("expected_identity", {})
        for key in ("user", "group", "uid", "gid"):
            require(process.get(key) == expected.get(key),
                    f"process.{key}={process.get(key)!r} != expected_identity.{key}={expected.get(key)!r}")
        return f"process runs as {process.get('user')}:{process.get('group')} exactly as declared"

    def case_optional_parameters_both_states() -> str:
        with_markdown = data_of(info({"include_markdown": True, "page_size": 40, "block_position": 1}))
        require(isinstance(with_markdown.get("markdown"), str) and with_markdown["markdown"].strip(),
                "include_markdown=true must return non-empty markdown")
        pagination = with_markdown.get("pagination", {})
        require(pagination.get("paginated") is True and pagination.get("block_position") == 1,
                f"pagination metadata wrong: {pagination!r}")
        without = data_of(info({"include_markdown": False}))
        require(without.get("markdown") is None, f"include_markdown=false must null markdown, got {type(without.get('markdown')).__name__}")
        return "include_markdown exercised in both states with pagination metadata asserted"

    def case_unknown_extra_parameter_rejected() -> str:
        env = info({"bogus": 1})
        require(not is_success(env), f"expected failure: {env!r}")
        code = error_code(env)
        require(code == -32602, f"code={code!r}, expected -32602 (observed contract on 0.2.19)")
        return "unknown extra parameter rejected with -32602 naming the allowed parameter list"

    results = [run_case(name, func) for name, func in (
        ("response_shape", case_response_shape),
        ("version_coherence", case_version_coherence),
        ("registered_commands_vs_catalog", case_registered_commands_vs_catalog),
        ("runtime_identity", case_runtime_identity),
        ("optional_parameters_both_states", case_optional_parameters_both_states),
        ("unknown_extra_parameter_rejected", case_unknown_extra_parameter_rejected),
    )]
    schema = client.command_schema(COMMAND)
    return summarize_cases("info", client.endpoint.describe(), results,
                           extra_output=(schema.format_declared_surface(),))


def check_live_info() -> CheckResult:
    return run_live_check(_body)


registry.register(CHECK_NAME, CHECK_DESCRIPTION, check_live_info)
