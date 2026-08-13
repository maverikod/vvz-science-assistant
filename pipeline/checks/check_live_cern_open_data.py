"""Live CERN Open Data checks: search, record, and the queued download path.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com

Exercises the three CERN commands against the REAL deployed server, the REAL
CERN Open Data portal, and the REAL Code Analysis Server: artifacts land in
this project's own registered CAS project (``PROJECT_ID`` below, the id in
this repository's ``projectid`` file) under ``data/cern_open_data/`` —
content-addressed names, so identical payloads overwrite rather than
accumulate.

``cern_open_data_download`` is queue-backed by design (``x-use-queue``); the
check drives the FULL queue lifecycle via ``call_completed``. Verified
product fact on 0.2.20: CERN record file metadata carries only ``root://``
(xrootd) URIs, so the download of record 5500 deterministically ends in the
SPECIFIC domain error ``CERN_OPEN_DATA_ERROR`` ("selected file has no HTTP
download URL"). The check asserts exactly that observed contract — the queue
machinery, parameter passing, and the stable error code — and the docstring
records the finding: a positive HTTP download is impossible for xrootd-only
records until the service learns to derive portal HTTP URLs.

Negatives assert ``PROJECT_RESOLUTION_ERROR`` for an unknown project and the
in-queue ``-32602`` for a wrong-typed ``record_id`` (queued commands validate
inside the job, not synchronously — also an observed contract).
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

CHECK_NAME = "live-cern-open-data"
CHECK_DESCRIPTION = (
    "Real CERN Open Data search and record delivery into this project's own CAS "
    "project, the queued download lifecycle with its observed CERN_OPEN_DATA_ERROR "
    "for xrootd-only records, plus PROJECT_RESOLUTION_ERROR and in-queue -32602 negatives.")

PROJECT_ID = "190d4d88-0555-4e98-a538-5b7a4cbbbebb"  # this repo's own CAS project (projectid)
RECORD_ID = 5500  # stable public CMS Higgs example software record
_UNKNOWN_PROJECT = "00000000-0000-4000-8000-000000000000"


def _assert_delivered_files(data: dict, label: str) -> dict:
    files = data.get("files", {})
    for artifact_kind in ("artifact", "manifest"):
        entry = files.get(artifact_kind, {})
        require(isinstance(entry.get("file_id"), str) and entry["file_id"],
                f"{label}: {artifact_kind} without file_id: {files!r}")
        require(str(entry.get("file_path", "")).startswith("data/cern_open_data/"),
                f"{label}: {artifact_kind} outside data/cern_open_data: {entry!r}")
        require(isinstance(entry.get("sha256"), str) and len(entry["sha256"]) == 64,
                f"{label}: {artifact_kind} without sha256: {entry!r}")
    return files


def _body(client: LiveClient) -> CheckResult:
    def case_search_delivers_into_project() -> str:
        envelope = client.call("cern_open_data_search", {
            "project_id": PROJECT_ID, "query": "CMS primary dataset", "page": 1,
            "size": 1, "timeout_seconds": 60})
        require(is_success(envelope), f"search failed: {envelope!r}")
        data = data_of(envelope)
        project = data.get("project", {})
        require(project.get("project_id") == PROJECT_ID, f"resolved project wrong: {project!r}")
        _assert_delivered_files(data, "search")
        return f"search artifact and provenance manifest delivered into CAS project {project.get('name')!r}"

    def case_record_delivers_into_project() -> str:
        envelope = client.call("cern_open_data_record", {
            "project_id": PROJECT_ID, "record_id": RECORD_ID, "timeout_seconds": 60})
        require(is_success(envelope), f"record failed: {envelope!r}")
        data = data_of(envelope)
        require(data.get("record_id") == RECORD_ID, f"record_id={data.get('record_id')!r}")
        _assert_delivered_files(data, "record")
        return f"record {RECORD_ID} raw JSON and manifest delivered with content-addressed names"

    def case_download_queue_lifecycle() -> str:
        envelope = client.call_completed("cern_open_data_download", {
            "project_id": PROJECT_ID, "record_id": RECORD_ID, "file_index": 0,
            "max_bytes": 200_000, "timeout_seconds": 60}, poll_timeout=180.0)
        require(envelope.get("job_id"), "download must run through the queue (x-use-queue)")
        require(not is_success(envelope), f"expected the observed xrootd-only failure: {envelope!r}")
        code = error_code(envelope)
        require(code == "CERN_OPEN_DATA_ERROR", f"code={code!r}, expected 'CERN_OPEN_DATA_ERROR'")
        message = str((data_of(envelope) or {}).get("message", ""))
        require("no HTTP download URL" in message, f"message={message!r}")
        return ("queued download completed through the full job lifecycle and returned the "
                "observed CERN_OPEN_DATA_ERROR: record files expose only root:// URIs")

    def case_unknown_project_rejected() -> str:
        envelope = client.call("cern_open_data_search", {
            "project_id": _UNKNOWN_PROJECT, "query": "x", "size": 1})
        require(error_code(envelope) == "PROJECT_RESOLUTION_ERROR",
                f"code={error_code(envelope)!r}, expected 'PROJECT_RESOLUTION_ERROR'")
        return "unknown project_id rejected with PROJECT_RESOLUTION_ERROR"

    def case_wrong_typed_record_id_in_queue() -> str:
        envelope = client.call_completed("cern_open_data_download", {
            "project_id": PROJECT_ID, "record_id": "bogus"}, poll_timeout=120.0)
        require(error_code(envelope) == -32602,
                f"code={error_code(envelope)!r}, expected in-queue -32602")
        return "wrong-typed record_id validated INSIDE the job and rejected with -32602"

    results = [run_case(name, func) for name, func in (
        ("search_delivers_into_project", case_search_delivers_into_project),
        ("record_delivers_into_project", case_record_delivers_into_project),
        ("download_queue_lifecycle", case_download_queue_lifecycle),
        ("unknown_project_rejected", case_unknown_project_rejected),
        ("wrong_typed_record_id_in_queue", case_wrong_typed_record_id_in_queue),
    )]
    return summarize_cases("cern-open-data", client.endpoint.describe(), results)


def check_live_cern_open_data() -> CheckResult:
    return run_live_check(_body)


registry.register(CHECK_NAME, CHECK_DESCRIPTION, check_live_cern_open_data)
