"""MCP commands for filesystem-backed file receive, retrieval, and listing."""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Dict, Type

from mcp_proxy_adapter.commands.base import Command, CommandResult

from science_assistant.services import file_store


class _FileCommand(Command):
    version: ClassVar[str] = "1.0.0"
    category: ClassVar[str] = "file-store"
    author: ClassVar[str] = "Vasiliy Zdanovskiy"
    email: ClassVar[str] = "vasilyvz@gmail.com"
    result_class: ClassVar[Type[CommandResult]] = CommandResult

    async def _call(self, func: Any, **kwargs: Any) -> CommandResult:
        try:
            data = await asyncio.to_thread(func, **kwargs)
            return CommandResult(success=True, data=data)
        except TimeoutError as exc:
            return CommandResult(success=False, error="FILE_SESSION_EXPIRED", data={"message": str(exc)})
        except (ValueError, FileNotFoundError, FileExistsError) as exc:
            return CommandResult(success=False, error="FILE_ERROR", data={"message": str(exc)})
        except Exception as exc:
            return CommandResult(success=False, error="COMMAND_ERROR", data={"message": str(exc)})


class FileReceiveCommand(_FileCommand):
    name = "file_receive"
    descr = "Receive one portion of a full-file Base64 stream through MCP."

    @classmethod
    def get_schema(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "upload_session_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "Omit for the first portion; use the returned UUID4 for later portions.",
                },
                "filename": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Required only in the first portion header.",
                },
                "part_index": {"type": "integer", "minimum": 0},
                "part_count": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Required only in the first portion header.",
                },
                "size_bytes": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Decoded file size; required only in the first portion header.",
                },
                "sha256": {
                    "type": "string",
                    "pattern": "^[0-9a-fA-F]{64}$",
                    "description": "SHA-256 of decoded file bytes; required only in the first portion header.",
                },
                "ttl_seconds": {
                    "type": "integer",
                    "minimum": file_store.MIN_TTL_SECONDS,
                    "maximum": file_store.MAX_TTL_SECONDS,
                    "description": "Session TTL renewed after every accepted portion; required in the first header.",
                },
                "data_base64_part": {
                    "type": "string",
                    "description": "A fragment of the complete file Base64 string. It need not be independently decodable.",
                },
            },
            "required": ["part_index", "data_base64_part"],
            "additionalProperties": False,
        }

    @classmethod
    def metadata(cls) -> Dict[str, Any]:
        return {
            "name": cls.name,
            "version": cls.version,
            "description": cls.descr,
            "category": cls.category,
            "author": cls.author,
            "email": cls.email,
            "detailed_description": (
                "The first call omits upload_session_id and must use part_index=0 with filename, part_count, "
                "decoded size_bytes, decoded-file sha256, and ttl_seconds. It returns only upload_session_id. "
                "Later calls use that session id. Each accepted portion renews the TTL. When the final missing "
                "portion arrives, the server concatenates all Base64 fragments, decodes to a temporary file, "
                "checks size and SHA-256, atomically stores it as <uuid4>-<filename>, deletes the session, and "
                "returns the permanent file_id UUID4 in the response to that final call."
            ),
            "parameters": {
                "upload_session_id": {"required": False, "description": "UUID4 returned by the first portion."},
                "filename": {"required": "first portion only"},
                "part_index": {"required": True, "description": "Zero-based Base64-fragment index."},
                "part_count": {"required": "first portion only"},
                "size_bytes": {"required": "first portion only"},
                "sha256": {"required": "first portion only"},
                "ttl_seconds": {"required": "first portion only"},
                "data_base64_part": {"required": True},
            },
            "return_value": {
                "receiving": {
                    "upload_session_id": "UUID4 used by subsequent portions.",
                    "file_id": "Absent until final verified portion.",
                    "expires_at": "Renewed expiry timestamp.",
                },
                "completed": {
                    "file_id": "Permanent UUID4 returned only after final verification.",
                    "stored_name": "<file_id>-<sanitized original name>.",
                    "size_bytes": "Verified decoded size.",
                    "sha256": "Verified decoded-file digest.",
                },
            },
            "error_cases": {
                "FILE_SESSION_EXPIRED": {"solution": "Start a new upload session from part 0."},
                "FILE_ERROR": {"solution": "Correct the header, part index, Base64 fragment, size, or checksum."},
            },
            "best_practices": [
                "Calculate SHA-256 before Base64 encoding.",
                "Split the complete Base64 text, not decoded raw chunks.",
                "Persist upload_session_id until the final response returns file_id.",
                "Retrying an identical portion is idempotent.",
            ],
        }

    async def execute(self, **kwargs: Any) -> CommandResult:
        kwargs.pop("context", None)
        return await self._call(
            file_store.receive_file_part,
            upload_session_id=kwargs.get("upload_session_id"),
            filename=kwargs.get("filename"),
            part_index=int(kwargs["part_index"]),
            part_count=kwargs.get("part_count"),
            size_bytes=kwargs.get("size_bytes"),
            sha256=kwargs.get("sha256"),
            ttl_seconds=kwargs.get("ttl_seconds"),
            data_base64_part=str(kwargs["data_base64_part"]),
        )


