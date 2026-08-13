"""Import-safe object contract shared by all scientific providers."""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, ClassVar, Final

from science_assistant.progress import (  # type: ignore[import-not-found]
    DownloadProgress,
    OperationState,
    ProgressSupport,
    ProgressTracker,
    ResumeSupport,
    TransferCapabilities,
)

SUPPORTED_OUTPUT_FORMATS: Final[tuple[str, ...]] = (
    "ECSV",
    "CSV",
    "FITS",
    "PARQUET",
)
CLIENT_STRATEGIES: Final[tuple[str, ...]] = (
    "official_client",
    "popular_client",
    "direct_api",
    "hybrid",
)
PROVIDER_NAME: Final[str] = "base-scientific-provider"


class ProviderError(RuntimeError):
    """Stable base error raised by the provider contract.

    Attributes:
        DEFAULT_CODE: Stable fallback code for this class.
        DEFAULT_RETRYABLE: Default retryability for this class.
        code: Stable machine-readable error code.
        retryable: Whether the lifecycle may retry the error.
        provider: Optional provider name.
        command: Optional command identifier.
        operation_id: Optional operation UUID.
        cause: Optional original exception.
    """

    DEFAULT_CODE: ClassVar[str] = "provider_error"
    DEFAULT_RETRYABLE: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool | None = None,
        provider: str | None = None,
        command: str | None = None,
        operation_id: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        """Initialize a stable provider error.

        Args:
            message: Human-readable error description.
            code: Optional stable code override.
            retryable: Optional retryability override.
            provider: Optional provider name.
            command: Optional command identifier.
            operation_id: Optional operation UUID.
            cause: Optional original exception.

        Returns:
            None.
        """
        super().__init__(message)
        self.code = code or self.DEFAULT_CODE
        self.retryable = self.DEFAULT_RETRYABLE if retryable is None else retryable
        self.provider = provider
        self.command = command
        self.operation_id = operation_id
        self.cause = cause

    def to_dict(self) -> dict[str, Any]:
        """Build a JSON-compatible error envelope.

        Returns:
            Stable error fields without serializing the cause.
        """
        return {
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
            "provider": self.provider,
            "command": self.command,
            "operation_id": self.operation_id,
        }


class ProviderConfigurationError(ProviderError):
    """Provider or command metadata is invalid.

    Attributes:
        DEFAULT_CODE: Stable configuration error code.
    """

    DEFAULT_CODE: ClassVar[str] = "provider_configuration_error"


class ProviderValidationError(ProviderError):
    """A request, response, or result violates the contract.

    Attributes:
        DEFAULT_CODE: Stable validation error code.
    """

    DEFAULT_CODE: ClassVar[str] = "provider_validation_error"


class ProviderOperationNotFoundError(ProviderError):
    """A requested progress operation does not exist.

    Attributes:
        DEFAULT_CODE: Stable operation-not-found code.
    """

    DEFAULT_CODE: ClassVar[str] = "provider_operation_not_found"


class ProviderUnsupportedError(ProviderError):
    """A requested command or capability is unsupported.

    Attributes:
        DEFAULT_CODE: Stable unsupported-operation code.
    """

    DEFAULT_CODE: ClassVar[str] = "provider_unsupported"


class ProviderTransportError(ProviderError):
    """A provider transport or selected client failed.

    Attributes:
        DEFAULT_CODE: Stable transport error code.
        DEFAULT_RETRYABLE: Transport failures are retryable by default.
    """

    DEFAULT_CODE: ClassVar[str] = "provider_transport_error"
    DEFAULT_RETRYABLE: ClassVar[bool] = True


class ProviderNormalizationError(ProviderError):
    """Raw provider data could not be normalized safely.

    Attributes:
        DEFAULT_CODE: Stable normalization error code.
    """

    DEFAULT_CODE: ClassVar[str] = "provider_normalization_error"


class ProviderTimeoutError(ProviderError):
    """A provider attempt exceeded its timeout.

    Attributes:
        DEFAULT_CODE: Stable timeout error code.
        DEFAULT_RETRYABLE: Timeout failures are retryable by default.
    """

    DEFAULT_CODE: ClassVar[str] = "provider_timeout"
    DEFAULT_RETRYABLE: ClassVar[bool] = True


