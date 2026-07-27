"""Commands for package-style agent file transfer through MCP Proxy."""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Dict, Type

from mcp_proxy_adapter.commands.base import Command, CommandResult

from science_assistant.commands.package_metadata import package_metadata
from science_assistant.services import package_transfer


class _PackageCommand(Command):
    version: ClassVar[str] = "1.0.0"
    category: ClassVar[str] = "data-transfer"
    author: ClassVar[str] = "Vasiliy Zdanovskiy"
    email: ClassVar[str] = "vasilyvz@gmail.com"
    result_class: ClassVar[Type[CommandResult]] = CommandResult
    operation: ClassVar[str]

    @classmethod
    def metadata(cls) -> Dict[str, Any]:
        return package_metadata(cls)

    async def _call(self, func: Any, **kwargs: Any) -> CommandResult:
        try:
            data = await asyncio.to_thread(func, **kwargs)
            return CommandResult(success=True, data=data)
        except TimeoutError as exc:
            return CommandResult(success=False, error="PACKAGE_TIMEOUT", data={"message": str(exc)})
        except (ValueError, FileNotFoundError, FileExistsError) as exc:
            return CommandResult(success=False, error="PACKAGE_ERROR", data={"message": str(exc)})
        except Exception as exc:
            return CommandResult(success=False, error="COMMAND_ERROR", data={"message": str(exc)})


class DataPackagePartCommand(_PackageCommand):
    name = "data_package_part"
    descr = "Store one independently checksummed Base64 package part."
    operation = "receive_part"

    @classmethod
    def get_schema(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "package_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"},
                "relative_path": {"type": "string", "minLength": 1},
                "part_index": {"type": "integer", "minimum": 0},
                "part_count": {"type": "integer", "minimum": 1, "maximum": package_transfer.MAX_PART_COUNT},
                "part_size_bytes": {"type": "integer", "minimum": 1, "maximum": package_transfer.MAX_PART_SIZE},
                "total_size_bytes": {"type": "integer", "minimum": 0},
                "sha256": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
                "part_sha256": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
                "data_base64": {"type": "string"},
                "overwrite": {"type": "boolean", "default": False},
            },
            "required": [
                "package_id", "relative_path", "part_index", "part_count", "part_size_bytes",
                "total_size_bytes", "sha256", "part_sha256", "data_base64",
            ],
            "additionalProperties": False,
        }

    async def execute(self, **kwargs: Any) -> CommandResult:
        kwargs.pop("context", None)
        return await self._call(
            package_transfer.receive_part,
            package_id=str(kwargs["package_id"]),
            relative_path=str(kwargs["relative_path"]),
            part_index=int(kwargs["part_index"]),
            part_count=int(kwargs["part_count"]),
            part_size_bytes=int(kwargs["part_size_bytes"]),
            total_size_bytes=int(kwargs["total_size_bytes"]),
            sha256=str(kwargs["sha256"]),
            part_sha256=str(kwargs["part_sha256"]),
            data_base64=str(kwargs["data_base64"]),
            overwrite=bool(kwargs.get("overwrite", False)),
        )


class DataPackageWaitCommand(_PackageCommand):
    name = "data_package_wait"
    descr = "Wait for all package parts, verify them, and atomically assemble the file."
    operation = "wait_and_assemble"

    @classmethod
    def get_schema(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "package_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"},
                "relative_path": {"type": "string", "minLength": 1},
                "part_count": {"type": "integer", "minimum": 1, "maximum": package_transfer.MAX_PART_COUNT},
                "part_size_bytes": {"type": "integer", "minimum": 1, "maximum": package_transfer.MAX_PART_SIZE},
                "total_size_bytes": {"type": "integer", "minimum": 0},
                "sha256": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
                "overwrite": {"type": "boolean", "default": False},
                "timeout_seconds": {"type": "number", "minimum": 0, "maximum": 86400, "default": 300},
                "poll_interval_ms": {"type": "integer", "minimum": 50, "maximum": 10000, "default": 250},
            },
            "required": [
                "package_id", "relative_path", "part_count", "part_size_bytes", "total_size_bytes", "sha256",
            ],
            "additionalProperties": False,
        }

    async def execute(self, **kwargs: Any) -> CommandResult:
        kwargs.pop("context", None)
        return await self._call(
            package_transfer.wait_and_assemble,
            package_id=str(kwargs["package_id"]),
            relative_path=str(kwargs["relative_path"]),
            part_count=int(kwargs["part_count"]),
            part_size_bytes=int(kwargs["part_size_bytes"]),
            total_size_bytes=int(kwargs["total_size_bytes"]),
            sha256=str(kwargs["sha256"]),
            overwrite=bool(kwargs.get("overwrite", False)),
            timeout_seconds=float(kwargs.get("timeout_seconds", 300)),
            poll_interval_ms=int(kwargs.get("poll_interval_ms", 250)),
        )


class DataPackageStatusCommand(_PackageCommand):
    name = "data_package_status"
    descr = "Return paginated received and missing package part indices."
    operation = "package_status"

    @classmethod
    def get_schema(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "package_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"},
                "page": {"type": "integer", "minimum": 1, "default": 1},
                "page_size": {"type": "integer", "minimum": 1, "maximum": package_transfer.MAX_STATUS_PAGE_SIZE, "default": package_transfer.DEFAULT_STATUS_PAGE_SIZE},
            },
            "required": ["package_id"],
            "additionalProperties": False,
        }

    async def execute(self, **kwargs: Any) -> CommandResult:
        kwargs.pop("context", None)
        return await self._call(
            package_transfer.package_status,
            package_id=str(kwargs["package_id"]),
            page=int(kwargs.get("page", 1)),
            page_size=int(kwargs.get("page_size", package_transfer.DEFAULT_STATUS_PAGE_SIZE)),
        )