class FileGetCommand(_FileCommand):
    name = "file_get"
    descr = "Return one Base64-encoded file part by permanent file_id UUID4."

    @classmethod
    def get_schema(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "format": "uuid"},
                "part_index": {"type": "integer", "minimum": 0, "default": 0},
                "part_size_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": file_store.MAX_GET_PART_SIZE,
                    "default": file_store.DEFAULT_GET_PART_SIZE,
                },
            },
            "required": ["file_id"],
            "additionalProperties": False,
        }

    @classmethod
    def metadata(cls) -> Dict[str, Any]:
        return {
            "name": cls.name,
            "version": cls.version,
            "description": cls.descr,
            "category": cls.category,
            "author": cls.author,
            "email": cls.email,
            "detailed_description": (
                "Reads an immutable stored file by file_id. The response contains one raw byte range encoded "
                "as Base64 plus part_index, part_count, offset, bytes_returned, eof, original name, total size, "
                "and whole-file SHA-256. Request consecutive indices until eof=true."
            ),
            "pagination": {
                "model": "zero-based file parts",
                "request": ["part_index", "part_size_bytes"],
                "termination": "Stop when eof=true.",
            },
            "best_practices": ["Verify reconstructed size and whole-file SHA-256 before accepting the file."],
        }

    async def execute(self, **kwargs: Any) -> CommandResult:
        kwargs.pop("context", None)
        return await self._call(
            file_store.get_file_part,
            file_id=str(kwargs["file_id"]),
            part_index=int(kwargs.get("part_index", 0)),
            part_size_bytes=int(kwargs.get("part_size_bytes", file_store.DEFAULT_GET_PART_SIZE)),
        )


class FileLsCommand(_FileCommand):
    name = "file_ls"
    descr = "List stored files with mandatory pagination and ls-style name filtering."

    @classmethod
    def get_schema(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "minimum": 1},
                "page_size": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": file_store.MAX_LIST_PAGE_SIZE,
                },
                "name_pattern": {
                    "type": "string",
                    "default": "*",
                    "description": "Case-sensitive shell glob such as *.pdf, report-??.csv, or theta*.",
                },
            },
            "required": ["page", "page_size"],
            "additionalProperties": False,
        }

    @classmethod
    def metadata(cls) -> Dict[str, Any]:
        return {
            "name": cls.name,
            "version": cls.version,
            "description": cls.descr,
            "category": cls.category,
            "author": cls.author,
            "email": cls.email,
            "detailed_description": (
                "Lists completed files only. page and page_size are mandatory. name_pattern follows Python/shell "
                "glob semantics compatible with common ls patterns: *, ?, and character classes such as [0-9]."
            ),
            "pagination": {
                "request": ["page", "page_size"],
                "response": ["page", "page_size", "total_items", "total_pages", "has_more", "next_page"],
                "mandatory": True,
            },
            "best_practices": ["Keep page_size stable while traversing pages.", "Use '*' to list all names."],
        }

    async def execute(self, **kwargs: Any) -> CommandResult:
        kwargs.pop("context", None)
        return await self._call(
            file_store.list_files,
            page=int(kwargs["page"]),
            page_size=int(kwargs["page_size"]),
            name_pattern=str(kwargs.get("name_pattern", "*")),
        )


class FileDeleteCommand(_FileCommand):
    name = "file_delete"
    descr = "Delete a stored file and its metadata by permanent file_id UUID4."

    @classmethod
    def get_schema(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "format": "uuid"},
            },
            "required": ["file_id"],
            "additionalProperties": False,
        }

    @classmethod
    def metadata(cls) -> Dict[str, Any]:
        return {
            "name": cls.name,
            "version": cls.version,
            "description": cls.descr,
            "category": cls.category,
            "author": cls.author,
            "email": cls.email,
            "detailed_description": (
                "Deletes the permanent file selected by file_id together with its JSON metadata sidecar. "
                "The server first moves both objects into a private transaction directory and rolls back "
                "the binary move if the metadata move fails. Unknown or already deleted identifiers return FILE_ERROR."
            ),
            "parameters": {
                "file_id": {"required": True, "description": "Permanent UUID4 returned by file_receive."},
            },
            "return_value": {
                "status": "deleted",
                "file_id": "Deleted permanent UUID4.",
                "name": "Original file name.",
                "size_bytes": "Deleted file size.",
                "sha256": "Deleted file digest.",
                "deleted_at": "UTC deletion timestamp.",
            },
            "error_cases": {
                "FILE_ERROR": {"solution": "Check file_id with file_ls before retrying."},
            },
            "best_practices": [
                "Use file_ls to resolve the intended UUID before deletion.",
                "Treat a successful response as permanent; a second deletion returns an unknown file_id error.",
            ],
        }

    async def execute(self, **kwargs: Any) -> CommandResult:
        kwargs.pop("context", None)
        return await self._call(file_store.delete_file, file_id=str(kwargs["file_id"]))
