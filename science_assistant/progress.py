"""Thread-safe progress telemetry for scientific data operations."""

from __future__ import annotations

import json
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from threading import RLock
from typing import Any


class ProgressSupport(StrEnum):
    """Accuracy level available for operation progress reporting.

    Attributes:
        EXACT: Exact byte percentage is available when total size is known.
        INDETERMINATE: Activity is observable but percentage is unavailable.
        UNSUPPORTED: The source exposes no progress telemetry.
    """

    EXACT = "exact"
    INDETERMINATE = "indeterminate"
    UNSUPPORTED = "unsupported"


class ResumeSupport(StrEnum):
    """Resume mechanism offered by a provider or selected client.

    Attributes:
        BYTE_RANGE: Resume by requesting a byte range from a known offset.
        CURSOR: Resume by supplying a provider-issued cursor token.
        CLIENT_MANAGED: Resume is delegated to an official or selected client.
        UNSUPPORTED: Resume attempts must be rejected explicitly.
    """

    BYTE_RANGE = "byte_range"
    CURSOR = "cursor"
    CLIENT_MANAGED = "client_managed"
    UNSUPPORTED = "unsupported"


class OperationState(StrEnum):
    """Lifecycle state of a data acquisition operation.

    Attributes:
        QUEUED: The operation exists but has not started network work.
        CONNECTING: The operation is establishing a transport connection.
        RUNNING: Data is actively being acquired.
        PAUSED: Active elapsed time is suspended until resume.
        RESUMING: The operation is restoring a prior transfer position.
        COMPLETED: The operation finished successfully and is immutable.
        FAILED: The operation ended with an error and is immutable.
        CANCELLED: The operation was cancelled and is immutable.
        UNSUPPORTED: The requested operation is unsupported and immutable.
    """

    QUEUED = "queued"
    CONNECTING = "connecting"
    RUNNING = "running"
    PAUSED = "paused"
    RESUMING = "resuming"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNSUPPORTED = "unsupported"


_TERMINAL_STATES = frozenset(
    {
        OperationState.COMPLETED,
        OperationState.FAILED,
        OperationState.CANCELLED,
        OperationState.UNSUPPORTED,
    }
)
_STALL_ELIGIBLE_STATES = frozenset(
    {
        OperationState.CONNECTING,
        OperationState.RUNNING,
        OperationState.RESUMING,
    }
)


