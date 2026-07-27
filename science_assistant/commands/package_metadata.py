"""Rich command metadata for package-style MCP file transfer."""

from __future__ import annotations

from typing import Any, Dict, Type

EXAMPLE_SHA = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
EXAMPLE_PART_SHA = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"


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
        "PACKAGE_ERROR": {
            "description": "Invalid manifest, unsafe path, invalid Base64, duplicate part with different bytes, part checksum mismatch, inconsistent package metadata, target conflict, or assembly integrity failure.",
            "message": "PACKAGE_ERROR",
            "solution": "Read data_package_status, resend only missing or rejected part indices, and keep all immutable manifest fields identical.",
        },
        "PACKAGE_TIMEOUT": {
            "description": "data_package_wait reached timeout_seconds before all parts were present or before another assembler completed.",
            "message": "PACKAGE_TIMEOUT",
            "solution": "Continue sending missing parts and call data_package_wait again. Completed calls are idempotent.",
        },
        "COMMAND_ERROR": {
            "description": "Unexpected internal server failure.",
            "message": "COMMAND_ERROR",
            "solution": "Inspect /var/log/science-assistant and retry after correcting the underlying server issue.",
        },
    }


def _manifest_parameters(*, include_part: bool, include_wait: bool) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "package_id": {
            "type": "string",
            "required": True,
            "description": "Stable package identifier matching [A-Za-z0-9][A-Za-z0-9._-]{0,127}. Reuse it for every part and retry.",
            "examples": ["pkg_14b66693_5f4dcc3b"],
        },
        "relative_path": {
            "type": "string",
            "required": True,
            "description": "Final destination relative to /var/science-assistant/data. Absolute paths and '..' are rejected.",
            "examples": ["incoming/catalogs/result.fits"],
        },
        "part_count": {
            "type": "integer",
            "required": True,
            "minimum": 1,
            "maximum": 100000,
            "description": "Exact number of raw parts. Must equal max(1, ceil(total_size_bytes / part_size_bytes)).",
        },
        "part_size_bytes": {
            "type": "integer",
            "required": True,
            "minimum": 1,
            "maximum": 524288,
            "description": "Raw bytes in every non-final part. The final part size is derived from total_size_bytes.",
            "default": 131072,
        },
        "total_size_bytes": {
            "type": "integer",
            "required": True,
            "minimum": 0,
            "description": "Exact size of the original raw file before Base64 encoding.",
        },
        "sha256": {
            "type": "string",
            "required": True,
            "description": "Whole-file SHA-256 over original raw bytes.",
            "examples": [EXAMPLE_SHA],
        },
        "overwrite": {
            "type": "boolean",
            "required": False,
            "default": False,
            "description": "Permit final atomic replacement after complete integrity verification.",
        },
    }
    if include_part:
        result.update({
            "part_index": {
                "type": "integer",
                "required": True,
                "minimum": 0,
                "description": "Zero-based independent part index. Parts may arrive out of order.",
            },
            "part_sha256": {
                "type": "string",
                "required": True,
                "description": "SHA-256 of decoded bytes in this one part.",
                "examples": [EXAMPLE_PART_SHA],
            },
            "data_base64": {
                "type": "string",
                "required": True,
                "description": "Strict standard Base64 containing exactly the expected raw bytes for part_index.",
            },
        })
    if include_wait:
        result.update({
            "timeout_seconds": {
                "type": "number",
                "required": False,
                "default": 300,
                "minimum": 0,
                "maximum": 86400,
                "description": "Maximum wait for all parts and any concurrent assembler. Use the MCP queue for long waits.",
            },
            "poll_interval_ms": {
                "type": "integer",
                "required": False,
                "default": 250,
                "minimum": 50,
                "maximum": 10000,
                "description": "Filesystem polling interval while waiting for missing parts.",
            },
        })
    return result


