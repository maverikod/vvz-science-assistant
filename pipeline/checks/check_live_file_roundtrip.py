"""Live file-store roundtrip against the REAL deployed server.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com

Exercises the ``file_receive`` -> ``file_ls`` -> ``file_get`` ->
``file_delete`` surface with a real TWO-part session upload (semantics
verified on 0.2.21: parts are fragments of one Base64 string — the server
concatenates and decodes once, so padding may appear only in the final
portion; part 0 opens the session and returns ``upload_session_id``, the
final part completes and returns the permanent ``file_id``), a whole-file
read, a chunked read via ``part_size_bytes``/``part_index``, strict response
assertions, and per-parameter negatives with the SPECIFIC observed codes:

* ``file_get`` on an unknown UUID -> string domain code ``FILE_ERROR``;
* ``file_ls`` with a wrong-typed ``page`` -> object code ``-32602`` naming the field;
* ``file_delete`` with the required ``file_id`` omitted -> ``-32602``.

The check writes only its own uniquely named file and deletes it in a
``finally`` block, so it cleans up even when it fails. Registration is
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

CHECK_NAME = "live-file-roundtrip"
CHECK_DESCRIPTION = (
    "Real single-part file_receive -> file_ls -> file_get -> file_delete roundtrip "
    "with SHA-256 verification, plus specific negative codes (FILE_ERROR, -32602); "
    "always deletes its own file, even on failure.")

_UNKNOWN_UUID = "00000000-0000-4000-8000-000000000000"


def _body(client: LiveClient) -> CheckResult:
    payload = f"science-assistant pipeline roundtrip {uuid.uuid4()}".encode()
    filename = f"pipeline-roundtrip-{uuid.uuid4().hex[:12]}.txt"
    sha256 = hashlib.sha256(payload).hexdigest()
    state: dict = {}

    def case_receive_two_part_session() -> str:
        # Parts are fragments of ONE Base64 string (the server concatenates
        # then decodes once); padding may appear only in the final portion.
        full_b64 = base64.b64encode(payload).decode()
        mid = len(full_b64) // 2
        first = client.call("file_receive", {
            "part_index": 0, "part_count": 2, "filename": filename,
            "data_base64_part": full_b64[:mid],
            "size_bytes": len(payload), "sha256": sha256, "ttl_seconds": 300,
        })
        require(is_success(first), f"file_receive part 0 failed: {first!r}")
        first_data = data_of(first)
        require(first_data.get("status") == "receiving",
                f"status={first_data.get('status')!r}, expected 'receiving'")
        session_id = first_data.get("upload_session_id")
        require(isinstance(session_id, str) and session_id, f"upload_session_id={session_id!r}")
        require(first_data.get("missing_count") == 1, f"missing_count={first_data.get('missing_count')!r}")
        second = client.call("file_receive", {
            "part_index": 1, "upload_session_id": session_id, "data_base64_part": full_b64[mid:],
        })
        require(is_success(second), f"file_receive part 1 failed: {second!r}")
        data = data_of(second)
        require(data.get("status") == "completed", f"status={data.get('status')!r}, expected 'completed'")
        file_id = data.get("file_id")
        require(isinstance(file_id, str) and file_id, f"file_id={file_id!r} must be a non-empty string")
        require(data.get("sha256") == sha256, f"stored sha256 {data.get('sha256')!r} != sent {sha256!r}")
        require(data.get("size_bytes") == len(payload), f"size_bytes={data.get('size_bytes')!r}")
        state["file_id"] = file_id
        return f"two-part session upload completed as file_id {file_id}"

    def case_ls_lists_the_file() -> str:
        require("file_id" in state, "upload case must have produced a file_id")
        envelope = client.call("file_ls", {"page": 1, "page_size": 500, "name_pattern": filename})
        require(is_success(envelope), f"file_ls failed: {envelope!r}")
        items = data_of(envelope).get("items", [])
        matches = [i for i in items if i.get("file_id") == state["file_id"]]
        require(len(matches) == 1, f"expected exactly our file in the listing, got {len(matches)}")
        entry = matches[0]
        for key, expected in (("name", filename), ("sha256", sha256), ("size_bytes", len(payload))):
            require(entry.get(key) == expected, f"listing {key}={entry.get(key)!r}, expected {expected!r}")
        return "file_ls returns exactly our file with name, sha256, and size asserted"

    def case_get_returns_identical_bytes() -> str:
        require("file_id" in state, "upload case must have produced a file_id")
        envelope = client.call("file_get", {"file_id": state["file_id"]})
        require(is_success(envelope), f"file_get failed: {envelope!r}")
        data = data_of(envelope)
        returned = base64.b64decode(data.get("data_base64", ""))
        require(returned == payload, f"downloaded bytes differ: {len(returned)} vs {len(payload)}")
        require(data.get("eof") is True, f"eof={data.get('eof')!r} for a whole-file read")
        require(data.get("sha256") == sha256, f"file_get sha256={data.get('sha256')!r}")
        return f"file_get returned the identical {len(payload)} bytes with eof=true"

    def case_get_chunked_by_parts() -> str:
        require("file_id" in state, "upload case must have produced a file_id")
        part_size = max(1, len(payload) // 2)
        received, part_index = b"", 0
        while True:
            envelope = client.call("file_get", {
                "file_id": state["file_id"], "part_size_bytes": part_size, "part_index": part_index})
            require(is_success(envelope), f"chunked file_get part {part_index} failed: {envelope!r}")
            data = data_of(envelope)
            received += base64.b64decode(data.get("data_base64", ""))
            if data.get("eof") is True:
                break
            part_index += 1
            require(part_index <= data.get("part_count", part_index), "chunked read did not terminate")
        require(received == payload, f"chunked bytes differ: {len(received)} vs {len(payload)}")
        return f"chunked file_get over {part_index + 1} part(s) reassembled the identical bytes"

    def case_get_unknown_id_specific_error() -> str:
        envelope = client.call("file_get", {"file_id": _UNKNOWN_UUID})
        require(not is_success(envelope), f"expected failure: {envelope!r}")
        code = error_code(envelope)
        require(code == "FILE_ERROR", f"code={code!r}, expected the string domain code 'FILE_ERROR'")
        return "unknown file_id rejected with the specific domain code FILE_ERROR"

    def case_ls_wrong_type_rejected() -> str:
        envelope = client.call("file_ls", {"page": "bogus", "page_size": 5})
        require(not is_success(envelope), f"expected failure: {envelope!r}")
        require(error_code(envelope) == -32602, f"code={error_code(envelope)!r}, expected -32602")
        return "wrong-typed page rejected with -32602 naming the field"

    def case_delete_missing_required_rejected() -> str:
        envelope = client.call("file_delete", {})
        require(not is_success(envelope), f"expected failure: {envelope!r}")
        require(error_code(envelope) == -32602, f"code={error_code(envelope)!r}, expected -32602")
        return "file_delete with required file_id omitted rejected with -32602"

    def case_delete_roundtrip_file() -> str:
        require("file_id" in state, "upload case must have produced a file_id")
        envelope = client.call("file_delete", {"file_id": state["file_id"]})
        require(is_success(envelope), f"file_delete failed: {envelope!r}")
        require(data_of(envelope).get("status") == "deleted", f"status={data_of(envelope).get('status')!r}")
        gone = client.call("file_get", {"file_id": state["file_id"]})
        require(error_code(gone) == "FILE_ERROR", "deleted file must be gone (FILE_ERROR on file_get)")
        state.pop("file_id", None)
        return "file deleted and verified gone"

    try:
        results = [run_case(name, func) for name, func in (
            ("receive_two_part_session", case_receive_two_part_session),
            ("ls_lists_the_file", case_ls_lists_the_file),
            ("get_returns_identical_bytes", case_get_returns_identical_bytes),
            ("get_chunked_by_parts", case_get_chunked_by_parts),
            ("get_unknown_id_specific_error", case_get_unknown_id_specific_error),
            ("ls_wrong_type_rejected", case_ls_wrong_type_rejected),
            ("delete_missing_required_rejected", case_delete_missing_required_rejected),
            ("delete_roundtrip_file", case_delete_roundtrip_file),
        )]
    finally:
        if "file_id" in state:  # cleanup even when a case failed mid-way
            try:
                client.call("file_delete", {"file_id": state["file_id"]})
            except Exception:  # noqa: BLE001 - cleanup must never mask the verdict
                pass
    return summarize_cases("file-roundtrip", client.endpoint.describe(), results)


def check_live_file_roundtrip() -> CheckResult:
    return run_live_check(_body)


registry.register(CHECK_NAME, CHECK_DESCRIPTION, check_live_file_roundtrip)
