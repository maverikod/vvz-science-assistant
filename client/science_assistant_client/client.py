"""High-level Science Assistant client built on mcp-proxy-adapter JsonRpcClient."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Mapping

from mcp_proxy_adapter.client.jsonrpc_client import JsonRpcClient

from .config import ProxyConfig
from .exceptions import RemoteCommandError, TransferIntegrityError, VersionMismatchError
from .models import FileTransferReceipt
from .version import __version__

SUCCESS_STATES = {"completed", "complete", "success", "succeeded", "done"}
FAILURE_STATES = {"failed", "failure", "error", "stopped", "cancelled", "canceled"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _error_text(payload: Mapping[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, Mapping):
        return str(error.get("message") or error.get("code") or error)
    data = payload.get("data")
    if isinstance(data, Mapping) and data.get("message"):
        return str(data["message"])
    return str(error or payload.get("message") or "remote command failed")


def unwrap(payload: Any) -> Any:
    """Remove proxy and CommandResult envelopes without discarding domain data."""
    current = payload
    for _ in range(10):
        if not isinstance(current, Mapping):
            return current
        if current.get("success") is False:
            raise RemoteCommandError(_error_text(current), payload=current)
        if isinstance(current.get("result"), Mapping):
            current = current["result"]
            continue
        if "data" in current and any(key in current for key in ("success", "error", "message")):
            current = current["data"]
            continue
        return dict(current)
    return current


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class AsyncScienceAssistantClient:
    """Async high-level client. All network traffic goes through MCP Proxy."""

    def __init__(self, config: ProxyConfig | None = None, *, proxy_client: Any | None = None) -> None:
        self.config = config or ProxyConfig.from_env()
        self._owns_client = proxy_client is None
        self._proxy = proxy_client or JsonRpcClient(
            protocol=self.config.protocol,
            host=self.config.host,
            port=self.config.port,
            token_header=self.config.token_header,
            token=self.config.token,
            cert=self.config.cert,
            key=self.config.key,
            ca=self.config.ca,
            check_hostname=self.config.check_hostname,
            timeout=self.config.timeout,
        )
        self._version_checked = False

    async def __aenter__(self) -> "AsyncScienceAssistantClient":
        if self.config.verify_version:
            await self.ensure_compatible()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client and hasattr(self._proxy, "close"):
            result = self._proxy.close()
            if asyncio.iscoroutine(result):
                await result

    async def _execute_proxy(self, payload: dict[str, Any]) -> Any:
        attempts = max(1, self.config.retries)
        for attempt in range(attempts):
            try:
                response = await self._proxy.execute_command("call_server", payload)
                return unwrap(response)
            except (RemoteCommandError, ValueError, TypeError):
                raise
            except Exception:
                if attempt + 1 >= attempts:
                    raise
                await asyncio.sleep(self.config.retry_delay * (attempt + 1))
        raise AssertionError("unreachable")

    async def _call_once(
        self,
        command: str,
        params: Mapping[str, Any] | None = None,
        *,
        use_queue: bool = False,
        job_id: str | None = None,
    ) -> Any:
        payload: dict[str, Any] = {
            "server_id": self.config.server_id,
            "copy_number": self.config.copy_number,
            "command": command,
            "params": dict(params or {}),
            "use_queue": use_queue,
        }
        if job_id:
            payload["job_id"] = job_id
        return await self._execute_proxy(payload)

    @staticmethod
    def _status(payload: Mapping[str, Any]) -> str:
        for key in ("queue_status", "terminal_status", "status", "job_status"):
            if payload.get(key) is not None:
                return str(payload[key]).lower()
        return ""

    async def _wait_job(self, acceptance: Mapping[str, Any], timeout: float | None = None) -> Any:
        job_id = acceptance.get("job_id") or acceptance.get("id")
        if not job_id:
            return dict(acceptance)
        deadline = time.monotonic() + (timeout or self.config.timeout)
        while True:
            status_payload = await self._call_once("queue_get_job_status", {"job_id": str(job_id)})
            if not isinstance(status_payload, Mapping):
                return status_payload
            status = self._status(status_payload)
            if status in SUCCESS_STATES:
                for key in ("command_result", "result", "output"):
                    if status_payload.get(key) is not None:
                        return unwrap(status_payload[key])
                return dict(status_payload)
            if status in FAILURE_STATES:
                raise RemoteCommandError(_error_text(status_payload), payload=status_payload)
            if time.monotonic() >= deadline:
                raise TimeoutError(f"queue job {job_id} did not finish before timeout")
            await asyncio.sleep(1.0)

    async def call(
        self,
        command: str,
        params: Mapping[str, Any] | None = None,
        *,
        use_queue: bool = False,
        wait: bool = True,
        job_id: str | None = None,
        timeout: float | None = None,
        check_version: bool = True,
    ) -> Any:
        if check_version and self.config.verify_version and command != "info" and not self._version_checked:
            await self.ensure_compatible()
        result = await self._call_once(command, params, use_queue=use_queue, job_id=job_id)
        if use_queue and wait and isinstance(result, Mapping):
            result = await self._wait_job(result, timeout=timeout)
        return result

    async def info_page(self, *, page_size: int = 80, block_position: int = 1, include_markdown: bool = True) -> dict[str, Any]:
        result = await self.call(
            "info",
            {"page_size": page_size, "block_position": block_position, "include_markdown": include_markdown},
            check_version=False,
        )
        if not isinstance(result, Mapping):
            raise RemoteCommandError("info returned a non-object", payload=result)
        return dict(result)

    async def iter_info_pages(self, *, page_size: int = 80) -> AsyncIterator[dict[str, Any]]:
        block = 1
        while True:
            page = await self.info_page(page_size=page_size, block_position=block, include_markdown=True)
            yield page
            pagination = page.get("pagination")
            if not isinstance(pagination, Mapping) or not pagination.get("has_more"):
                break
            block = int(pagination.get("next_block_position") or block + 1)

    async def info(self, *, page_size: int = 80) -> dict[str, Any]:
        pages: list[dict[str, Any]] = []
        text: list[str] = []
        async for page in self.iter_info_pages(page_size=page_size):
            pages.append(page)
            text.append(str(page.get("markdown") or ""))
        assembled = dict(pages[0]) if pages else {}
        assembled["markdown"] = "".join(text)
        assembled["pagination"] = {
            **dict(assembled.get("pagination") or {}),
            "assembled_pages": len(pages),
            "has_more": False,
            "next_block_position": None,
        }
        return assembled

    async def ensure_compatible(self) -> str:
        info = await self.info_page(include_markdown=False)
        package = info.get("package")
        server_version = str(package.get("version")) if isinstance(package, Mapping) else ""
        if server_version != __version__:
            raise VersionMismatchError(f"science-assistant-client {__version__} != server {server_version or 'unknown'}")
        self._version_checked = True
        return server_version

    async def query_catalog(self, *, use_queue: bool = False, **params: Any) -> Any:
        return await self.call("astroquery_catalog", params, use_queue=use_queue)

    async def query_object(self, *, use_queue: bool = False, **params: Any) -> Any:
        return await self.call("astroquery_object", params, use_queue=use_queue)

    async def query_adql(self, *, use_queue: bool = True, **params: Any) -> Any:
        return await self.call("astroquery_adql", params, use_queue=use_queue)

    async def fetch_url(self, *, use_queue: bool = False, **params: Any) -> Any:
        return await self.call("download_file", params, use_queue=use_queue)

    async def upload_file(
        self,
        local_path: str | Path,
        remote_path: str,
        *,
        chunk_size: int = 262144,
        overwrite: bool = False,
        resume: bool = True,
        state_path: str | Path | None = None,
    ) -> FileTransferReceipt:
        source = Path(local_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        size = source.stat().st_size
        digest = sha256_file(source)
        state_file = Path(state_path) if state_path else source.with_name(f".{source.name}.science-upload.json")
        transfer_id: str | None = None
        offset = 0
        if resume and state_file.exists():
            saved = json.loads(state_file.read_text(encoding="utf-8"))
            matches = saved.get("remote_path") == remote_path and saved.get("sha256") == digest and int(saved.get("size_bytes", -1)) == size
            if matches and saved.get("transfer_id"):
                transfer_id = str(saved["transfer_id"])
                status = await self.call("data_upload_status", {"transfer_id": transfer_id})
                offset = int(status.get("offset", 0))
        if not transfer_id:
            begun = await self.call("data_upload_begin", {
                "relative_path": remote_path,
                "size_bytes": size,
                "sha256": digest,
                "chunk_size": chunk_size,
                "overwrite": overwrite,
            })
            transfer_id = str(begun["transfer_id"])
            offset = int(begun.get("offset", 0))
        atomic_json(state_file, {"direction":"upload", "transfer_id":transfer_id, "local_path":str(source), "remote_path":remote_path, "size_bytes":size, "sha256":digest, "offset":offset})
        with source.open("rb") as stream:
            stream.seek(offset)
            while offset < size:
                raw = stream.read(min(chunk_size, size - offset))
                if not raw:
                    raise TransferIntegrityError("local file ended before declared size")
                result = await self.call("data_upload_chunk", {
                    "transfer_id": transfer_id,
                    "offset": offset,
                    "data_base64": base64.b64encode(raw).decode("ascii"),
                })
                next_offset = int(result.get("offset", -1))
                if next_offset != offset + len(raw):
                    raise TransferIntegrityError(f"server returned invalid upload offset {next_offset}")
                offset = next_offset
                atomic_json(state_file, {"direction":"upload", "transfer_id":transfer_id, "local_path":str(source), "remote_path":remote_path, "size_bytes":size, "sha256":digest, "offset":offset})
        completed = await self.call("data_upload_complete", {"transfer_id": transfer_id})
        file_data = completed.get("file") if isinstance(completed, Mapping) else None
        if not isinstance(file_data, Mapping) or int(file_data.get("size_bytes", -1)) != size or str(file_data.get("sha256", "")).lower() != digest:
            raise TransferIntegrityError("server completion metadata does not match local file")
        state_file.unlink(missing_ok=True)
        return FileTransferReceipt("upload", transfer_id, source, remote_path, size, digest, dict(completed))

    async def upload_package_file(
        self,
        local_path: str | Path,
        remote_path: str,
        *,
        part_size: int = 131072,
        overwrite: bool = False,
        resume: bool = True,
        state_path: str | Path | None = None,
        package_id: str | None = None,
        wait_timeout: float = 300.0,
    ) -> FileTransferReceipt:
        """Upload independent MCP parts and let data_package_wait assemble them."""
        source = Path(local_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        if part_size < 1 or part_size > 524288:
            raise ValueError("part_size must be between 1 and 524288")
        size = source.stat().st_size
        digest = sha256_file(source)
        part_count = max(1, (size + part_size - 1) // part_size)
        state_file = Path(state_path) if state_path else source.with_name(f".{source.name}.science-package.json")
        resolved_package_id = package_id
        if resume and state_file.exists():
            saved = json.loads(state_file.read_text(encoding="utf-8"))
            matches = (
                saved.get("remote_path") == remote_path
                and saved.get("sha256") == digest
                and int(saved.get("size_bytes", -1)) == size
                and int(saved.get("part_size", -1)) == part_size
            )
            if matches and saved.get("package_id"):
                resolved_package_id = str(saved["package_id"])
        if not resolved_package_id:
            resolved_package_id = f"pkg_{digest[:12]}_{uuid.uuid4().hex[:8]}"
        common = {
            "package_id": resolved_package_id,
            "relative_path": remote_path,
            "part_count": part_count,
            "part_size_bytes": part_size,
            "total_size_bytes": size,
            "sha256": digest,
            "overwrite": overwrite,
        }
        atomic_json(state_file, {
            "direction": "package-upload",
            "package_id": resolved_package_id,
            "local_path": str(source),
            "remote_path": remote_path,
            "size_bytes": size,
            "sha256": digest,
            "part_size": part_size,
            "part_count": part_count,
        })
        with source.open("rb") as stream:
            for index in range(part_count):
                expected = size if part_count == 1 else (part_size if index < part_count - 1 else size - part_size * (part_count - 1))
                raw = stream.read(expected)
                if len(raw) != expected:
                    raise TransferIntegrityError(f"local file ended before part {index} was complete")
                result = await self.call("data_package_part", {
                    **common,
                    "part_index": index,
                    "part_sha256": hashlib.sha256(raw).hexdigest(),
                    "data_base64": base64.b64encode(raw).decode("ascii"),
                })
                if int(result.get("part_index", -1)) != index:
                    raise TransferIntegrityError(f"server acknowledged unexpected package part {result.get('part_index')}")
        completed = await self.call(
            "data_package_wait",
            {**common, "timeout_seconds": wait_timeout, "poll_interval_ms": 250},
            use_queue=True,
            wait=True,
            timeout=wait_timeout + 30,
        )
        file_data = completed.get("file") if isinstance(completed, Mapping) else None
        if not isinstance(file_data, Mapping) or int(file_data.get("size_bytes", -1)) != size or str(file_data.get("sha256", "")).lower() != digest:
            raise TransferIntegrityError("package assembly metadata does not match local file")
        state_file.unlink(missing_ok=True)
        return FileTransferReceipt("package-upload", resolved_package_id, source, remote_path, size, digest, dict(completed))

    async def download_file(
        self,
        remote_path: str,
        local_path: str | Path,
        *,
        chunk_size: int = 262144,
        overwrite: bool = False,
        resume: bool = True,
        state_path: str | Path | None = None,
    ) -> FileTransferReceipt:
        destination = Path(local_path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and not overwrite:
            raise FileExistsError(destination)
        part = destination.with_name(destination.name + ".part")
        state_file = Path(state_path) if state_path else destination.with_name(f".{destination.name}.science-download.json")
        transfer_id: str | None = None
        expected_size: int | None = None
        expected_sha: str | None = None
        if resume and state_file.exists():
            saved = json.loads(state_file.read_text(encoding="utf-8"))
            if saved.get("remote_path") == remote_path and saved.get("transfer_id"):
                transfer_id = str(saved["transfer_id"])
                status = await self.call("data_download_status", {"transfer_id": transfer_id})
                expected_size = int(status["size_bytes"])
                expected_sha = str(status["sha256"]).lower()
        if not transfer_id:
            begun = await self.call("data_download_begin", {"relative_path": remote_path, "chunk_size": chunk_size})
            transfer_id = str(begun["transfer_id"])
            expected_size = int(begun["size_bytes"])
            expected_sha = str(begun["sha256"]).lower()
            part.unlink(missing_ok=True)
        assert expected_size is not None and expected_sha is not None
        offset = part.stat().st_size if resume and part.exists() else 0
        if offset > expected_size:
            raise TransferIntegrityError("partial local file is larger than remote source")
        atomic_json(state_file, {"direction":"download", "transfer_id":transfer_id, "local_path":str(destination), "remote_path":remote_path, "size_bytes":expected_size, "sha256":expected_sha, "offset":offset})
        mode = "ab" if offset else "wb"
        with part.open(mode) as stream:
            while offset < expected_size:
                result = await self.call("data_download_chunk", {"transfer_id":transfer_id, "offset":offset, "limit":chunk_size})
                raw = base64.b64decode(str(result.get("data_base64", "")), validate=True)
                if int(result.get("offset", -1)) != offset or int(result.get("bytes_returned", -1)) != len(raw):
                    raise TransferIntegrityError("download chunk metadata does not match decoded bytes")
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
                next_offset = int(result.get("next_offset", -1))
                if next_offset != offset + len(raw):
                    raise TransferIntegrityError("server returned an invalid next_offset")
                offset = next_offset
                atomic_json(state_file, {"direction":"download", "transfer_id":transfer_id, "local_path":str(destination), "remote_path":remote_path, "size_bytes":expected_size, "sha256":expected_sha, "offset":offset})
                if result.get("eof") and offset != expected_size:
                    raise TransferIntegrityError("server reported EOF before expected size")
        actual_size = part.stat().st_size
        actual_sha = sha256_file(part)
        if actual_size != expected_size or actual_sha != expected_sha:
            raise TransferIntegrityError(f"download integrity mismatch: size {actual_size}/{expected_size}, sha256 {actual_sha}/{expected_sha}")
        if destination.exists() and overwrite:
            destination.unlink()
        os.replace(part, destination)
        state_file.unlink(missing_ok=True)
        return FileTransferReceipt("download", transfer_id, destination, remote_path, expected_size, expected_sha, {"status":"completed"})


class ScienceAssistantClient:
    """Synchronous facade for scripts and interactive analysis environments."""

    def __init__(self, config: ProxyConfig | None = None) -> None:
        self.config = config or ProxyConfig.from_env()

    @staticmethod
    def _run(coro: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        raise RuntimeError("ScienceAssistantClient sync facade cannot run inside an active event loop; use AsyncScienceAssistantClient")

    def call(self, command: str, params: Mapping[str, Any] | None = None, **options: Any) -> Any:
        async def operation() -> Any:
            async with AsyncScienceAssistantClient(self.config) as client:
                return await client.call(command, params, **options)
        return self._run(operation())

    def info(self, *, page_size: int = 80) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            async with AsyncScienceAssistantClient(self.config) as client:
                return await client.info(page_size=page_size)
        return self._run(operation())

    def upload_file(self, local_path: str | Path, remote_path: str, **options: Any) -> FileTransferReceipt:
        async def operation() -> FileTransferReceipt:
            async with AsyncScienceAssistantClient(self.config) as client:
                return await client.upload_file(local_path, remote_path, **options)
        return self._run(operation())

    def upload_package_file(self, local_path: str | Path, remote_path: str, **options: Any) -> FileTransferReceipt:
        async def operation() -> FileTransferReceipt:
            async with AsyncScienceAssistantClient(self.config) as client:
                return await client.upload_package_file(local_path, remote_path, **options)
        return self._run(operation())

    def download_file(self, remote_path: str, local_path: str | Path, **options: Any) -> FileTransferReceipt:
        async def operation() -> FileTransferReceipt:
            async with AsyncScienceAssistantClient(self.config) as client:
                return await client.download_file(remote_path, local_path, **options)
        return self._run(operation())