class ProviderRateLimitError(ProviderError):
    """A provider rejected work because of a rate limit.

    Attributes:
        DEFAULT_CODE: Stable rate-limit error code.
        DEFAULT_RETRYABLE: Rate-limit failures are retryable by default.
    """

    DEFAULT_CODE: ClassVar[str] = "provider_rate_limited"
    DEFAULT_RETRYABLE: ClassVar[bool] = True


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """Immutable request accepted by a scientific provider.

    Attributes:
        provider: Provider name expected to execute the request.
        command: Provider command identifier.
        params: Provider-specific read-only parameter mapping.
        output_format: Canonical output format name.
        limit: Optional positive row or item limit.
        offset_bytes: Non-negative transfer offset in bytes.
        block_size_bytes: Optional transfer block size in bytes.
        resume_operation_id: Optional operation identifier to resume.
        resume_token: Optional cursor or selected-client token.
        timeout_seconds: Optional positive per-attempt timeout override.
        max_retries: Optional non-negative retry-count override.
        retry_backoff_seconds: Optional non-negative retry backoff.
        rate_limit_delay_seconds: Optional non-negative request spacing.
    """

    provider: str
    command: str
    params: Mapping[str, Any]
    output_format: str
    limit: int | None = None
    offset_bytes: int = 0
    block_size_bytes: int | None = None
    resume_operation_id: str | None = None
    resume_token: str | None = None
    timeout_seconds: float | None = None
    max_retries: int | None = None
    retry_backoff_seconds: float | None = None
    rate_limit_delay_seconds: float | None = None

    def __post_init__(self) -> None:
        """Normalize immutable fields and validate common values.

        Returns:
            None.
        """
        provider = self.provider.strip()
        command = self.command.strip()
        output_format = self.output_format.strip().upper()
        if not provider:
            raise ProviderValidationError("provider must not be empty")
        if not command:
            raise ProviderValidationError("command must not be empty")
        if output_format not in SUPPORTED_OUTPUT_FORMATS:
            raise ProviderValidationError(
                f"Unsupported output_format: {self.output_format!r}"
            )
        if self.limit is not None and self.limit <= 0:
            raise ProviderValidationError("limit must be > 0 when supplied")
        if self.offset_bytes < 0:
            raise ProviderValidationError("offset_bytes must be >= 0")
        if self.block_size_bytes is not None and self.block_size_bytes <= 0:
            raise ProviderValidationError("block_size_bytes must be > 0")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ProviderValidationError("timeout_seconds must be > 0")
        if self.max_retries is not None and self.max_retries < 0:
            raise ProviderValidationError("max_retries must be >= 0")
        if self.retry_backoff_seconds is not None and self.retry_backoff_seconds < 0:
            raise ProviderValidationError("retry_backoff_seconds must be >= 0")
        if (
            self.rate_limit_delay_seconds is not None
            and self.rate_limit_delay_seconds < 0
        ):
            raise ProviderValidationError("rate_limit_delay_seconds must be >= 0")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "output_format", output_format)
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))

    def validate_capabilities(self, capabilities: TransferCapabilities) -> None:
        """Validate transfer controls against command capabilities.

        Args:
            capabilities: Immutable command transfer capabilities.

        Returns:
            None.
        """
        try:
            capabilities.validate_offset(self.offset_bytes)
            capabilities.validate_block_size(self.block_size_bytes)
        except ValueError as exc:
            raise ProviderValidationError(str(exc), cause=exc) from exc
        resume_requested = bool(self.resume_operation_id or self.resume_token)
        if (
            resume_requested
            and capabilities.resume_support is ResumeSupport.UNSUPPORTED
        ):
            raise ProviderUnsupportedError("Resume is unsupported for this command")
        if (
            capabilities.resume_support is ResumeSupport.CURSOR
            and self.resume_operation_id
            and not self.resume_token
        ):
            raise ProviderValidationError(
                "Cursor resume requires resume_token with resume_operation_id"
            )

    def to_dict(self) -> dict[str, Any]:
        """Build a JSON-compatible request document.

        Returns:
            Request fields with a mutable parameter copy.
        """
        return {
            "provider": self.provider,
            "command": self.command,
            "params": dict(self.params),
            "output_format": self.output_format,
            "limit": self.limit,
            "offset_bytes": self.offset_bytes,
            "block_size_bytes": self.block_size_bytes,
            "resume_operation_id": self.resume_operation_id,
            "resume_token": self.resume_token,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "retry_backoff_seconds": self.retry_backoff_seconds,
            "rate_limit_delay_seconds": self.rate_limit_delay_seconds,
        }


@dataclass(frozen=True, slots=True)
class ProviderCommandSpec:
    """Immutable public command declaration.

    Attributes:
        name: Stable command identifier.
        description: Human-readable command purpose.
        transfer_capabilities: Progress and resume capabilities.
        output_formats: Supported canonical output formats.
        supports_percentage: Whether exact percentage is available.
        supports_resume: Whether any resume mechanism is available.
        supports_offset: Whether caller offsets are accepted.
        supports_block_size: Whether caller block sizes are accepted.
    """

    name: str
    description: str
    transfer_capabilities: TransferCapabilities
    output_formats: tuple[str, ...] = SUPPORTED_OUTPUT_FORMATS

    def __post_init__(self) -> None:
        """Normalize command metadata and validate output formats.

        Returns:
            None.
        """
        name = self.name.strip()
        formats = tuple(value.strip().upper() for value in self.output_formats)
        if not name:
            raise ProviderConfigurationError("Command name must not be empty")
        if not formats:
            raise ProviderConfigurationError(
                f"Command {name!r} must declare an output format"
            )
        invalid = sorted(set(formats) - set(SUPPORTED_OUTPUT_FORMATS))
        if invalid:
            raise ProviderConfigurationError(
                f"Command {name!r} declares unsupported formats: {invalid}"
            )
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", self.description.strip())
        object.__setattr__(self, "output_formats", formats)

    @property
    def supports_percentage(self) -> bool:
        """Report whether exact percentage may be exposed.

        Returns:
            True only for exact progress support.
        """
        return self.transfer_capabilities.supports_percentage

    @property
    def supports_resume(self) -> bool:
        """Report whether the command declares resume support.

        Returns:
            True unless resume is explicitly unsupported.
        """
        return (
            self.transfer_capabilities.resume_support is not ResumeSupport.UNSUPPORTED
        )

    @property
    def supports_offset(self) -> bool:
        """Report whether non-zero caller offsets are accepted.

        Returns:
            The declared offset capability.
        """
        return self.transfer_capabilities.supports_offset

    @property
    def supports_block_size(self) -> bool:
        """Report whether caller-selected block sizes are accepted.

        Returns:
            The declared block-size capability.
        """
        return self.transfer_capabilities.supports_block_size

    def to_dict(self) -> dict[str, Any]:
        """Build a JSON-compatible command declaration.

        Returns:
            Command metadata and derived capability flags.
        """
        return {
            "name": self.name,
            "description": self.description,
            "output_formats": list(self.output_formats),
            "transfer_capabilities": self.transfer_capabilities.to_dict(),
            "supports_percentage": self.supports_percentage,
            "supports_resume": self.supports_resume,
            "supports_offset": self.supports_offset,
            "supports_block_size": self.supports_block_size,
        }


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    """Immutable provider identity and selected client strategy.

    Attributes:
        name: Stable provider name.
        description: Human-readable provider purpose.
        default_capabilities: Provider-wide default capabilities.
        client_strategy: official_client, popular_client, direct_api, or hybrid.
        client_name: Selected client or direct protocol name.
        client_version: Selected client version.
        repository_url: Repository or implementation URL.
        license: Client or implementation license.
        research_decision_ref: Path or URL of the research decision.
    """

    name: str
    description: str
    default_capabilities: TransferCapabilities
    client_strategy: str
    client_name: str | None = None
    client_version: str | None = None
    repository_url: str | None = None
    license: str | None = None
    research_decision_ref: str | None = None

    def __post_init__(self) -> None:
        """Normalize fields and validate the strategy vocabulary.

        Returns:
            None.
        """
        name = self.name.strip()
        strategy = self.client_strategy.strip().lower()
        if not name:
            raise ProviderConfigurationError("Provider descriptor name is empty")
        if strategy not in CLIENT_STRATEGIES:
            raise ProviderConfigurationError(
                f"Unsupported client_strategy: {self.client_strategy!r}"
            )
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", self.description.strip())
        object.__setattr__(self, "client_strategy", strategy)

    def to_dict(self) -> dict[str, Any]:
        """Build a JSON-compatible provider descriptor.

        Returns:
            Provider identity, strategy, and research metadata.
        """
        return {
            "name": self.name,
            "description": self.description,
            "default_capabilities": self.default_capabilities.to_dict(),
            "client_strategy": self.client_strategy,
            "client_name": self.client_name,
            "client_version": self.client_version,
            "repository_url": self.repository_url,
            "license": self.license,
            "research_decision_ref": self.research_decision_ref,
        }


