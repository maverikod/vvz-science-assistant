"""Extended metadata for MCP-native bidirectional data-transfer commands."""

from __future__ import annotations

from typing import Any, Dict, Type

ROOT = "/var/science-assistant/data"
DEFAULT_CHUNK = 262144
MAX_CHUNK = 786432
EXAMPLE_SHA = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def _base(cls: Type[Any], detailed: str) -> Dict[str, Any]:
    return {
        "name": cls.name,
        "version": cls.version,
        "description": cls.descr,
        "category": cls.category,
        "author": cls.author,
        "email": cls.email,
        "detailed_description": detailed,
    }


def _errors() -> Dict[str, Any]:
    return {
        "TRANSFER_ERROR": {
            "description": (
                "Stable validation or storage error: unsafe path, unknown transfer_id, wrong transfer direction, "
                "invalid Base64, offset mismatch, oversized chunk, size overflow, incomplete upload, existing target, "
                "or SHA-256 mismatch."
            ),
            "message": "TRANSFER_ERROR",
            "solution": "Call the matching status command, use its exact offset, and retry only the failed chunk.",
        },
        "COMMAND_ERROR": {
            "description": "Unexpected internal failure outside normal transfer validation.",
            "message": "COMMAND_ERROR",
            "solution": "Inspect /var/log/science-assistant and retry when safe.",
        },
    }


def _practices() -> list[str]:
    return [
        "Persist transfer_id and the latest confirmed offset until the transfer completes.",
        "Use the default 256 KiB raw chunk; Base64 increases the JSON payload by roughly one third.",
        "Never exceed 768 KiB raw bytes in one chunk.",
        "Verify the final SHA-256 in both directions before accepting a copy.",
        f"Use relative paths only; the server prefixes {ROOT} and rejects absolute paths and '..'.",
        "Run chunks synchronously and in order; do not enqueue individual chunk commands.",
    ]


def _common(cls: Type[Any], detailed: str, parameters: Dict[str, Any], success_data: Dict[str, Any], example: Dict[str, Any], explanation: str) -> Dict[str, Any]:
    return {
        **_base(cls, detailed),
        "parameters": parameters,
        "return_value": {
            "success": {"description": "Command completed successfully.", "data": success_data},
            "error": {"description": "Transfer request failed.", "code": "TRANSFER_ERROR", "message": "Human-readable reason in data.message."},
        },
        "usage_examples": [{"description": explanation, "command": example, "explanation": explanation}],
        "error_cases": _errors(),
        "best_practices": _practices(),
    }


