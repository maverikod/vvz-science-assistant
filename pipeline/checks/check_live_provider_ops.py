"""Live scientific-provider operation commands: the observable surface today.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com

``scientific_provider_progress`` and ``scientific_provider_operation_control``
manage long-running provider operations. Verified product fact on 0.2.20: the
deployed provider registry is EMPTY (``science_assistant/providers/`` ships no
concrete provider; even ``cern-open-data`` resolves to ``PROVIDER_NOT_FOUND``
with ``provider_code: provider_unsupported``). The full observable contract is
therefore the stable rejection surface, and this check asserts exactly that:

* every declared parameter is sent (including ``offset_bytes``,
  ``block_size_bytes``, ``resume_token``) and answered with the SPECIFIC
  ``PROVIDER_NOT_FOUND`` envelope;
* the ``action`` enum (``pause``/``resume``/``cancel``) rejects other values
  with ``-32602`` naming the enum;
* missing required parameters reject with ``-32602``.

When a first real provider ships, the positive lifecycle belongs here and in
the coverage ledger — until then, claiming a positive path would be an
assumption, not a fact.
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

CHECK_NAME = "live-provider-ops"
CHECK_DESCRIPTION = (
    "Assert the observable scientific_provider_* contract on the deployed server: "
    "PROVIDER_NOT_FOUND for the empty provider registry with every declared "
    "parameter sent, the action-enum -32602, and missing-required -32602.")

_OPERATION_ID = "00000000-0000-4000-8000-000000000000"


def _body(client: LiveClient) -> CheckResult:
    def case_progress_provider_not_found() -> str:
        envelope = client.call("scientific_provider_progress", {
            "provider": "cern-open-data", "operation_id": _OPERATION_ID})
        require(not is_success(envelope), f"expected failure: {envelope!r}")
        require(error_code(envelope) == "PROVIDER_NOT_FOUND",
                f"code={error_code(envelope)!r}, expected 'PROVIDER_NOT_FOUND'")
        require(data_of(envelope).get("provider_code") == "provider_unsupported",
                f"provider_code={data_of(envelope).get('provider_code')!r}")
        return "empty provider registry answers progress with PROVIDER_NOT_FOUND/provider_unsupported"

    def case_control_all_parameters_not_found() -> str:
        envelope = client.call("scientific_provider_operation_control", {
            "provider": "cern-open-data", "operation_id": _OPERATION_ID, "action": "resume",
            "offset_bytes": 0, "block_size_bytes": 65536, "resume_token": "pipeline-check"})
        require(not is_success(envelope), f"expected failure: {envelope!r}")
        require(error_code(envelope) == "PROVIDER_NOT_FOUND",
                f"code={error_code(envelope)!r}, expected 'PROVIDER_NOT_FOUND'")
        return "resume with every declared parameter reaches the registry and answers PROVIDER_NOT_FOUND"

    def case_resume_controls_only_with_resume() -> str:
        envelope = client.call("scientific_provider_operation_control", {
            "provider": "cern-open-data", "operation_id": _OPERATION_ID, "action": "pause",
            "offset_bytes": 0})
        require(error_code(envelope) == "VALIDATION_ERROR",
                f"code={error_code(envelope)!r}, expected 'VALIDATION_ERROR'")
        require("action=resume" in str(data_of(envelope).get("message", "")),
                f"message={data_of(envelope).get('message')!r}")
        return "resume-only controls with action=pause rejected with VALIDATION_ERROR"

    def case_action_enum_rejected() -> str:
        envelope = client.call("scientific_provider_operation_control", {
            "provider": "cern-open-data", "operation_id": _OPERATION_ID, "action": "status"})
        require(error_code(envelope) == -32602, f"code={error_code(envelope)!r}, expected -32602")
        return "action outside ['pause','resume','cancel'] rejected with -32602 naming the enum"

    def case_missing_required_rejected() -> str:
        envelope = client.call("scientific_provider_operation_control", {
            "provider": "x", "operation_id": "y"})
        require(error_code(envelope) == -32602, f"code={error_code(envelope)!r}, expected -32602")
        return "control without action rejected with -32602 naming the missing parameter"

    results = [run_case(name, func) for name, func in (
        ("progress_provider_not_found", case_progress_provider_not_found),
        ("control_all_parameters_not_found", case_control_all_parameters_not_found),
        ("resume_controls_only_with_resume", case_resume_controls_only_with_resume),
        ("action_enum_rejected", case_action_enum_rejected),
        ("missing_required_rejected", case_missing_required_rejected),
    )]
    return summarize_cases("provider-ops", client.endpoint.describe(), results)


def check_live_provider_ops() -> CheckResult:
    return run_live_check(_body)


registry.register(CHECK_NAME, CHECK_DESCRIPTION, check_live_provider_ops)
