"""Store scientific artifacts in Code Analysis projects through the official client."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import Any, Self

from code_analysis_client import CodeAnalysisAsyncClient


class ProjectResolutionError(RuntimeError):
    """A Code Analysis project cannot be resolved or written safely."""


@dataclass(frozen=True, slots=True)
class CodeAnalysisProject:
    """Resolved Code Analysis project identity.

    Attributes:
        project_id: Canonical UUID4 project identifier.
        name: Project presentation name.
        root_path: Project-relative root path reported by CAS.
        watch_dir_id: Owning observed-directory UUID.
    """

    project_id: str
    name: str
    root_path: str
    watch_dir_id: str

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-compatible project identity.

        Returns:
            Resolved project identity fields.
        """
        return {
            "project_id": self.project_id,
            "name": self.name,
            "root_path": self.root_path,
            "watch_dir_id": self.watch_dir_id,
        }


@dataclass(frozen=True, slots=True)
class UploadedProjectFile:
    """One file created in a Code Analysis project.

    Attributes:
        file_id: Indexed CAS file UUID.
        file_path: Project-relative path created through CAS.
        size_bytes: Exact uploaded byte count.
        sha256: SHA-256 digest of uploaded bytes.
    """

    file_id: str
    file_path: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible upload result.

        Returns:
            File identity, path, size, and checksum.
        """
        return {
            "file_id": self.file_id,
            "file_path": self.file_path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


def _require_uuid4(value: str, field_name: str) -> str:
    """Normalize and validate one UUID4 string.

    Args:
        value: Candidate UUID text.
        field_name: Field name used in validation messages.

    Returns:
        Canonical UUID4 string.
    """
    normalized = str(value or "").strip()
    try:
        parsed = uuid.UUID(normalized)
    except ValueError as exc:
        raise ProjectResolutionError(f"{field_name} must be a UUID4") from exc
    if parsed.version != 4:
        raise ProjectResolutionError(f"{field_name} must be a UUID4")
    return str(parsed)


def _unwrap_payload(payload: Any, command: str) -> Mapping[str, Any]:
    """Normalize direct and adapter-wrapped command results.

    Args:
        payload: Raw Code Analysis client response.
        command: Command name used in validation messages.

    Returns:
        Mapping contained in the command data envelope.
    """
    if not isinstance(payload, Mapping):
        raise ProjectResolutionError(
            f"{command} returned {type(payload).__name__}, expected mapping"
        )
    if payload.get("success") is False:
        raise ProjectResolutionError(
            f"{command} failed: {payload.get('error') or payload.get('message')}"
        )
    data = payload.get("data", payload)
    if not isinstance(data, Mapping):
        raise ProjectResolutionError(f"{command} returned invalid data envelope")
    return data


def _project_rows(payload: Any) -> list[Mapping[str, Any]]:
    """Extract project rows from a list_projects response.

    Args:
        payload: Raw list_projects response.

    Returns:
        Project mappings returned by CAS.
    """
    data = _unwrap_payload(payload, "list_projects")
    raw_rows = data.get("projects", data.get("items"))
    if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
        raise ProjectResolutionError("list_projects returned no project array")
    rows = [row for row in raw_rows if isinstance(row, Mapping)]
    if len(rows) != len(raw_rows):
        raise ProjectResolutionError("list_projects contains non-object rows")
    return rows


def _safe_project_path(value: str) -> str:
    """Validate a project-relative path below the data directory.

    Args:
        value: Candidate project-relative file path.

    Returns:
        Canonical POSIX path starting with ``data/``.
    """
    raw = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if (
        not raw
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or len(path.parts) < 2
        or path.parts[0] != "data"
    ):
        raise ProjectResolutionError(
            "file_path must be a safe project-relative path below data/"
        )
    return path.as_posix()


def _client_from_environment() -> CodeAnalysisAsyncClient:
    """Build the official Code Analysis client from environment settings.

    Returns:
        Configured asynchronous Code Analysis client.
    """
    timeout = float(os.getenv("SCIENCE_ASSISTANT_CODE_ANALYSIS_TIMEOUT", "120"))
    config_path = os.getenv("SCIENCE_ASSISTANT_CODE_ANALYSIS_CONFIG", "").strip()
    if config_path:
        return CodeAnalysisAsyncClient.from_server_config_path(
            config_path,
            timeout=timeout,
            check_hostname=False,
        )

    kwargs: dict[str, Any] = {
        "protocol": os.getenv(
            "SCIENCE_ASSISTANT_CODE_ANALYSIS_PROTOCOL",
            "https",
        ),
        "host": os.getenv("SCIENCE_ASSISTANT_CODE_ANALYSIS_HOST", "casmgr"),
        "port": int(os.getenv("SCIENCE_ASSISTANT_CODE_ANALYSIS_PORT", "15010")),
        "timeout": timeout,
        "check_hostname": False,
    }
    for argument, variable in (
        ("cert", "SCIENCE_ASSISTANT_CODE_ANALYSIS_CERT"),
        ("key", "SCIENCE_ASSISTANT_CODE_ANALYSIS_KEY"),
        ("ca", "SCIENCE_ASSISTANT_CODE_ANALYSIS_CA"),
    ):
        value = os.getenv(variable, "").strip()
        if value:
            kwargs[argument] = value
    return CodeAnalysisAsyncClient(**kwargs)


async def resolve_project(
    client: CodeAnalysisAsyncClient,
    project_id: str,
) -> CodeAnalysisProject:
    """Resolve one project through the official CAS client.

    Args:
        client: Connected official Code Analysis client.
        project_id: UUID4 project identifier.

    Returns:
        Resolved project identity.
    """
    normalized = _require_uuid4(project_id, "project_id")
    block_position = 1
    while True:
        payload = await client.commands.list_projects(
            page_size=200,
            block_position=block_position,
        )
        data = _unwrap_payload(payload, "list_projects")
        matches = [
            row
            for row in _project_rows(payload)
            if str(row.get("id") or row.get("project_id") or "") == normalized
        ]
        if len(matches) > 1:
            raise ProjectResolutionError(
                f"project_id {normalized} resolved to {len(matches)} projects"
            )
        if matches:
            row = matches[0]
            if bool(row.get("deleted")):
                raise ProjectResolutionError(f"project_id {normalized} is deleted")
            return CodeAnalysisProject(
                project_id=normalized,
                name=str(row.get("name") or ""),
                root_path=str(row.get("root_path") or ""),
                watch_dir_id=str(row.get("watch_dir_id") or ""),
            )
        if not bool(data.get("has_more")):
            break
        block_position += 1
    raise ProjectResolutionError(f"project_id {normalized} was not found")


def _write_temporary_payload(payload: bytes, suffix: str) -> Path:
    """Write bytes to a process-writable temporary file.

    Args:
        payload: Exact bytes to stage for the adapter upload.
        suffix: Optional target suffix retained for diagnostics.

    Returns:
        Path to the completed temporary file.
    """
    descriptor, name = tempfile.mkstemp(
        prefix="science-assistant-cas-",
        suffix=suffix,
    )
    path = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _sha256_path(path: Path) -> str:
    """Return the SHA-256 digest for one local staging file.

    Args:
        path: Existing local file.

    Returns:
        Lowercase SHA-256 hexadecimal digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class CodeAnalysisProjectWriter:
    """Create project files through the official CAS file-session client.

    Attributes:
        project_id: Target Code Analysis project UUID.
        comment: Session audit comment.
    """

    def __init__(self, project_id: str, *, comment: str) -> None:
        """Initialize a lazy project writer.

        Args:
            project_id: Target Code Analysis project UUID.
            comment: Session audit comment.

        Returns:
            None.
        """
        self.project_id = _require_uuid4(project_id, "project_id")
        self.comment = str(comment or "").strip() or "Science Assistant artifact upload"
        self.client: CodeAnalysisAsyncClient | None = None
        self.session_id: str | None = None
        self.project: CodeAnalysisProject | None = None

    async def __aenter__(self) -> Self:
        """Connect, resolve the project, and create one CAS session.

        Returns:
            Ready project writer.
        """
        client = _client_from_environment()
        try:
            project = await resolve_project(client, self.project_id)
            session_id = await client.file_sessions.create_session(self.comment)
        except Exception:
            await client.close()
            raise
        self.client = client
        self.project = project
        self.session_id = session_id
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Delete the CAS session and close the client.

        Args:
            exc_type: Active exception type, when any.
            exc: Active exception instance, when any.
            traceback: Active traceback, when any.

        Returns:
            None.
        """
        del exc_type, exc, traceback
        if self.client is None:
            return
        try:
            if self.session_id:
                await self.client.file_sessions.delete_session(
                    self.session_id,
                    force=True,
                )
        finally:
            await self.client.close()
            self.client = None
            self.session_id = None

    def _ready(self) -> tuple[CodeAnalysisAsyncClient, str, CodeAnalysisProject]:
        """Return initialized writer state.

        Returns:
            Client, session id, and resolved project.
        """
        if self.client is None or self.session_id is None or self.project is None:
            raise ProjectResolutionError("CodeAnalysisProjectWriter is not active")
        return self.client, self.session_id, self.project

    async def upload_bytes(
        self,
        file_path: str,
        payload: bytes,
        *,
        commit_message: str | None = None,
    ) -> UploadedProjectFile:
        """Create one new project file from bytes.

        Args:
            file_path: Project-relative target below ``data/``.
            payload: Exact file bytes.
            commit_message: Optional CAS save audit message.

        Returns:
            Created CAS file identity and checksum.
        """
        self._ready()
        target = _safe_project_path(file_path)
        temporary = await asyncio.to_thread(
            _write_temporary_payload,
            bytes(payload),
            Path(target).suffix,
        )
        try:
            return await self.upload_path(
                target,
                temporary,
                commit_message=commit_message,
            )
        finally:
            await asyncio.to_thread(temporary.unlink, missing_ok=True)

    async def upload_path(
        self,
        file_path: str,
        source_path: str | Path,
        *,
        commit_message: str | None = None,
    ) -> UploadedProjectFile:
        """Create one new project file by streaming a local staging file.

        Args:
            file_path: Project-relative target below ``data/``.
            source_path: Existing local staging file.
            commit_message: Optional CAS save audit message.

        Returns:
            Created CAS file identity and checksum.
        """
        client, session_id, project = self._ready()
        target = _safe_project_path(file_path)
        source = Path(source_path)
        if not source.is_file():
            raise ProjectResolutionError(f"staging file is unavailable: {source}")

        digest = await asyncio.to_thread(_sha256_path, source)

        receipt = await client.rpc.upload_file(
            str(source),
            filename=Path(target).name,
            compression="identity",
        )
        if not getattr(receipt, "completed", False):
            raise ProjectResolutionError("CAS adapter upload did not complete")
        saved_raw = await client.call_validated(
            "project_file_transfer_upload_save",
            {
                "session_id": session_id,
                "transfer_id": str(receipt.transfer_id),
                "project_id": project.project_id,
                "file_path": target,
                "unlock_after_write": True,
                "dry_run": False,
                "backup": False,
                "diff": False,
                "commit_message": commit_message
                or f"Science Assistant created {target}",
                "lock_mode": "full",
            },
        )
        saved = _unwrap_payload(
            saved_raw,
            "project_file_transfer_upload_save",
        )
        file_id = str(saved.get("file_id") or "").strip()
        if not file_id:
            raise ProjectResolutionError(
                "project_file_transfer_upload_save returned no file_id"
            )
        return UploadedProjectFile(
            file_id=file_id,
            file_path=target,
            size_bytes=source.stat().st_size,
            sha256=digest,
        )


def manifest_bytes(payload: Mapping[str, Any]) -> bytes:
    """Serialize a project artifact manifest deterministically.

    Args:
        payload: Manifest mapping.

    Returns:
        UTF-8 JSON bytes ending with a newline.
    """
    return (
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


__all__ = [
    "CodeAnalysisProject",
    "CodeAnalysisProjectWriter",
    "ProjectResolutionError",
    "UploadedProjectFile",
    "manifest_bytes",
    "resolve_project",
]
