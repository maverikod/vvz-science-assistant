"""Full Science Assistant documentation command with stable pagination."""

from __future__ import annotations

from typing import Any, ClassVar, Type

from mcp_proxy_adapter.commands.base import Command, CommandResult

from science_assistant.commands.info_resources import (
    GUIDE_VERSION,
    guide_markdown,
    integrations,
    package_info,
    registered_commands,
    runtime_info,
)
from science_assistant.commands.metadata import info_metadata
from science_assistant.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, MIN_PAGE_SIZE, paginate_lines


class InfoCommand(Command):
    name: ClassVar[str] = "info"
    version: ClassVar[str] = "1.1.0"
    descr: ClassVar[str] = "Paginated Science Assistant guide, command catalog, integrations, runtime identity, directories, and ports."
    category: ClassVar[str] = "system"
    author: ClassVar[str] = "Vasiliy Zdanovskiy"
    email: ClassVar[str] = "vasilyvz@gmail.com"
    result_class: ClassVar[Type[CommandResult]] = CommandResult

    @classmethod
    def get_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "page_size": {
                    "type": "integer",
                    "minimum": MIN_PAGE_SIZE,
                    "maximum": MAX_PAGE_SIZE,
                    "default": DEFAULT_PAGE_SIZE,
                    "description": "Number of documentation lines in one page.",
                },
                "block_position": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 1,
                    "description": "One-based documentation page number.",
                },
                "include_markdown": {
                    "type": "boolean",
                    "default": True,
                    "description": "When false, return inventory/runtime data without the documentation text.",
                },
            },
            "required": [],
            "additionalProperties": False,
            "description": "Returns one stable page of the guide plus compact runtime and command inventory.",
        }

    @classmethod
    def metadata(cls) -> dict[str, Any]:
        return info_metadata(cls, guide_markdown())

    async def execute(self, **kwargs: Any) -> CommandResult:
        kwargs.pop("context", None)
        try:
            page_size = int(kwargs.pop("page_size", DEFAULT_PAGE_SIZE))
            block_position = int(kwargs.pop("block_position", 1))
            include_markdown = bool(kwargs.pop("include_markdown", True))
            if kwargs:
                return CommandResult(success=False, error="VALIDATION_ERROR", data={"unexpected": sorted(kwargs)})
            page = paginate_lines(guide_markdown(), page_size=page_size, block_position=block_position)
            content = page.pop("content")
            markdown = content if include_markdown else None
            return CommandResult(success=True, data={
                "guide_version": GUIDE_VERSION,
                "package": package_info(),
                "summary": "astroquery/TAP/file gateway -> persistent mounted dataset -> manifest + SHA-256",
                "markdown": markdown,
                "pagination": page,
                "runtime": runtime_info(),
                "registered_commands": registered_commands(),
                "integrations": integrations(),
                "docs": [
                    "science-assistant-info",
                    "/usr/share/doc/science-assistant-docker/INFO.md",
                    "/etc/science-assistant/science-assistant.json",
                ],
            })
        except (TypeError, ValueError) as exc:
            return CommandResult(success=False, error="VALIDATION_ERROR", data={"message": str(exc)})
