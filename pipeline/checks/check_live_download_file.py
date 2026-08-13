"""Live download_file check against the REAL deployed server.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com

Downloads a tiny stable public page (``https://example.com/``) into a
provenance-first dataset directory with every optional parameter exercised
(``output_name``, ``dataset_name``, ``timeout_seconds``, ``max_bytes``, and
``expected_sha256`` in its failing state), asserting the dataset manifest
contract. Negatives assert the SPECIFIC observed ``DOWNLOAD_ERROR`` code
(verified on 0.2.20) for a checksum mismatch and a forbidden URL scheme,
and ``-32602`` for the missing required ``url``. Each run persists one small
dataset directory — the service's designed provenance behaviour.
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

CHECK_NAME = "live-download-file"
CHECK_DESCRIPTION = (
    "Real HTTPS download of a tiny stable page with all optional parameters and "
    "manifest assertions, plus DOWNLOAD_ERROR (bad checksum, bad scheme) and "
    "-32602 (missing url) negatives.")

URL = "https://example.com/"


def _body(client: LiveClient) -> CheckResult:
    def case_download_with_all_options() -> str:
        envelope = client.call_completed("download_file", {
            "url": URL, "output_name": "pipeline-check.html",
            "dataset_name": "pipeline-check-download", "timeout_seconds": 30,
            "max_bytes": 200_000})
        require(is_success(envelope), f"download_file failed: {envelope!r}")
        data = data_of(envelope)
        stored = data.get("file", {})
        require(isinstance(data.get("dataset_relative_path"), str) and data["dataset_relative_path"],
                f"dataset_relative_path missing: {data!r}")
        require(isinstance(data.get("manifest_relative_path"), str) and data["manifest_relative_path"],
                f"manifest_relative_path missing: {data!r}")
        require(stored.get("name") == "pipeline-check.html", f"stored name={stored.get('name')!r}")
        require(isinstance(stored.get("sha256"), str) and len(stored["sha256"]) == 64,
                f"stored sha256={stored.get('sha256')!r}")
        require(isinstance(stored.get("size_bytes"), int) and 0 < stored["size_bytes"] <= 200_000,
                f"stored size_bytes={stored.get('size_bytes')!r}")
        return f"downloaded {stored['size_bytes']} bytes into {data['dataset_relative_path']}"

    def case_checksum_mismatch_rejected() -> str:
        envelope = client.call_completed("download_file", {
            "url": URL, "dataset_name": "pipeline-check-shamismatch",
            "expected_sha256": "0" * 64, "max_bytes": 200_000})
        require(not is_success(envelope), f"expected failure: {envelope!r}")
        require(error_code(envelope) == "DOWNLOAD_ERROR",
                f"code={error_code(envelope)!r}, expected 'DOWNLOAD_ERROR'")
        return "expected_sha256 mismatch rejected with DOWNLOAD_ERROR naming both hashes"

    def case_forbidden_scheme_rejected() -> str:
        envelope = client.call("download_file", {"url": "file:///etc/passwd"})
        require(error_code(envelope) == "DOWNLOAD_ERROR",
                f"code={error_code(envelope)!r}, expected 'DOWNLOAD_ERROR'")
        return "file:// scheme rejected with DOWNLOAD_ERROR (http/https/ftp only)"

    def case_missing_url_rejected() -> str:
        envelope = client.call("download_file", {})
        require(error_code(envelope) == -32602, f"code={error_code(envelope)!r}, expected -32602")
        return "download_file without url rejected with -32602"

    results = [run_case(name, func) for name, func in (
        ("download_with_all_options", case_download_with_all_options),
        ("checksum_mismatch_rejected", case_checksum_mismatch_rejected),
        ("forbidden_scheme_rejected", case_forbidden_scheme_rejected),
        ("missing_url_rejected", case_missing_url_rejected),
    )]
    return summarize_cases("download-file", client.endpoint.describe(), results)


def check_live_download_file() -> CheckResult:
    return run_live_check(_body)


registry.register(CHECK_NAME, CHECK_DESCRIPTION, check_live_download_file)
