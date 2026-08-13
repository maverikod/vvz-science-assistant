#!/usr/bin/env python3
"""Pure-stdlib helper for transferring files through model MCP tool calls.

The script deliberately has no network client.  It prepares one MCP command
payload at a time and assembles received parts locally.  The model invokes the
actual MCP tool between these local operations.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

__version__ = "0.2.18"
DEFAULT_PART_SIZE = 32 * 1024
MAX_PART_SIZE = 512 * 1024


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def load_json(path_or_dash: str) -> Any:
    text = sys.stdin.read() if path_or_dash == "-" else Path(path_or_dash).read_text(encoding="utf-8")
    return json.loads(text)


def unwrap(payload: Any) -> Any:
    current = payload
    for _ in range(12):
        if not isinstance(current, Mapping):
            return current
        if current.get("success") is False:
            raise ValueError(str(current.get("error") or current.get("message") or current))
        if isinstance(current.get("result"), Mapping):
            current = current["result"]
            continue
        if "data" in current and any(key in current for key in ("success", "error", "message")):
            current = current["data"]
            continue
        return dict(current)
    return current


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest must be a JSON object")
    required = {
        "package_id", "relative_path", "part_count", "part_size_bytes",
        "total_size_bytes", "sha256", "overwrite",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"manifest misses fields: {', '.join(missing)}")
    return payload


def expected_part_size(manifest: Mapping[str, Any], index: int) -> int:
    count = int(manifest["part_count"])
    part_size = int(manifest["part_size_bytes"])
    total = int(manifest["total_size_bytes"])
    if index < 0 or index >= count:
        raise ValueError(f"part index must be between 0 and {count - 1}")
    if count == 1:
        return total
    if index < count - 1:
        return part_size
    return total - part_size * (count - 1)


def parts_dir(manifest_path: Path) -> Path:
    return manifest_path.with_name(manifest_path.name + ".parts")


def prepare(args: argparse.Namespace) -> None:
    source = args.source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    part_size = int(args.part_size)
    if part_size < 1 or part_size > MAX_PART_SIZE:
        raise ValueError(f"part size must be between 1 and {MAX_PART_SIZE}")
    size = source.stat().st_size
    digest = sha256_file(source)
    count = max(1, math.ceil(size / part_size))
    package_id = args.package_id or f"pkg_{digest[:12]}_{uuid.uuid4().hex[:8]}"
    manifest_path = args.manifest or source.with_name(f".{source.name}.mcp-package.json")
    payload = {
        "schema_version": "1.0",
        "tool_version": __version__,
        "package_id": package_id,
        "source_path": str(source),
        "relative_path": args.remote_path,
        "part_count": count,
        "part_size_bytes": part_size,
        "total_size_bytes": size,
        "sha256": digest,
        "overwrite": bool(args.overwrite),
    }
    atomic_json(manifest_path, payload)
    print(json.dumps({"manifest": str(manifest_path.resolve()), **payload}, ensure_ascii=False, indent=2))


def common_remote_fields(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "package_id": str(manifest["package_id"]),
        "relative_path": str(manifest["relative_path"]),
        "part_count": int(manifest["part_count"]),
        "part_size_bytes": int(manifest["part_size_bytes"]),
        "total_size_bytes": int(manifest["total_size_bytes"]),
        "sha256": str(manifest["sha256"]),
        "overwrite": bool(manifest.get("overwrite", False)),
    }


def download_begin_payload(args: argparse.Namespace) -> None:
    params = {"relative_path": args.remote_path, "chunk_size": int(args.part_size)}
    payload: Any = params
    if args.envelope:
        payload = {
            "server_id": args.server_id,
            "copy_number": args.copy_number,
            "command": "data_download_begin",
            "params": params,
            "use_queue": False,
        }
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def init_download(args: argparse.Namespace) -> None:
    payload = unwrap(load_json(args.begin_json))
    if not isinstance(payload, Mapping):
        raise ValueError("download begin JSON did not resolve to an object")
    transfer_id = str(payload.get("transfer_id") or "")
    remote_path = str(payload.get("relative_path") or args.remote_path or "")
    size = int(payload.get("size_bytes", -1))
    digest = str(payload.get("sha256") or "").lower()
    part_size = int(payload.get("chunk_size") or args.part_size or DEFAULT_PART_SIZE)
    if not transfer_id or not remote_path or size < 0 or len(digest) != 64:
        raise ValueError("download begin response lacks transfer_id, relative_path, size_bytes, or sha256")
    if part_size < 1 or part_size > MAX_PART_SIZE:
        raise ValueError(f"part size must be between 1 and {MAX_PART_SIZE}")
    count = max(1, math.ceil(size / part_size))
    manifest = {
        "schema_version": "1.0",
        "tool_version": __version__,
        "direction": "download",
        "package_id": transfer_id,
        "transfer_id": transfer_id,
        "relative_path": remote_path,
        "part_count": count,
        "part_size_bytes": part_size,
        "total_size_bytes": size,
        "sha256": digest,
        "overwrite": bool(args.overwrite),
    }
    atomic_json(args.manifest, manifest)
    print(json.dumps({"manifest": str(args.manifest.resolve()), **manifest}, ensure_ascii=False, indent=2))


def download_part_payload(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest)
    transfer_id = str(manifest.get("transfer_id") or manifest.get("package_id") or "")
    if not transfer_id.startswith("down_"):
        raise ValueError("manifest has no down_* transfer_id")
    index = int(args.index)
    limit = expected_part_size(manifest, index)
    params = {
        "transfer_id": transfer_id,
        "offset": index * int(manifest["part_size_bytes"]),
        "limit": max(1, limit),
    }
    payload: Any = params
    if args.envelope:
        payload = {
            "server_id": args.server_id,
            "copy_number": args.copy_number,
            "command": "data_download_chunk",
            "params": params,
            "use_queue": False,
        }
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def wait_payload(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest)
    params = {
        **common_remote_fields(manifest),
        "timeout_seconds": float(args.timeout_seconds),
        "poll_interval_ms": int(args.poll_interval_ms),
    }
    payload: Any = params
    if args.envelope:
        payload = {
            "server_id": args.server_id,
            "copy_number": args.copy_number,
            "command": "data_package_wait",
            "params": params,
            "use_queue": True,
        }
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def part_payload(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest)
    index = int(args.index)
    source_value = manifest.get("source_path")
    if not source_value:
        raise ValueError("manifest has no source_path; it cannot produce upload payloads")
    source = Path(str(source_value))
    expected = expected_part_size(manifest, index)
    with source.open("rb") as stream:
        stream.seek(index * int(manifest["part_size_bytes"]))
        raw = stream.read(expected)
    if len(raw) != expected:
        raise ValueError(f"source ended early for part {index}: expected {expected}, got {len(raw)}")
    params = {
        **common_remote_fields(manifest),
        "part_index": index,
        "part_sha256": sha256_bytes(raw),
        "data_base64": base64.b64encode(raw).decode("ascii"),
    }
    payload: Any = params
    if args.envelope:
        payload = {
            "server_id": args.server_id,
            "copy_number": args.copy_number,
            "command": "data_package_part",
            "params": params,
            "use_queue": False,
        }
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def accept_part(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest)
    payload = unwrap(load_json(args.part_json))
    if not isinstance(payload, Mapping):
        raise ValueError("part JSON did not resolve to an object")
    encoded = payload.get("data_base64")
    if not isinstance(encoded, str):
        raise ValueError("part JSON has no data_base64")
    raw = base64.b64decode(encoded.encode("ascii"), validate=True)
    if payload.get("part_index") is not None:
        index = int(payload["part_index"])
    elif payload.get("offset") is not None:
        offset = int(payload["offset"])
        part_size = int(manifest["part_size_bytes"])
        if offset % part_size:
            raise ValueError(f"offset {offset} is not aligned to part_size_bytes {part_size}")
        index = offset // part_size
    else:
        raise ValueError("part JSON has neither part_index nor offset")
    expected = expected_part_size(manifest, index)
    if len(raw) != expected:
        raise ValueError(f"part {index} size mismatch: expected {expected}, got {len(raw)}")
    expected_digest = payload.get("part_sha256")
    actual_digest = sha256_bytes(raw)
    if expected_digest is not None and str(expected_digest).lower() != actual_digest:
        raise ValueError(f"part {index} SHA-256 mismatch")
    if payload.get("sha256") is not None and str(payload["sha256"]).lower() != str(manifest["sha256"]).lower():
        raise ValueError("whole-file SHA-256 in part response differs from manifest")
    destination = parts_dir(args.manifest) / f"{index:08d}.part"
    if destination.exists():
        if destination.stat().st_size != len(raw) or sha256_file(destination) != actual_digest:
            raise ValueError(f"local part {index} already exists with different content")
        idempotent = True
    else:
        atomic_bytes(destination, raw)
        idempotent = False
    print(json.dumps({
        "package_id": manifest["package_id"],
        "part_index": index,
        "size_bytes": len(raw),
        "sha256": actual_digest,
        "idempotent": idempotent,
        "path": str(destination.resolve()),
    }, ensure_ascii=False, indent=2))


def assemble(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest)
    destination = args.output.expanduser().resolve()
    if destination.exists() and not args.overwrite:
        raise FileExistsError(destination)
    directory = parts_dir(args.manifest)
    deadline = time.monotonic() + float(args.wait_seconds)
    count = int(manifest["part_count"])
    while True:
        missing = [index for index in range(count) if not (directory / f"{index:08d}.part").is_file()]
        if not missing:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(f"missing {len(missing)} of {count} parts; first missing indices: {missing[:20]}")
        time.sleep(float(args.poll_interval))

    temporary = destination.with_name(destination.name + ".assembling")
    temporary.unlink(missing_ok=True)
    digest = hashlib.sha256()
    size = 0
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("wb") as output:
            for index in range(count):
                part = directory / f"{index:08d}.part"
                expected = expected_part_size(manifest, index)
                if part.stat().st_size != expected:
                    raise ValueError(f"part {index} size changed before assembly")
                with part.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        output.write(block)
                        digest.update(block)
                        size += len(block)
            output.flush()
            os.fsync(output.fileno())
        actual = digest.hexdigest()
        if size != int(manifest["total_size_bytes"]):
            raise ValueError(f"assembled size mismatch: {size} != {manifest['total_size_bytes']}")
        if actual != str(manifest["sha256"]).lower():
            raise ValueError(f"assembled SHA-256 mismatch: {actual} != {manifest['sha256']}")
        if destination.exists() and args.overwrite:
            destination.unlink()
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    print(json.dumps({
        "status": "completed",
        "path": str(destination),
        "size_bytes": size,
        "sha256": actual,
        "part_count": count,
    }, ensure_ascii=False, indent=2))


def verify(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest)
    path = args.file.expanduser().resolve()
    actual_size = path.stat().st_size
    actual_sha = sha256_file(path)
    expected_size = int(manifest["total_size_bytes"])
    expected_sha = str(manifest["sha256"]).lower()
    if actual_size != expected_size or actual_sha != expected_sha:
        raise ValueError(
            f"integrity mismatch: size {actual_size}/{expected_size}, sha256 {actual_sha}/{expected_sha}"
        )
    print(json.dumps({"status": "verified", "path": str(path), "size_bytes": actual_size, "sha256": actual_sha}, indent=2))


def self_test(_: argparse.Namespace) -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as raw_root:
        root = Path(raw_root)
        source = root / "source.bin"
        raw = bytes(range(256)) * 17 + b"\x00\xffagent-package"
        source.write_bytes(raw)
        manifest_path = root / "manifest.json"
        ns = argparse.Namespace(
            source=source,
            remote_path="acceptance/agent-package.bin",
            manifest=manifest_path,
            part_size=257,
            package_id="pkg_self_test",
            overwrite=False,
        )
        prepare(ns)
        manifest = load_manifest(manifest_path)
        for index in range(int(manifest["part_count"])):
            expected = expected_part_size(manifest, index)
            chunk = raw[index * int(manifest["part_size_bytes"]): index * int(manifest["part_size_bytes"]) + expected]
            response = root / f"response-{index}.json"
            response.write_text(json.dumps({
                "success": True,
                "result": {"success": True, "data": {
                    "part_index": index,
                    "part_sha256": sha256_bytes(chunk),
                    "sha256": sha256_bytes(raw),
                    "data_base64": base64.b64encode(chunk).decode("ascii"),
                }},
            }), encoding="utf-8")
            accept_part(argparse.Namespace(manifest=manifest_path, part_json=str(response)))
        destination = root / "assembled.bin"
        assemble(argparse.Namespace(
            manifest=manifest_path,
            output=destination,
            wait_seconds=1,
            poll_interval=0.01,
            overwrite=False,
        ))
        if destination.read_bytes() != raw:
            raise AssertionError("self-test byte mismatch")
    print(json.dumps({"status": "ok", "version": __version__}))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="action", required=True)

    cmd = sub.add_parser("prepare", help="Create a deterministic file manifest for MCP package upload")
    cmd.add_argument("source", type=Path)
    cmd.add_argument("remote_path")
    cmd.add_argument("--manifest", type=Path)
    cmd.add_argument("--part-size", type=int, default=DEFAULT_PART_SIZE)
    cmd.add_argument("--package-id")
    cmd.add_argument("--overwrite", action="store_true")
    cmd.set_defaults(func=prepare)

    cmd = sub.add_parser("download-begin-payload", help="Print params for data_download_begin")
    cmd.add_argument("remote_path")
    cmd.add_argument("--part-size", type=int, default=DEFAULT_PART_SIZE)
    cmd.add_argument("--envelope", action="store_true")
    cmd.add_argument("--server-id", default="science-assistant-vvz")
    cmd.add_argument("--copy-number", type=int, default=1)
    cmd.set_defaults(func=download_begin_payload)

    cmd = sub.add_parser("init-download", help="Create a local manifest from data_download_begin response")
    cmd.add_argument("begin_json", help="JSON response file, or '-' for stdin")
    cmd.add_argument("--manifest", type=Path, required=True)
    cmd.add_argument("--remote-path")
    cmd.add_argument("--part-size", type=int)
    cmd.add_argument("--overwrite", action="store_true")
    cmd.set_defaults(func=init_download)

    cmd = sub.add_parser("download-part-payload", help="Print params for one data_download_chunk call")
    cmd.add_argument("manifest", type=Path)
    cmd.add_argument("index", type=int)
    cmd.add_argument("--envelope", action="store_true")
    cmd.add_argument("--server-id", default="science-assistant-vvz")
    cmd.add_argument("--copy-number", type=int, default=1)
    cmd.set_defaults(func=download_part_payload)

    cmd = sub.add_parser("wait-payload", help="Print params for queued data_package_wait")
    cmd.add_argument("manifest", type=Path)
    cmd.add_argument("--timeout-seconds", type=float, default=300)
    cmd.add_argument("--poll-interval-ms", type=int, default=250)
    cmd.add_argument("--envelope", action="store_true")
    cmd.add_argument("--server-id", default="science-assistant-vvz")
    cmd.add_argument("--copy-number", type=int, default=1)
    cmd.set_defaults(func=wait_payload)

    cmd = sub.add_parser("part-payload", help="Print params for one data_package_part call")
    cmd.add_argument("manifest", type=Path)
    cmd.add_argument("index", type=int)
    cmd.add_argument("--envelope", action="store_true")
    cmd.add_argument("--server-id", default="science-assistant-vvz")
    cmd.add_argument("--copy-number", type=int, default=1)
    cmd.set_defaults(func=part_payload)

    cmd = sub.add_parser("accept-part", help="Decode and persist one MCP response part locally")
    cmd.add_argument("manifest", type=Path)
    cmd.add_argument("part_json", help="JSON response file, or '-' for stdin")
    cmd.set_defaults(func=accept_part)

    cmd = sub.add_parser("assemble", help="Wait for all local parts and atomically assemble them")
    cmd.add_argument("manifest", type=Path)
    cmd.add_argument("output", type=Path)
    cmd.add_argument("--wait-seconds", type=float, default=300)
    cmd.add_argument("--poll-interval", type=float, default=0.25)
    cmd.add_argument("--overwrite", action="store_true")
    cmd.set_defaults(func=assemble)

    cmd = sub.add_parser("verify", help="Verify a complete file against a manifest")
    cmd.add_argument("manifest", type=Path)
    cmd.add_argument("file", type=Path)
    cmd.set_defaults(func=verify)

    cmd = sub.add_parser("self-test", help="Run a local binary roundtrip without network")
    cmd.set_defaults(func=self_test)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except Exception as exc:
        print(json.dumps({"status": "error", "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
