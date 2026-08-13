"""Deterministic, secret-free provenance for scientific datasets."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from science_assistant.progress import (  # type: ignore[import-not-found]
    ResumeSupport,
    TransferCapabilities,
)

_CLIENT_STRATEGIES: Final[frozenset[str]] = frozenset(
    {"official_client", "popular_client", "direct_api", "hybrid"}
)
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_SECRET_KEYS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "cookie",
        "credential",
        "credentials",
        "password",
        "private_key",
        "secret",
        "token",
        "access_token",
        "refresh_token",
    }
)
_SECRET_VALUE_PREFIXES: Final[tuple[str, ...]] = (
    "bearer ",
    "basic ",
    "password=",
    "api_key=",
    "apikey=",
    "client_secret=",
)
_ALLOWED_RESUME_KEY: Final[str] = "resume_token"


def _canonical_value(value: Any) -> Any:
    """Convert supported values into deterministic JSON-compatible values.

    Args:
        value: Arbitrary supported Python value.

    Returns:
        Canonical JSON-compatible value.
    """
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _canonical_value(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime values must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return _canonical_value(value.value)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        document: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Canonical JSON mappings require string keys")
            document[key] = _canonical_value(item)
        return document
    if isinstance(value, (set, frozenset)):
        canonical_items = [_canonical_value(item) for item in value]
        return sorted(
            canonical_items,
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
        )
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return {"encoding": "hex", "value": bytes(value).hex()}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Canonical JSON does not permit NaN or infinity")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"Unsupported canonical JSON value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize a value as deterministic UTF-8-safe JSON text.

    Args:
        value: Supported Python value.

    Returns:
        Canonical JSON text independent of mapping insertion order.
    """
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_sha256(value: Any) -> str:
    """Compute SHA-256 over deterministic canonical JSON.

    Args:
        value: Supported Python value.

    Returns:
        Lowercase hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_bytes(payload: bytes) -> str:
    """Compute SHA-256 for exact result bytes.

    Args:
        payload: Exact result bytes.

    Returns:
        Lowercase hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: str | Path, *, block_size: int = 1024 * 1024) -> str:
    """Compute SHA-256 for an exact file without loading it fully.

    Args:
        path: File path to hash.
        block_size: Positive read block size in bytes.

    Returns:
        Lowercase hexadecimal SHA-256 digest.
    """
    if block_size <= 0:
        raise ValueError("block_size must be > 0")
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_text(name: str, value: str) -> str:
    """Normalize a required non-empty text field.

    Args:
        name: Field name used in validation errors.
        value: Field value to normalize.

    Returns:
        Stripped non-empty value.
    """
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _validate_sha256(name: str, value: str) -> str:
    """Normalize and validate a hexadecimal SHA-256 digest.

    Args:
        name: Field name used in validation errors.
        value: Digest value to normalize.

    Returns:
        Lowercase validated digest.
    """
    normalized = value.strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise ValueError(f"{name} must be a 64-character SHA-256 digest")
    return normalized


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a shallow read-only mapping copy.

    Args:
        value: Mapping to copy and freeze.

    Returns:
        Read-only mapping proxy.
    """
    return MappingProxyType(dict(value))


def _contains_secret_value(value: str) -> bool:
    """Detect common embedded credential representations.

    Args:
        value: String value to inspect.

    Returns:
        True when the value resembles a credential or authorization header.
    """
    lowered = value.strip().lower()
    has_prefix = any(lowered.startswith(prefix) for prefix in _SECRET_VALUE_PREFIXES)
    resembles_jwt = lowered.count(".") == 2 and len(lowered) > 40
    return has_prefix or resembles_jwt


def _reject_secrets(value: Any, *, path: str = "root") -> None:
    """Reject credentials recursively while allowing non-secret resume cursors.

    Args:
        value: Arbitrary provenance value to inspect.
        path: Human-readable traversal path for errors.

    Returns:
        None.
    """
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            if (
                normalized_key in _FORBIDDEN_SECRET_KEYS
                and normalized_key != _ALLOWED_RESUME_KEY
            ):
                raise ValueError(
                    f"Secret or credential field is forbidden: {path}.{key}"
                )
            _reject_secrets(item, path=f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        for index, item in enumerate(value):
            _reject_secrets(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and _contains_secret_value(value):
        raise ValueError(f"Secret-like value is forbidden at {path}")


@dataclass(frozen=True, slots=True)
class InputFileProvenance:
    """Immutable version and digest of one input file.

    Attributes:
        identifier: Stable file name, URI, or dataset-relative identifier.
        version: Source-declared file or schema version.
        sha256: SHA-256 of the exact input bytes.
    """

    identifier: str
    version: str
    sha256: str

    def __post_init__(self) -> None:
        """Normalize and validate the input-file record.

        Returns:
            None.
        """
        object.__setattr__(
            self,
            "identifier",
            _require_text("identifier", self.identifier),
        )
        object.__setattr__(self, "version", _require_text("version", self.version))
        object.__setattr__(
            self,
            "sha256",
            _validate_sha256("sha256", self.sha256),
        )

    def to_dict(self) -> dict[str, str]:
        """Build a JSON-compatible input-file record.

        Returns:
            Identifier, version, and digest.
        """
        return {
            "identifier": self.identifier,
            "version": self.version,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class ClientAndTransferProvenance:
    """Immutable client decision and transfer execution provenance.

    Attributes:
        client_strategy: official_client, popular_client, direct_api, or hybrid.
        client_name: Selected client or protocol implementation name.
        client_package: Package name or direct protocol identifier.
        client_version: Selected client or protocol version.
        repository_url: Repository or implementation URL.
        license: Client or implementation license.
        official_status: Exact official, endorsed, community, or direct status.
        decision_record_path: Dated client-decision record path or URL.
        decision_record_date: ISO calendar date of the decision check.
        transport: Actual HTTP, TAP, FTP, file, library, or hybrid transport.
        operation_id: Stable progress operation UUID.
        transfer_capabilities: Declared immutable transfer capabilities.
        requested_offset_bytes: Caller-requested non-negative byte offset.
        requested_block_size_bytes: Caller-requested positive block size.
        actual_range: Exact range expression used by the transport.
        actual_cursor: Actual non-secret cursor used by the provider.
        resume_token: Actual non-secret resume cursor or checkpoint reference.
        attempts: Positive number of performed attempts.
        bytes_received: Final non-negative received-byte count.
        elapsed_seconds: Final non-negative elapsed seconds.
        average_speed_bps: Final non-negative average speed.
    """

    client_strategy: str
    client_name: str
    client_package: str
    client_version: str
    repository_url: str
    license: str
    official_status: str
    decision_record_path: str
    decision_record_date: str
    transport: str
    operation_id: str
    transfer_capabilities: TransferCapabilities
    requested_offset_bytes: int = 0
    requested_block_size_bytes: int | None = None
    actual_range: str | None = None
    actual_cursor: str | None = None
    resume_token: str | None = None
    attempts: int = 1
    bytes_received: int = 0
    elapsed_seconds: float = 0.0
    average_speed_bps: float = 0.0

    def __post_init__(self) -> None:
        """Validate client decision, capabilities, and transfer telemetry.

        Returns:
            None.
        """
        strategy = self.client_strategy.strip().lower()
        if strategy not in _CLIENT_STRATEGIES:
            raise ValueError(f"Unsupported client_strategy: {self.client_strategy!r}")
        object.__setattr__(self, "client_strategy", strategy)
        for name in (
            "client_name",
            "client_package",
            "client_version",
            "repository_url",
            "license",
            "official_status",
            "decision_record_path",
            "transport",
            "operation_id",
        ):
            object.__setattr__(self, name, _require_text(name, getattr(self, name)))
        try:
            date.fromisoformat(self.decision_record_date)
        except ValueError as exc:
            raise ValueError("decision_record_date must be ISO YYYY-MM-DD") from exc
        self.transfer_capabilities.validate_offset(self.requested_offset_bytes)
        self.transfer_capabilities.validate_block_size(self.requested_block_size_bytes)
        if self.attempts < 1:
            raise ValueError("attempts must be >= 1")
        if self.bytes_received < 0:
            raise ValueError("bytes_received must be >= 0")
        if self.elapsed_seconds < 0:
            raise ValueError("elapsed_seconds must be >= 0")
        if self.average_speed_bps < 0:
            raise ValueError("average_speed_bps must be >= 0")
        if self.actual_range and not self.transfer_capabilities.supports_offset:
            raise ValueError("actual_range requires supports_offset=True")
        if (
            self.actual_cursor
            and self.transfer_capabilities.resume_support is not ResumeSupport.CURSOR
        ):
            raise ValueError("actual_cursor requires cursor resume support")
        if (
            self.resume_token
            and self.transfer_capabilities.resume_support is ResumeSupport.UNSUPPORTED
        ):
            raise ValueError("resume_token is invalid when resume is unsupported")
        _reject_secrets(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        """Build a JSON-compatible client and transfer record.

        Returns:
            Client decision, capabilities, controls, and final telemetry.
        """
        return {
            "client_strategy": self.client_strategy,
            "client_name": self.client_name,
            "client_package": self.client_package,
            "client_version": self.client_version,
            "repository_url": self.repository_url,
            "license": self.license,
            "official_status": self.official_status,
            "decision_record_path": self.decision_record_path,
            "decision_record_date": self.decision_record_date,
            "transport": self.transport,
            "operation_id": self.operation_id,
            "transfer_capabilities": self.transfer_capabilities.to_dict(),
            "requested_offset_bytes": self.requested_offset_bytes,
            "requested_block_size_bytes": self.requested_block_size_bytes,
            "actual_range": self.actual_range,
            "actual_cursor": self.actual_cursor,
            "resume_token": self.resume_token,
            "attempts": self.attempts,
            "bytes_received": self.bytes_received,
            "elapsed_seconds": self.elapsed_seconds,
            "average_speed_bps": self.average_speed_bps,
        }


@dataclass(frozen=True, slots=True)
class ProvenanceManifest:
    """Immutable complete provenance manifest for a scientific result.

    Attributes:
        source_identifier: Stable source, mission, catalog, or dataset identifier.
        source_version: Exact source API, schema, or dataset version.
        source_release: Exact source release or data release name.
        exact_query: Exact query, request, or parameter document.
        retrieved_at: Timezone-aware retrieval completion timestamp.
        endpoint: Exact source URL or endpoint identifier without credentials.
        units: Read-only mapping of field or axis names to units.
        time_systems: Immutable declared time-system tuple.
        coordinate_systems: Immutable declared coordinate-system tuple.
        input_files: Immutable input file version and digest tuple.
        result_sha256: SHA-256 of the exact result bytes.
        client_transfer: Immutable client decision and transfer provenance.
        manifest_version: Provenance schema version.
        metadata: Read-only additional non-secret provenance metadata.
        canonical_digest: SHA-256 of the canonical manifest document.
    """

    source_identifier: str
    source_version: str
    source_release: str
    exact_query: Any
    retrieved_at: datetime
    endpoint: str
    units: Mapping[str, str]
    time_systems: tuple[str, ...]
    coordinate_systems: tuple[str, ...]
    input_files: tuple[InputFileProvenance, ...]
    result_sha256: str
    client_transfer: ClientAndTransferProvenance
    manifest_version: str = "1.0"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize, freeze, and validate every mandatory manifest field.

        Returns:
            None.
        """
        for name in (
            "source_identifier",
            "source_version",
            "source_release",
            "endpoint",
            "manifest_version",
        ):
            object.__setattr__(self, name, _require_text(name, getattr(self, name)))
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        object.__setattr__(
            self,
            "retrieved_at",
            self.retrieved_at.astimezone(timezone.utc),
        )
        object.__setattr__(
            self,
            "result_sha256",
            _validate_sha256("result_sha256", self.result_sha256),
        )
        object.__setattr__(
            self,
            "units",
            MappingProxyType(
                {
                    _require_text("unit field", key): _require_text(
                        f"unit for {key}",
                        value,
                    )
                    for key, value in self.units.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "time_systems",
            tuple(_require_text("time_system", item) for item in self.time_systems),
        )
        object.__setattr__(
            self,
            "coordinate_systems",
            tuple(
                _require_text("coordinate_system", item)
                for item in self.coordinate_systems
            ),
        )
        object.__setattr__(self, "input_files", tuple(self.input_files))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))
        _reject_secrets(self.exact_query, path="exact_query")
        _reject_secrets(self.metadata, path="metadata")
        _reject_secrets(self.endpoint, path="endpoint")
        _reject_secrets(self.client_transfer.to_dict(), path="client_transfer")
        canonical_json(self.exact_query)
        canonical_json(self.metadata)

    @property
    def canonical_digest(self) -> str:
        """Compute SHA-256 of the canonical manifest document.

        Returns:
            Lowercase hexadecimal canonical manifest digest.
        """
        return canonical_sha256(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        """Build the complete JSON-compatible manifest document.

        Returns:
            Complete immutable provenance represented as mutable JSON values.
        """
        return {
            "manifest_version": self.manifest_version,
            "source_identifier": self.source_identifier,
            "source_version": self.source_version,
            "source_release": self.source_release,
            "exact_query": _canonical_value(self.exact_query),
            "retrieved_at": self.retrieved_at.isoformat(),
            "endpoint": self.endpoint,
            "units": dict(self.units),
            "time_systems": list(self.time_systems),
            "coordinate_systems": list(self.coordinate_systems),
            "input_files": [item.to_dict() for item in self.input_files],
            "result_sha256": self.result_sha256,
            "client_transfer": self.client_transfer.to_dict(),
            "metadata": _canonical_value(self.metadata),
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialize the manifest as stable JSON.

        Args:
            indent: Optional human-readable indentation width.

        Returns:
            Deterministically ordered UTF-8-safe JSON text.
        """
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            indent=indent,
            allow_nan=False,
        )

    def verify_result(self, payload: bytes) -> bool:
        """Verify exact result bytes against the recorded digest.

        Args:
            payload: Exact result bytes to verify.

        Returns:
            True when the payload digest matches result_sha256.
        """
        return sha256_bytes(payload) == self.result_sha256


__all__ = [
    "ClientAndTransferProvenance",
    "InputFileProvenance",
    "ProvenanceManifest",
    "canonical_json",
    "canonical_sha256",
    "sha256_bytes",
    "sha256_file",
]
