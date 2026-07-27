"""Filesystem-backed MCP file inbox with expiring upload sessions.

No database is used. Completed files live below ``<data>/files`` as
``<uuid4>-<sanitized-original-name>`` and have JSON metadata sidecars.
Upload sessions live below ``<data>/files/.upload-sessions`` until completion
or TTL expiry.
"""

from __future__ import annotations

import base64
import binascii
import fcntl
import fnmatch
import hashlib
import json
import math
import os
import re
import shutil
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from science_assistant.paths import data_dir

DEFAULT_TTL_SECONDS = 900
MIN_TTL_SECONDS = 1
MAX_TTL_SECONDS = 86_400
DEFAULT_GET_PART_SIZE = 64 * 1024
MAX_GET_PART_SIZE = 512 * 1024
DEFAULT_LIST_PAGE_SIZE = 50
MAX_LIST_PAGE_SIZE = 500

_FILES_DIR_NAME = "files"
_SESSIONS_DIR_NAME = ".upload-sessions"
_METADATA_DIR_NAME = ".metadata"
_BASE64_FRAGMENT_RE = re.compile(r"^[A-Za-z0-9+/=]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _epoch() -> float:
    return time.time()


def _iso(epoch: float | None = None) -> str:
    return datetime.fromtimestamp(epoch if epoch is not None else _epoch(), timezone.utc).isoformat()


def _data_root() -> Path:
    root = data_dir().expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def files_root() -> Path:
    root = _data_root() / _FILES_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    (root / _SESSIONS_DIR_NAME).mkdir(parents=True, exist_ok=True)
    (root / _METADATA_DIR_NAME).mkdir(parents=True, exist_ok=True)
    return root


def _sessions_root() -> Path:
    return files_root() / _SESSIONS_DIR_NAME


def _metadata_root() -> Path:
    return files_root() / _METADATA_DIR_NAME


def _validate_uuid4(value: str, *, field: str) -> str:
    text = str(value).strip().lower()
    if not _UUID4_RE.fullmatch(text):
        raise ValueError(f"{field} must be a canonical UUID4")
    parsed = uuid.UUID(text)
    if parsed.version != 4:
        raise ValueError(f"{field} must be UUID4")
    return text


def _normalize_sha256(value: str) -> str:
    digest = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError("sha256 must be exactly 64 hexadecimal characters")
    return digest


def _safe_filename(filename: str) -> tuple[str, str]:
    original = Path(str(filename).replace("\\", "/")).name.strip()
    if not original or original in {".", ".."}:
        raise ValueError("filename must contain a regular file name")
    safe = _SAFE_NAME_RE.sub("-", original).strip(" .-_")
    if not safe:
        safe = "file"
    return original[:255], safe[:200]


def _session_dir(session_id: str) -> Path:
    return _sessions_root() / _validate_uuid4(session_id, field="upload_session_id")


def _manifest_path(session_id: str) -> Path:
    return _session_dir(session_id) / "manifest.json"


def _session_part_path(session_id: str, part_index: int) -> Path:
    return _session_dir(session_id) / "parts" / f"{int(part_index):08d}.b64part"


def _metadata_path(file_id: str) -> Path:
    return _metadata_root() / f"{_validate_uuid4(file_id, field='file_id')}.json"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


@contextmanager
def _session_lock(session_id: str) -> Iterator[None]:
    directory = _session_dir(session_id)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / ".lock"
    with lock_path.open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _load_manifest(session_id: str) -> dict[str, Any]:
    path = _manifest_path(session_id)
    if not path.is_file():
        raise FileNotFoundError(f"Unknown upload_session_id: {session_id}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Upload session manifest is invalid")
    return payload


def _write_fragment(path: Path, fragment: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = fragment.encode("ascii")
    if path.exists():
        if path.read_bytes() != encoded:
            raise ValueError(f"Part {path.stem} already exists with different content")
        return True
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return False


def _received_indices(manifest: dict[str, Any]) -> list[int]:
    raw = manifest.get("received")
    if not isinstance(raw, dict):
        return []
    indices: list[int] = []
    for key in raw:
        try:
            indices.append(int(key))
        except (TypeError, ValueError):
            continue
    return sorted(set(indices))


def _cleanup_expired_sessions(*, exclude_session_id: str | None = None) -> int:
    root = _sessions_root()
    now = _epoch()
    removed = 0
    for directory in list(root.iterdir()):
        if not directory.is_dir():
            continue
        if exclude_session_id is not None and directory.name == exclude_session_id:
            continue
        manifest_path = directory / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            expires_at_epoch = float(manifest.get("expires_at_epoch", 0))
        except Exception:
            continue
        if expires_at_epoch and expires_at_epoch <= now:
            shutil.rmtree(directory, ignore_errors=True)
            removed += 1
    return removed


def _new_session(
    *,
    filename: str,
    part_count: int,
    size_bytes: int,
    sha256: str,
    ttl_seconds: int,
) -> tuple[str, dict[str, Any]]:
    original_name, safe_name = _safe_filename(filename)
    count = int(part_count)
    size = int(size_bytes)
    ttl = int(ttl_seconds)
    if count < 1:
        raise ValueError("part_count must be at least 1")
    if size < 0:
        raise ValueError("size_bytes must be non-negative")
    if ttl < MIN_TTL_SECONDS or ttl > MAX_TTL_SECONDS:
        raise ValueError(f"ttl_seconds must be between {MIN_TTL_SECONDS} and {MAX_TTL_SECONDS}")
    digest = _normalize_sha256(sha256)
    session_id = str(uuid.uuid4())
    now = _epoch()
    manifest = {
        "schema_version": "1.0",
        "upload_session_id": session_id,
        "filename": original_name,
        "safe_filename": safe_name,
        "part_count": count,
        "size_bytes": size,
        "sha256": digest,
        "ttl_seconds": ttl,
        "status": "receiving",
        "received": {},
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "expires_at": _iso(now + ttl),
        "expires_at_epoch": now + ttl,
    }
    directory = _session_dir(session_id)
    directory.mkdir(parents=True, exist_ok=False)
    _atomic_json(_manifest_path(session_id), manifest)
    return session_id, manifest


def _ensure_live(manifest: dict[str, Any]) -> None:
    if float(manifest.get("expires_at_epoch", 0)) <= _epoch():
        session_id = str(manifest.get("upload_session_id", ""))
        if session_id:
            shutil.rmtree(_session_dir(session_id), ignore_errors=True)
        raise TimeoutError("Upload session TTL has expired")


def _refresh_ttl(manifest: dict[str, Any]) -> None:
    now = _epoch()
    ttl = int(manifest["ttl_seconds"])
    manifest["updated_at"] = _iso(now)
    manifest["expires_at"] = _iso(now + ttl)
    manifest["expires_at_epoch"] = now + ttl


def _decode_complete_session(manifest: dict[str, Any]) -> dict[str, Any]:
    session_id = str(manifest["upload_session_id"])
    file_id = str(uuid.uuid4())
    target = files_root() / f"{file_id}-{manifest['safe_filename']}"
    temporary = files_root() / f".{file_id}.decoding"
    temporary.unlink(missing_ok=True)

    digest = hashlib.sha256()
    total = 0
    buffer = b""
    try:
        with temporary.open("wb") as output:
            for index in range(int(manifest["part_count"])):
                part_path = _session_part_path(session_id, index)
                if not part_path.is_file():
                    raise ValueError(f"Part {index} disappeared before finalization")
                buffer += part_path.read_bytes()
                is_last_part = index == int(manifest["part_count"]) - 1
                if is_last_part:
                    if len(buffer) % 4:
                        raise ValueError("Combined Base64 length is not divisible by 4")
                    decode_length = len(buffer)
                else:
                    decode_length = len(buffer) - (len(buffer) % 4)
                if decode_length:
                    chunk, buffer = buffer[:decode_length], buffer[decode_length:]
                    if not is_last_part and b"=" in chunk:
                        raise ValueError("Base64 padding appeared before the final portion")
                    try:
                        raw = base64.b64decode(chunk, validate=True)
                    except (binascii.Error, ValueError) as exc:
                        raise ValueError("Combined data is not valid Base64") from exc
                    output.write(raw)
                    digest.update(raw)
                    total += len(raw)
            if buffer:
                raise ValueError("Incomplete Base64 tail after final portion")
            output.flush()
            os.fsync(output.fileno())

        actual_sha256 = digest.hexdigest()
        if total != int(manifest["size_bytes"]):
            raise ValueError(
                f"Decoded size mismatch: expected {manifest['size_bytes']}, got {total}"
            )
        if actual_sha256 != str(manifest["sha256"]):
            raise ValueError(
                f"Decoded SHA-256 mismatch: expected {manifest['sha256']}, got {actual_sha256}"
            )
        os.replace(temporary, target)
        created_at = _iso()
        metadata = {
            "schema_version": "1.0",
            "file_id": file_id,
            "name": manifest["filename"],
            "stored_name": target.name,
            "relative_path": target.relative_to(_data_root()).as_posix(),
            "size_bytes": total,
            "sha256": actual_sha256,
            "created_at": created_at,
        }
        _atomic_json(_metadata_path(file_id), metadata)
        shutil.rmtree(_session_dir(session_id), ignore_errors=True)
        return metadata
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def receive_file_part(
    *,
    part_index: int,
    data_base64_part: str,
    upload_session_id: str | None = None,
    filename: str | None = None,
    part_count: int | None = None,
    size_bytes: int | None = None,
    sha256: str | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    """Accept one fragment of a full-file Base64 string.

    The first portion has no ``upload_session_id`` and must include the complete
    header. It returns only the newly created session identifier. The final
    portion returns the permanent UUID4 ``file_id`` after decode and validation.
    """

    index = int(part_index)
    fragment = str(data_base64_part)
    if index < 0:
        raise ValueError("part_index must be non-negative")
    if not _BASE64_FRAGMENT_RE.fullmatch(fragment):
        raise ValueError("data_base64_part contains characters outside standard Base64")

    if upload_session_id is None:
        _cleanup_expired_sessions()
        if index != 0:
            raise ValueError("The first portion must have part_index=0")
        missing = [
            name
            for name, value in (
                ("filename", filename),
                ("part_count", part_count),
                ("size_bytes", size_bytes),
                ("sha256", sha256),
                ("ttl_seconds", ttl_seconds),
            )
            if value is None
        ]
        if missing:
            raise ValueError("First portion header misses: " + ", ".join(missing))
        session_id, _ = _new_session(
            filename=str(filename),
            part_count=int(part_count),
            size_bytes=int(size_bytes),
            sha256=str(sha256),
            ttl_seconds=int(ttl_seconds),
        )
    else:
        session_id = _validate_uuid4(upload_session_id, field="upload_session_id")
        _cleanup_expired_sessions(exclude_session_id=session_id)

    with _session_lock(session_id):
        manifest = _load_manifest(session_id)
        _ensure_live(manifest)
        if index >= int(manifest["part_count"]):
            raise ValueError(
                f"part_index must be between 0 and {int(manifest['part_count']) - 1}"
            )
        if manifest.get("status") != "receiving":
            raise ValueError(f"Upload session is {manifest.get('status')}")
        idempotent = _write_fragment(_session_part_path(session_id, index), fragment)
        received = manifest.setdefault("received", {})
        received[str(index)] = {
            "base64_chars": len(fragment),
            "received_at": (
                received.get(str(index), {}).get("received_at")
                if isinstance(received.get(str(index)), dict)
                else _iso()
            )
            or _iso(),
        }
        _refresh_ttl(manifest)
        received_count = len(_received_indices(manifest))
        complete = received_count == int(manifest["part_count"])
        if not complete:
            _atomic_json(_manifest_path(session_id), manifest)
            return {
                "status": "receiving",
                "upload_session_id": session_id,
                "part_index": index,
                "received_count": received_count,
                "part_count": manifest["part_count"],
                "missing_count": int(manifest["part_count"]) - received_count,
                "expires_at": manifest["expires_at"],
                "idempotent": idempotent,
            }

        manifest["status"] = "decoding"
        _atomic_json(_manifest_path(session_id), manifest)
        metadata = _decode_complete_session(manifest)
        return {
            "status": "completed",
            "upload_session_id": session_id,
            "file_id": metadata["file_id"],
            "name": metadata["name"],
            "stored_name": metadata["stored_name"],
            "size_bytes": metadata["size_bytes"],
            "sha256": metadata["sha256"],
            "created_at": metadata["created_at"],
        }


def _load_file_metadata(file_id: str) -> dict[str, Any]:
    path = _metadata_path(file_id)
    if not path.is_file():
        raise FileNotFoundError(f"Unknown file_id: {file_id}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Metadata for file_id {file_id} is invalid")
    file_path = _data_root() / str(payload["relative_path"])
    if not file_path.is_file():
        raise FileNotFoundError(f"Stored file for file_id {file_id} is missing")
    return payload


def get_file_part(
    *,
    file_id: str,
    part_index: int = 0,
    part_size_bytes: int = DEFAULT_GET_PART_SIZE,
) -> dict[str, Any]:
    metadata = _load_file_metadata(file_id)
    index = int(part_index)
    part_size = int(part_size_bytes)
    if index < 0:
        raise ValueError("part_index must be non-negative")
    if part_size < 1 or part_size > MAX_GET_PART_SIZE:
        raise ValueError(f"part_size_bytes must be between 1 and {MAX_GET_PART_SIZE}")
    size = int(metadata["size_bytes"])
    part_count = max(1, math.ceil(size / part_size))
    if index >= part_count:
        raise ValueError(f"part_index must be between 0 and {part_count - 1}")
    offset = index * part_size
    path = _data_root() / str(metadata["relative_path"])
    with path.open("rb") as stream:
        stream.seek(offset)
        raw = stream.read(part_size)
    return {
        **metadata,
        "part_index": index,
        "part_count": part_count,
        "part_size_bytes": part_size,
        "offset": offset,
        "bytes_returned": len(raw),
        "eof": index == part_count - 1,
        "data_base64": base64.b64encode(raw).decode("ascii"),
    }


def list_files(
    *,
    page: int,
    page_size: int,
    name_pattern: str = "*",
) -> dict[str, Any]:
    _cleanup_expired_sessions()
    page_number = int(page)
    size = int(page_size)
    if page_number < 1:
        raise ValueError("page must be at least 1")
    if size < 1 or size > MAX_LIST_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_LIST_PAGE_SIZE}")
    pattern = str(name_pattern or "*")
    items: list[dict[str, Any]] = []
    for path in _metadata_root().glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            if not fnmatch.fnmatchcase(str(payload.get("name", "")), pattern):
                continue
            stored = _data_root() / str(payload.get("relative_path", ""))
            if not stored.is_file():
                continue
            items.append(payload)
        except Exception:
            continue
    items.sort(key=lambda item: (str(item.get("name", "")), str(item.get("file_id", ""))))
    total_items = len(items)
    total_pages = max(1, math.ceil(total_items / size))
    start = (page_number - 1) * size
    page_items = items[start : start + size]
    return {
        "items": page_items,
        "name_pattern": pattern,
        "pagination": {
            "page": page_number,
            "page_size": size,
            "total_items": total_items,
            "total_pages": total_pages,
            "has_more": page_number < total_pages,
            "next_page": page_number + 1 if page_number < total_pages else None,
        },
    }


@contextmanager
def _file_lock(file_id: str) -> Iterator[None]:
    """Serialize destructive operations for one permanent file id."""
    normalized = _validate_uuid4(file_id, field="file_id")
    lock_root = files_root() / ".locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / f"{normalized}.lock"
    with lock_path.open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def delete_file(*, file_id: str) -> dict[str, Any]:
    """Delete a stored file and its metadata by permanent UUID4.

    The binary and sidecar are first moved into a private transaction directory.
    If the second move fails, the first is rolled back.  Once both moves succeed,
    the transaction directory is removed and the file is no longer discoverable.
    """
    normalized = _validate_uuid4(file_id, field="file_id")
    with _file_lock(normalized):
        metadata_path = _metadata_path(normalized)
        if not metadata_path.is_file():
            raise FileNotFoundError(f"Unknown file_id: {normalized}")
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Metadata for file_id {normalized} is invalid")

        stored_path = (_data_root() / str(payload.get("relative_path", ""))).resolve()
        root = files_root().resolve()
        if stored_path == root or root not in stored_path.parents:
            raise ValueError(f"Stored path for file_id {normalized} escaped the files directory")
        if not stored_path.is_file():
            raise FileNotFoundError(f"Stored file for file_id {normalized} is missing")

        transaction = root / ".trash" / str(uuid.uuid4())
        transaction.mkdir(parents=True, exist_ok=False)
        moved_file = transaction / stored_path.name
        moved_metadata = transaction / metadata_path.name
        file_moved = False
        metadata_moved = False
        try:
            os.replace(stored_path, moved_file)
            file_moved = True
            os.replace(metadata_path, moved_metadata)
            metadata_moved = True
        except Exception:
            if metadata_moved and moved_metadata.exists() and not metadata_path.exists():
                os.replace(moved_metadata, metadata_path)
            if file_moved and moved_file.exists() and not stored_path.exists():
                os.replace(moved_file, stored_path)
            shutil.rmtree(transaction, ignore_errors=True)
            raise

        deleted_at = _iso()
        result = {
            "status": "deleted",
            "file_id": normalized,
            "name": payload.get("name"),
            "stored_name": payload.get("stored_name"),
            "size_bytes": payload.get("size_bytes"),
            "sha256": payload.get("sha256"),
            "created_at": payload.get("created_at"),
            "deleted_at": deleted_at,
        }
        shutil.rmtree(transaction, ignore_errors=True)
        return result
