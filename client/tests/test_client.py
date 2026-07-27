from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

import pytest

from science_assistant_client import AsyncScienceAssistantClient, ProxyConfig, VersionMismatchError, __version__


class FakeProxy:
    def __init__(self, root: Path, *, server_version: str = __version__) -> None:
        self.root = root
        self.server_version = server_version
        self.uploads: dict[str, dict[str, Any]] = {}
        self.downloads: dict[str, dict[str, Any]] = {}
        self.packages: dict[str, dict[str, Any]] = {}
        self.calls: list[dict[str, Any]] = []

    async def close(self) -> None:
        return None

    def envelope(self, data: Any) -> dict[str, Any]:
        return {"success": True, "result": {"success": True, "data": data}}

    async def execute_command(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert command == "call_server"
        self.calls.append(payload)
        name = payload["command"]
        params = payload["params"]
        if name == "info":
            block = int(params.get("block_position", 1))
            pages = ["alpha\nbeta\n", "gamma\ndelta\n"]
            return self.envelope({
                "package": {"version": self.server_version},
                "markdown": None if not params.get("include_markdown", True) else pages[block - 1],
                "pagination": {
                    "paginated": True,
                    "page_size": int(params.get("page_size", 80)),
                    "block_position": block,
                    "total_lines": 4,
                    "total_blocks": 2,
                    "has_more": block < 2,
                    "next_block_position": block + 1 if block < 2 else None,
                },
            })
        if name == "data_upload_begin":
            transfer_id = f"up_{len(self.uploads) + 1}"
            target = self.root / params["relative_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            self.uploads[transfer_id] = {**params, "target": target, "bytes": bytearray(), "offset": 0}
            return self.envelope({"transfer_id": transfer_id, "offset": 0, "size_bytes": params["size_bytes"], "sha256": params["sha256"]})
        if name == "data_upload_status":
            state = self.uploads[params["transfer_id"]]
            return self.envelope({"transfer_id": params["transfer_id"], "offset": state["offset"], "size_bytes": state["size_bytes"], "sha256": state["sha256"]})
        if name == "data_upload_chunk":
            state = self.uploads[params["transfer_id"]]
            assert params["offset"] == state["offset"]
            raw = base64.b64decode(params["data_base64"], validate=True)
            state["bytes"].extend(raw)
            state["offset"] += len(raw)
            return self.envelope({"offset": state["offset"], "bytes_received": len(raw)})
        if name == "data_upload_complete":
            state = self.uploads[params["transfer_id"]]
            raw = bytes(state["bytes"])
            assert len(raw) == state["size_bytes"]
            assert hashlib.sha256(raw).hexdigest() == state["sha256"]
            state["target"].write_bytes(raw)
            return self.envelope({"status": "completed", "file": {"relative_path": state["relative_path"], "size_bytes": len(raw), "sha256": state["sha256"]}})
        if name == "data_package_part":
            package_id = str(params["package_id"])
            state = self.packages.setdefault(package_id, {
                **params, "parts": {}, "target": self.root / params["relative_path"],
            })
            for key in ("relative_path", "part_count", "part_size_bytes", "total_size_bytes", "sha256", "overwrite"):
                assert state[key] == params[key]
            raw = base64.b64decode(params["data_base64"], validate=True)
            assert hashlib.sha256(raw).hexdigest() == params["part_sha256"]
            index = int(params["part_index"])
            previous = state["parts"].get(index)
            if previous is not None:
                assert previous == raw
            state["parts"][index] = raw
            return self.envelope({
                "package_id": package_id, "status": "ready" if len(state["parts"]) == state["part_count"] else "receiving",
                "part_index": index, "received_count": len(state["parts"]),
                "missing_count": state["part_count"] - len(state["parts"]), "idempotent": previous is not None,
            })
        if name == "data_package_wait":
            package_id = str(params["package_id"])
            state = self.packages[package_id]
            assert len(state["parts"]) == state["part_count"]
            raw = b"".join(state["parts"][index] for index in range(state["part_count"]))
            assert len(raw) == state["total_size_bytes"]
            assert hashlib.sha256(raw).hexdigest() == state["sha256"]
            state["target"].parent.mkdir(parents=True, exist_ok=True)
            state["target"].write_bytes(raw)
            return self.envelope({
                "package_id": package_id, "status": "completed",
                "file": {"relative_path": state["relative_path"], "size_bytes": len(raw), "sha256": state["sha256"]},
            })
        if name == "queue_get_job_status":
            return self.envelope({"status": "completed", "result": {}})
        if name == "data_download_begin":
            source = self.root / params["relative_path"]
            raw = source.read_bytes()
            transfer_id = f"down_{len(self.downloads) + 1}"
            self.downloads[transfer_id] = {"raw": raw, "relative_path": params["relative_path"], "offset": 0}
            return self.envelope({"transfer_id": transfer_id, "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "offset": 0})
        if name == "data_download_status":
            state = self.downloads[params["transfer_id"]]
            raw = state["raw"]
            return self.envelope({"transfer_id": params["transfer_id"], "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "offset": state["offset"]})
        if name == "data_download_chunk":
            state = self.downloads[params["transfer_id"]]
            offset = int(params.get("offset", state["offset"]))
            limit = int(params.get("limit", 262144))
            raw = state["raw"][offset : offset + limit]
            next_offset = offset + len(raw)
            state["offset"] = max(state["offset"], next_offset)
            return self.envelope({
                "offset": offset,
                "next_offset": next_offset,
                "bytes_returned": len(raw),
                "size_bytes": len(state["raw"]),
                "eof": next_offset == len(state["raw"]),
                "sha256": hashlib.sha256(state["raw"]).hexdigest(),
                "data_base64": base64.b64encode(raw).decode("ascii"),
            })
        return self.envelope({"command": name, "params": params})


def config() -> ProxyConfig:
    return ProxyConfig(verify_version=True, retries=1)


@pytest.mark.asyncio
async def test_version_and_paginated_info(tmp_path: Path) -> None:
    proxy = FakeProxy(tmp_path)
    client = AsyncScienceAssistantClient(config(), proxy_client=proxy)
    assert await client.ensure_compatible() == __version__
    info = await client.info(page_size=10)
    assert info["markdown"] == "alpha\nbeta\ngamma\ndelta\n"
    assert info["pagination"]["assembled_pages"] == 2
    assert [call["params"].get("block_position") for call in proxy.calls if call["command"] == "info" and call["params"].get("include_markdown", True)] == [1, 2]


@pytest.mark.asyncio
async def test_version_mismatch(tmp_path: Path) -> None:
    client = AsyncScienceAssistantClient(config(), proxy_client=FakeProxy(tmp_path, server_version="9.9.9"))
    with pytest.raises(VersionMismatchError):
        await client.ensure_compatible()


@pytest.mark.asyncio
async def test_binary_roundtrip(tmp_path: Path) -> None:
    proxy = FakeProxy(tmp_path / "remote")
    client = AsyncScienceAssistantClient(config(), proxy_client=proxy)
    source = tmp_path / "source.bin"
    data = bytes(range(256)) * 31 + b"\x00\xffscience-assistant"
    source.write_bytes(data)
    uploaded = await client.upload_file(source, "roundtrip/source.bin", chunk_size=257)
    assert uploaded.sha256 == hashlib.sha256(data).hexdigest()
    destination = tmp_path / "destination.bin"
    downloaded = await client.download_file("roundtrip/source.bin", destination, chunk_size=199)
    assert destination.read_bytes() == data
    assert downloaded.sha256 == uploaded.sha256
    assert not source.with_name(f".{source.name}.science-upload.json").exists()
    assert not destination.with_name(f".{destination.name}.science-download.json").exists()


@pytest.mark.asyncio
async def test_package_upload_roundtrip(tmp_path: Path) -> None:
    proxy = FakeProxy(tmp_path / "remote-package")
    client = AsyncScienceAssistantClient(config(), proxy_client=proxy)
    source = tmp_path / "package-source.bin"
    raw = bytes(range(251)) * 23 + b"\x00\xffpackage-protocol"
    source.write_bytes(raw)
    receipt = await client.upload_package_file(
        source, "packages/source.bin", part_size=233, package_id="pkg_client_test", wait_timeout=5,
    )
    assert receipt.direction == "package-upload"
    assert receipt.sha256 == hashlib.sha256(raw).hexdigest()
    assert (tmp_path / "remote-package/packages/source.bin").read_bytes() == raw
    assert not source.with_name(f".{source.name}.science-package.json").exists()
