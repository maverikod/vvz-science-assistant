"""Bidirectional file streaming over MCP JSON-RPC using Base64 chunks."""

from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from science_assistant.services.storage import (
    ensure_data_root,
    file_record,
    relative_data_path,
    resolve_data_path,
    sha256_file,
)

DEFAULT_CHUNK_SIZE = 256 * 1024
MAX_CHUNK_SIZE = 768 * 1024
_STATE_DIR_NAME = ".mcp-transfers"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_dir() -> Path:
    root = ensure_data_root()
    path = root / _STATE_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(transfer_id: str) -> Path:
    if not transfer_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in transfer_id):
        raise ValueError("Invalid transfer_id")
    return _state_dir() / f"{transfer_id}.json"


def _save(state: dict[str, Any]) -> None:
    path = _state_path(str(state["transfer_id"]))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _load(transfer_id: str, *, direction: str | None = None) -> dict[str, Any]:
    path = _state_path(transfer_id)
    if not path.is_file():
        raise FileNotFoundError(f"Unknown transfer_id: {transfer_id}")
    state = json.loads(path.read_text(encoding="utf-8"))
    if direction and state.get("direction") != direction:
        raise ValueError(f"Transfer {transfer_id} is not a {direction} transfer")
    return state


def _normalize_chunk_size(value: int | None) -> int:
    size = int(value or DEFAULT_CHUNK_SIZE)
    if size < 1 or size > MAX_CHUNK_SIZE:
        raise ValueError(f"chunk_size must be between 1 and {MAX_CHUNK_SIZE}")
    return size