def package_metadata(cls: Type[Any]) -> Dict[str, Any]:
    op = str(getattr(cls, "operation", ""))
    common_practices = [
        "Generate package_id once and reuse it for all parts and retries.",
        "Compute whole-file and per-part SHA-256 before Base64 encoding.",
        "Send parts independently; order is not significant and identical retries are idempotent.",
        "Never change relative_path, counts, sizes, digest, or overwrite for an existing package_id.",
        "Use data_package_status to request only missing indices after interruption.",
        "Use data_package_wait through the queue when it may block longer than one synchronous command budget.",
    ]
    if op == "receive_part":
        return {
            **_base(
                cls,
                "Stores one independently addressed package part below the server data root. The command validates manifest invariants, strict Base64, exact derived part size, and per-part SHA-256 before atomically publishing the part. Parts may arrive in any order. Repeating the same part with identical bytes succeeds idempotently; conflicting duplicate content is rejected.",
            ),
            "parameters": _manifest_parameters(include_part=True, include_wait=False),
            "return_value": {
                "success": {
                    "description": "The part is durable and reflected in the package manifest.",
                    "data": {
                        "package_id": "Package identifier.",
                        "status": "receiving, ready, or completed.",
                        "part_index": "Stored zero-based index.",
                        "part_size_bytes": "Verified decoded size.",
                        "part_sha256": "Verified digest.",
                        "received_count": "Current durable part count.",
                        "missing_count": "Remaining part count.",
                        "idempotent": "True when the identical part already existed.",
                    },
                },
                "error": {"description": "Part validation or storage failed.", "code": "PACKAGE_ERROR"},
            },
            "usage_examples": [{
                "description": "Send part 0 of a two-part package.",
                "command": {
                    "package_id": "pkg_example",
                    "relative_path": "incoming/example.bin",
                    "part_index": 0,
                    "part_count": 2,
                    "part_size_bytes": 4,
                    "total_size_bytes": 7,
                    "sha256": EXAMPLE_SHA,
                    "part_sha256": EXAMPLE_PART_SHA,
                    "data_base64": "AAECAw==",
                    "overwrite": False,
                },
                "explanation": "The second part may be sent before or after this call; retries are safe when content is identical.",
            }],
            "error_cases": _errors(),
            "best_practices": common_practices,
        }
    if op == "wait_and_assemble":
        return {
            **_base(
                cls,
                "Creates or validates the package manifest, waits until every independent part is durable, then becomes the single assembler. It rechecks every stored part, streams them in index order into a hidden temporary file, verifies total size and whole-file SHA-256, fsyncs, and atomically promotes the target. The command may be queued before the first part is sent. Repeating it after completion returns the same verified file record.",
            ),
            "parameters": _manifest_parameters(include_part=False, include_wait=True),
            "queue": {
                "recommended": True,
                "reason": "The command intentionally waits for parts and can exceed synchronous request timeouts.",
                "pattern": "Start data_package_wait with use_queue=true, send data_package_part calls, then poll the queue job.",
            },
            "return_value": {
                "success": {
                    "description": "All parts were assembled and atomically published.",
                    "data": {
                        "package_id": "Package identifier.",
                        "status": "completed.",
                        "file.relative_path": "Final reusable server-relative path.",
                        "file.size_bytes": "Verified total bytes.",
                        "file.sha256": "Verified whole-file digest.",
                        "idempotent": "True when the package had already completed.",
                    },
                },
                "error": {"description": "Timeout or integrity failure.", "code": "PACKAGE_TIMEOUT or PACKAGE_ERROR"},
            },
            "usage_examples": [{
                "description": "Queue the assembler before sending parts.",
                "command": {
                    "package_id": "pkg_example",
                    "relative_path": "incoming/example.bin",
                    "part_count": 2,
                    "part_size_bytes": 4,
                    "total_size_bytes": 7,
                    "sha256": EXAMPLE_SHA,
                    "overwrite": False,
                    "timeout_seconds": 300,
                    "poll_interval_ms": 250,
                },
                "explanation": "Invoke through call_server(use_queue=true), then send all data_package_part calls.",
            }],
            "error_cases": _errors(),
            "best_practices": common_practices,
        }
    if op == "package_status":
        return {
            **_base(
                cls,
                "Returns package progress without modifying parts. Received and missing indices are paginated because large files may contain many parts. The summary counts always describe the whole package; index arrays describe only the requested page.",
            ),
            "parameters": {
                "package_id": {"type": "string", "required": True, "description": "Existing package identifier."},
                "page": {"type": "integer", "required": False, "default": 1, "minimum": 1, "description": "One-based index page."},
                "page_size": {"type": "integer", "required": False, "default": 100, "minimum": 1, "maximum": 1000, "description": "Maximum received and missing indices returned per page."},
            },
            "pagination": {
                "request": ["page", "page_size"],
                "response": ["page", "page_size", "total_pages", "has_more", "next_page"],
                "termination": "Stop when pagination.has_more is false.",
            },
            "return_value": {
                "success": {
                    "description": "Current package state and one index page.",
                    "data": {
                        "status": "receiving, ready, assembling, completed, or failed.",
                        "received_count": "Whole-package durable count.",
                        "missing_count": "Whole-package missing count.",
                        "received_indices": "Current page only.",
                        "missing_indices": "Current page only.",
                        "completed_file": "Verified file record after completion.",
                        "failure_reason": "Stored failure description when status=failed.",
                    },
                },
                "error": {"description": "Unknown package or invalid page.", "code": "PACKAGE_ERROR"},
            },
            "usage_examples": [{
                "description": "Read the first page of missing indices.",
                "command": {"package_id": "pkg_example", "page": 1, "page_size": 100},
                "explanation": "Continue with next_page while has_more=true.",
            }],
            "error_cases": _errors(),
            "best_practices": common_practices,
        }
    return {
        **_base(cls, "Unknown package operation."),
        "parameters": {},
        "return_value": {},
        "usage_examples": [],
        "error_cases": _errors(),
        "best_practices": common_practices,
    }
