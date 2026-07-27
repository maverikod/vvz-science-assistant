"""MCP-native bidirectional file streaming commands."""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Dict, Type

from mcp_proxy_adapter.commands.base import Command, CommandResult

from science_assistant.commands.transfer_metadata import transfer_metadata
from science_assistant.services import mcp_transfer


class _TransferCommand(Command):
    version: ClassVar[str] = "1.0.0"
    category: ClassVar[str] = "data-transfer"
    author: ClassVar[str] = "Vasiliy Zdanovskiy"
    email: ClassVar[str] = "vasilyvz@gmail.com"
    result_class: ClassVar[Type[CommandResult]] = CommandResult
    operation: ClassVar[str]

    @classmethod
    def metadata(cls) -> Dict[str, Any]:
        return transfer_metadata(cls)

    async def _call(self, func: Any, **kwargs: Any) -> CommandResult:
        try:
            data = await asyncio.to_thread(func, **kwargs)
            return CommandResult(success=True, data=data)
        except (ValueError, FileNotFoundError, FileExistsError) as exc:
            return CommandResult(success=False, error="TRANSFER_ERROR", data={"message": str(exc)})
        except Exception as exc:
            return CommandResult(success=False, error="COMMAND_ERROR", data={"message": str(exc)})


class DataUploadBeginCommand(_TransferCommand):
    name = "data_upload_begin"
    descr = "Begin an MCP-native Base64 upload into the server data directory."
    operation = "upload_begin"

    @classmethod
    def get_schema(cls) -> Dict[str, Any]:
        return {"type":"object","properties":{"relative_path":{"type":"string","minLength":1},"size_bytes":{"type":"integer","minimum":0},"sha256":{"type":"string","pattern":"^[0-9a-fA-F]{64}$"},"chunk_size":{"type":"integer","minimum":1,"maximum":mcp_transfer.MAX_CHUNK_SIZE,"default":mcp_transfer.DEFAULT_CHUNK_SIZE},"overwrite":{"type":"boolean","default":False}},"required":["relative_path","size_bytes","sha256"],"additionalProperties":False}

    async def execute(self, **kwargs: Any) -> CommandResult:
        kwargs.pop("context", None)
        return await self._call(mcp_transfer.upload_begin, relative_path=str(kwargs["relative_path"]), size_bytes=int(kwargs["size_bytes"]), sha256=str(kwargs["sha256"]), chunk_size=kwargs.get("chunk_size"), overwrite=bool(kwargs.get("overwrite", False)))


class DataUploadChunkCommand(_TransferCommand):
    name = "data_upload_chunk"
    descr = "Append one Base64 chunk to an MCP-native upload session."
    operation = "upload_chunk"

    @classmethod
    def get_schema(cls) -> Dict[str, Any]:
        return {"type":"object","properties":{"transfer_id":{"type":"string","minLength":1},"offset":{"type":"integer","minimum":0},"data_base64":{"type":"string"}},"required":["transfer_id","offset","data_base64"],"additionalProperties":False}

    async def execute(self, **kwargs: Any) -> CommandResult:
        kwargs.pop("context", None)
        return await self._call(mcp_transfer.upload_chunk, transfer_id=str(kwargs["transfer_id"]), offset=int(kwargs["offset"]), data_base64=str(kwargs["data_base64"]))


class DataUploadCompleteCommand(_TransferCommand):
    name = "data_upload_complete"
    descr = "Verify SHA-256 and atomically complete an MCP-native upload."
    operation = "upload_complete"

    @classmethod
    def get_schema(cls) -> Dict[str, Any]:
        return {"type":"object","properties":{"transfer_id":{"type":"string","minLength":1}},"required":["transfer_id"],"additionalProperties":False}

    async def execute(self, **kwargs: Any) -> CommandResult:
        kwargs.pop("context", None)
        return await self._call(mcp_transfer.upload_complete, transfer_id=str(kwargs["transfer_id"]))


class DataUploadStatusCommand(_TransferCommand):
    name = "data_upload_status"
    descr = "Return persisted state for an MCP-native upload."
    operation = "upload_status"

    @classmethod
    def get_schema(cls) -> Dict[str, Any]:
        return {"type":"object","properties":{"transfer_id":{"type":"string","minLength":1}},"required":["transfer_id"],"additionalProperties":False}

    async def execute(self, **kwargs: Any) -> CommandResult:
        kwargs.pop("context", None)
        return await self._call(mcp_transfer.upload_status, transfer_id=str(kwargs["transfer_id"]))


class DataDownloadBeginCommand(_TransferCommand):
    name = "data_download_begin"
    descr = "Begin an MCP-native Base64 download from the server data directory."
    operation = "download_begin"

    @classmethod
    def get_schema(cls) -> Dict[str, Any]:
        return {"type":"object","properties":{"relative_path":{"type":"string","minLength":1},"chunk_size":{"type":"integer","minimum":1,"maximum":mcp_transfer.MAX_CHUNK_SIZE,"default":mcp_transfer.DEFAULT_CHUNK_SIZE}},"required":["relative_path"],"additionalProperties":False}

    async def execute(self, **kwargs: Any) -> CommandResult:
        kwargs.pop("context", None)
        return await self._call(mcp_transfer.download_begin, relative_path=str(kwargs["relative_path"]), chunk_size=kwargs.get("chunk_size"))


class DataDownloadChunkCommand(_TransferCommand):
    name = "data_download_chunk"
    descr = "Read one Base64 chunk from an MCP-native download session."
    operation = "download_chunk"

    @classmethod
    def get_schema(cls) -> Dict[str, Any]:
        return {"type":"object","properties":{"transfer_id":{"type":"string","minLength":1},"offset":{"type":"integer","minimum":0},"limit":{"type":"integer","minimum":1,"maximum":mcp_transfer.MAX_CHUNK_SIZE}},"required":["transfer_id"],"additionalProperties":False}

    async def execute(self, **kwargs: Any) -> CommandResult:
        kwargs.pop("context", None)
        return await self._call(mcp_transfer.download_chunk, transfer_id=str(kwargs["transfer_id"]), offset=kwargs.get("offset"), limit=kwargs.get("limit"))


class DataDownloadStatusCommand(_TransferCommand):
    name = "data_download_status"
    descr = "Return persisted state for an MCP-native download."
    operation = "download_status"

    @classmethod
    def get_schema(cls) -> Dict[str, Any]:
        return {"type":"object","properties":{"transfer_id":{"type":"string","minLength":1}},"required":["transfer_id"],"additionalProperties":False}

    async def execute(self, **kwargs: Any) -> CommandResult:
        kwargs.pop("context", None)
        return await self._call(mcp_transfer.download_status, transfer_id=str(kwargs["transfer_id"]))
