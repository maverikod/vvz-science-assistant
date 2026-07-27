"""Package-style MCP transfers using independent Base64 parts.

This protocol is designed for agent tool calls where a Python process cannot open
its own network connection to MCP Proxy.  Every part is independently addressed,
checksummed, durable, and safe to retry.  A queued wait command assembles the file
only after the complete package is present and verified.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from science_assistant.paths import data_dir

DEFAULT_PART_SIZE = 128 * 1024
MAX_PART_SIZE = 512 * 1024
MAX_PART_COUNT = 100_000
DEFAULT_STATUS_PAGE_SIZE = 100
MAX_STATUS_PAGE_SIZE = 1_000
_PACKAGE_ROOT_NAME = ".mcp-packages"
_PACKAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _data_root() -> Path:
    root = data_dir().expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_data_path(relative_path: str, *, create_parent: bool = False) -> Path:
    root = _data_root()
    raw = str(relative_path).strip().replace("\\", "/")
    if not raw:
        raise ValueError("relative_path must not be empty")
    candidate = PurePosixPath(raw)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("relative_path must be a safe path below the data directory")
    target = root.joinpath(*candidate.parts).resolve()
    if target != root and root not in target.parents:
        raise ValueError("relative_path escaped the data directory")
    if create_parent:
        target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _relative_path(path: Path) -> str:
    root = _data_root()
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("path is outside the data directory")
    return resolved.relative_to(root).as_posix()


def _validate_package_id(package_id: str) -> str:
    value = str(package_id).strip()
    if not _PACKAGE_ID_RE.fullmatch(value):
        raise ValueError("package_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}")
    return value


def _normalize_sha256(value: str, *, field: str) -> str:
    digest = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError(f"{field} must be exactly 64 hexadecimal characters")
    return digest


def _normalize_manifest_fields(
    *,
    package_id: str,
    relative_path: str,
    part_count: int,
    part_size_bytes: int,
    total_size_bytes: int,
    sha256: str,
    overwrite: bool,
) -> dict[str, Any]:
    package_id = _validate_package_id(package_id)
    target = _resolve_data_path(relative_path, create_parent=True)
    count = int(part_count)
    part_size = int(part_size_bytes)
    total_size = int(total_size_bytes)
    if count < 1 or count > MAX_PART_COUNT:
        raise ValueError(f"part_count must be between 1 and {MAX_PART_COUNT}")
    if part_size < 1 or part_size > MAX_PART_SIZE:
        raise ValueError(f"part_size_bytes must be between 1 and {MAX_PART_SIZE}")
    if total_size < 0:
        raise ValueError("total_size_bytes must be non-negative")
    expected_count = max(1, math.ceil(total_size / part_size))
    if count != expected_count:
        raise ValueError(
            f"part_count mismatch: expected {expected_count} for total_size_bytes={total_size} "
            f"and part_size_bytes={part_size}, got {count}"
        )
    return {
        "package_id": package_id,
        "relative_path": _relative_path(target),
        "part_count": count,
        "part_size_bytes": part_size,
        "total_size_bytes": total_size,
        "sha256": _normalize_sha256(sha256, field="sha256"),
        "overwrite": bool(overwrite),
    }


def _package_dir(package_id: str) -> Path:
    return _data_root() / _PACKAGE_ROOT_NAME / _validate_package_id(package_id)


def _manifest_path(package_id: str) -> Path:
    return _package_dir(package_id) / "manifest.json"


def _part_path(package_id: str, part_index: int) -> Path:
    return _package_dir(package_id) / "parts" / f"{int(part_index):08d}.part"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


@contextmanager
def _package_lock(package_id: str) -> Iterator[None]:
    directory = _package_dir(package_id)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / ".lock"
    with lock_path.open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _load_manifest(package_id: str) -> dict[str, Any]:
    path = _manifest_path(package_id)
    if not path.is_file():
        raise FileNotFoundError(f"Unknown package_id: {package_id}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Package manifest is not a JSON object")
    return payload


def _new_manifest(fields: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    return {
        "schema_version": "1.0",
        "direction": "upload",
        **fields,
        "status": "receiving",
        "received": {},
        "created_at": now,
        "updated_at": now,
    }


def _validate_existing(manifest: dict[str, Any], fields: dict[str, Any]) -> None:
    for key in ("package_id", "relative_path", "part_count", "part_size_bytes", "total_size_bytes", "sha256", "overwrite"):
        if manifest.get(key) != fields.get(key):
            raise ValueError(
                f"Package manifest mismatch for {key}: existing={manifest.get(key)!r}, supplied={fields.get(key)!r}"
            )


def _get_or_create_manifest(fields: dict[str, Any]) -> dict[str, Any]:
    package_id = str(fields["package_id"])
    path = _manifest_path(package_id)
    if path.is_file():
        manifest = _load_manifest(package_id)
        _validate_existing(manifest, fields)
        return manifest
    target = _resolve_data_path(str(fields["relative_path"]), create_parent=True)
    if target.exists() and not bool(fields["overwrite"]):
        raise FileExistsError(f"Target already exists: {fields['relative_path']}")
    manifest = _new_manifest(fields)
    _atomic_json(path, manifest)
    return manifest


def _expected_part_size(manifest: dict[str, Any], part_index: int) -> int:
    index = int(part_index)
    count = int(manifest["part_count"])
    part_size = int(manifest["part_size_bytes"])
    total = int(manifest["total_size_bytes"])
    if index < 0 or index >= count:
        raise ValueError(f"part_index must be between 0 and {count - 1}")
    if count == 1:
        return total
    if index < count - 1:
        return part_size
    return total - part_size * (count - 1)


def _received_indices(manifest: dict[str, Any]) -> list[int]:
    received = manifest.get("received")
    if not isinstance(received, dict):
        return []
    result: list[int] = []
    for key in received:
        try:
            result.append(int(key))
        except (TypeError, ValueError):
            continue
    return sorted(set(result))


def _missing_indices(manifest: dict[str, Any]) -> list[int]:
    received = set(_received_indices(manifest))
    return [index for index in range(int(manifest["part_count"])) if index not in received]


def receive_part(
    *,
    package_id: str,
    relative_path: str,
    part_index: int,
    part_count: int,
    part_size_bytes: int,
    total_size_bytes: int,
    sha256: str,
    part_sha256: str,
    data_base64: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    fields = _normalize_manifest_fields(
        package_id=package_id,
        relative_path=relative_path,
        part_count=part_count,
        part_size_bytes=part_size_bytes,
        total_size_bytes=total_size_bytes,
        sha256=sha256,
        overwrite=overwrite,
    )
    expected_part_digest = _normalize_sha256(part_sha256, field="part_sha256")
    try:
        raw = base64.b64decode(str(data_base64).encode("ascii"), validate=True)
    except Exception as exc:
        raise ValueError("data_base64 is not valid standard Base64") from exc

    with _package_lock(str(fields["package_id"])):
        manifest = _get_or_create_manifest(fields)
        if manifest.get("status") == "completed":
            target = _resolve_data_path(str(manifest["relative_path"]))
            return {
                "package_id": fields["package_id"],
                "status": "completed",
                "idempotent": True,
                "file": _file_record(target),
            }
        assembling = manifest.get("status") == "assembling"
        if manifest.get("status") == "failed":
            raise ValueError(f"Package is failed: {manifest.get('failure_reason', 'unknown reason')}")

        index = int(part_index)
        expected_size = _expected_part_size(manifest, index)
        if len(raw) != expected_size:
            raise ValueError(f"Part {index} size mismatch: expected {expected_size}, got {len(raw)}")
        actual_part_digest = _sha256_bytes(raw)
        if actual_part_digest != expected_part_digest:
            raise ValueError(
                f"Part {index} SHA-256 mismatch: expected {expected_part_digest}, got {actual_part_digest}"
            )

        path = _part_path(str(fields["package_id"]), index)
        path.parent.mkdir(parents=True, exist_ok=True)
        idempotent = False
        if path.exists():
            existing_digest = _sha256_file(path)
            if existing_digest != actual_part_digest or path.stat().st_size != len(raw):
                raise ValueError(f"Part {index} already exists with different content")
            idempotent = True
        elif assembling:
            raise ValueError("Package is already being assembled; only identical retries of stored parts are accepted")
        else:
            temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
            with temporary.open("wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)

        if assembling:
            return {
                "package_id": fields["package_id"],
                "status": "assembling",
                "part_index": index,
                "part_size_bytes": len(raw),
                "part_sha256": actual_part_digest,
                "received_count": len(_received_indices(manifest)),
                "part_count": manifest["part_count"],
                "missing_count": len(_missing_indices(manifest)),
                "idempotent": True,
            }

        received = manifest.setdefault("received", {})
        received[str(index)] = {
            "size_bytes": len(raw),
            "sha256": actual_part_digest,
            "received_at": received.get(str(index), {}).get("received_at", _now())
            if isinstance(received.get(str(index)), dict)
            else _now(),
        }
        received_count = len(_received_indices(manifest))
        manifest["status"] = "ready" if received_count == int(manifest["part_count"]) else "receiving"
        manifest["updated_at"] = _now()
        _atomic_json(_manifest_path(str(fields["package_id"])), manifest)
        return {
            "package_id": fields["package_id"],
            "status": manifest["status"],
            "part_index": index,
            "part_size_bytes": len(raw),
            "part_sha256": actual_part_digest,
            "received_count": received_count,
            "part_count": manifest["part_count"],
            "missing_count": int(manifest["part_count"]) - received_count,
            "idempotent": idempotent,
        }


def package_status(
    *,
    package_id: str,
    page: int = 1,
    page_size: int = DEFAULT_STATUS_PAGE_SIZE,
) -> dict[str, Any]:
    package_id = _validate_package_id(package_id)
    page = int(page)
    page_size = int(page_size)
    if page < 1:
        raise ValueError("page must be at least 1")
    if page_size < 1 or page_size > MAX_STATUS_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_STATUS_PAGE_SIZE}")
    with _package_lock(package_id):
        manifest = _load_manifest(package_id)
        received = _received_indices(manifest)
        missing = _missing_indices(manifest)
    start = (page - 1) * page_size
    end = start + page_size
    total_items = max(len(received), len(missing))
    total_pages = max(1, math.ceil(total_items / page_size))
    return {
        "package_id": package_id,
        "status": manifest.get("status"),
        "relative_path": manifest.get("relative_path"),
        "part_count": manifest.get("part_count"),
        "part_size_bytes": manifest.get("part_size_bytes"),
        "total_size_bytes": manifest.get("total_size_bytes"),
        "sha256": manifest.get("sha256"),
        "received_count": len(received),
        "missing_count": len(missing),
        "received_indices": received[start:end],
        "missing_indices": missing[start:end],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_more": page < total_pages,
            "next_page": page + 1 if page < total_pages else None,
        },
        "completed_file": manifest.get("completed_file"),
        "failure_reason": manifest.get("failure_reason"),
        "created_at": manifest.get("created_at"),
        "updated_at": manifest.get("updated_at"),
        "completed_at": manifest.get("completed_at"),
    }


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "relative_path": _relative_path(path),
        "server_path": str(path.resolve()),
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _assemble_owner(package_id: str, fields: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Return (owner, manifest). Only one waiter becomes the assembler."""
    with _package_lock(package_id):
        manifest = _get_or_create_manifest(fields)
        status = str(manifest.get("status", "receiving"))
        if status == "completed":
            return False, manifest
        if status == "failed":
            raise ValueError(f"Package is failed: {manifest.get('failure_reason', 'unknown reason')}")
        if _missing_indices(manifest):
            return False, manifest
        if status == "assembling":
            return False, manifest
        manifest["status"] = "assembling"
        manifest["assembler_pid"] = os.getpid()
        manifest["updated_at"] = _now()
        _atomic_json(_manifest_path(package_id), manifest)
        return True, manifest


