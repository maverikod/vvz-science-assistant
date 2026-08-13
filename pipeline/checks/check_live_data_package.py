"""Live independent-parts package assembly roundtrip.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com

Exercises ``data_package_part`` -> ``data_package_status`` ->
``data_package_wait`` against the REAL deployed server: a two-part package
on the fixed path ``pipeline-check/package-roundtrip.bin`` with
``overwrite=true`` (repeated runs replace one file), missing-part
accounting asserted between parts, and the atomic assembly verified by
downloading the assembled file back and comparing bytes. Negatives assert
the SPECIFIC observed ``PACKAGE_ERROR`` code (verified on 0.2.20) for an
unknown ``package_id`` and for a corrupted ``part_sha256``. Registration is
unconditional; an unreachable server FAILS the check.
"""

from __future__ import annotations

import base64
import hashlib
import uuid

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

CHECK_NAME = "live-data-package"
CHECK_DESCRIPTION = (
    "Two-part data_package_part/status/wait assembly on a fixed overwrite path, "
    "verified by downloading the assembled bytes back, plus PACKAGE_ERROR negatives.")

RELATIVE_PATH = "pipeline-check/package-roundtrip.bin"


def _body(client: LiveClient) -> CheckResult:
    part_a = b"science-assistant package part A: "
    part_b = b"part B closes the pipeline check."
    payload = part_a + part_b
    sha256 = hashlib.sha256(payload).hexdigest()
    package_id = f"pipeline-check-{uuid.uuid4().hex[:12]}"
    part_size = len(part_a)

    def common(part_index: int, chunk: bytes) -> dict:
        return {
            "package_id": package_id, "relative_path": RELATIVE_PATH,
            "part_index": part_index, "part_count": 2, "part_size_bytes": part_size,
            "total_size_bytes": len(payload), "sha256": sha256,
            "part_sha256": hashlib.sha256(chunk).hexdigest(),
            "data_base64": base64.b64encode(chunk).decode(), "overwrite": True,
        }

    def case_parts_and_status_accounting() -> str:
        first = client.call("data_package_part", common(0, part_a))
        require(is_success(first), f"part 0 failed: {first!r}")
        data = data_of(first)
        require(data.get("received_count") == 1 and data.get("part_count") == 2,
                f"part 0 accounting wrong: {data!r}")
        status = client.call("data_package_status", {"package_id": package_id, "page": 1, "page_size": 10})
        require(is_success(status), f"status failed: {status!r}")
        require(data_of(status).get("status") == "receiving",
                f"status={data_of(status).get('status')!r}, expected 'receiving' with one part missing")
        second = client.call("data_package_part", common(1, part_b))
        require(is_success(second), f"part 1 failed: {second!r}")
        require(data_of(second).get("received_count") == 2, f"part 1 accounting wrong: {data_of(second)!r}")
        return "both parts accepted; status between them is 'receiving' with correct accounting"

    def case_wait_assembles_atomically() -> str:
        envelope = client.call("data_package_wait", {
            "package_id": package_id, "relative_path": RELATIVE_PATH, "part_count": 2,
            "part_size_bytes": part_size, "total_size_bytes": len(payload), "sha256": sha256,
            "overwrite": True, "timeout_seconds": 30, "poll_interval_ms": 200})
        require(is_success(envelope), f"data_package_wait failed: {envelope!r}")
        data = data_of(envelope)
        require(data.get("status") == "completed", f"status={data.get('status')!r}")
        stored = data.get("file", {})
        require(stored.get("sha256") == sha256 and stored.get("size_bytes") == len(payload),
                f"assembled file mismatch: {stored!r}")
        return f"package assembled atomically into {RELATIVE_PATH} with matching SHA-256"

    def case_assembled_bytes_downloadable() -> str:
        begin = client.call("data_download_begin", {"relative_path": RELATIVE_PATH})
        require(is_success(begin), f"download_begin failed: {begin!r}")
        tid = data_of(begin)["transfer_id"]
        chunk = client.call("data_download_chunk", {"transfer_id": tid, "offset": 0, "limit": len(payload)})
        require(is_success(chunk), f"download_chunk failed: {chunk!r}")
        received = base64.b64decode(data_of(chunk).get("data_base64", ""))
        require(received == payload, f"assembled bytes differ: {len(received)} vs {len(payload)}")
        return "assembled file downloaded back and byte-identical"

    def case_unknown_package_rejected() -> str:
        envelope = client.call("data_package_status", {"package_id": f"no-such-{uuid.uuid4().hex[:8]}"})
        require(error_code(envelope) == "PACKAGE_ERROR",
                f"code={error_code(envelope)!r}, expected 'PACKAGE_ERROR'")
        return "unknown package_id rejected with PACKAGE_ERROR"

    def case_corrupted_part_sha_rejected() -> str:
        params = common(0, part_a)
        params.update({"package_id": f"pipeline-check-bad-{uuid.uuid4().hex[:8]}",
                       "relative_path": "pipeline-check/package-bad.bin", "part_sha256": "0" * 64})
        envelope = client.call("data_package_part", params)
        require(error_code(envelope) == "PACKAGE_ERROR",
                f"code={error_code(envelope)!r}, expected 'PACKAGE_ERROR'")
        return "part with wrong part_sha256 rejected with PACKAGE_ERROR"

    results = [run_case(name, func) for name, func in (
        ("parts_and_status_accounting", case_parts_and_status_accounting),
        ("wait_assembles_atomically", case_wait_assembles_atomically),
        ("assembled_bytes_downloadable", case_assembled_bytes_downloadable),
        ("unknown_package_rejected", case_unknown_package_rejected),
        ("corrupted_part_sha_rejected", case_corrupted_part_sha_rejected),
    )]
    return summarize_cases("data-package", client.endpoint.describe(), results)


def check_live_data_package() -> CheckResult:
    return run_live_check(_body)


registry.register(CHECK_NAME, CHECK_DESCRIPTION, check_live_data_package)
