"""VizieR catalog download command."""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Type

from mcp_proxy_adapter.commands.base import Command, CommandResult

from science_assistant.commands.metadata import catalog_metadata
from science_assistant.services.astroquery_gateway import query_catalog
from science_assistant.services.storage import dataset_directory, dataset_result, write_manifest, write_tables


class AstroqueryCatalogCommand(Command):
    name: ClassVar[str] = "astroquery_catalog"
    version: ClassVar[str] = "1.0.0"
    descr: ClassVar[str] = "Download VizieR catalog tables through astroquery and persist them with a manifest and hashes."
    category: ClassVar[str] = "science-data"
    author: ClassVar[str] = "Vasiliy Zdanovskiy"
    email: ClassVar[str] = "vasilyvz@gmail.com"
    result_class: ClassVar[Type[CommandResult]] = CommandResult

    @classmethod
    def get_schema(cls) -> dict[str, Any]:
        return {"type": "object", "properties": {
            "catalog": {"type": "string", "minLength": 1},
            "table": {"type": "string"},
            "columns": {"type": "array", "items": {"type": "string"}, "default": ["*"]},
            "constraints": {"type": "object", "default": {}},
            "row_limit": {"type": "integer", "minimum": -1, "default": -1},
            "output_format": {"type": "string", "enum": ["ecsv", "csv", "fits", "parquet"], "default": "ecsv"},
            "dataset_name": {"type": "string"},
        }, "required": ["catalog"], "additionalProperties": False}

    @classmethod
    def metadata(cls) -> dict[str, Any]:
        return catalog_metadata(cls)

    async def execute(self, **kwargs: Any) -> CommandResult:
        kwargs.pop("context", None)
        catalog = str(kwargs.get("catalog", "")).strip()
        if not catalog:
            return CommandResult(success=False, error="VALIDATION_ERROR", data={"field": "catalog"})
        table = str(kwargs.get("table", "")).strip() or None
        columns = kwargs.get("columns") or ["*"]
        constraints = kwargs.get("constraints") or {}
        row_limit = int(kwargs.get("row_limit", -1))
        output_format = str(kwargs.get("output_format", "ecsv"))
        dataset_name = str(kwargs.get("dataset_name", "")).strip() or None
        try:
            tables = await asyncio.to_thread(query_catalog, catalog=catalog, table=table, columns=columns, constraints=constraints, row_limit=row_limit)
            directory = dataset_directory(dataset_name, "vizier")
            records = await asyncio.to_thread(write_tables, tables, directory, output_format, selected_table=table)
            manifest = write_manifest(directory, {"command": self.name, "service": "vizier", "request": {"catalog": catalog, "table": table, "columns": columns, "constraints": constraints, "row_limit": row_limit, "output_format": output_format}, "files": records})
            return CommandResult(success=True, data=dataset_result(directory, manifest, table_count=len(records), files=records))
        except LookupError as exc:
            return CommandResult(success=False, error="NO_RESULTS", data={"message": str(exc)})
        except Exception as exc:
            return CommandResult(success=False, error="ASTROQUERY_ERROR", data={"type": type(exc).__name__, "message": str(exc)})