def transfer_metadata(cls: Type[Any]) -> Dict[str, Any]:
    op = str(getattr(cls, "operation", ""))
    if op == "upload_begin":
        return _common(
            cls,
            "Creates a persistent MCP/JSON-RPC upload session. The SHA-256 is calculated over the original raw bytes before Base64 encoding. relative_path is resolved below the server data root; a hidden .part file and durable state record are created. The returned offset is the only valid starting position for data_upload_chunk.",
            {
                "relative_path": {"type": "string", "required": True, "description": "Destination relative to the data root.", "examples": ["incoming/observation.fits"]},
                "size_bytes": {"type": "integer", "required": True, "minimum": 0, "description": "Exact raw-byte size.", "examples": [1048576]},
                "sha256": {"type": "string", "required": True, "description": "64-character SHA-256 of raw bytes.", "examples": [EXAMPLE_SHA]},
                "chunk_size": {"type": "integer", "required": False, "default": DEFAULT_CHUNK, "minimum": 1, "maximum": MAX_CHUNK, "description": "Maximum decoded bytes per chunk."},
                "overwrite": {"type": "boolean", "required": False, "default": False, "description": "Permit final atomic replacement after verification."},
            },
            {"transfer_id": "Opaque up_* id.", "relative_path": "Normalized target.", "server_path": "Diagnostic absolute path.", "size_bytes": "Declared raw size.", "sha256": "Expected digest.", "chunk_size": "Negotiated limit.", "offset": "Always 0.", "status": "created."},
            {"relative_path": "incoming/observation.fits", "size_bytes": 1048576, "sha256": EXAMPLE_SHA, "chunk_size": DEFAULT_CHUNK},
            "Start a resumable binary upload; then send the first chunk with offset 0.",
        )
    if op == "upload_chunk":
        return _common(
            cls,
            "Decodes and appends exactly one Base64 chunk. offset is measured in raw bytes and must equal the persisted server offset. Duplicate, skipped, out-of-order, malformed, oversized, and overflowing chunks are rejected before commit. The file is fsynced and the next offset is persisted for resume.",
            {
                "transfer_id": {"type": "string", "required": True, "description": "up_* id from data_upload_begin."},
                "offset": {"type": "integer", "required": True, "minimum": 0, "description": "Exact raw-byte offset returned by begin/status/previous chunk."},
                "data_base64": {"type": "string", "required": True, "description": "Strict standard Base64 for one raw chunk."},
            },
            {"bytes_received": "Decoded bytes written.", "offset": "Next required raw offset.", "size_bytes": "Declared total.", "completed_bytes": "True when all bytes arrived.", "status": "uploading or uploaded."},
            {"transfer_id": "up_example", "offset": 0, "data_base64": "AAECA/7/SGVsbG8="},
            "Send one chunk and continue from the returned offset, not from the Base64 string length.",
        )
    if op == "upload_complete":
        return _common(
            cls,
            "Finalizes only when offset equals size_bytes. The server computes SHA-256 over the complete .part file, removes it on mismatch, and atomically promotes it to the requested relative path on success. Repeating completion after success is idempotent.",
            {"transfer_id": {"type": "string", "required": True, "description": "up_* id whose bytes are fully uploaded."}},
            {"status": "completed.", "file.relative_path": "Reusable path for download.", "file.server_path": "Diagnostic path.", "file.size_bytes": "Verified size.", "file.sha256": "Verified digest."},
            {"transfer_id": "up_example"},
            "Verify size and SHA-256 and atomically install the uploaded file.",
        )
    if op == "upload_status":
        return _common(
            cls,
            "Returns durable upload state without modifying it. Use after a timeout, client restart, or uncertain response. The returned offset is authoritative; internal temporary paths are deliberately hidden.",
            {"transfer_id": {"type": "string", "required": True, "description": "up_* upload id."}},
            {"status": "created/uploading/uploaded/completed/failed.", "offset": "Next raw-byte offset.", "size_bytes": "Declared total.", "sha256": "Expected digest.", "relative_path": "Destination."},
            {"transfer_id": "up_example"},
            "Recover the exact position and resume an interrupted upload.",
        )
    if op == "download_begin":
        return _common(
            cls,
            "Creates an MCP-native download session for an existing regular file below the data root. The server computes size and SHA-256 before streaming and returns a down_* id. No direct HTTP access to the host or container is required.",
            {
                "relative_path": {"type": "string", "required": True, "description": "Existing file relative to the data root.", "examples": ["exports/result.ecsv"]},
                "chunk_size": {"type": "integer", "required": False, "default": DEFAULT_CHUNK, "minimum": 1, "maximum": MAX_CHUNK, "description": "Default raw bytes returned per chunk."},
            },
            {"transfer_id": "Opaque down_* id.", "relative_path": "Normalized source.", "server_path": "Diagnostic path.", "size_bytes": "Raw size.", "sha256": "Digest to verify locally.", "chunk_size": "Negotiated chunk limit.", "offset": "Initial 0.", "status": "ready."},
            {"relative_path": "exports/result.ecsv", "chunk_size": DEFAULT_CHUNK},
            "Begin a download and then request chunks from offset 0.",
        )
    if op == "download_chunk":
        return _common(
            cls,
            "Reads one byte range and returns it as Base64 inside the MCP response. offset is a raw-byte position. Decode data_base64, append bytes locally, and continue from next_offset until eof=true. Explicit offsets allow deterministic retry of a lost response.",
            {
                "transfer_id": {"type": "string", "required": True, "description": "down_* id from data_download_begin."},
                "offset": {"type": "integer", "required": False, "minimum": 0, "description": "Raw-byte start; omitted uses saved progress."},
                "limit": {"type": "integer", "required": False, "minimum": 1, "maximum": MAX_CHUNK, "description": "Raw bytes to return, clamped to the command maximum."},
            },
            {"offset": "Start of returned range.", "next_offset": "Start for the next call.", "bytes_returned": "Decoded byte count.", "size_bytes": "Total source size.", "eof": "True after the final range.", "sha256": "Whole-file digest.", "data_base64": "Encoded chunk bytes."},
            {"transfer_id": "down_example", "offset": 0, "limit": DEFAULT_CHUNK},
            "Read, Base64-decode, append, and continue from next_offset until eof.",
        )
    if op == "download_status":
        return _common(
            cls,
            "Returns durable download metadata and the furthest confirmed streamed offset. It does not return bytes. Use it to recover size, digest, path, chunk size, and current status after interruption.",
            {"transfer_id": {"type": "string", "required": True, "description": "down_* download id."}},
            {"status": "ready/streaming/completed.", "offset": "Furthest streamed byte.", "size_bytes": "Source size.", "sha256": "Whole-file digest.", "relative_path": "Source path."},
            {"transfer_id": "down_example"},
            "Inspect or resume a server-to-client download.",
        )
    return {
        **_base(cls, "Unknown transfer metadata operation."),
        "parameters": {}, "return_value": {}, "usage_examples": [], "error_cases": _errors(), "best_practices": _practices(),
    }
