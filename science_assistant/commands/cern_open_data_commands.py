"""MCP commands for CERN Open Data acquisition into Code Analysis projects."""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from typing import Any, ClassVar

from mcp_proxy_adapter.commands.base import Command, CommandResult

from science_assistant.services.cern_open_data import (
    CernOpenDataError,
    StoredCernArtifact,
    download_record_file,
    fetch_cern_json,
    select_record_file,
    store_json_artifact,
)
from science_assistant.services.code_analysis_project_resolver import (
    CodeAnalysisProjectWriter,
    ProjectResolutionError,
    manifest_bytes,
)

_AUTHOR = "Vasiliy Zdanovskiy"
_EMAIL = "vasilyvz@gmail.com"
_UUID4_PATTERN = (
    "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-"
    "[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


def _project_property() -> dict[str, Any]:
    """Return the common project-id schema property.

    Returns:
        UUID4 schema for the target Code Analysis project.
    """
    return {
        "type": "string",
        "pattern": _UUID4_PATTERN,
        "description": (
            "Code Analysis project UUID. CERN artifacts are created through the "
            "official CAS client below the project's data/cern_open_data directory."
        ),
    }


def _metadata(
    command: type[Command],
    *,
    purpose: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Build complete AI-facing command metadata.

    Args:
        command: Concrete command class.
        purpose: Detailed command behavior.
        parameters: Parameter documentation mapping.

    Returns:
        Complete metadata document.
    """
    return {
        "name": command.name,
        "version": command.version,
        "description": command.descr,
        "category": command.category,
        "author": command.author,
        "email": command.email,
        "detailed_description": purpose,
        "parameters": parameters,
        "return_value": {
            "success": {
                "description": (
                    "Resolved CAS project and project-relative files created below data/."
                )
            },
            "error": {
                "description": "Validation, CERN acquisition, or CAS upload failure.",
                "code": "CERN_OPEN_DATA_ERROR",
            },
        },
        "usage_examples": [],
        "error_cases": {
            "VALIDATION_ERROR": {
                "description": "Input values do not satisfy the closed schema."
            },
            "PROJECT_RESOLUTION_ERROR": {
                "description": (
                    "The target project cannot be resolved or written through CAS."
                )
            },
            "CERN_OPEN_DATA_ERROR": {
                "description": (
                    "The CERN request, file selection, checksum, or staging failed."
                )
            },
        },
        "best_practices": [
            "Pass only project_id; never pass an absolute output path.",
            "Call search or record before starting a large file download.",
            "Keep every generated manifest beside its corresponding artifact.",
        ],
    }


def _error_result(exc: Exception) -> CommandResult:
    """Map implementation failures to stable command errors.

    Args:
        exc: Raised validation, CERN, or CAS exception.

    Returns:
        Failed command result.
    """
    if isinstance(exc, ProjectResolutionError):
        code = "PROJECT_RESOLUTION_ERROR"
    elif isinstance(exc, CernOpenDataError):
        code = "CERN_OPEN_DATA_ERROR"
    elif isinstance(exc, (TypeError, ValueError)):
        code = "VALIDATION_ERROR"
    else:
        code = "CERN_OPEN_DATA_ERROR"
    return CommandResult(
        success=False,
        error=code,
        data={"type": type(exc).__name__, "message": str(exc)},
    )


async def _upload_stored_artifact(
    writer: CodeAnalysisProjectWriter,
    artifact: StoredCernArtifact,
    *,
    category: str,
    target_name: str | None = None,
) -> dict[str, Any]:
    """Upload an artifact and corrected manifest through the CAS client.

    Args:
        writer: Active target-project writer.
        artifact: Local staged CERN artifact.
        category: Target subdirectory below data/cern_open_data.
        target_name: Optional target filename override.

    Returns:
        Artifact and manifest CAS upload records.
    """
    name = target_name or artifact.path.name
    target_path = f"data/cern_open_data/{category}/{name}"
    uploaded = await writer.upload_path(
        target_path,
        artifact.path,
        commit_message=f"Store CERN Open Data artifact {target_path}",
    )

    manifest_payload = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest_payload, dict):
        raise CernOpenDataError("staged provenance manifest must be a JSON object")
    if "artifact_path" in manifest_payload:
        manifest_payload["artifact_path"] = target_path
    if "stored_path" in manifest_payload:
        manifest_payload["stored_path"] = target_path
    manifest_payload["target_project"] = (
        writer.project.to_dict() if writer.project is not None else {}
    )
    manifest_payload["target_file"] = uploaded.to_dict()
    manifest_target = f"{target_path}.manifest.json"
    uploaded_manifest = await writer.upload_bytes(
        manifest_target,
        manifest_bytes(manifest_payload),
        commit_message=f"Store CERN Open Data manifest {manifest_target}",
    )
    return {
        "artifact": uploaded.to_dict(),
        "manifest": uploaded_manifest.to_dict(),
    }


class CernOpenDataSearchCommand(Command):
    """Search CERN records and store the exact JSON response in a CAS project."""

    name: ClassVar[str] = "cern_open_data_search"
    version: ClassVar[str] = "1.0.0"
    descr: ClassVar[str] = (
        "Search CERN Open Data records and create raw JSON plus provenance files "
        "inside a Code Analysis project."
    )
    category: ClassVar[str] = "science-data"
    author: ClassVar[str] = _AUTHOR
    email: ClassVar[str] = _EMAIL
    result_class: ClassVar[type[CommandResult]] = CommandResult

    @classmethod
    def get_schema(cls) -> dict[str, Any]:
        """Return the closed search schema.

        Returns:
            JSON Schema for project-targeted CERN record search.
        """
        return {
            "type": "object",
            "properties": {
                "project_id": _project_property(),
                "query": {"type": "string", "minLength": 1},
                "page": {"type": "integer", "minimum": 1, "default": 1},
                "size": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 20,
                },
                "timeout_seconds": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 600,
                    "default": 120,
                },
            },
            "required": ["project_id", "query"],
            "additionalProperties": False,
        }

    @classmethod
    def metadata(cls) -> dict[str, Any]:
        """Return search command metadata.

        Returns:
            Complete help metadata.
        """
        return _metadata(
            cls,
            purpose=(
                "Call CERN Open Data /api/records, preserve the exact JSON response "
                "and request provenance, and create both files through the official "
                "Code Analysis client below data/cern_open_data/search."
            ),
            parameters={
                "project_id": {"type": "string", "required": True},
                "query": {"type": "string", "required": True},
                "page": {"type": "integer", "required": False},
                "size": {"type": "integer", "required": False},
                "timeout_seconds": {"type": "number", "required": False},
            },
        )

    async def execute(self, **kwargs: Any) -> CommandResult:
        """Search records and upload the staged result.

        Args:
            **kwargs: Validated command parameters.

        Returns:
            Target project and CAS file identities.
        """
        kwargs.pop("context", None)
        try:
            project_id = str(kwargs["project_id"])
            query = str(kwargs["query"]).strip()
            if not query:
                raise ValueError("query must not be empty")
            page = int(kwargs.get("page", 1))
            size = int(kwargs.get("size", 20))
            timeout = float(kwargs.get("timeout_seconds", 120))
            payload, provenance = await fetch_cern_json(
                "/api/records",
                params={"q": query, "page": page, "size": size},
                timeout_seconds=timeout,
            )
            provenance.update(
                {
                    "project_id": project_id,
                    "query": query,
                    "page": page,
                    "size": size,
                }
            )
            with tempfile.TemporaryDirectory(
                prefix="science-assistant-cern-search-"
            ) as staging:
                artifact = store_json_artifact(
                    Path(staging),
                    category="search",
                    stem="records-search",
                    payload=payload,
                    provenance=provenance,
                )
                async with CodeAnalysisProjectWriter(
                    project_id,
                    comment=f"CERN Open Data search: {query}",
                ) as writer:
                    uploaded = await _upload_stored_artifact(
                        writer,
                        artifact,
                        category="search",
                    )
                    project = (
                        writer.project.to_dict() if writer.project is not None else {}
                    )
            return CommandResult(
                success=True,
                data={"project": project, "files": uploaded},
            )
        except Exception as exc:  # noqa: BLE001
            return _error_result(exc)


class CernOpenDataRecordCommand(Command):
    """Fetch one CERN record and store exact JSON in a CAS project."""

    name: ClassVar[str] = "cern_open_data_record"
    version: ClassVar[str] = "1.0.0"
    descr: ClassVar[str] = (
        "Fetch one CERN Open Data record and create raw JSON plus provenance files "
        "inside a Code Analysis project."
    )
    category: ClassVar[str] = "science-data"
    author: ClassVar[str] = _AUTHOR
    email: ClassVar[str] = _EMAIL
    result_class: ClassVar[type[CommandResult]] = CommandResult

    @classmethod
    def get_schema(cls) -> dict[str, Any]:
        """Return the closed record schema.

        Returns:
            JSON Schema for one CERN record.
        """
        return {
            "type": "object",
            "properties": {
                "project_id": _project_property(),
                "record_id": {"type": "integer", "minimum": 1},
                "timeout_seconds": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 600,
                    "default": 120,
                },
            },
            "required": ["project_id", "record_id"],
            "additionalProperties": False,
        }

    @classmethod
    def metadata(cls) -> dict[str, Any]:
        """Return record command metadata.

        Returns:
            Complete help metadata.
        """
        return _metadata(
            cls,
            purpose=(
                "Fetch /api/records/{record_id}, preserve the exact JSON response "
                "and request provenance, and create both files through the official "
                "Code Analysis client below data/cern_open_data/records."
            ),
            parameters={
                "project_id": {"type": "string", "required": True},
                "record_id": {"type": "integer", "required": True},
                "timeout_seconds": {"type": "number", "required": False},
            },
        )

    async def execute(self, **kwargs: Any) -> CommandResult:
        """Fetch one record and upload the staged result.

        Args:
            **kwargs: Validated command parameters.

        Returns:
            Target project and CAS file identities.
        """
        kwargs.pop("context", None)
        try:
            project_id = str(kwargs["project_id"])
            record_id = int(kwargs["record_id"])
            timeout = float(kwargs.get("timeout_seconds", 120))
            payload, provenance = await fetch_cern_json(
                f"/api/records/{record_id}",
                timeout_seconds=timeout,
            )
            provenance.update({"project_id": project_id, "record_id": record_id})
            with tempfile.TemporaryDirectory(
                prefix="science-assistant-cern-record-"
            ) as staging:
                artifact = store_json_artifact(
                    Path(staging),
                    category="records",
                    stem=f"record-{record_id}",
                    payload=payload,
                    provenance=provenance,
                )
                async with CodeAnalysisProjectWriter(
                    project_id,
                    comment=f"CERN Open Data record {record_id}",
                ) as writer:
                    uploaded = await _upload_stored_artifact(
                        writer,
                        artifact,
                        category="records",
                    )
                    project = (
                        writer.project.to_dict() if writer.project is not None else {}
                    )
            return CommandResult(
                success=True,
                data={
                    "project": project,
                    "record_id": record_id,
                    "files": uploaded,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return _error_result(exc)


class CernOpenDataDownloadCommand(Command):
    """Download one CERN record file and create it in a CAS project."""

    name: ClassVar[str] = "cern_open_data_download"
    version: ClassVar[str] = "1.0.0"
    descr: ClassVar[str] = (
        "Download one file selected from a CERN record and create the file, record "
        "metadata, and provenance manifests inside a Code Analysis project."
    )
    category: ClassVar[str] = "science-data"
    author: ClassVar[str] = _AUTHOR
    email: ClassVar[str] = _EMAIL
    result_class: ClassVar[type[CommandResult]] = CommandResult
    use_queue: ClassVar[bool] = True

    @classmethod
    def get_schema(cls) -> dict[str, Any]:
        """Return the closed download schema.

        Returns:
            JSON Schema for record-file selection and transfer controls.
        """
        return {
            "type": "object",
            "x-use-queue": True,
            "properties": {
                "project_id": _project_property(),
                "record_id": {"type": "integer", "minimum": 1},
                "file_name": {"type": "string", "minLength": 1},
                "file_index": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                },
                "output_name": {"type": "string", "minLength": 1},
                "timeout_seconds": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 86400,
                    "default": 3600,
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 53687091200,
                },
                "resume": {"type": "boolean", "default": True},
            },
            "required": ["project_id", "record_id"],
            "additionalProperties": False,
        }

    @classmethod
    def metadata(cls) -> dict[str, Any]:
        """Return download command metadata.

        Returns:
            Complete help metadata.
        """
        return _metadata(
            cls,
            purpose=(
                "Fetch the source record JSON, select one file by exact name or "
                "zero-based index, stream it into local staging with optional HTTP "
                "Range resume, verify the CERN checksum, then create all resulting "
                "files through the official CAS client below data/cern_open_data."
            ),
            parameters={
                "project_id": {"type": "string", "required": True},
                "record_id": {"type": "integer", "required": True},
                "file_name": {"type": "string", "required": False},
                "file_index": {"type": "integer", "required": False},
                "output_name": {"type": "string", "required": False},
                "timeout_seconds": {"type": "number", "required": False},
                "max_bytes": {"type": "integer", "required": False},
                "resume": {"type": "boolean", "required": False},
            },
        )

    async def execute(self, **kwargs: Any) -> CommandResult:
        """Download a record file and upload all resulting artifacts.

        Args:
            **kwargs: Validated command parameters.

        Returns:
            Target project, selected file metadata, and CAS file identities.
        """
        kwargs.pop("context", None)
        try:
            project_id = str(kwargs["project_id"])
            record_id = int(kwargs["record_id"])
            file_name_raw = kwargs.get("file_name")
            file_name = (
                str(file_name_raw).strip() if file_name_raw is not None else None
            )
            file_index = int(kwargs.get("file_index", 0))
            output_name_raw = kwargs.get("output_name")
            output_name = (
                str(output_name_raw).strip() if output_name_raw is not None else None
            )
            timeout = float(kwargs.get("timeout_seconds", 3600))
            max_bytes = int(kwargs.get("max_bytes", 53687091200))
            resume = bool(kwargs.get("resume", True))

            record_payload, provenance = await fetch_cern_json(
                f"/api/records/{record_id}",
                timeout_seconds=min(timeout, 600),
            )
            provenance.update({"project_id": project_id, "record_id": record_id})
            selected = select_record_file(
                record_payload,
                file_name=file_name,
                file_index=file_index,
            )
            with tempfile.TemporaryDirectory(
                prefix="science-assistant-cern-download-"
            ) as staging:
                staging_root = Path(staging)
                record_artifact = store_json_artifact(
                    staging_root,
                    category="records",
                    stem=f"record-{record_id}",
                    payload=record_payload,
                    provenance=provenance,
                )
                downloaded = await download_record_file(
                    staging_root,
                    file_metadata=selected,
                    output_name=output_name,
                    timeout_seconds=timeout,
                    max_bytes=max_bytes,
                    resume=resume,
                )
                binary_name = (
                    f"record-{record_id}-{downloaded.sha256[:12]}-"
                    f"{uuid.uuid4().hex[:12]}-{downloaded.path.name}"
                )
                async with CodeAnalysisProjectWriter(
                    project_id,
                    comment=f"CERN Open Data record {record_id} file download",
                ) as writer:
                    record_files = await _upload_stored_artifact(
                        writer,
                        record_artifact,
                        category="records",
                    )
                    binary_files = await _upload_stored_artifact(
                        writer,
                        downloaded,
                        category="files",
                        target_name=binary_name,
                    )
                    project = (
                        writer.project.to_dict() if writer.project is not None else {}
                    )
            return CommandResult(
                success=True,
                data={
                    "project": project,
                    "record_id": record_id,
                    "selected_file": dict(selected),
                    "record_files": record_files,
                    "download_files": binary_files,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return _error_result(exc)


COMMAND_TYPES = (
    CernOpenDataSearchCommand,
    CernOpenDataRecordCommand,
    CernOpenDataDownloadCommand,
)

__all__ = [
    "COMMAND_TYPES",
    "CernOpenDataDownloadCommand",
    "CernOpenDataRecordCommand",
    "CernOpenDataSearchCommand",
]