@dataclass(frozen=True, slots=True)
class TransferCapabilities:
    """Immutable transfer features and source-imposed limits.

    Attributes:
        progress_support: Accuracy level of observable transfer progress.
        resume_support: Resume mechanism declared by the source or client.
        supports_offset: Whether callers may choose a non-zero byte offset.
        supports_block_size: Whether callers may choose transfer block size.
        min_block_size: Smallest accepted block size in bytes.
        max_block_size: Largest accepted block size in bytes.
        default_block_size: Source or client default block size in bytes.
        source_constraints: Human-readable source-specific restrictions.
        supports_percentage: Whether exact percentage may be computed.
    """

    progress_support: ProgressSupport = ProgressSupport.UNSUPPORTED
    resume_support: ResumeSupport = ResumeSupport.UNSUPPORTED
    supports_offset: bool = False
    supports_block_size: bool = False
    min_block_size: int | None = None
    max_block_size: int | None = None
    default_block_size: int | None = None
    source_constraints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate capability combinations and block-size limits.

        Returns:
            None.
        """
        if self.resume_support is ResumeSupport.BYTE_RANGE and not self.supports_offset:
            raise ValueError("ResumeSupport.byte_range requires supports_offset=True")

        limits = (self.min_block_size, self.max_block_size, self.default_block_size)
        if any(value is not None and value <= 0 for value in limits):
            raise ValueError("Block-size limits must be positive integers")

        if not self.supports_block_size and any(value is not None for value in limits):
            raise ValueError("Block-size limits require supports_block_size=True")

        if (
            self.min_block_size is not None
            and self.max_block_size is not None
            and self.min_block_size > self.max_block_size
        ):
            raise ValueError("min_block_size cannot exceed max_block_size")

        if self.default_block_size is not None:
            self.validate_block_size(self.default_block_size)

    @property
    def supports_percentage(self) -> bool:
        """Report whether exact percentage may be computed.

        Returns:
            True only for exact progress support.
        """
        return self.progress_support is ProgressSupport.EXACT

    def validate_offset(self, offset: int) -> None:
        """Validate an operation offset against declared capabilities.

        Args:
            offset: Requested non-negative byte offset.

        Returns:
            None.
        """
        if offset < 0:
            raise ValueError("offset must be >= 0")
        if offset and not self.supports_offset:
            raise ValueError("This transfer does not support a non-zero offset")

    def validate_block_size(self, block_size: int | None) -> None:
        """Validate a requested transfer block size.

        Args:
            block_size: Requested byte block size, or None for no override.

        Returns:
            None.
        """
        if block_size is None:
            return
        if block_size <= 0:
            raise ValueError("block_size must be > 0")
        if not self.supports_block_size:
            raise ValueError("This transfer does not support block-size control")
        if self.min_block_size is not None and block_size < self.min_block_size:
            raise ValueError(f"block_size must be >= {self.min_block_size}")
        if self.max_block_size is not None and block_size > self.max_block_size:
            raise ValueError(f"block_size must be <= {self.max_block_size}")

    def to_dict(self) -> dict[str, Any]:
        """Build a JSON-compatible capability document.

        Returns:
            Capability fields containing scalar values and lists.
        """
        return {
            "progress_support": self.progress_support.value,
            "supports_percentage": self.supports_percentage,
            "resume_support": self.resume_support.value,
            "supports_offset": self.supports_offset,
            "supports_block_size": self.supports_block_size,
            "min_block_size": self.min_block_size,
            "max_block_size": self.max_block_size,
            "default_block_size": self.default_block_size,
            "source_constraints": list(self.source_constraints),
        }


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    """Immutable snapshot of one data acquisition operation.

    Attributes:
        operation_id: Stable UUID created before network activity.
        provider: Provider identifier that owns the operation.
        command: Command identifier that initiated the operation.
        state: Current lifecycle state.
        support: Accuracy level of progress reporting.
        capabilities: Immutable transfer capability declaration.
        bytes_received: Monotonic bytes received during this operation.
        total_bytes: Known total byte count, or None when unavailable.
        percent: Exact percentage, or None when unsupported or unknown.
        started_at: UTC timestamp at operation creation.
        updated_at: UTC timestamp of the latest state mutation.
        last_activity_at: UTC timestamp of the latest real transfer activity.
        elapsed_seconds: Wall-clock seconds since operation creation.
        seconds_since_activity: Seconds since bytes, offset, or token advanced.
        instantaneous_speed_bps: Speed over the bounded sample window.
        average_speed_bps: Bytes received divided by active elapsed time.
        is_stalled: Whether an active state exceeded the stall threshold.
        stall_threshold_seconds: Inactivity threshold used for stall detection.
        current_offset: Monotonic current source or destination offset.
        requested_block_size: Validated caller-selected block size.
        resume_token: Provider cursor or client resume token.
        attempt: Monotonic attempt number beginning at one.
        message: Optional human-readable operation message.
        error_code: Optional stable failure code.
    """

    operation_id: str
    provider: str
    command: str
    state: OperationState
    support: ProgressSupport
    capabilities: TransferCapabilities
    bytes_received: int
    total_bytes: int | None
    percent: float | None
    started_at: datetime
    updated_at: datetime
    last_activity_at: datetime
    elapsed_seconds: float
    seconds_since_activity: float
    instantaneous_speed_bps: float
    average_speed_bps: float
    is_stalled: bool
    stall_threshold_seconds: float
    current_offset: int
    requested_block_size: int | None
    resume_token: str | None
    attempt: int
    message: str | None
    error_code: str | None

    def to_dict(self) -> dict[str, Any]:
        """Build a JSON-compatible progress document.

        Returns:
            Progress fields with ISO-8601 timestamps.
        """
        return {
            "operation_id": self.operation_id,
            "provider": self.provider,
            "command": self.command,
            "state": self.state.value,
            "support": self.support.value,
            "capabilities": self.capabilities.to_dict(),
            "bytes_received": self.bytes_received,
            "total_bytes": self.total_bytes,
            "percent": self.percent,
            "started_at": self.started_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_activity_at": self.last_activity_at.isoformat(),
            "elapsed_seconds": self.elapsed_seconds,
            "seconds_since_activity": self.seconds_since_activity,
            "instantaneous_speed_bps": self.instantaneous_speed_bps,
            "average_speed_bps": self.average_speed_bps,
            "is_stalled": self.is_stalled,
            "stall_threshold_seconds": self.stall_threshold_seconds,
            "current_offset": self.current_offset,
            "requested_block_size": self.requested_block_size,
            "resume_token": self.resume_token,
            "attempt": self.attempt,
            "message": self.message,
            "error_code": self.error_code,
        }

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize this snapshot as JSON.

        Args:
            indent: Optional indentation width.

        Returns:
            UTF-8-safe JSON text.
        """
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


