"""Gaia and generic TAP/ADQL command."""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Type

from mcp_proxy_adapter.commands.base import Command, CommandResult

from science_assistant.commands.metadata import adql_metadata
from science_assistant.services.astroquery_gateway import query_adql
from science_assistant.services.storage import dataset_directory, dataset_result, write_manifest, write_tables


class AstroqueryAdqlCommand(Command):
    name: ClassVar[str] = "astroquery_adql"
    version: ClassVar[str] = "1.0.0"
    descr: ClassVar[str] = "Run Gaia or custom TAP ADQL and persist the result with exact query provenance."
    category: ClassVar[str] = "science-data"
    author: ClassVar[str] = "Vasiliy Zdanovskiy"
    email: ClassVar[str] = "vasilyvz@gmail.com"
    result_class: ClassVar[Type[CommandResult]] = CommandResult

    @classmethod
    def get_schema(cls) -> dict[str, Any]:
        return {"type": "object", "properties": {
            "service": {"type": "string", "enum": ["gaia", "custom"]},
            "query": {"type": "string", "minLength": 1},
            "tap_url": {"type": "string"},
            "output_format": {"type": "string", "enum": ["ecsv", "csv", "fits", "parquet"], "default": "ecsv"},
            "dataset_name": {"type": "string"},
        }, "required": ["service", "query"], "additionalProperties": False}

    @classmethod
    def metadata(cls) -> dict[str, Any]:
        return adql_metadata(cls)

    async def execute(self, **kwargs: Any) -> CommandResult:
        kwargs.pop("context", None)
        service = str(kwargs.get("service", "")).lower().strip()
        query = str(kwargs.get("query", "")).strip()
        tap_url = str(kwargs.get("tap_url", "")).strip() or None
        if not query:
            return CommandResult(success=False, error="VALIDATION_ERROR", data={"field": "query"})
        if service == "custom" and not tap_url:
            return CommandResult(success=False, error="VALIDATION_ERROR", data={"field": "tap_url"})
        output_format = str(kwargs.get("output_format", "ecsv"))
        dataset_name = str(kwargs.get("dataset_name", "")).strip() or None
        try:
            table = await asyncio.to_thread(query_adql, service=service, query=query, tap_url=tap_url)
            directory = dataset_directory(dataset_name, f"{service}-adql")
            records = await asyncio.to_thread(write_tables, table, directory, output_format, selected_table="result")
            manifest = write_manifest(directory, {"command": self.name, "service": service, "request": {"query": query, "tap_url": tap_url, "output_format": output_format}, "files": records})
            return CommandResult(success=True, data=dataset_result(directory, manifest, files=records))
        except Exception as exc:
            return CommandResult(success=False, error="ASTROQUERY_ERROR", data={"type": type(exc).__name__, "message": str(exc)})
