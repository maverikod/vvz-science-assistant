#!/usr/bin/env python3
"""Prepare resumable multipart MCP file transfers for Science Assistant.

The script uses only the Python standard library. It cannot import ChatGPT's
MCP_proxy tool; instead it emits one exact ``call_server`` payload at a time,
accepts the corresponding JSON response, and persists enough state to resume.

Upload direction uses ``file_receive``. Download direction uses ``file_get``.
Both directions transfer a file through multiple MCP requests and verify the
whole-file size and SHA-256 before reporting completion.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping

__version__ = "0.2.20"
DEFAULT_BASE64_CHARS = 32 * 1024
DEFAULT_DOWNLOAD_PART_SIZE = 64 * 1024
MAX_DOWNLOAD_PART_SIZE = 512 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def unwrap(payload: Any) -> Any:
    current = payload
    for _ in range(12):
        if not isinstance(current, Mapping):
            return current
        if current.get("success") is False:
            raise RuntimeError(str(current.get("error") or current.get("message") or current))
        if isinstance(current.get("result"), Mapping):
            current = current["result"]
            continue
        if "data" in current and any(key in current for key in ("success", "error", "message")):
            current = current["data"]
            continue
        return dict(current)
    return current


def load_state(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("state must be a JSON object")
    return payload


def load_response(path_text: str) -> Any:
    if path_text == "-":
        return json.load(sys.stdin)
    return json.loads(Path(path_text).read_text(encoding="utf-8"))


def ensure_direction(state: Mapping[str, Any], expected: str) -> None:
    direction = state.get("direction")
    if direction is None and expected == "upload":
        return  # Compatibility with state files produced before schema 1.1.
    if direction != expected:
        raise ValueError(f"state direction is {direction!r}, expected {expected!r}")


def normalize_uuid4(value: str, *, field: str) -> str:
    text = str(value).strip().lower()
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{field} must be a canonical UUID4") from exc
    if parsed.version != 4 or str(parsed) != text:
        raise ValueError(f"{field} must be a canonical UUID4")
    return text


def normalize_sha256(value: Any) -> str:
    digest = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError("sha256 must be exactly 64 hexadecimal characters")
    return digest


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decode_base64(value: Any) -> bytes:
    try:
        return base64.b64decode(str(value).encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise ValueError("response data_base64 is not valid standard Base64") from exc


def call_payload(state: Mapping[str, Any], command: str, params: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "server_id": state["server_id"],
        "copy_number": state["copy_number"],
        "command": command,
        "params": dict(params),
        "use_queue": False,
    }


# Upload: local environment -> Science Assistant file store.
def cmd_prepare(args: argparse.Namespace) -> None:
    source = args.file.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if args.part_chars < 4:
        raise ValueError("part-chars must be at least 4")
    raw = source.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    parts = [encoded[i : i + args.part_chars] for i in range(0, len(encoded), args.part_chars)] or [""]
    parts_path = args.state.with_name(args.state.name + ".parts.json")
    atomic_json(parts_path, {"parts": parts})
    state = {
        "schema_version": "1.1",
        "direction": "upload",
        "source_path": str(source),
        "filename": args.name or source.name,
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "ttl_seconds": args.ttl,
        "part_count": len(parts),
        "next_part_index": 0,
        "upload_session_id": None,
        "file_id": None,
        "parts_path": str(parts_path.resolve()),
        "server_id": args.server_id,
        "copy_number": args.copy_number,
        "status": "prepared",
    }
    atomic_json(args.state, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))


def cmd_next(args: argparse.Namespace) -> None:
    state = load_state(args.state)
    ensure_direction(state, "upload")
    if state.get("file_id"):
        print(json.dumps({"status": "completed", "file_id": state["file_id"]}, indent=2))
        return
    index = int(state["next_part_index"])
    count = int(state["part_count"])
    if index >= count:
        raise RuntimeError("all portions were emitted, but no final file_id was recorded")
    parts_doc = json.loads(Path(state["parts_path"]).read_text(encoding="utf-8"))
    fragment = parts_doc["parts"][index]
    params: dict[str, Any] = {"part_index": index, "data_base64_part": fragment}
    session_id = state.get("upload_session_id")
    if session_id:
        params["upload_session_id"] = session_id
    else:
        if index != 0:
            raise RuntimeError("upload_session_id is missing after the first portion")
        params.update(
            {
                "filename": state["filename"],
                "part_count": count,
                "size_bytes": state["size_bytes"],
                "sha256": state["sha256"],
                "ttl_seconds": state["ttl_seconds"],
            }
        )
    print(json.dumps(call_payload(state, "file_receive", params), ensure_ascii=False, separators=(",", ":")))


def cmd_accept(args: argparse.Namespace) -> None:
    state = load_state(args.state)
    ensure_direction(state, "upload")
    data = unwrap(load_response(args.response))
    if not isinstance(data, Mapping):
        raise ValueError("response did not resolve to an object")

    session_id = data.get("upload_session_id")
    if session_id:
        normalized_session = normalize_uuid4(str(session_id), field="upload_session_id")
        existing_session = state.get("upload_session_id")
        if existing_session and existing_session != normalized_session:
            raise ValueError("response upload_session_id does not match the saved session")
        state["upload_session_id"] = normalized_session

    file_id = data.get("file_id")
    if file_id:
        state["file_id"] = normalize_uuid4(str(file_id), field="file_id")
        if "size_bytes" in data and int(data["size_bytes"]) != int(state["size_bytes"]):
            raise ValueError("completed upload size does not match the source")
        if "sha256" in data and normalize_sha256(data["sha256"]) != state["sha256"]:
            raise ValueError("completed upload SHA-256 does not match the source")
        state["status"] = "completed"
    else:
        if "part_index" not in data:
            raise ValueError("non-final file_receive response has no part_index")
        acknowledged = int(data["part_index"])
        expected = int(state["next_part_index"])
        if acknowledged > expected:
            raise ValueError(f"server acknowledged future part {acknowledged}; expected {expected}")
        if acknowledged == expected:
            state["next_part_index"] = expected + 1
            state["status"] = "receiving"
        elif acknowledged < expected:
            # Re-accepting an already persisted response is intentionally idempotent.
            state["status"] = "receiving"

    state["last_response"] = dict(data)
    atomic_json(args.state, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))


# Download: Science Assistant file store -> local environment.
def cmd_download_prepare(args: argparse.Namespace) -> None:
    file_id = normalize_uuid4(args.file_id, field="file_id")
    part_size = int(args.part_size_bytes)
    if part_size < 1 or part_size > MAX_DOWNLOAD_PART_SIZE:
        raise ValueError(f"part-size-bytes must be between 1 and {MAX_DOWNLOAD_PART_SIZE}")

    output = args.output.expanduser().resolve()
    temporary = output.with_name(output.name + ".mcp.part")
    if output.exists() and not args.overwrite:
        raise FileExistsError(f"output already exists: {output}")
    if temporary.exists():
        if args.overwrite:
            temporary.unlink()
        else:
            raise FileExistsError(f"temporary download already exists: {temporary}")

    state = {
        "schema_version": "1.1",
        "direction": "download",
        "file_id": file_id,
        "output_path": str(output),
        "temporary_path": str(temporary),
        "part_size_bytes": part_size,
        "next_part_index": 0,
        "bytes_written": 0,
        "part_count": None,
        "size_bytes": None,
        "sha256": None,
        "remote_name": None,
        "overwrite": bool(args.overwrite),
        "server_id": args.server_id,
        "copy_number": args.copy_number,
        "status": "prepared",
    }
    atomic_json(args.state, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))


def cmd_download_next(args: argparse.Namespace) -> None:
    state = load_state(args.state)
    ensure_direction(state, "download")
    if state.get("status") == "completed":
        print(
            json.dumps(
                {"status": "completed", "file_id": state["file_id"], "output_path": state["output_path"]},
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    params = {
        "file_id": state["file_id"],
        "part_index": int(state["next_part_index"]),
        "part_size_bytes": int(state["part_size_bytes"]),
    }
    print(json.dumps(call_payload(state, "file_get", params), ensure_ascii=False, separators=(",", ":")))


def _validate_download_metadata(state: dict[str, Any], data: Mapping[str, Any]) -> tuple[int, int, int, int, bytes, bool]:
    required = {
        "file_id",
        "part_index",
        "part_count",
        "part_size_bytes",
        "offset",
        "bytes_returned",
        "eof",
        "size_bytes",
        "sha256",
        "data_base64",
    }
    missing = sorted(required.difference(data))
    if missing:
        raise ValueError(f"file_get response is missing fields: {', '.join(missing)}")
    if normalize_uuid4(str(data["file_id"]), field="file_id") != state["file_id"]:
        raise ValueError("response file_id does not match the requested file")

    index = int(data["part_index"])
    part_count = int(data["part_count"])
    part_size = int(data["part_size_bytes"])
    offset = int(data["offset"])
    bytes_returned = int(data["bytes_returned"])
    total_size = int(data["size_bytes"])
    eof = bool(data["eof"])
    raw = decode_base64(data["data_base64"])
    digest = normalize_sha256(data["sha256"])

    if part_count < 1 or index < 0 or index >= part_count:
        raise ValueError("response part_index/part_count is invalid")
    if part_size != int(state["part_size_bytes"]):
        raise ValueError("response part_size_bytes differs from the requested size")
    if offset != index * part_size:
        raise ValueError("response offset does not match part_index and part_size_bytes")
    if bytes_returned != len(raw):
        raise ValueError("response bytes_returned does not match decoded Base64 length")
    if eof != (index == part_count - 1):
        raise ValueError("response eof flag does not match part_index/part_count")
    if total_size < 0:
        raise ValueError("response size_bytes must be non-negative")

    if state.get("part_count") is None:
        state["part_count"] = part_count
        state["size_bytes"] = total_size
        state["sha256"] = digest
        state["remote_name"] = data.get("name")
    else:
        if part_count != int(state["part_count"]):
            raise ValueError("response part_count changed during download")
        if total_size != int(state["size_bytes"]):
            raise ValueError("response size_bytes changed during download")
        if digest != state["sha256"]:
            raise ValueError("response SHA-256 changed during download")

    return index, part_count, offset, total_size, raw, eof


def _verify_duplicate_part(path: Path, *, offset: int, raw: bytes) -> None:
    if not path.is_file():
        raise ValueError("download state says a part was written, but the temporary file is missing")
    with path.open("rb") as stream:
        stream.seek(offset)
        existing = stream.read(len(raw))
    if existing != raw:
        raise ValueError("repeated part differs from bytes already stored locally")


def cmd_download_accept(args: argparse.Namespace) -> None:
    state = load_state(args.state)
    ensure_direction(state, "download")
    if state.get("status") == "completed":
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return

    data = unwrap(load_response(args.response))
    if not isinstance(data, Mapping):
        raise ValueError("response did not resolve to an object")
    index, part_count, offset, total_size, raw, eof = _validate_download_metadata(state, data)
    expected = int(state["next_part_index"])
    temporary = Path(state["temporary_path"])

    if index < expected:
        _verify_duplicate_part(temporary, offset=offset, raw=raw)
        state["last_response"] = dict(data)
        state["last_response_idempotent"] = True
        atomic_json(args.state, state)
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return
    if index > expected:
        raise ValueError(f"out-of-order part {index}; expected {expected}")
    if offset != int(state["bytes_written"]):
        raise ValueError("response offset does not match local download progress")

    output = Path(state["output_path"])
    if eof and output.exists() and not bool(state.get("overwrite")):
        raise FileExistsError(f"output already exists: {output}")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    mode = "r+b" if temporary.exists() else "w+b"
    with temporary.open(mode) as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() != offset:
            raise ValueError("temporary file size does not match the expected offset")
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())

    try:
        new_bytes_written = offset + len(raw)
        if eof:
            if index + 1 != part_count:
                raise ValueError("final response arrived before all parts")
            if new_bytes_written != total_size:
                raise ValueError(
                    f"downloaded size mismatch: expected {total_size}, got {new_bytes_written}"
                )
            actual_sha256 = file_sha256(temporary)
            if actual_sha256 != state["sha256"]:
                raise ValueError(
                    f"downloaded SHA-256 mismatch: expected {state['sha256']}, got {actual_sha256}"
                )
            output.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, output)
            state["status"] = "completed"
            state["completed_path"] = str(output)
            state["verified_sha256"] = actual_sha256
        else:
            state["status"] = "receiving"
        state["bytes_written"] = new_bytes_written
        state["next_part_index"] = index + 1
        state["last_response"] = dict(data)
        state["last_response_idempotent"] = False
        atomic_json(args.state, state)
    except Exception:
        if temporary.exists():
            with temporary.open("r+b") as stream:
                stream.truncate(offset)
                stream.flush()
                os.fsync(stream.fileno())
        raise

    print(json.dumps(state, ensure_ascii=False, indent=2))


def cmd_status(args: argparse.Namespace) -> None:
    print(json.dumps(load_state(args.state), ensure_ascii=False, indent=2))


def add_upload_prepare_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser], name: str) -> None:
    prepare = sub.add_parser(name)
    prepare.add_argument("file", type=Path)
    prepare.add_argument("--state", type=Path, required=True)
    prepare.add_argument("--name")
    prepare.add_argument("--part-chars", type=int, default=DEFAULT_BASE64_CHARS)
    prepare.add_argument("--ttl", type=int, default=900)
    prepare.add_argument("--server-id", default="science-assistant-vvz")
    prepare.add_argument("--copy-number", type=int, default=1)
    prepare.set_defaults(func=cmd_prepare)


def add_state_response_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    func: Any,
) -> None:
    command = sub.add_parser(name)
    command.add_argument("state", type=Path)
    command.add_argument("response", help="JSON response file or '-' for stdin")
    command.set_defaults(func=func)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    add_upload_prepare_parser(sub, "prepare")
    add_upload_prepare_parser(sub, "upload-prepare")

    for name in ("next", "upload-next"):
        next_cmd = sub.add_parser(name)
        next_cmd.add_argument("state", type=Path)
        next_cmd.set_defaults(func=cmd_next)
    add_state_response_parser(sub, "accept", cmd_accept)
    add_state_response_parser(sub, "upload-accept", cmd_accept)

    download_prepare = sub.add_parser("download-prepare")
    download_prepare.add_argument("file_id")
    download_prepare.add_argument("output", type=Path)
    download_prepare.add_argument("--state", type=Path, required=True)
    download_prepare.add_argument(
        "--part-size-bytes",
        type=int,
        default=DEFAULT_DOWNLOAD_PART_SIZE,
    )
    download_prepare.add_argument("--overwrite", action="store_true")
    download_prepare.add_argument("--server-id", default="science-assistant-vvz")
    download_prepare.add_argument("--copy-number", type=int, default=1)
    download_prepare.set_defaults(func=cmd_download_prepare)

    download_next = sub.add_parser("download-next")
    download_next.add_argument("state", type=Path)
    download_next.set_defaults(func=cmd_download_next)
    add_state_response_parser(sub, "download-accept", cmd_download_accept)

    status = sub.add_parser("status")
    status.add_argument("state", type=Path)
    status.set_defaults(func=cmd_status)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