@dataclass(slots=True)
class _ProgressRecord:
    """Mutable internal state guarded by ProgressTracker._lock.

    Attributes:
        operation_id: Stable operation UUID.
        provider: Provider identifier.
        command: Initiating command identifier.
        state: Mutable operation lifecycle state.
        capabilities: Immutable transfer capabilities.
        bytes_received: Monotonic received-byte counter.
        total_bytes: Known total bytes when available.
        started_at: UTC operation creation timestamp.
        updated_at: UTC latest mutation timestamp.
        last_activity_at: UTC latest real activity timestamp.
        started_monotonic: Monotonic operation start time.
        active_started_monotonic: Start of the current active interval.
        accumulated_active_seconds: Completed active intervals in seconds.
        last_activity_monotonic: Monotonic latest activity time.
        samples: Bounded speed sample window.
        stall_threshold_seconds: Inactivity threshold for stall detection.
        current_offset: Monotonic transfer offset.
        requested_block_size: Validated requested block size.
        resume_token: Current provider or client resume token.
        attempt: Monotonic attempt number.
        message: Optional human-readable message.
        error_code: Optional stable error code.
    """

    operation_id: str
    provider: str
    command: str
    state: OperationState
    capabilities: TransferCapabilities
    bytes_received: int
    total_bytes: int | None
    started_at: datetime
    updated_at: datetime
    last_activity_at: datetime
    started_monotonic: float
    active_started_monotonic: float | None
    accumulated_active_seconds: float
    last_activity_monotonic: float
    samples: deque[tuple[float, int]]
    stall_threshold_seconds: float
    current_offset: int
    requested_block_size: int | None
    resume_token: str | None
    attempt: int
    message: str | None = None
    error_code: str | None = None


