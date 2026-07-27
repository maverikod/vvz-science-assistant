"""Cross-service astronomical object and cone query."""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Type

from mcp_proxy_adapter.commands.base import Command, CommandResult

from science_assistant.commands.metadata import object_metadata
from science_assistant.services.astroquery_gateway import query_object
from science_assistant.services.storage import dataset_directory, dataset_result, write_manifest, write_tables


class AstroqueryObjectCommand(Command):
    name: ClassVar[str] = "astroquery_object"
    version: ClassVar[str] = "1.0.0"
    descr: ClassVar[str] = "Query objects or cone regions through SIMBAD, NED, VizieR, HEASARC, or IRSA."
    category: ClassVar[str] = "science-data"
    author: ClassVar[str] = "Vasiliy Zdanovskiy"
    email: ClassVar[str] = "vasilyvz@gmail.com"
    result_class: ClassVar[Type[CommandResult]] = CommandResult

    @classmethod
    def get_schema(cls) -> dict[str, Any]:
        return {"type": "object", "properties": {
            "service": {"type": "string", "enum": ["simbad", "ned", "vizier", "heasarc", "irsa"]},
            "target": {"type": "string"},
            "ra_deg": {"type": "number", "minimum": 0, "maximum": 360},
            "dec_deg": {"type": "number", "minimum": -90, "maximum": 90},
            "radius_arcmin": {"type": "number", "exclusiveMinimum": 0},
            "catalog": {"type": "string"},
            "columns": {"type": "array", "items": {"type": "string"}},
            "row_limit": {"type": "integer", "minimum": -1, "default": -1},
            "output_format": {"type": "string", "enum": ["ecsv", "csv", "fits", "parquet"], "default": "ecsv"},
            "dataset_name": {"type": "string"},
        }, "required": ["service"], "additionalProperties": False}

    @classmethod
    def metadata(cls) -> dict[str, Any]:
        return object_metadata(cls)

    async def execute(self, **kwargs: Any) -> CommandResult:
        kwargs.pop("context", None)
        service = str(kwargs.get("service", "")).lower().strip()
        target = str(kwargs.get("target", "")).strip() or None
        ra_deg = kwargs.get("ra_deg")
        dec_deg = kwargs.get("dec_deg")
        if not target and (ra_deg is None or dec_deg is None):
            return CommandResult(success=False, error="VALIDATION_ERROR", data={"message": "Provide target or both ra_deg and dec_deg"})
        if (ra_deg is None) != (dec_deg is None):
            return CommandResult(success=False, error="VALIDATION_ERROR", data={"message": "ra_deg and dec_deg must be supplied together"})
        catalog = str(kwargs.get("catalog", "")).strip() or None
        if service in {"heasarc", "irsa"} and not catalog:
            return CommandResult(success=False, error="CATALOG_REQUIRED", data={"service": service})
        radius = kwargs.get("radius_arcmin")
        columns = kwargs.get("columns") or None
        row_limit = int(kwargs.get("row_limit", -1))
        output_format = str(kwargs.get("output_format", "ecsv"))
        dataset_name = str(kwargs.get("dataset_name", "")).strip() or None
        try:
            tables = await asyncio.to_thread(query_object, service=service, target=target, ra_deg=ra_deg, dec_deg=dec_deg, radius_arcmin=radius, catalog=catalog, columns=columns, row_limit=row_limit)
            if tables is None or (hasattr(tables, "__len__") and len(tables) == 0):
                return CommandResult(success=False, error="NO_RESULTS", data={"service": service, "target": target})
            directory = dataset_directory(dataset_name, service)
            records = await asyncio.to_thread(write_tables, tables, directory, output_format)
            manifest = write_manifest(directory, {"command": self.name, "service": service, "request": {"target": target, "ra_deg": ra_deg, "dec_deg": dec_deg, "radius_arcmin": radius, "catalog": catalog, "columns": columns, "row_limit": row_limit, "output_format": output_format}, "files": records})
            return CommandResult(success=True, data=dataset_result(directory, manifest, table_count=len(records), files=records))
        except Exception as exc:
            return CommandResult(success=False, error="ASTROQUERY_ERROR", data={"type": type(exc).__name__, "message": str(exc)})
