"""Generic public scientific file download command."""

from __future__ import annotations

import asyncio
import re
from typing import Any, ClassVar, Type

from mcp_proxy_adapter.commands.base import Command, CommandResult

from science_assistant.commands.metadata import download_metadata
from science_assistant.services.downloader import download
from science_assistant.services.storage import dataset_directory, dataset_result, write_manifest

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class DownloadFileCommand(Command):
    name: ClassVar[str] = "download_file"
    version: ClassVar[str] = "1.0.0"
    descr: ClassVar[str] = "Stream an HTTP, HTTPS, or FTP file into persistent data storage with size and SHA-256 checks."
    category: ClassVar[str] = "science-data"
    author: ClassVar[str] = "Vasiliy Zdanovskiy"
    email: ClassVar[str] = "vasilyvz@gmail.com"
    result_class: ClassVar[Type[CommandResult]] = CommandResult

    @classmethod
    def get_schema(cls) -> dict[str, Any]:
        return {"type": "object", "properties": {
            "url": {"type": "string", "minLength": 1},
            "output_name": {"type": "string"},
            "dataset_name": {"type": "string"},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600, "default": 120},
            "max_bytes": {"type": "integer", "minimum": 1, "default": 53687091200},
            "expected_sha256": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
        }, "required": ["url"], "additionalProperties": False}

    @classmethod
    def metadata(cls) -> dict[str, Any]:
        return download_metadata(cls)

    async def execute(self, **kwargs: Any) -> CommandResult:
        kwargs.pop("context", None)
        url = str(kwargs.get("url", "")).strip()
        if not url:
            return CommandResult(success=False, error="VALIDATION_ERROR", data={"field": "url"})
        expected = str(kwargs.get("expected_sha256", "")).strip() or None
        if expected and not _SHA256.fullmatch(expected):
            return CommandResult(success=False, error="VALIDATION_ERROR", data={"field": "expected_sha256"})
        output_name = str(kwargs.get("output_name", "")).strip() or None
        dataset_name = str(kwargs.get("dataset_name", "")).strip() or None
        timeout_seconds = int(kwargs.get("timeout_seconds", 120))
        max_bytes = int(kwargs.get("max_bytes", 53687091200))
        try:
            directory = dataset_directory(dataset_name, "download")
            record = await asyncio.to_thread(download, url=url, directory=directory, output_name=output_name, timeout_seconds=timeout_seconds, max_bytes=max_bytes, expected_sha256=expected)
            manifest = write_manifest(directory, {"command": self.name, "request": {"url": url, "output_name": output_name, "timeout_seconds": timeout_seconds, "max_bytes": max_bytes, "expected_sha256": expected}, "files": [record]})
            return CommandResult(success=True, data=dataset_result(directory, manifest, file=record))
        except Exception as exc:
            return CommandResult(success=False, error="DOWNLOAD_ERROR", data={"type": type(exc).__name__, "message": str(exc)})