class ProgressTracker:
    """Create and update monotonic progress records under a lock.

    Attributes:
        _sample_window_size: Maximum number of instantaneous-speed samples.
        _clock: Callable returning timezone-aware UTC timestamps.
        _monotonic: Callable returning monotonic seconds.
        _records: Mutable records keyed by operation UUID.
        _lock: Re-entrant lock guarding all records.
    """

    def __init__(
        self,
        *,
        sample_window_size: int = 8,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        """Initialize the tracker with injectable clocks.

        Args:
            sample_window_size: Bounded speed-sample window, at least two.
            clock: Optional timezone-aware wall-clock callable.
            monotonic: Optional monotonic-seconds callable.

        Returns:
            None.
        """
        if sample_window_size < 2:
            raise ValueError("sample_window_size must be >= 2")
        self._sample_window_size = sample_window_size
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._records: dict[str, _ProgressRecord] = {}
        self._lock = RLock()

    def begin(
        self,
        *,
        provider: str,
        command: str,
        capabilities: TransferCapabilities,
        total_bytes: int | None = None,
        offset: int = 0,
        block_size: int | None = None,
        resume_token: str | None = None,
        attempt: int = 1,
        stall_threshold_seconds: float = 60.0,
        state: OperationState = OperationState.QUEUED,
    ) -> DownloadProgress:
        """Create an operation record before any network activity.

        Args:
            provider: Non-empty provider identifier.
            command: Non-empty command identifier.
            capabilities: Declared source and client transfer capabilities.
            total_bytes: Known total bytes, or None when unavailable.
            offset: Initial non-negative transfer offset.
            block_size: Optional caller-selected transfer block size.
            resume_token: Optional provider cursor or client token.
            attempt: Attempt number beginning at one.
            stall_threshold_seconds: Active-state inactivity threshold.
            state: Initial non-terminal, non-paused lifecycle state.

        Returns:
            Initial immutable progress snapshot.
        """
        if not provider.strip():
            raise ValueError("provider must not be empty")
        if not command.strip():
            raise ValueError("command must not be empty")
        if total_bytes is not None and total_bytes < 0:
            raise ValueError("total_bytes must be >= 0")
        if attempt < 1:
            raise ValueError("attempt must be >= 1")
        if stall_threshold_seconds < 0:
            raise ValueError("stall_threshold_seconds must be >= 0")
        if state in _TERMINAL_STATES or state is OperationState.PAUSED:
            raise ValueError("begin requires a non-terminal, non-paused state")

        capabilities.validate_offset(offset)
        requested_block_size = block_size
        if requested_block_size is None and capabilities.supports_block_size:
            requested_block_size = capabilities.default_block_size
        capabilities.validate_block_size(requested_block_size)
        self._validate_resume_token(capabilities, resume_token, required=False)

        now = self._clock()
        monotonic_now = self._monotonic()
        operation_id = str(uuid.uuid4())
        record = _ProgressRecord(
            operation_id=operation_id,
            provider=provider.strip(),
            command=command.strip(),
            state=state,
            capabilities=capabilities,
            bytes_received=0,
            total_bytes=total_bytes,
            started_at=now,
            updated_at=now,
            last_activity_at=now,
            started_monotonic=monotonic_now,
            active_started_monotonic=monotonic_now,
            accumulated_active_seconds=0.0,
            last_activity_monotonic=monotonic_now,
            samples=deque([(monotonic_now, 0)], maxlen=self._sample_window_size),
            stall_threshold_seconds=float(stall_threshold_seconds),
            current_offset=offset,
            requested_block_size=requested_block_size,
            resume_token=resume_token,
            attempt=attempt,
        )
        with self._lock:
            self._records[operation_id] = record
            return self._snapshot(record, now=now, monotonic_now=monotonic_now)

    def get(self, operation_id: str) -> DownloadProgress:
        """Return the current immutable snapshot for an operation.

        Args:
            operation_id: Stable operation UUID returned by begin.

        Returns:
            Current progress snapshot.
        """
        with self._lock:
            record = self._require(operation_id)
            return self._snapshot(record)

    def update(
        self,
        operation_id: str,
        *,
        bytes_received: int | None = None,
        current_offset: int | None = None,
        total_bytes: int | None = None,
        state: OperationState | None = None,
        resume_token: str | None = None,
        attempt: int | None = None,
        message: str | None = None,
        error_code: str | None = None,
    ) -> DownloadProgress:
        """Apply a monotonic non-terminal update.

        Args:
            operation_id: Stable operation UUID returned by begin.
            bytes_received: New absolute received-byte count.
            current_offset: New absolute source or destination offset.
            total_bytes: New known total that may not decrease.
            state: Optional non-terminal, non-paused lifecycle state.
            resume_token: Optional new cursor or client token.
            attempt: Optional attempt number that may not decrease.
            message: Optional human-readable status message.
            error_code: Optional non-terminal diagnostic code.

        Returns:
            Updated immutable progress snapshot.
        """
        with self._lock:
            record = self._require(operation_id)
            self._ensure_mutable(record)
            if state in _TERMINAL_STATES:
                raise ValueError(
                    "Use complete(), fail(), or cancel() for terminal states"
                )
            if state is OperationState.PAUSED:
                raise ValueError("Use pause() to enter the paused state")

            now = self._clock()
            monotonic_now = self._monotonic()
            activity = False

            if bytes_received is not None:
                if bytes_received < record.bytes_received:
                    raise ValueError("bytes_received cannot decrease")
                activity = activity or bytes_received > record.bytes_received
                record.bytes_received = bytes_received

            if current_offset is not None:
                record.capabilities.validate_offset(current_offset)
                if current_offset < record.current_offset:
                    raise ValueError("current_offset cannot decrease")
                activity = activity or current_offset > record.current_offset
                record.current_offset = current_offset

            if total_bytes is not None:
                if total_bytes < 0:
                    raise ValueError("total_bytes must be >= 0")
                if record.total_bytes is not None and total_bytes < record.total_bytes:
                    raise ValueError("total_bytes cannot decrease")
                record.total_bytes = total_bytes

            progress_bytes = max(record.bytes_received, record.current_offset)
            if record.total_bytes is not None and progress_bytes > record.total_bytes:
                raise ValueError("Progress cannot exceed total_bytes")

            if resume_token is not None:
                self._validate_resume_token(
                    record.capabilities, resume_token, required=False
                )
                activity = activity or resume_token != record.resume_token
                record.resume_token = resume_token

            if attempt is not None:
                if attempt < record.attempt:
                    raise ValueError("attempt cannot decrease")
                if attempt < 1:
                    raise ValueError("attempt must be >= 1")
                record.attempt = attempt

            if state is not None:
                record.state = state
            elif activity and record.state in {
                OperationState.QUEUED,
                OperationState.CONNECTING,
                OperationState.RESUMING,
            }:
                record.state = OperationState.RUNNING

            record.message = message
            record.error_code = error_code
            record.updated_at = now
            if activity:
                record.last_activity_at = now
                record.last_activity_monotonic = monotonic_now
                record.samples.append((monotonic_now, record.bytes_received))
            return self._snapshot(record, now=now, monotonic_now=monotonic_now)

    def pause(
        self, operation_id: str, *, message: str | None = None
    ) -> DownloadProgress:
        """Pause an active operation and preserve active elapsed time.

        Args:
            operation_id: Stable operation UUID returned by begin.
            message: Optional pause reason.

        Returns:
            Paused progress snapshot.
        """
        with self._lock:
            record = self._require(operation_id)
            self._ensure_mutable(record)
            if record.state is OperationState.PAUSED:
                return self._snapshot(record)
            now = self._clock()
            monotonic_now = self._monotonic()
            self._stop_active_clock(record, monotonic_now)
            record.state = OperationState.PAUSED
            record.message = message
            record.updated_at = now
            return self._snapshot(record, now=now, monotonic_now=monotonic_now)

    def resume(
        self,
        operation_id: str,
        *,
        resume_token: str | None = None,
        offset: int | None = None,
        block_size: int | None = None,
        message: str | None = None,
    ) -> DownloadProgress:
        """Resume a paused operation using its declared mechanism.

        Args:
            operation_id: Stable operation UUID returned by begin.
            resume_token: Provider cursor or selected-client resume token.
            offset: Optional non-decreasing resume offset.
            block_size: Optional validated block-size override.
            message: Optional resume status message.

        Returns:
            Resuming progress snapshot.
        """
        with self._lock:
            record = self._require(operation_id)
            self._ensure_mutable(record)
            if record.state is not OperationState.PAUSED:
                raise ValueError("Only a paused operation can be resumed")
            if record.capabilities.resume_support is ResumeSupport.UNSUPPORTED:
                raise ValueError("Resume is explicitly unsupported")

            if offset is not None:
                record.capabilities.validate_offset(offset)
                if offset < record.current_offset:
                    raise ValueError("offset cannot decrease")
                record.current_offset = offset
            if block_size is not None:
                record.capabilities.validate_block_size(block_size)
                record.requested_block_size = block_size
            if resume_token is not None:
                record.resume_token = resume_token
            self._validate_resume_token(
                record.capabilities,
                record.resume_token,
                required=record.capabilities.resume_support is ResumeSupport.CURSOR,
            )

            now = self._clock()
            monotonic_now = self._monotonic()
            record.state = OperationState.RESUMING
            record.active_started_monotonic = monotonic_now
            record.updated_at = now
            record.message = message
            return self._snapshot(record, now=now, monotonic_now=monotonic_now)

    def complete(
        self, operation_id: str, *, message: str | None = None
    ) -> DownloadProgress:
        """Mark an operation completed and immutable.

        Args:
            operation_id: Stable operation UUID returned by begin.
            message: Optional completion message.

        Returns:
            Terminal completed snapshot.
        """
        return self._finish(operation_id, OperationState.COMPLETED, message=message)

    def fail(
        self,
        operation_id: str,
        *,
        error_code: str,
        message: str | None = None,
    ) -> DownloadProgress:
        """Mark an operation failed with a stable error code.

        Args:
            operation_id: Stable operation UUID returned by begin.
            error_code: Non-empty stable error identifier.
            message: Optional failure message.

        Returns:
            Terminal failed snapshot.
        """
        if not error_code.strip():
            raise ValueError("error_code must not be empty")
        return self._finish(
            operation_id,
            OperationState.FAILED,
            message=message,
            error_code=error_code.strip(),
        )

    def cancel(
        self, operation_id: str, *, message: str | None = None
    ) -> DownloadProgress:
        """Mark an operation cancelled and immutable.

        Args:
            operation_id: Stable operation UUID returned by begin.
            message: Optional cancellation message.

        Returns:
            Terminal cancelled snapshot.
        """
        return self._finish(operation_id, OperationState.CANCELLED, message=message)

    def to_json(self, operation_id: str, *, indent: int | None = None) -> str:
        """Serialize the current operation snapshot as JSON.

        Args:
            operation_id: Stable operation UUID returned by begin.
            indent: Optional indentation width.

        Returns:
            UTF-8-safe JSON text.
        """
        return self.get(operation_id).to_json(indent=indent)

    def _finish(
        self,
        operation_id: str,
        state: OperationState,
        *,
        message: str | None,
        error_code: str | None = None,
    ) -> DownloadProgress:
        """Apply one terminal state transition.

        Args:
            operation_id: Stable operation UUID.
            state: Requested terminal state.
            message: Optional terminal message.
            error_code: Optional stable error code.

        Returns:
            Terminal immutable snapshot.
        """
        with self._lock:
            record = self._require(operation_id)
            self._ensure_mutable(record)
            now = self._clock()
            monotonic_now = self._monotonic()
            self._stop_active_clock(record, monotonic_now)
            record.state = state
            record.updated_at = now
            record.message = message
            record.error_code = error_code
            return self._snapshot(record, now=now, monotonic_now=monotonic_now)

    def _snapshot(
        self,
        record: _ProgressRecord,
        *,
        now: datetime | None = None,
        monotonic_now: float | None = None,
    ) -> DownloadProgress:
        """Build an immutable snapshot from a guarded mutable record.

        Args:
            record: Mutable record guarded by the tracker lock.
            now: Optional wall-clock value reused by the caller.
            monotonic_now: Optional monotonic value reused by the caller.

        Returns:
            Computed immutable progress snapshot.
        """
        now = now or self._clock()
        monotonic_now = self._monotonic() if monotonic_now is None else monotonic_now
        elapsed = max(0.0, monotonic_now - record.started_monotonic)
        active_elapsed = self._active_elapsed(record, monotonic_now)
        seconds_since_activity = max(
            0.0, monotonic_now - record.last_activity_monotonic
        )
        progress_bytes = max(record.bytes_received, record.current_offset)
        percent = self._percent(record, progress_bytes)
        instantaneous_speed = self._instantaneous_speed(record.samples)
        average_speed = (
            record.bytes_received / active_elapsed if active_elapsed > 0 else 0.0
        )
        is_stalled = (
            record.state in _STALL_ELIGIBLE_STATES
            and seconds_since_activity >= record.stall_threshold_seconds
        )
        return DownloadProgress(
            operation_id=record.operation_id,
            provider=record.provider,
            command=record.command,
            state=record.state,
            support=record.capabilities.progress_support,
            capabilities=record.capabilities,
            bytes_received=record.bytes_received,
            total_bytes=record.total_bytes,
            percent=percent,
            started_at=record.started_at,
            updated_at=record.updated_at,
            last_activity_at=record.last_activity_at,
            elapsed_seconds=elapsed,
            seconds_since_activity=seconds_since_activity,
            instantaneous_speed_bps=instantaneous_speed,
            average_speed_bps=max(0.0, average_speed),
            is_stalled=is_stalled,
            stall_threshold_seconds=record.stall_threshold_seconds,
            current_offset=record.current_offset,
            requested_block_size=record.requested_block_size,
            resume_token=record.resume_token,
            attempt=record.attempt,
            message=record.message,
            error_code=record.error_code,
        )

    @staticmethod
    def _percent(record: _ProgressRecord, progress_bytes: int) -> float | None:
        """Compute exact percentage when permitted and possible.

        Args:
            record: Mutable operation record.
            progress_bytes: Monotonic effective progress byte count.

        Returns:
            Exact percentage or None when unavailable.
        """
        if record.capabilities.progress_support is not ProgressSupport.EXACT:
            return None
        if record.total_bytes is None:
            return None
        if record.total_bytes == 0:
            return 100.0 if record.state is OperationState.COMPLETED else 0.0
        return min(100.0, (progress_bytes / record.total_bytes) * 100.0)

    @staticmethod
    def _instantaneous_speed(samples: deque[tuple[float, int]]) -> float:
        """Compute speed over the bounded sample window.

        Args:
            samples: Ordered monotonic-time and byte-count samples.

        Returns:
            Non-negative instantaneous bytes per second.
        """
        if len(samples) < 2:
            return 0.0
        oldest_time, oldest_bytes = samples[0]
        newest_time, newest_bytes = samples[-1]
        duration = newest_time - oldest_time
        if duration <= 0:
            return 0.0
        return max(0.0, (newest_bytes - oldest_bytes) / duration)

    @staticmethod
    def _active_elapsed(record: _ProgressRecord, monotonic_now: float) -> float:
        """Compute active elapsed seconds excluding paused intervals.

        Args:
            record: Mutable operation record.
            monotonic_now: Current monotonic timestamp.

        Returns:
            Non-negative active elapsed seconds.
        """
        active = record.accumulated_active_seconds
        if record.active_started_monotonic is not None:
            active += max(0.0, monotonic_now - record.active_started_monotonic)
        return active

    @staticmethod
    def _stop_active_clock(record: _ProgressRecord, monotonic_now: float) -> None:
        """Close the current active interval if one exists.

        Args:
            record: Mutable operation record.
            monotonic_now: Current monotonic timestamp.

        Returns:
            None.
        """
        if record.active_started_monotonic is not None:
            record.accumulated_active_seconds += max(
                0.0, monotonic_now - record.active_started_monotonic
            )
            record.active_started_monotonic = None

    @staticmethod
    def _validate_resume_token(
        capabilities: TransferCapabilities,
        resume_token: str | None,
        *,
        required: bool,
    ) -> None:
        """Validate token use against the declared resume mechanism.

        Args:
            capabilities: Immutable transfer capabilities.
            resume_token: Provider cursor or client token.
            required: Whether absence of a cursor token is invalid.

        Returns:
            None.
        """
        if capabilities.resume_support is ResumeSupport.CURSOR:
            if required and not resume_token:
                raise ValueError("Cursor resume requires a resume_token")
            return
        if (
            resume_token is not None
            and capabilities.resume_support is ResumeSupport.UNSUPPORTED
        ):
            raise ValueError("resume_token is invalid when resume is unsupported")

    @staticmethod
    def _ensure_mutable(record: _ProgressRecord) -> None:
        """Reject updates to terminal records.

        Args:
            record: Mutable operation record to inspect.

        Returns:
            None.
        """
        if record.state in _TERMINAL_STATES:
            raise ValueError(
                f"Operation is terminal and immutable: {record.state.value}"
            )

    def _require(self, operation_id: str) -> _ProgressRecord:
        """Resolve an operation record or raise a stable key error.

        Args:
            operation_id: Stable operation UUID.

        Returns:
            Mutable operation record guarded by the caller.
        """
        try:
            return self._records[operation_id]
        except KeyError as exc:
            raise KeyError(f"Unknown operation_id: {operation_id}") from exc


__all__ = [
    "DownloadProgress",
    "OperationState",
    "ProgressSupport",
    "ProgressTracker",
    "ResumeSupport",
    "TransferCapabilities",
]
