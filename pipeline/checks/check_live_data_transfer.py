"""Live MCP-native data transfer roundtrip: upload then download streams.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com

Exercises the full ``data_upload_begin/chunk/status/complete`` and
``data_download_begin/chunk/status`` surface against the REAL deployed
server on the fixed path ``pipeline-check/upload-roundtrip.bin`` with
``overwrite=true``, so repeated runs replace one file instead of
accumulating. Chunked in two parts to exercise offsets, with the download
read back in sub-chunks and byte-compared. Negatives assert the SPECIFIC
observed codes (verified on 0.2.20): ``TRANSFER_ERROR`` for an existing
target without overwrite and for unknown transfer ids, ``-32602`` for a
missing required parameter. Registration is unconditional; an unreachable
server FAILS the check.
"""

from __future__ import annotations

import base64
import hashlib

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

CHECK_NAME = "live-data-transfer"
CHECK_DESCRIPTION = (
    "Full data_upload_*/data_download_* roundtrip on a fixed overwrite path with "
    "chunked offsets and byte comparison, plus TRANSFER_ERROR and -32602 negatives.")

RELATIVE_PATH = "pipeline-check/upload-roundtrip.bin"


def _body(client: LiveClient) -> CheckResult:
    # Even length: the server enforces the negotiated chunk_size as a hard cap,
    # so both halves must be exactly chunk_size bytes.
    payload = b"science-assistant pipeline data-transfer roundtrip payload 012345678"
    assert len(payload) % 2 == 0
    half = len(payload) // 2
    sha256 = hashlib.sha256(payload).hexdigest()
    state: dict = {}

    def case_upload_full_lifecycle() -> str:
        begin = client.call("data_upload_begin", {
            "relative_path": RELATIVE_PATH, "size_bytes": len(payload), "sha256": sha256,
            "chunk_size": half, "overwrite": True})
        require(is_success(begin), f"data_upload_begin failed: {begin!r}")
        data = data_of(begin)
        require(data.get("direction") == "upload" and data.get("status") == "created",
                f"begin direction/status wrong: {data!r}")
        tid = data.get("transfer_id")
        require(isinstance(tid, str) and tid.startswith("up_"), f"transfer_id={tid!r}")
        state["upload_id"] = tid
        for offset, chunk in ((0, payload[:half]), (half, payload[half:])):
            envelope = client.call("data_upload_chunk", {
                "transfer_id": tid, "offset": offset,
                "data_base64": base64.b64encode(chunk).decode()})
            require(is_success(envelope), f"chunk at {offset} failed: {envelope!r}")
            require(data_of(envelope).get("offset") == offset + len(chunk),
                    f"offset after chunk wrong: {data_of(envelope)!r}")
        status = client.call("data_upload_status", {"transfer_id": tid})
        require(is_success(status) and data_of(status).get("status") == "uploaded",
                f"status after chunks: {data_of(status).get('status')!r}, expected 'uploaded'")
        complete = client.call("data_upload_complete", {"transfer_id": tid})
        require(is_success(complete), f"data_upload_complete failed: {complete!r}")
        stored = data_of(complete).get("file", {})
        require(data_of(complete).get("status") == "completed", "complete status must be 'completed'")
        require(stored.get("sha256") == sha256 and stored.get("size_bytes") == len(payload),
                f"stored file mismatch: {stored!r}")
        return f"two-chunk upload of {len(payload)} bytes completed and verified by the server"

    def case_download_full_lifecycle() -> str:
        begin = client.call("data_download_begin", {"relative_path": RELATIVE_PATH, "chunk_size": half})
        require(is_success(begin), f"data_download_begin failed: {begin!r}")
        data = data_of(begin)
        require(data.get("direction") == "download" and data.get("status") == "ready",
                f"begin direction/status wrong: {data!r}")
        require(data.get("size_bytes") == len(payload), f"size_bytes={data.get('size_bytes')!r}")
        tid = data.get("transfer_id")
        require(isinstance(tid, str) and tid.startswith("down_"), f"transfer_id={tid!r}")
        received, offset = b"", 0
        while True:
            chunk_envelope = client.call("data_download_chunk", {"transfer_id": tid, "offset": offset, "limit": half})
            require(is_success(chunk_envelope), f"download chunk at {offset} failed: {chunk_envelope!r}")
            chunk_data = data_of(chunk_envelope)
            received += base64.b64decode(chunk_data.get("data_base64", ""))
            offset = chunk_data.get("next_offset", offset)
            if chunk_data.get("eof") is True:
                break
            require(len(received) <= len(payload), "download did not terminate")
        require(received == payload, f"downloaded bytes differ: {len(received)} vs {len(payload)}")
        status = client.call("data_download_status", {"transfer_id": tid})
        require(is_success(status) and data_of(status).get("direction") == "download",
                f"download status wrong: {data_of(status)!r}")
        return f"chunked download returned the identical {len(payload)} bytes with eof"

    def case_existing_target_without_overwrite() -> str:
        envelope = client.call("data_upload_begin", {
            "relative_path": RELATIVE_PATH, "size_bytes": 5, "sha256": "0" * 64})
        require(not is_success(envelope), f"expected failure: {envelope!r}")
        require(error_code(envelope) == "TRANSFER_ERROR",
                f"code={error_code(envelope)!r}, expected 'TRANSFER_ERROR'")
        return "existing target without overwrite rejected with TRANSFER_ERROR"

    def case_unknown_transfer_ids() -> str:
        for command in ("data_upload_status", "data_upload_complete", "data_download_status"):
            envelope = client.call(command, {"transfer_id": "up_00000000000000000000000000000000"})
            require(error_code(envelope) == "TRANSFER_ERROR",
                    f"{command}: code={error_code(envelope)!r}, expected 'TRANSFER_ERROR'")
        return "unknown transfer_id rejected with TRANSFER_ERROR on status and complete paths"

    def case_missing_required_rejected() -> str:
        envelope = client.call("data_upload_chunk", {"offset": 0, "data_base64": "QQ=="})
        require(error_code(envelope) == -32602, f"code={error_code(envelope)!r}, expected -32602")
        return "data_upload_chunk without transfer_id rejected with -32602"

    results = [run_case(name, func) for name, func in (
        ("upload_full_lifecycle", case_upload_full_lifecycle),
        ("download_full_lifecycle", case_download_full_lifecycle),
        ("existing_target_without_overwrite", case_existing_target_without_overwrite),
        ("unknown_transfer_ids", case_unknown_transfer_ids),
        ("missing_required_rejected", case_missing_required_rejected),
    )]
    return summarize_cases("data-transfer", client.endpoint.describe(), results)


def check_live_data_transfer() -> CheckResult:
    return run_live_check(_body)


registry.register(CHECK_NAME, CHECK_DESCRIPTION, check_live_data_transfer)