def _assemble(package_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    target = _resolve_data_path(str(manifest["relative_path"]), create_parent=True)
    temporary = target.with_name(f".{target.name}.{package_id}.assembling")
    temporary.unlink(missing_ok=True)
    full_digest = hashlib.sha256()
    total_written = 0
    try:
        with temporary.open("wb") as destination:
            received = manifest.get("received") if isinstance(manifest.get("received"), dict) else {}
            for index in range(int(manifest["part_count"])):
                part = _part_path(package_id, index)
                if not part.is_file():
                    raise ValueError(f"Part {index} disappeared before assembly")
                expected = received.get(str(index)) if isinstance(received, dict) else None
                expected_digest = str(expected.get("sha256")) if isinstance(expected, dict) else ""
                actual_digest = _sha256_file(part)
                if actual_digest != expected_digest:
                    raise ValueError(
                        f"Part {index} changed before assembly: expected {expected_digest}, got {actual_digest}"
                    )
                with part.open("rb") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        destination.write(block)
                        full_digest.update(block)
                        total_written += len(block)
            destination.flush()
            os.fsync(destination.fileno())
        actual_sha256 = full_digest.hexdigest()
        if total_written != int(manifest["total_size_bytes"]):
            raise ValueError(
                f"Assembled size mismatch: expected {manifest['total_size_bytes']}, got {total_written}"
            )
        if actual_sha256 != str(manifest["sha256"]):
            raise ValueError(
                f"Assembled SHA-256 mismatch: expected {manifest['sha256']}, got {actual_sha256}"
            )
        if target.exists() and not bool(manifest.get("overwrite")):
            raise FileExistsError(f"Target already exists: {manifest['relative_path']}")
        os.replace(temporary, target)
        record = _file_record(target)
        with _package_lock(package_id):
            current = _load_manifest(package_id)
            current["status"] = "completed"
            current["completed_file"] = record
            current["completed_at"] = _now()
            current["updated_at"] = _now()
            current.pop("assembler_pid", None)
            _atomic_json(_manifest_path(package_id), current)
        parts_dir = _package_dir(package_id) / "parts"
        if parts_dir.exists():
            shutil.rmtree(parts_dir)
        return {"package_id": package_id, "status": "completed", "file": record}
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        with _package_lock(package_id):
            try:
                current = _load_manifest(package_id)
                current["status"] = "failed"
                current["failure_reason"] = str(exc)
                current["updated_at"] = _now()
                current.pop("assembler_pid", None)
                _atomic_json(_manifest_path(package_id), current)
            except Exception:
                pass
        raise


def wait_and_assemble(
    *,
    package_id: str,
    relative_path: str,
    part_count: int,
    part_size_bytes: int,
    total_size_bytes: int,
    sha256: str,
    overwrite: bool = False,
    timeout_seconds: float = 300.0,
    poll_interval_ms: int = 250,
) -> dict[str, Any]:
    fields = _normalize_manifest_fields(
        package_id=package_id,
        relative_path=relative_path,
        part_count=part_count,
        part_size_bytes=part_size_bytes,
        total_size_bytes=total_size_bytes,
        sha256=sha256,
        overwrite=overwrite,
    )
    timeout = float(timeout_seconds)
    poll_seconds = int(poll_interval_ms) / 1000.0
    if timeout < 0 or timeout > 86_400:
        raise ValueError("timeout_seconds must be between 0 and 86400")
    if poll_seconds < 0.05 or poll_seconds > 10.0:
        raise ValueError("poll_interval_ms must be between 50 and 10000")

    package_id = str(fields["package_id"])
    with _package_lock(package_id):
        _get_or_create_manifest(fields)

    deadline = time.monotonic() + timeout
    while True:
        owner, manifest = _assemble_owner(package_id, fields)
        status = str(manifest.get("status", "receiving"))
        if status == "completed":
            target = _resolve_data_path(str(manifest["relative_path"]))
            record = manifest.get("completed_file")
            if not isinstance(record, dict):
                record = _file_record(target)
            return {"package_id": package_id, "status": "completed", "file": record, "idempotent": True}
        if owner:
            return _assemble(package_id, manifest)
        if time.monotonic() >= deadline:
            snapshot = package_status(package_id=package_id, page=1, page_size=100)
            raise TimeoutError(
                f"Package {package_id} is incomplete after {timeout:g}s: "
                f"received {snapshot['received_count']} of {snapshot['part_count']} parts"
            )
        time.sleep(poll_seconds)
