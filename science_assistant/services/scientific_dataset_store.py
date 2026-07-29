"""Atomic persistence adapter for normalized scientific datasets."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Final

from astropy.table import Table  # type: ignore[import-not-found]
from science_assistant.progress import (  # type: ignore[import-not-found]
    DownloadProgress,
    OperationState,
    ProgressTracker,
    ResumeSupport,
    TransferCapabilities,
)
from science_assistant.provenance import (  # type: ignore[import-not-found]
    ProvenanceManifest,
)
from science_assistant.provider_contract import (  # type: ignore[import-not-found]
    NormalizedDataset,
    ProviderContext,
)
from science_assistant.services import (  # type: ignore[import-not-found]
    file_store,
    mcp_transfer,
    storage,
)

_FORMAT_EXTENSIONS: Final[dict[str, str]] = {
    "ECSV": "ecsv",
    "CSV": "csv",
    "FITS": "fits",
    "PARQUET": "parquet",
}
_CHECKPOINT_SUFFIX: Final[str] = ".checkpoint.json"
QueueSubmitter = Callable[[Callable[[], "StoredDataset"]], Awaitable["StoredDataset"]]


@dataclass(frozen=True, slots=True)
class SourceValidator:
    """Immutable source identity used to authorize local resume.

    Attributes:
        etag: Optional source ETag.
        last_modified: Optional source Last-Modified value.
        checksum: Optional source checksum or digest.
        identity: Optional provider-specific immutable identity.
    """

    etag: str | None = None
    last_modified: str | None = None
    checksum: str | None = None
    identity: str | None = None

    def __post_init__(self) -> None:
        """Normalize values and require at least one validator.

        Returns:
            None.
        """
        values: list[str | None] = []
        for field_name in ("etag", "last_modified", "checksum", "identity"):
            raw = getattr(self, field_name)
            value = str(raw).strip() if raw is not None else None
            normalized = value or None
            object.__setattr__(self, field_name, normalized)
            values.append(normalized)
        if not any(values):
            raise ValueError("source validator must contain at least one value")

    def to_dict(self) -> dict[str, str | None]:
        """Build a JSON-compatible validator document.

        Returns:
            Source validator fields.
        """
        return {
            "etag": self.etag,
            "last_modified": self.last_modified,
            "checksum": self.checksum,
            "identity": self.identity,
        }


@dataclass(frozen=True, slots=True)
class TransferCheckpoint:
    """Durable local transfer checkpoint bound to a source validator.

    Attributes:
        operation_id: Progress operation UUID.
        relative_path: Final safe data-root-relative path.
        transfer_id: Existing MCP upload transfer identifier.
        expected_size: Exact serialized byte size.
        expected_sha256: Exact serialized-file SHA-256.
        offset: Next strict local byte offset.
        block_size: Negotiated transfer block size.
        output_format: ECSV, CSV, FITS, or PARQUET.
        source_validator: Immutable source validator, if resume is permitted.
        status: partial, completed, or failed.
        updated_at: Timezone-aware checkpoint update time.
    """

    operation_id: str
    relative_path: str
    transfer_id: str
    expected_size: int
    expected_sha256: str
    offset: int
    block_size: int
    output_format: str
    source_validator: SourceValidator | None
    status: str
    updated_at: datetime

    def __post_init__(self) -> None:
        """Validate checkpoint identity and monotonic numeric values.

        Returns:
            None.
        """
        if not self.operation_id.strip():
            raise ValueError("operation_id must not be empty")
        if not self.transfer_id.strip():
            raise ValueError("transfer_id must not be empty")
        if self.expected_size < 0:
            raise ValueError("expected_size must be >= 0")
        if self.offset < 0 or self.offset > self.expected_size:
            raise ValueError("checkpoint offset is outside expected_size")
        if self.block_size < 1:
            raise ValueError("block_size must be positive")
        digest = self.expected_sha256.strip().lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("expected_sha256 must be a lowercase SHA-256 digest")
        object.__setattr__(self, "expected_sha256", digest)
        object.__setattr__(
            self, "relative_path", _safe_relative_path(self.relative_path)
        )
        output_format = self.output_format.strip().upper()
        if output_format not in _FORMAT_EXTENSIONS:
            raise ValueError(f"unsupported output format: {self.output_format!r}")
        object.__setattr__(self, "output_format", output_format)
        if self.status not in {"partial", "completed", "failed"}:
            raise ValueError("checkpoint status must be partial, completed, or failed")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("updated_at must be timezone-aware")
        object.__setattr__(self, "updated_at", self.updated_at.astimezone(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Build a JSON-compatible checkpoint document.

        Returns:
            Durable checkpoint fields.
        """
        return {
            "schema_version": "1.0",
            "operation_id": self.operation_id,
            "relative_path": self.relative_path,
            "transfer_id": self.transfer_id,
            "expected_size": self.expected_size,
            "expected_sha256": self.expected_sha256,
            "offset": self.offset,
            "block_size": self.block_size,
            "output_format": self.output_format,
            "source_validator": (
                self.source_validator.to_dict()
                if self.source_validator is not None
                else None
            ),
            "status": self.status,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TransferCheckpoint:
        """Parse one checkpoint JSON object.

        Args:
            value: Checkpoint mapping loaded from disk.

        Returns:
            Validated immutable checkpoint.
        """
        validator_value = value.get("source_validator")
        validator = (
            SourceValidator(**dict(validator_value))
            if isinstance(validator_value, Mapping)
            else None
        )
        updated_at = datetime.fromisoformat(str(value["updated_at"]))
        return cls(
            operation_id=str(value["operation_id"]),
            relative_path=str(value["relative_path"]),
            transfer_id=str(value["transfer_id"]),
            expected_size=int(value["expected_size"]),
            expected_sha256=str(value["expected_sha256"]),
            offset=int(value["offset"]),
            block_size=int(value["block_size"]),
            output_format=str(value["output_format"]),
            source_validator=validator,
            status=str(value["status"]),
            updated_at=updated_at,
        )


@dataclass(frozen=True, slots=True)
class StoredDataset:
    """Immutable result of one persisted normalized dataset.

    Attributes:
        file_id: Content-addressed file identifier.
        relative_path: Safe data-root-relative dataset path.
        server_path: Resolved server dataset path.
        size_bytes: Exact serialized file size.
        sha256: Exact serialized-file SHA-256.
        manifest_relative_path: Data-root-relative provenance manifest path.
        transfer_id: MCP transfer identifier used for atomic publication.
        resumed: Whether a matching checkpoint was resumed.
        restart_reason: Explicit reason a prior partial transfer was discarded.
        progress: Terminal immutable progress snapshot.
    """

    file_id: str
    relative_path: str
    server_path: str
    size_bytes: int
    sha256: str
    manifest_relative_path: str
    transfer_id: str
    resumed: bool
    restart_reason: str | None
    progress: DownloadProgress

    def to_dict(self) -> dict[str, Any]:
        """Build a JSON-compatible stored dataset envelope.

        Returns:
            File identity, paths, transfer state, and progress.
        """
        return {
            "file_id": self.file_id,
            "relative_path": self.relative_path,
            "server_path": self.server_path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "manifest_relative_path": self.manifest_relative_path,
            "transfer_id": self.transfer_id,
            "resumed": self.resumed,
            "restart_reason": self.restart_reason,
            "progress": self.progress.to_dict(),
        }


class ScientificDatasetStore:
    """Coordinate serialization and existing storage/transfer components.

    Attributes:
        _progress: Shared operation progress tracker.
        _enqueue: Optional application queue submitter.
    """

    def __init__(
        self,
        progress_tracker: ProgressTracker,
        *,
        enqueue: QueueSubmitter | None = None,
    ) -> None:
        """Initialize the adapter without creating storage roots.

        Args:
            progress_tracker: Shared provider operation tracker.
            enqueue: Optional application queue submission callback.

        Returns:
            None.
        """
        if not isinstance(progress_tracker, ProgressTracker):
            raise TypeError("progress_tracker must be a ProgressTracker")
        self._progress = progress_tracker
        self._enqueue = enqueue

    async def store(
        self,
        dataset: NormalizedDataset,
        context: ProviderContext,
        provenance: ProvenanceManifest,
        *,
        relative_path: str,
        source_validator: SourceValidator | None = None,
        resume: bool = False,
        overwrite: bool = False,
    ) -> StoredDataset:
        """Queue serialization and atomic persistence outside the event loop.

        Args:
            dataset: Validated normalized dataset.
            context: Provider operation context sharing this store's tracker.
            provenance: Base immutable provenance manifest.
            relative_path: Final safe path below the shared data root.
            source_validator: Immutable source validator required for resume.
            resume: Whether a matching local checkpoint may be resumed.
            overwrite: Whether an existing final target may be replaced.

        Returns:
            Stored dataset identity and terminal progress.
        """

        def job() -> StoredDataset:
            return self.store_sync(
                dataset,
                context,
                provenance,
                relative_path=relative_path,
                source_validator=source_validator,
                resume=resume,
                overwrite=overwrite,
            )

        if self._enqueue is not None:
            return await self._enqueue(job)
        return await asyncio.to_thread(job)

    def store_sync(
        self,
        dataset: NormalizedDataset,
        context: ProviderContext,
        provenance: ProvenanceManifest,
        *,
        relative_path: str,
        source_validator: SourceValidator | None = None,
        resume: bool = False,
        overwrite: bool = False,
    ) -> StoredDataset:
        """Serialize and persist one dataset in a worker or queue process.

        Args:
            dataset: Validated normalized dataset.
            context: Provider operation context.
            provenance: Base immutable provenance manifest.
            relative_path: Final safe data-root-relative path.
            source_validator: Immutable source validator required for resume.
            resume: Whether to resume a matching checkpoint.
            overwrite: Whether an existing final target may be replaced.

        Returns:
            Stored dataset identity and terminal progress.
        """
        if not isinstance(dataset, NormalizedDataset):
            raise TypeError("dataset must be a NormalizedDataset")
        if not isinstance(context, ProviderContext):
            raise TypeError("context must be a ProviderContext")
        if not isinstance(provenance, ProvenanceManifest):
            raise TypeError("provenance must be a ProvenanceManifest")
        output_format = dataset.output_format.upper()
        target_relative = _normalized_target_path(relative_path, output_format)
        capabilities = context.command_spec.transfer_capabilities
        block_size = _resolve_block_size(context, capabilities)
        if resume and capabilities.resume_support is ResumeSupport.UNSUPPORTED:
            raise ValueError("resume is unsupported by transfer capabilities")
        if resume and source_validator is None:
            raise ValueError("resume requires an immutable source validator")
        checkpoint_path = _checkpoint_path(target_relative)
        checkpoint: TransferCheckpoint | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="science-dataset-") as directory:
                serialized = (
                    Path(directory) / f"dataset.{_FORMAT_EXTENSIONS[output_format]}"
                )
                self._mark_running(context, message="serializing normalized dataset")
                _serialize_dataset(dataset, serialized)
                size_bytes = serialized.stat().st_size
                digest = storage.sha256_file(serialized)
                base = self._progress.get(context.operation_id)
                base_bytes = base.bytes_received
                base_offset = base.current_offset
                total_bytes = max(base.total_bytes or 0, base_bytes + size_bytes)
                self._progress.update(
                    context.operation_id,
                    total_bytes=total_bytes,
                    state=OperationState.RUNNING,
                    message="serialized dataset ready for atomic transfer",
                )
                checkpoint, resumed, restart_reason = self._prepare_transfer(
                    context=context,
                    checkpoint_path=checkpoint_path,
                    relative_path=target_relative,
                    expected_size=size_bytes,
                    expected_sha256=digest,
                    block_size=block_size,
                    output_format=output_format,
                    source_validator=source_validator,
                    resume=resume,
                    overwrite=overwrite,
                )
                checkpoint = self._upload_serialized(
                    context=context,
                    serialized=serialized,
                    checkpoint=checkpoint,
                    checkpoint_path=checkpoint_path,
                    base_bytes=base_bytes,
                    base_offset=base_offset,
                    total_bytes=total_bytes,
                )
                completed = mcp_transfer.upload_complete(
                    transfer_id=checkpoint.transfer_id,
                )
                file_record = completed["file"]
                current = self._progress.get(context.operation_id)
                manifest = _completed_manifest(
                    provenance=provenance,
                    dataset=dataset,
                    context=context,
                    progress=current,
                    digest=digest,
                    size_bytes=size_bytes,
                    relative_path=target_relative,
                    transfer_id=checkpoint.transfer_id,
                    resumed=resumed,
                    source_validator=source_validator,
                )
                manifest_relative = f"{target_relative}.manifest.json"
                _atomic_text(
                    storage.resolve_data_path(
                        manifest_relative,
                        create_parent=True,
                    ),
                    manifest.to_json(indent=2) + "\n",
                )
                checkpoint_path.unlink(missing_ok=True)
                terminal = self._progress.complete(
                    context.operation_id,
                    message="dataset stored and provenance verified",
                )
                return StoredDataset(
                    file_id=f"sha256:{digest}",
                    relative_path=str(file_record["relative_path"]),
                    server_path=str(file_record["server_path"]),
                    size_bytes=int(file_record["size_bytes"]),
                    sha256=str(file_record["sha256"]),
                    manifest_relative_path=manifest_relative,
                    transfer_id=checkpoint.transfer_id,
                    resumed=resumed,
                    restart_reason=restart_reason,
                    progress=terminal,
                )
        except Exception as exc:
            self._handle_failure(
                context=context,
                checkpoint=checkpoint,
                checkpoint_path=checkpoint_path,
                capabilities=capabilities,
                source_validator=source_validator,
                error=exc,
            )
            raise

    def receive_file_part(
        self,
        context: ProviderContext,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Delegate a multipart FileStore write and update common progress.

        Args:
            context: Provider operation context.
            **kwargs: Existing file_store.receive_file_part parameters.

        Returns:
            Unmodified FileStore result.
        """
        result = file_store.receive_file_part(**kwargs)
        fragment = str(kwargs.get("data_base64_part", ""))
        approximate_bytes = len(fragment) * 3 // 4
        current = self._progress.get(context.operation_id)
        self._progress.update(
            context.operation_id,
            bytes_received=current.bytes_received + approximate_bytes,
            state=OperationState.RUNNING,
            message="FileStore multipart fragment persisted",
        )
        return result

    def _prepare_transfer(
        self,
        *,
        context: ProviderContext,
        checkpoint_path: Path,
        relative_path: str,
        expected_size: int,
        expected_sha256: str,
        block_size: int,
        output_format: str,
        source_validator: SourceValidator | None,
        resume: bool,
        overwrite: bool,
    ) -> tuple[TransferCheckpoint, bool, str | None]:
        """Resume a matching checkpoint or create a new MCP upload.

        Returns:
            Checkpoint, resumed flag, and explicit restart reason.
        """
        restart_reason: str | None = None
        if resume and checkpoint_path.is_file():
            existing = _load_checkpoint(checkpoint_path)
            mismatch = _checkpoint_mismatch(
                existing,
                operation_id=context.operation_id,
                relative_path=relative_path,
                expected_size=expected_size,
                expected_sha256=expected_sha256,
                block_size=block_size,
                output_format=output_format,
                source_validator=source_validator,
            )
            if mismatch is None:
                state = mcp_transfer.upload_status(transfer_id=existing.transfer_id)
                offset = int(state["offset"])
                resumed_checkpoint = replace(
                    existing,
                    offset=offset,
                    status="partial",
                    updated_at=datetime.now(timezone.utc),
                )
                _save_checkpoint(checkpoint_path, resumed_checkpoint)
                self._progress.update(
                    context.operation_id,
                    state=OperationState.RESUMING,
                    message=f"resuming local transfer at offset {offset}",
                )
                return resumed_checkpoint, True, None
            restart_reason = mismatch
            _discard_transfer(existing.transfer_id)
            checkpoint_path.unlink(missing_ok=True)
            self._progress.update(
                context.operation_id,
                state=OperationState.RUNNING,
                message=f"partial transfer discarded: {mismatch}",
            )
        transfer = mcp_transfer.upload_begin(
            relative_path=relative_path,
            size_bytes=expected_size,
            sha256=expected_sha256,
            chunk_size=block_size,
            overwrite=overwrite,
        )
        checkpoint = TransferCheckpoint(
            operation_id=context.operation_id,
            relative_path=relative_path,
            transfer_id=str(transfer["transfer_id"]),
            expected_size=expected_size,
            expected_sha256=expected_sha256,
            offset=int(transfer["offset"]),
            block_size=int(transfer["chunk_size"]),
            output_format=output_format,
            source_validator=source_validator,
            status="partial",
            updated_at=datetime.now(timezone.utc),
        )
        _save_checkpoint(checkpoint_path, checkpoint)
        return checkpoint, False, restart_reason

    def _upload_serialized(
        self,
        *,
        context: ProviderContext,
        serialized: Path,
        checkpoint: TransferCheckpoint,
        checkpoint_path: Path,
        base_bytes: int,
        base_offset: int,
        total_bytes: int,
    ) -> TransferCheckpoint:
        """Stream strict offset blocks through the existing MCP transfer service.

        Returns:
            Final uploaded checkpoint.
        """
        offset = checkpoint.offset
        with serialized.open("rb") as stream:
            stream.seek(offset)
            while offset < checkpoint.expected_size:
                raw = stream.read(checkpoint.block_size)
                if not raw:
                    raise OSError("serialized file ended before expected_size")
                result = mcp_transfer.upload_chunk(
                    transfer_id=checkpoint.transfer_id,
                    offset=offset,
                    data_base64=base64.b64encode(raw).decode("ascii"),
                )
                offset = int(result["offset"])
                checkpoint = replace(
                    checkpoint,
                    offset=offset,
                    updated_at=datetime.now(timezone.utc),
                )
                _save_checkpoint(checkpoint_path, checkpoint)
                self._progress.update(
                    context.operation_id,
                    bytes_received=base_bytes + offset,
                    total_bytes=total_bytes,
                    current_offset=base_offset + offset,
                    state=OperationState.RUNNING,
                    message=f"stored {offset} of {checkpoint.expected_size} bytes",
                )
        return checkpoint

    def _mark_running(self, context: ProviderContext, *, message: str) -> None:
        """Move a mutable operation into storage work.

        Args:
            context: Provider operation context.
            message: Human-readable storage phase message.

        Returns:
            None.
        """
        snapshot = self._progress.get(context.operation_id)
        if snapshot.state in {
            OperationState.COMPLETED,
            OperationState.FAILED,
            OperationState.CANCELLED,
            OperationState.UNSUPPORTED,
        }:
            raise ValueError("cannot store through a terminal operation")
        self._progress.update(
            context.operation_id,
            state=OperationState.RUNNING,
            attempt=context.attempt,
            message=message,
        )

    def _handle_failure(
        self,
        *,
        context: ProviderContext,
        checkpoint: TransferCheckpoint | None,
        checkpoint_path: Path,
        capabilities: TransferCapabilities,
        source_validator: SourceValidator | None,
        error: Exception,
    ) -> None:
        """Preserve only validated resumable partial state and fail progress.

        Returns:
            None.
        """
        resumable = (
            checkpoint is not None
            and capabilities.resume_support is not ResumeSupport.UNSUPPORTED
            and source_validator is not None
        )
        if checkpoint is not None:
            if resumable:
                failed = replace(
                    checkpoint,
                    status="failed",
                    updated_at=datetime.now(timezone.utc),
                )
                _save_checkpoint(checkpoint_path, failed)
            else:
                _discard_transfer(checkpoint.transfer_id)
                checkpoint_path.unlink(missing_ok=True)
        try:
            self._progress.fail(
                context.operation_id,
                error_code="dataset_store_error",
                message=f"{type(error).__name__}: {error}",
            )
        except (KeyError, ValueError):
            pass


def _safe_relative_path(value: str) -> str:
    """Normalize one safe data-root-relative path.

    Args:
        value: Candidate project data path.

    Returns:
        Safe POSIX relative path.
    """
    raw = str(value).strip().replace("\\", "/")
    candidate = PurePosixPath(raw)
    if not raw or candidate.is_absolute():
        raise ValueError("relative path must be non-empty and relative")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("relative path contains an unsafe component")
    return candidate.as_posix()


def _normalized_target_path(relative_path: str, output_format: str) -> str:
    """Attach or validate the canonical output extension.

    Args:
        relative_path: Candidate data-root-relative target path.
        output_format: Canonical dataset output format.

    Returns:
        Safe relative target path.
    """
    normalized = _safe_relative_path(relative_path)
    expected = f".{_FORMAT_EXTENSIONS[output_format]}"
    if Path(normalized).suffix:
        if not normalized.lower().endswith(expected):
            raise ValueError(f"target extension must be {expected} for {output_format}")
        return normalized
    return normalized + expected


def _resolve_block_size(
    context: ProviderContext,
    capabilities: TransferCapabilities,
) -> int:
    """Resolve the strict internal MCP transfer block size.

    Args:
        context: Provider operation context containing request controls.
        capabilities: Effective transfer capability declaration.

    Returns:
        Positive MCP transfer block size.
    """
    requested = context.request.block_size_bytes
    if requested is not None:
        capabilities.validate_block_size(requested)
        block_size = requested
    elif capabilities.supports_block_size:
        block_size = capabilities.default_block_size or mcp_transfer.DEFAULT_CHUNK_SIZE
        capabilities.validate_block_size(block_size)
    else:
        block_size = mcp_transfer.DEFAULT_CHUNK_SIZE
    if block_size > mcp_transfer.MAX_CHUNK_SIZE:
        raise ValueError(
            f"block_size exceeds MCP maximum {mcp_transfer.MAX_CHUNK_SIZE}"
        )
    return block_size


def _serialize_dataset(dataset: NormalizedDataset, path: Path) -> None:
    """Serialize a normalized dataset strictly to its declared format.

    Args:
        dataset: Validated normalized dataset.
        path: Temporary output path.

    Returns:
        None.
    """
    output_format = dataset.output_format.upper()
    data = dataset.data
    if isinstance(data, Table):
        storage._write_table(  # type: ignore[attr-defined]
            data,
            path,
            output_format.lower(),
        )
        return
    serialized_format = str(dataset.provenance.get("serialized_format", "")).upper()
    if serialized_format != output_format:
        raise TypeError(
            "non-Table data requires provenance.serialized_format "
            "matching output_format"
        )
    if isinstance(data, (bytes, bytearray)):
        path.write_bytes(bytes(data))
        return
    if isinstance(data, Path):
        if not data.is_file():
            raise FileNotFoundError(data)
        shutil.copyfile(data, path)
        return
    raise TypeError(f"unsupported normalized dataset payload: {type(data).__name__}")


def _checkpoint_path(relative_path: str) -> Path:
    """Resolve the hidden checkpoint sidecar path.

    Args:
        relative_path: Final data-root-relative dataset path.

    Returns:
        Safe absolute checkpoint path below the data root.
    """
    target = PurePosixPath(relative_path)
    checkpoint_name = f".{target.name}{_CHECKPOINT_SUFFIX}"
    relative = PurePosixPath(
        *target.parts[:-1],
        checkpoint_name,
    ).as_posix()
    return storage.resolve_data_path(relative, create_parent=True)


def _save_checkpoint(path: Path, checkpoint: TransferCheckpoint) -> None:
    """Atomically persist one checkpoint sidecar.

    Args:
        path: Absolute checkpoint sidecar path.
        checkpoint: Validated immutable checkpoint.

    Returns:
        None.
    """
    _atomic_text(
        path,
        json.dumps(
            checkpoint.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def _load_checkpoint(path: Path) -> TransferCheckpoint:
    """Load and validate one checkpoint sidecar.

    Args:
        path: Absolute checkpoint sidecar path.

    Returns:
        Validated checkpoint.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise TypeError("checkpoint must contain a JSON object")
    return TransferCheckpoint.from_dict(payload)


def _checkpoint_mismatch(
    checkpoint: TransferCheckpoint,
    *,
    operation_id: str,
    relative_path: str,
    expected_size: int,
    expected_sha256: str,
    block_size: int,
    output_format: str,
    source_validator: SourceValidator | None,
) -> str | None:
    """Return an explicit reason a checkpoint cannot be resumed.

    Args:
        checkpoint: Existing durable checkpoint.
        operation_id: Current progress operation UUID.
        relative_path: Current final dataset path.
        expected_size: Current serialized byte size.
        expected_sha256: Current serialized-file digest.
        block_size: Current negotiated block size.
        output_format: Current canonical output format.
        source_validator: Current immutable source validator.

    Returns:
        Mismatch reason or None for an exact match.
    """
    expected = {
        "operation_id": operation_id,
        "relative_path": relative_path,
        "expected_size": expected_size,
        "expected_sha256": expected_sha256,
        "block_size": block_size,
        "output_format": output_format,
        "source_validator": source_validator,
    }
    for name, value in expected.items():
        if getattr(checkpoint, name) != value:
            return f"checkpoint {name} does not match current source"
    if checkpoint.status == "completed":
        return "checkpoint already completed"
    return None


def _discard_transfer(transfer_id: str) -> None:
    """Remove an abandoned MCP partial file and durable transfer state.

    Args:
        transfer_id: Existing MCP upload transfer identifier.

    Returns:
        None.
    """
    try:
        state = mcp_transfer._load(  # type: ignore[attr-defined]
            transfer_id,
            direction="upload",
        )
    except (FileNotFoundError, ValueError):
        return
    temp_path = state.get("temp_path")
    if isinstance(temp_path, str) and temp_path:
        Path(temp_path).unlink(missing_ok=True)
    try:
        state_path = mcp_transfer._state_path(transfer_id)  # type: ignore[attr-defined]
        state_path.unlink(missing_ok=True)
    except ValueError:
        pass


def _completed_manifest(
    *,
    provenance: ProvenanceManifest,
    dataset: NormalizedDataset,
    context: ProviderContext,
    progress: DownloadProgress,
    digest: str,
    size_bytes: int,
    relative_path: str,
    transfer_id: str,
    resumed: bool,
    source_validator: SourceValidator | None,
) -> ProvenanceManifest:
    """Complete immutable provenance with actual storage telemetry.

    Args:
        provenance: Base immutable provenance manifest.
        dataset: Persisted normalized dataset.
        context: Provider operation context.
        progress: Current storage progress snapshot.
        digest: Exact serialized-file SHA-256.
        size_bytes: Exact serialized byte size.
        relative_path: Final data-root-relative dataset path.
        transfer_id: MCP transfer identifier.
        resumed: Whether a matching partial transfer was resumed.
        source_validator: Immutable source validator used for resume.

    Returns:
        Completed manifest carrying result digest and transfer facts.
    """
    supports_range = context.command_spec.transfer_capabilities.supports_offset
    actual_range = (
        f"bytes={context.request.offset_bytes}-{size_bytes - 1}"
        if supports_range and size_bytes
        else None
    )
    transfer = replace(
        provenance.client_transfer,
        operation_id=context.operation_id,
        transfer_capabilities=context.command_spec.transfer_capabilities,
        requested_offset_bytes=context.request.offset_bytes,
        requested_block_size_bytes=context.request.block_size_bytes,
        actual_range=actual_range,
        actual_cursor=(
            source_validator.identity
            if resumed and source_validator is not None
            else None
        ),
        attempts=progress.attempt,
        bytes_received=size_bytes,
        elapsed_seconds=progress.elapsed_seconds,
        average_speed_bps=progress.average_speed_bps,
    )
    metadata = {
        **dict(provenance.metadata),
        **dict(dataset.provenance),
        "storage_relative_path": relative_path,
        "storage_transfer_id": transfer_id,
        "storage_resumed": resumed,
    }
    return replace(
        provenance,
        result_sha256=digest,
        client_transfer=transfer,
        metadata=metadata,
    )


def _atomic_text(path: Path, text: str) -> None:
    """Atomically replace one UTF-8 text sidecar.

    Args:
        path: Absolute sidecar destination path.
        text: Complete UTF-8 text content.

    Returns:
        None.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


__all__ = [
    "ScientificDatasetStore",
    "SourceValidator",
    "StoredDataset",
    "TransferCheckpoint",
]