@dataclass(frozen=True, slots=True)
class ProviderContext:
    """Immutable context passed to fetch and normalization methods.

    Attributes:
        provider_name: Stable provider name.
        request: Validated provider request.
        command_spec: Resolved command declaration.
        operation_id: Progress operation UUID.
        attempt: Current one-based attempt number.
        started_at: UTC timestamp of the current attempt.
    """

    provider_name: str
    request: ProviderRequest
    command_spec: ProviderCommandSpec
    operation_id: str
    attempt: int
    started_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderOperationHandle:
    """Immutable handle for a provider operation.

    Attributes:
        operation_id: Stable progress operation UUID.
        provider: Provider that owns the operation.
        command: Command being executed.
        capabilities: Effective transfer capabilities.
        created_at: UTC operation creation timestamp.
        progress: Current immutable progress snapshot.
    """

    operation_id: str
    provider: str
    command: str
    capabilities: TransferCapabilities
    created_at: datetime
    progress: DownloadProgress

    def to_dict(self) -> dict[str, Any]:
        """Build a JSON-compatible operation handle.

        Returns:
            Operation identity, capabilities, and progress.
        """
        return {
            "operation_id": self.operation_id,
            "provider": self.provider,
            "command": self.command,
            "capabilities": self.capabilities.to_dict(),
            "created_at": self.created_at.isoformat(),
            "progress": self.progress.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class RawProviderResponse:
    """Immutable response before dataset normalization.

    Attributes:
        payload: Provider-specific raw payload or file reference.
        status_code: Optional HTTP-like status code.
        headers: Read-only response-header mapping.
        bytes_received: Absolute operation byte count.
        total_bytes: Known total bytes, or None.
        current_offset: Absolute current transfer offset.
        resume_token: Optional cursor or selected-client token.
        media_type: Optional raw media type.
        provenance: Read-only raw-source provenance mapping.
    """

    payload: Any
    status_code: int | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    bytes_received: int = 0
    total_bytes: int | None = None
    current_offset: int = 0
    resume_token: str | None = None
    media_type: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Freeze mappings and validate numeric fields.

        Returns:
            None.
        """
        if self.bytes_received < 0:
            raise ProviderValidationError("bytes_received must be >= 0")
        if self.total_bytes is not None and self.total_bytes < 0:
            raise ProviderValidationError("total_bytes must be >= 0")
        if self.current_offset < 0:
            raise ProviderValidationError("current_offset must be >= 0")
        if (
            self.total_bytes is not None
            and max(self.bytes_received, self.current_offset) > self.total_bytes
        ):
            raise ProviderValidationError(
                "Raw response progress cannot exceed total_bytes"
            )
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
        object.__setattr__(
            self,
            "provenance",
            MappingProxyType(dict(self.provenance)),
        )


@dataclass(frozen=True, slots=True)
class NormalizedDataset:
    """Immutable normalized result returned by a provider.

    Attributes:
        data: Normalized in-memory data or durable reference.
        output_format: Canonical ECSV, CSV, FITS, or PARQUET format.
        row_count: Optional non-negative row or item count.
        files: Immutable generated-file tuple.
        provenance: Read-only normalized provenance mapping.
    """

    data: Any
    output_format: str
    row_count: int | None = None
    files: tuple[str, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize output metadata and freeze provenance.

        Returns:
            None.
        """
        output_format = self.output_format.strip().upper()
        if output_format not in SUPPORTED_OUTPUT_FORMATS:
            raise ProviderNormalizationError(
                f"Unsupported normalized format: {self.output_format!r}"
            )
        if self.row_count is not None and self.row_count < 0:
            raise ProviderNormalizationError("row_count must be >= 0")
        object.__setattr__(self, "output_format", output_format)
        object.__setattr__(self, "files", tuple(self.files))
        object.__setattr__(
            self,
            "provenance",
            MappingProxyType(dict(self.provenance)),
        )

    def with_provenance(self, values: Mapping[str, Any]) -> NormalizedDataset:
        """Return a copy with additional provenance.

        Args:
            values: Provenance values that override existing keys.

        Returns:
            New immutable normalized dataset.
        """
        combined = dict(self.provenance)
        combined.update(values)
        return NormalizedDataset(
            data=self.data,
            output_format=self.output_format,
            row_count=self.row_count,
            files=self.files,
            provenance=combined,
        )

    def to_dict(self) -> dict[str, Any]:
        """Build a JSON-compatible result envelope.

        Returns:
            Output metadata, files, provenance, and data.
        """
        return {
            "data": self.data,
            "output_format": self.output_format,
            "row_count": self.row_count,
            "files": list(self.files),
            "provenance": dict(self.provenance),
        }


class BaseScientificProvider(ABC):
    """Abstract provider with the common execution lifecycle.

    Attributes:
        name: Stable provider name supplied by the concrete class.
        descriptor: Immutable provider and client metadata.
        _progress: Thread-safe operation progress tracker.
        _timeout_seconds: Default per-attempt timeout.
        _max_retries: Default retry count.
        _retry_backoff_seconds: Default retry backoff.
        _rate_limit_delay_seconds: Default request spacing.
        _sleep: Awaitable sleep callable.
        _monotonic: Monotonic clock callable.
        _rate_lock: Lock serializing request-start windows.
        _next_allowed_at: Earliest monotonic request-start time.
    """

    def __init__(
        self,
        *,
        progress_tracker: ProgressTracker | None = None,
        timeout_seconds: float = 60.0,
        max_retries: int = 0,
        retry_backoff_seconds: float = 0.5,
        rate_limit_delay_seconds: float = 0.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize common policies without network activity.

        Args:
            progress_tracker: Optional shared progress tracker.
            timeout_seconds: Default positive per-attempt timeout.
            max_retries: Default non-negative retry count.
            retry_backoff_seconds: Default non-negative retry backoff.
            rate_limit_delay_seconds: Default non-negative request spacing.
            sleep: Awaitable sleep callable for tests.
            monotonic: Monotonic clock callable for tests.

        Returns:
            None.
        """
        if timeout_seconds <= 0:
            raise ProviderConfigurationError("timeout_seconds must be > 0")
        if max_retries < 0:
            raise ProviderConfigurationError("max_retries must be >= 0")
        if retry_backoff_seconds < 0:
            raise ProviderConfigurationError("retry_backoff_seconds must be >= 0")
        if rate_limit_delay_seconds < 0:
            raise ProviderConfigurationError("rate_limit_delay_seconds must be >= 0")
        self._progress = progress_tracker or ProgressTracker()
        self._timeout_seconds = float(timeout_seconds)
        self._max_retries = max_retries
        self._retry_backoff_seconds = float(retry_backoff_seconds)
        self._rate_limit_delay_seconds = float(rate_limit_delay_seconds)
        self._sleep = sleep
        self._monotonic = monotonic
        self._rate_lock = asyncio.Lock()
        self._next_allowed_at = 0.0

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the stable provider name.

        Returns:
            Provider name matching ProviderRequest.provider.
        """

    @property
    @abstractmethod
    def descriptor(self) -> ProviderDescriptor:
        """Return immutable provider and client metadata.

        Returns:
            Provider descriptor.
        """

    @abstractmethod
    def commands(self) -> tuple[ProviderCommandSpec, ...]:
        """Return every command supported by this provider.

        Returns:
            Immutable command declaration tuple.
        """

    @abstractmethod
    def validate_request(
        self,
        request: ProviderRequest,
        command_spec: ProviderCommandSpec,
    ) -> None:
        """Validate provider-specific parameters without network work.

        Args:
            request: Common validated provider request.
            command_spec: Resolved command declaration.

        Returns:
            None.
        """

    @abstractmethod
    async def fetch_raw(
        self,
        request: ProviderRequest,
        context: ProviderContext,
    ) -> RawProviderResponse:
        """Fetch a raw response using the concrete transport.

        Args:
            request: Fully validated provider request.
            context: Immutable execution context.

        Returns:
            Raw response with absolute transfer telemetry.
        """

    @abstractmethod
    def normalize(
        self,
        response: RawProviderResponse,
        context: ProviderContext,
    ) -> NormalizedDataset:
        """Normalize raw data without losing provenance.

        Args:
            response: Raw provider response.
            context: Immutable execution context.

        Returns:
            Normalized dataset in the requested format.
        """

    def begin_operation(self, request: ProviderRequest) -> ProviderOperationHandle:
        """Create an operation identifier before network activity.

        Args:
            request: Provider request to validate and prepare.

        Returns:
            New queued operation handle.
        """
        command_spec = self._resolve_command(request.command)
        self._validate_common_request(request, command_spec)
        progress = self._progress.begin(
            provider=self.name,
            command=request.command,
            capabilities=command_spec.transfer_capabilities,
            total_bytes=None,
            offset=request.offset_bytes,
            block_size=request.block_size_bytes,
            resume_token=request.resume_token,
            attempt=1,
            state=OperationState.QUEUED,
        )
        return self._handle(progress)

    def get_progress(self, operation_id: str) -> DownloadProgress:
        """Return progress at every lifecycle stage.

        Args:
            operation_id: Stable operation UUID.

        Returns:
            Current immutable progress snapshot.
        """
        try:
            return self._progress.get(operation_id)
        except KeyError as exc:
            raise ProviderOperationNotFoundError(
                f"Unknown operation_id: {operation_id}",
                provider=self.name,
                operation_id=operation_id,
                cause=exc,
            ) from exc

    def pause_operation(self, operation_id: str) -> ProviderOperationHandle:
        """Pause only when the operation can later resume.

        Args:
            operation_id: Stable operation UUID.

        Returns:
            Paused operation handle.
        """
        progress = self.get_progress(operation_id)
        if progress.capabilities.resume_support is ResumeSupport.UNSUPPORTED:
            raise ProviderUnsupportedError(
                "Pause is unavailable because resume is unsupported",
                provider=self.name,
                command=progress.command,
                operation_id=operation_id,
            )
        try:
            paused = self._progress.pause(operation_id)
        except ValueError as exc:
            raise ProviderValidationError(
                str(exc),
                provider=self.name,
                command=progress.command,
                operation_id=operation_id,
                cause=exc,
            ) from exc
        return self._handle(paused)

    def resume_operation(
        self,
        operation_id: str,
        *,
        resume_token: str | None = None,
        offset_bytes: int | None = None,
        block_size_bytes: int | None = None,
    ) -> ProviderOperationHandle:
        """Resume according to declared capabilities.

        Args:
            operation_id: Stable operation UUID.
            resume_token: Optional cursor or client token.
            offset_bytes: Optional non-decreasing byte offset.
            block_size_bytes: Optional block-size override.

        Returns:
            Resuming operation handle.
        """
        progress = self.get_progress(operation_id)
        if progress.capabilities.resume_support is ResumeSupport.UNSUPPORTED:
            raise ProviderUnsupportedError(
                "Resume is unsupported for this operation",
                provider=self.name,
                command=progress.command,
                operation_id=operation_id,
            )
        try:
            resumed = self._progress.resume(
                operation_id,
                resume_token=resume_token,
                offset=offset_bytes,
                block_size=block_size_bytes,
            )
        except ValueError as exc:
            raise ProviderValidationError(
                str(exc),
                provider=self.name,
                command=progress.command,
                operation_id=operation_id,
                cause=exc,
            ) from exc
        return self._handle(resumed)

    def cancel_operation(self, operation_id: str) -> ProviderOperationHandle:
        """Cancel a mutable operation.

        Args:
            operation_id: Stable operation UUID.

        Returns:
            Cancelled operation handle.
        """
        progress = self.get_progress(operation_id)
        try:
            cancelled = self._progress.cancel(operation_id)
        except ValueError as exc:
            raise ProviderValidationError(
                str(exc),
                provider=self.name,
                command=progress.command,
                operation_id=operation_id,
                cause=exc,
            ) from exc
        return self._handle(cancelled)

    async def execute(
        self,
        request: ProviderRequest,
        operation_id: str | None = None,
    ) -> NormalizedDataset:
        """Execute the common observable provider lifecycle.

        Args:
            request: Immutable provider request.
            operation_id: Optional paused operation to resume.

        Returns:
            Normalized dataset with common and source provenance.
        """
        command_spec = self._resolve_command(request.command)
        self._validate_common_request(request, command_spec)
        resolved_operation_id = operation_id or request.resume_operation_id
        if resolved_operation_id:
            handle = self._prepare_existing_operation(
                request,
                command_spec,
                resolved_operation_id,
            )
        else:
            handle = self.begin_operation(request)
        timeout_seconds = request.timeout_seconds or self._timeout_seconds
        max_retries = (
            self._max_retries if request.max_retries is None else request.max_retries
        )
        retry_backoff = (
            self._retry_backoff_seconds
            if request.retry_backoff_seconds is None
            else request.retry_backoff_seconds
        )
        rate_delay = (
            self._rate_limit_delay_seconds
            if request.rate_limit_delay_seconds is None
            else request.rate_limit_delay_seconds
        )
        for attempt in range(1, max_retries + 2):
            context = ProviderContext(
                provider_name=self.name,
                request=request,
                command_spec=command_spec,
                operation_id=handle.operation_id,
                attempt=attempt,
                started_at=datetime.now(timezone.utc),
            )
            self._progress.update(
                handle.operation_id,
                state=OperationState.CONNECTING,
                attempt=attempt,
                message=f"attempt {attempt} connecting",
            )
            error: ProviderError | None = None
            try:
                await self._wait_for_rate_limit(rate_delay)
                async with asyncio.timeout(timeout_seconds):
                    raw_response = await self.fetch_raw(request, context)
                if not isinstance(raw_response, RawProviderResponse):
                    raise ProviderValidationError(
                        "fetch_raw must return RawProviderResponse",
                        provider=self.name,
                        command=request.command,
                        operation_id=handle.operation_id,
                    )
                self._apply_raw_progress(context, raw_response)
                normalized = self.normalize(raw_response, context)
                normalized = self._validate_result(
                    normalized,
                    request,
                    context,
                    raw_response,
                )
                completed = self._progress.complete(
                    handle.operation_id,
                    message="completed",
                )
                provenance = self._build_provenance(
                    request,
                    context,
                    raw_response,
                    completed,
                )
                return normalized.with_provenance(provenance)
            except asyncio.CancelledError:
                self._cancel_after_task_cancellation(handle.operation_id)
                raise
            except TimeoutError as exc:
                error = ProviderTimeoutError(
                    f"Provider attempt exceeded {timeout_seconds} seconds",
                    provider=self.name,
                    command=request.command,
                    operation_id=handle.operation_id,
                    cause=exc,
                )
            except ProviderError as exc:
                error = self._enrich_error(exc, request, handle.operation_id)
            except Exception as exc:  # noqa: BLE001
                error = ProviderError(
                    f"Unhandled provider failure: {type(exc).__name__}: {exc}",
                    code="provider_unhandled_error",
                    retryable=False,
                    provider=self.name,
                    command=request.command,
                    operation_id=handle.operation_id,
                    cause=exc,
                )
            if error.retryable and attempt <= max_retries:
                self._progress.update(
                    handle.operation_id,
                    state=OperationState.CONNECTING,
                    attempt=attempt + 1,
                    message=f"retrying after {error.code}",
                    error_code=error.code,
                )
                delay = retry_backoff * (2 ** (attempt - 1))
                if delay:
                    await self._sleep(delay)
                continue
            self._fail_operation(handle.operation_id, error)
            raise error
        raise ProviderError(
            "Provider lifecycle exhausted without a result",
            code="provider_lifecycle_exhausted",
            provider=self.name,
            command=request.command,
            operation_id=handle.operation_id,
        )

    def report_progress(
        self,
        context: ProviderContext,
        *,
        bytes_received: int | None = None,
        current_offset: int | None = None,
        total_bytes: int | None = None,
        resume_token: str | None = None,
        state: OperationState | None = OperationState.RUNNING,
        message: str | None = None,
    ) -> DownloadProgress:
        """Expose the common tracker to streaming implementations.

        Args:
            context: Immutable execution context.
            bytes_received: Optional absolute byte count.
            current_offset: Optional absolute transfer offset.
            total_bytes: Optional known total byte count.
            resume_token: Optional cursor or client token.
            state: Optional non-terminal state.
            message: Optional status message.

        Returns:
            Updated immutable progress snapshot.
        """
        try:
            return self._progress.update(
                context.operation_id,
                bytes_received=bytes_received,
                current_offset=current_offset,
                total_bytes=total_bytes,
                resume_token=resume_token,
                state=state,
                attempt=context.attempt,
                message=message,
            )
        except ValueError as exc:
            raise ProviderValidationError(
                str(exc),
                provider=self.name,
                command=context.request.command,
                operation_id=context.operation_id,
                cause=exc,
            ) from exc

    def _validate_common_request(
        self,
        request: ProviderRequest,
        command_spec: ProviderCommandSpec,
    ) -> None:
        """Apply common and provider-specific validation.

        Args:
            request: Immutable provider request.
            command_spec: Resolved command declaration.

        Returns:
            None.
        """
        if request.provider != self.name:
            raise ProviderValidationError(
                f"Request provider {request.provider!r} does not match {self.name!r}"
            )
        if request.output_format not in command_spec.output_formats:
            raise ProviderValidationError(
                f"Command {request.command!r} does not support "
                f"{request.output_format!r}"
            )
        request.validate_capabilities(command_spec.transfer_capabilities)
        self.validate_request(request, command_spec)

    def _resolve_command(self, command: str) -> ProviderCommandSpec:
        """Resolve one command and reject duplicates.

        Args:
            command: Requested command identifier.

        Returns:
            Matching immutable command declaration.
        """
        matches = [spec for spec in self.commands() if spec.name == command]
        if not matches:
            raise ProviderUnsupportedError(
                f"Unknown command {command!r} for provider {self.name!r}",
                provider=self.name,
                command=command,
            )
        if len(matches) > 1:
            raise ProviderConfigurationError(
                f"Provider {self.name!r} declares {command!r} more than once",
                provider=self.name,
                command=command,
            )
        return matches[0]

    def _prepare_existing_operation(
        self,
        request: ProviderRequest,
        command_spec: ProviderCommandSpec,
        operation_id: str,
    ) -> ProviderOperationHandle:
        """Validate and resume an existing paused operation.

        Args:
            request: Validated provider request.
            command_spec: Resolved command declaration.
            operation_id: Existing operation UUID.

        Returns:
            Resuming operation handle.
        """
        progress = self.get_progress(operation_id)
        if progress.provider != self.name or progress.command != request.command:
            raise ProviderValidationError(
                "Existing operation does not match provider and command",
                provider=self.name,
                command=request.command,
                operation_id=operation_id,
            )
        if progress.capabilities != command_spec.transfer_capabilities:
            raise ProviderValidationError(
                "Existing operation capabilities do not match command",
                provider=self.name,
                command=request.command,
                operation_id=operation_id,
            )
        if progress.state is not OperationState.PAUSED:
            raise ProviderValidationError(
                "Only a paused operation may be resumed by execute",
                provider=self.name,
                command=request.command,
                operation_id=operation_id,
            )
        offset = request.offset_bytes if request.offset_bytes else None
        return self.resume_operation(
            operation_id,
            resume_token=request.resume_token,
            offset_bytes=offset,
            block_size_bytes=request.block_size_bytes,
        )

    async def _wait_for_rate_limit(self, delay_seconds: float) -> None:
        """Serialize request starts and enforce spacing.

        Args:
            delay_seconds: Non-negative minimum delay between starts.

        Returns:
            None.
        """
        if delay_seconds <= 0:
            return
        async with self._rate_lock:
            now = self._monotonic()
            wait_seconds = max(0.0, self._next_allowed_at - now)
            if wait_seconds:
                await self._sleep(wait_seconds)
            self._next_allowed_at = self._monotonic() + delay_seconds

    def _apply_raw_progress(
        self,
        context: ProviderContext,
        response: RawProviderResponse,
    ) -> DownloadProgress:
        """Merge raw-response telemetry into the tracker.

        Args:
            context: Immutable execution context.
            response: Validated raw provider response.

        Returns:
            Updated immutable progress snapshot.
        """
        current = self.get_progress(context.operation_id)
        return self.report_progress(
            context,
            bytes_received=max(current.bytes_received, response.bytes_received),
            current_offset=max(current.current_offset, response.current_offset),
            total_bytes=response.total_bytes,
            resume_token=response.resume_token,
            state=OperationState.RUNNING,
            message="raw response received",
        )

    def _validate_result(
        self,
        result: NormalizedDataset,
        request: ProviderRequest,
        context: ProviderContext,
        response: RawProviderResponse,
    ) -> NormalizedDataset:
        """Validate the normalized result.

        Args:
            result: Concrete provider result.
            request: Original provider request.
            context: Immutable execution context.
            response: Raw provider response.

        Returns:
            Validated normalized dataset.
        """
        if not isinstance(result, NormalizedDataset):
            raise ProviderNormalizationError(
                "normalize must return NormalizedDataset",
                provider=self.name,
                command=request.command,
                operation_id=context.operation_id,
            )
        if result.output_format != request.output_format:
            raise ProviderNormalizationError(
                "Normalized output_format does not match the request",
                provider=self.name,
                command=request.command,
                operation_id=context.operation_id,
            )
        if response.payload is None and result.data is None and not result.files:
            raise ProviderNormalizationError(
                "Provider returned no payload, data, or files",
                provider=self.name,
                command=request.command,
                operation_id=context.operation_id,
            )
        return result

    def _build_provenance(
        self,
        request: ProviderRequest,
        context: ProviderContext,
        response: RawProviderResponse,
        completed: DownloadProgress,
    ) -> dict[str, Any]:
        """Build common provenance without hiding raw metadata.

        Args:
            request: Original provider request.
            context: Immutable execution context.
            response: Raw response with source provenance.
            completed: Terminal progress snapshot.

        Returns:
            Combined source, client, request, and operation provenance.
        """
        descriptor = self.descriptor
        provenance = dict(response.provenance)
        provenance.update(
            {
                "provider": self.name,
                "command": request.command,
                "operation_id": context.operation_id,
                "attempt": context.attempt,
                "output_format": request.output_format,
                "request": request.to_dict(),
                "client_strategy": descriptor.client_strategy,
                "client_name": descriptor.client_name,
                "client_version": descriptor.client_version,
                "repository_url": descriptor.repository_url,
                "license": descriptor.license,
                "research_decision_ref": descriptor.research_decision_ref,
                "started_at": completed.started_at.isoformat(),
                "completed_at": completed.updated_at.isoformat(),
                "bytes_received": completed.bytes_received,
                "total_bytes": completed.total_bytes,
                "current_offset": completed.current_offset,
                "progress_support": completed.support.value,
                "resume_support": completed.capabilities.resume_support.value,
                "raw_status_code": response.status_code,
                "raw_headers": dict(response.headers),
                "raw_media_type": response.media_type,
            }
        )
        return provenance

    def _enrich_error(
        self,
        error: ProviderError,
        request: ProviderRequest,
        operation_id: str,
    ) -> ProviderError:
        """Fill missing context on a stable provider error.

        Args:
            error: Provider error from a concrete implementation.
            request: Original provider request.
            operation_id: Current operation UUID.

        Returns:
            The same error with missing context filled.
        """
        if error.provider is None:
            error.provider = self.name
        if error.command is None:
            error.command = request.command
        if error.operation_id is None:
            error.operation_id = operation_id
        return error

    def _fail_operation(
        self,
        operation_id: str,
        error: ProviderError,
    ) -> None:
        """Record terminal failure without replacing the error.

        Args:
            operation_id: Stable operation UUID.
            error: Stable provider error to record.

        Returns:
            None.
        """
        try:
            self._progress.fail(
                operation_id,
                error_code=error.code,
                message=str(error),
            )
        except ValueError:
            return

    def _cancel_after_task_cancellation(self, operation_id: str) -> None:
        """Record cancellation while preserving asyncio semantics.

        Args:
            operation_id: Stable operation UUID.

        Returns:
            None.
        """
        try:
            self._progress.cancel(
                operation_id,
                message="asyncio task cancelled",
            )
        except ValueError:
            return

    @staticmethod
    def _handle(progress: DownloadProgress) -> ProviderOperationHandle:
        """Build an operation handle from progress.

        Args:
            progress: Current immutable progress snapshot.

        Returns:
            Operation handle.
        """
        return ProviderOperationHandle(
            operation_id=progress.operation_id,
            provider=progress.provider,
            command=progress.command,
            capabilities=progress.capabilities,
            created_at=progress.started_at,
            progress=progress,
        )


class BaseHttpProvider(BaseScientificProvider):
    """Abstract streaming HTTP provider helper profile."""

    def build_http_headers(
        self,
        request: ProviderRequest,
        command_spec: ProviderCommandSpec,
    ) -> dict[str, str]:
        """Build Range headers from offset and block-size controls.

        Args:
            request: Provider request with transfer controls.
            command_spec: Resolved command declaration.

        Returns:
            HTTP headers for a concrete streaming client.
        """
        request.validate_capabilities(command_spec.transfer_capabilities)
        if request.offset_bytes == 0 and request.block_size_bytes is None:
            return {}
        if not command_spec.supports_offset:
            raise ProviderUnsupportedError(
                "HTTP range control is unsupported",
                provider=self.name,
                command=request.command,
            )
        start = request.offset_bytes
        if request.block_size_bytes is None:
            value = f"bytes={start}-"
        else:
            value = f"bytes={start}-{start + request.block_size_bytes - 1}"
        return {"Range": value}

    def report_http_response(
        self,
        context: ProviderContext,
        headers: Mapping[str, str],
    ) -> DownloadProgress:
        """Report Content-Length and range support.

        Args:
            context: Immutable execution context.
            headers: Raw response headers.

        Returns:
            Updated progress snapshot.
        """
        normalized = {key.lower(): value for key, value in headers.items()}
        total_bytes = self._http_total_bytes(normalized)
        if context.request.offset_bytes and not self._http_accepts_ranges(normalized):
            raise ProviderTransportError(
                "HTTP source did not confirm byte-range support",
                retryable=False,
                provider=self.name,
                command=context.request.command,
                operation_id=context.operation_id,
            )
        return self.report_progress(
            context,
            total_bytes=total_bytes,
            state=OperationState.RUNNING,
            message="HTTP response opened",
        )

    def report_http_chunk(
        self,
        context: ProviderContext,
        chunk_size: int,
        *,
        current_offset: int | None = None,
    ) -> DownloadProgress:
        """Report one streamed HTTP chunk.

        Args:
            context: Immutable execution context.
            chunk_size: Non-negative bytes in the chunk.
            current_offset: Optional absolute offset after the chunk.

        Returns:
            Updated progress snapshot.
        """
        if chunk_size < 0:
            raise ProviderValidationError("chunk_size must be >= 0")
        current = self.get_progress(context.operation_id)
        offset = (
            current.current_offset + chunk_size
            if current_offset is None
            else current_offset
        )
        return self.report_progress(
            context,
            bytes_received=current.bytes_received + chunk_size,
            current_offset=offset,
            state=OperationState.RUNNING,
            message="HTTP chunk received",
        )

    @staticmethod
    def _http_total_bytes(headers: Mapping[str, str]) -> int | None:
        """Extract total bytes from HTTP headers.

        Args:
            headers: Lowercase HTTP response headers.

        Returns:
            Known total bytes or None.
        """
        content_range = headers.get("content-range")
        if content_range and "/" in content_range:
            total = content_range.rsplit("/", 1)[1].strip()
            if total.isdigit():
                return int(total)
        content_length = headers.get("content-length")
        if content_length and content_length.strip().isdigit():
            return int(content_length.strip())
        return None

    @staticmethod
    def _http_accepts_ranges(headers: Mapping[str, str]) -> bool:
        """Report whether HTTP headers confirm byte ranges.

        Args:
            headers: Lowercase HTTP response headers.

        Returns:
            True when Accept-Ranges or Content-Range confirms bytes.
        """
        accepts = headers.get("accept-ranges", "").strip().lower()
        content_range = headers.get("content-range", "").strip().lower()
        return accepts == "bytes" or content_range.startswith("bytes ")


class BaseTapProvider(BaseScientificProvider):
    """Abstract TAP provider helper with honest job telemetry."""

    def report_tap_phase(
        self,
        context: ProviderContext,
        phase: str,
    ) -> DownloadProgress:
        """Map a TAP phase without inventing percentage.

        Args:
            context: Immutable execution context.
            phase: Provider-reported TAP or UWS phase.

        Returns:
            Updated progress snapshot.
        """
        normalized = phase.strip().upper()
        if normalized in {"PENDING", "QUEUED", "HELD"}:
            state = OperationState.QUEUED
        elif normalized in {"EXECUTING", "RUN", "RUNNING"}:
            state = OperationState.RUNNING
        elif normalized in {"ABORTED", "ERROR", "FAILED"}:
            raise ProviderTransportError(
                f"TAP job entered terminal phase {normalized}",
                retryable=False,
                provider=self.name,
                command=context.request.command,
                operation_id=context.operation_id,
            )
        else:
            state = OperationState.RUNNING
        return self.report_progress(
            context,
            state=state,
            message=f"TAP phase: {normalized or 'UNKNOWN'}",
        )

    def report_tap_download(
        self,
        context: ProviderContext,
        *,
        bytes_received: int,
        total_bytes: int | None = None,
        current_offset: int | None = None,
    ) -> DownloadProgress:
        """Report absolute TAP result-download counters.

        Args:
            context: Immutable execution context.
            bytes_received: Absolute downloaded bytes.
            total_bytes: Optional known result size.
            current_offset: Optional absolute offset.

        Returns:
            Updated progress snapshot.
        """
        return self.report_progress(
            context,
            bytes_received=bytes_received,
            total_bytes=total_bytes,
            current_offset=current_offset,
            state=OperationState.RUNNING,
            message="TAP result download",
        )


class BaseFileDatasetProvider(BaseScientificProvider):
    """Abstract file-dataset provider helper profile."""

    def report_file_size(
        self,
        context: ProviderContext,
        size_bytes: int,
    ) -> DownloadProgress:
        """Report a known source file size.

        Args:
            context: Immutable execution context.
            size_bytes: Non-negative source file size.

        Returns:
            Updated progress snapshot.
        """
        if size_bytes < 0:
            raise ProviderValidationError("size_bytes must be >= 0")
        return self.report_progress(
            context,
            total_bytes=size_bytes,
            state=OperationState.RUNNING,
            message="file size known",
        )

    def report_file_chunk(
        self,
        context: ProviderContext,
        chunk_size: int,
    ) -> DownloadProgress:
        """Report one file chunk and advance counters.

        Args:
            context: Immutable execution context.
            chunk_size: Non-negative transferred bytes.

        Returns:
            Updated progress snapshot.
        """
        if chunk_size < 0:
            raise ProviderValidationError("chunk_size must be >= 0")
        current = self.get_progress(context.operation_id)
        return self.report_progress(
            context,
            bytes_received=current.bytes_received + chunk_size,
            current_offset=current.current_offset + chunk_size,
            state=OperationState.RUNNING,
            message="file chunk received",
        )


class BaseLibraryProvider(BaseScientificProvider):
    """Abstract selected-library provider helper profile."""

    def report_library_callback(
        self,
        context: ProviderContext,
        *,
        bytes_received: int,
        total_bytes: int | None = None,
        current_offset: int | None = None,
        resume_token: str | None = None,
    ) -> DownloadProgress:
        """Translate an official-client callback into common telemetry.

        Args:
            context: Immutable execution context.
            bytes_received: Absolute bytes reported by the client.
            total_bytes: Optional client-reported total bytes.
            current_offset: Optional absolute client offset.
            resume_token: Optional client-managed resume token.

        Returns:
            Updated progress snapshot.
        """
        capabilities = context.command_spec.transfer_capabilities
        if capabilities.progress_support is ProgressSupport.UNSUPPORTED:
            raise ProviderUnsupportedError(
                "Selected library declares callbacks unsupported",
                provider=self.name,
                command=context.request.command,
                operation_id=context.operation_id,
            )
        return self.report_progress(
            context,
            bytes_received=bytes_received,
            total_bytes=total_bytes,
            current_offset=current_offset,
            resume_token=resume_token,
            state=OperationState.RUNNING,
            message="library progress callback",
        )


def create_provider(
    factory: Callable[[], BaseScientificProvider],
) -> BaseScientificProvider:
    """Create and validate a fully constructed concrete provider.

    Args:
        factory: Zero-argument provider factory without import side effects.

    Returns:
        Fully constructed non-abstract provider.
    """
    provider = factory()
    if not isinstance(provider, BaseScientificProvider):
        raise ProviderConfigurationError(
            "create_provider factory must return BaseScientificProvider"
        )
    if getattr(type(provider), "__abstractmethods__", frozenset()):
        raise ProviderConfigurationError(
            "create_provider factory returned an abstract provider"
        )
    return provider


__all__ = [
    "CLIENT_STRATEGIES",
    "PROVIDER_NAME",
    "SUPPORTED_OUTPUT_FORMATS",
    "BaseFileDatasetProvider",
    "BaseHttpProvider",
    "BaseLibraryProvider",
    "BaseScientificProvider",
    "BaseTapProvider",
    "NormalizedDataset",
    "ProviderCommandSpec",
    "ProviderConfigurationError",
    "ProviderContext",
    "ProviderDescriptor",
    "ProviderError",
    "ProviderNormalizationError",
    "ProviderOperationHandle",
    "ProviderOperationNotFoundError",
    "ProviderRateLimitError",
    "ProviderRequest",
    "ProviderTimeoutError",
    "ProviderTransportError",
    "ProviderUnsupportedError",
    "ProviderValidationError",
    "RawProviderResponse",
    "create_provider",
]