def upload_begin(
    *,
    relative_path: str,
    size_bytes: int,
    sha256: str,
    chunk_size: int | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    if size_bytes < 0:
        raise ValueError("size_bytes must be non-negative")
    expected = sha256.lower().strip()
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ValueError("sha256 must be exactly 64 hexadecimal characters")
    target = resolve_data_path(relative_path, create_parent=True)
    if target.exists() and not overwrite:
        raise FileExistsError(f"Target already exists: {relative_data_path(target)}")
    transfer_id = f"up_{uuid.uuid4().hex}"
    temp = target.with_name(f".{target.name}.{transfer_id}.part")
    temp.unlink(missing_ok=True)
    temp.touch(mode=0o640)
    state = {
        "schema_version": "1.0",
        "transfer_id": transfer_id,
        "direction": "upload",
        "status": "created",
        "relative_path": relative_data_path(target),
        "server_path": str(target),
        "temp_path": str(temp),
        "size_bytes": int(size_bytes),
        "sha256": expected,
        "chunk_size": _normalize_chunk_size(chunk_size),
        "offset": 0,
        "overwrite": bool(overwrite),
        "created_at": _now(),
        "updated_at": _now(),
    }
    _save(state)
    return {key: value for key, value in state.items() if key != "temp_path"}


def upload_chunk(*, transfer_id: str, offset: int, data_base64: str) -> dict[str, Any]:
    state = _load(transfer_id, direction="upload")
    if state["status"] in {"completed", "failed"}:
        raise ValueError(f"Transfer is already {state['status']}")
    if int(offset) != int(state["offset"]):
        raise ValueError(f"Offset mismatch: expected {state['offset']}, got {offset}")
    try:
        raw = base64.b64decode(data_base64.encode("ascii"), validate=True)
    except Exception as exc:
        raise ValueError("data_base64 is not valid Base64") from exc
    if len(raw) > int(state["chunk_size"]):
        raise ValueError(f"Chunk exceeds negotiated chunk_size {state['chunk_size']}")
    next_offset = int(state["offset"]) + len(raw)
    if next_offset > int(state["size_bytes"]):
        raise ValueError("Chunk would exceed declared size_bytes")
    temp = Path(state["temp_path"])
    with temp.open("ab") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    state["offset"] = next_offset
    state["status"] = "uploading" if next_offset < int(state["size_bytes"]) else "uploaded"
    state["updated_at"] = _now()
    _save(state)
    return {
        "transfer_id": transfer_id,
        "bytes_received": len(raw),
        "offset": next_offset,
        "size_bytes": state["size_bytes"],
        "completed_bytes": next_offset == int(state["size_bytes"]),
        "status": state["status"],
    }


def upload_complete(*, transfer_id: str) -> dict[str, Any]:
    state = _load(transfer_id, direction="upload")
    if state["status"] == "completed":
        target = resolve_data_path(state["relative_path"])
        return {"transfer_id": transfer_id, "status": "completed", "file": file_record(target)}
    if int(state["offset"]) != int(state["size_bytes"]):
        raise ValueError(f"Upload incomplete: {state['offset']} of {state['size_bytes']} bytes")
    temp = Path(state["temp_path"])
    actual = sha256_file(temp)
    if actual != state["sha256"]:
        state["status"] = "failed"
        state["actual_sha256"] = actual
        state["updated_at"] = _now()
        _save(state)
        temp.unlink(missing_ok=True)
        raise ValueError(f"SHA-256 mismatch: expected {state['sha256']}, got {actual}")
    target = resolve_data_path(state["relative_path"], create_parent=True)
    if target.exists() and not bool(state.get("overwrite")):
        raise FileExistsError(f"Target already exists: {state['relative_path']}")
    os.replace(temp, target)
    state["status"] = "completed"
    state["completed_at"] = _now()
    state["updated_at"] = _now()
    state.pop("temp_path", None)
    _save(state)
    return {"transfer_id": transfer_id, "status": "completed", "file": file_record(target)}


def upload_status(*, transfer_id: str) -> dict[str, Any]:
    state = _load(transfer_id, direction="upload")
    return {key: value for key, value in state.items() if key != "temp_path"}


def download_begin(*, relative_path: str, chunk_size: int | None = None) -> dict[str, Any]:
    source = resolve_data_path(relative_path)
    if not source.is_file():
        raise FileNotFoundError(f"Source file not found: {relative_path}")
    transfer_id = f"down_{uuid.uuid4().hex}"
    state = {
        "schema_version": "1.0",
        "transfer_id": transfer_id,
        "direction": "download",
        "status": "ready",
        "relative_path": relative_data_path(source),
        "server_path": str(source),
        "size_bytes": source.stat().st_size,
        "sha256": sha256_file(source),
        "chunk_size": _normalize_chunk_size(chunk_size),
        "offset": 0,
        "created_at": _now(),
        "updated_at": _now(),
    }
    _save(state)
    return state


def download_chunk(*, transfer_id: str, offset: int | None = None, limit: int | None = None) -> dict[str, Any]:
    state = _load(transfer_id, direction="download")
    read_offset = int(state["offset"] if offset is None else offset)
    if read_offset < 0 or read_offset > int(state["size_bytes"]):
        raise ValueError("offset is outside the file")
    read_limit = _normalize_chunk_size(limit or int(state["chunk_size"]))
    source = resolve_data_path(state["relative_path"])
    with source.open("rb") as stream:
        stream.seek(read_offset)
        raw = stream.read(read_limit)
    next_offset = read_offset + len(raw)
    eof = next_offset >= int(state["size_bytes"])
    state["offset"] = max(int(state.get("offset", 0)), next_offset)
    state["status"] = "completed" if eof else "streaming"
    state["updated_at"] = _now()
    if eof:
        state["completed_at"] = _now()
    _save(state)
    return {
        "transfer_id": transfer_id,
        "relative_path": state["relative_path"],
        "offset": read_offset,
        "next_offset": next_offset,
        "size_bytes": state["size_bytes"],
        "bytes_returned": len(raw),
        "eof": eof,
        "sha256": state["sha256"],
        "data_base64": base64.b64encode(raw).decode("ascii"),
    }


def download_status(*, transfer_id: str) -> dict[str, Any]:
    return _load(transfer_id, direction="download")
