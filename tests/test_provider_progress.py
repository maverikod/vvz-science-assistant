"""Deterministic tests for provider progress and resume contracts."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

import science_assistant  # type: ignore[import-not-found]
from science_assistant.progress import (  # type: ignore[import-not-found]
    OperationState,
    ProgressSupport,
    ProgressTracker,
    ResumeSupport,
    TransferCapabilities,
)
from science_assistant.provider_contract import (  # type: ignore[import-not-found]
    BaseScientificProvider,
    NormalizedDataset,
    ProviderCommandSpec,
    ProviderContext,
    ProviderDescriptor,
    ProviderOperationNotFoundError,
    ProviderRequest,
    ProviderUnsupportedError,
    RawProviderResponse,
)
from science_assistant.provider_registry import (  # type: ignore[import-not-found]
    ProviderRegistry,
)


class FakeClock:
    """Wall and monotonic clocks advanced explicitly by tests.

    Attributes:
        wall: Current timezone-aware UTC wall time.
        monotonic_value: Current monotonic-seconds value.
    """

    def __init__(self) -> None:
        """Initialize both clocks at deterministic zero.

        Returns:
            None.
        """
        self.wall = datetime(2026, 1, 1, tzinfo=UTC)
        self.monotonic_value = 0.0

    def now(self) -> datetime:
        """Return current wall time.

        Returns:
            Timezone-aware UTC timestamp.
        """
        return self.wall

    def monotonic(self) -> float:
        """Return current monotonic seconds.

        Returns:
            Monotonic-seconds value.
        """
        return self.monotonic_value

    def advance(self, seconds: float) -> None:
        """Advance both clocks without sleeping.

        Args:
            seconds: Non-negative number of seconds to advance.

        Returns:
            None.
        """
        if seconds < 0:
            raise ValueError("seconds must be non-negative")
        self.wall += timedelta(seconds=seconds)
        self.monotonic_value += seconds


def _byte_range_capabilities() -> TransferCapabilities:
    """Build exact byte-range capabilities with block-size limits.

    Returns:
        Validated byte-range capability declaration.
    """
    return TransferCapabilities(
        progress_support=ProgressSupport.EXACT,
        resume_support=ResumeSupport.BYTE_RANGE,
        supports_offset=True,
        supports_block_size=True,
        min_block_size=64,
        max_block_size=1024,
        default_block_size=256,
    )


def _cursor_capabilities() -> TransferCapabilities:
    """Build indeterminate cursor-resume capabilities.

    Returns:
        Validated cursor capability declaration.
    """
    return TransferCapabilities(
        progress_support=ProgressSupport.INDETERMINATE,
        resume_support=ResumeSupport.CURSOR,
    )


def _client_managed_capabilities() -> TransferCapabilities:
    """Build client-managed resume capabilities.

    Returns:
        Validated client-managed capability declaration.
    """
    return TransferCapabilities(
        progress_support=ProgressSupport.INDETERMINATE,
        resume_support=ResumeSupport.CLIENT_MANAGED,
    )


class FakeProvider(BaseScientificProvider):
    """Network-free provider exposing the common operation lifecycle.

    Attributes:
        _descriptor: Immutable provider descriptor.
        _command_spec: Single fake download command.
    """

    def __init__(
        self,
        capabilities: TransferCapabilities,
        *,
        progress_tracker: ProgressTracker | None = None,
    ) -> None:
        """Initialize a fake provider without optional dependencies.

        Args:
            capabilities: Capabilities for the fake download command.
            progress_tracker: Optional deterministic shared tracker.

        Returns:
            None.
        """
        super().__init__(progress_tracker=progress_tracker)
        self._descriptor = ProviderDescriptor(
            name="fake",
            description="Network-free provider for unit tests",
            default_capabilities=capabilities,
            client_strategy="direct_api",
            client_name="fake-client",
            client_version="1.0",
            repository_url="https://example.invalid/fake",
            license="MIT",
            research_decision_ref="docs/research/providers/fake.md",
        )
        self._command_spec = ProviderCommandSpec(
            name="download",
            description="Return deterministic fake bytes",
            transfer_capabilities=capabilities,
        )

    @property
    def name(self) -> str:
        """Return stable provider name.

        Returns:
            Fake provider name.
        """
        return "fake"

    @property
    def descriptor(self) -> ProviderDescriptor:
        """Return immutable provider descriptor.

        Returns:
            Fake provider descriptor.
        """
        return self._descriptor

    def commands(self) -> tuple[ProviderCommandSpec, ...]:
        """Return the single fake command.

        Returns:
            One-element immutable command tuple.
        """
        return (self._command_spec,)

    def validate_request(
        self,
        request: ProviderRequest,
        command_spec: ProviderCommandSpec,
    ) -> None:
        """Accept the already validated fake request.

        Args:
            request: Provider request under test.
            command_spec: Resolved fake command specification.

        Returns:
            None.
        """
        if request.command != command_spec.name:
            raise ValueError("request command does not match command spec")

    async def fetch_raw(
        self,
        request: ProviderRequest,
        context: ProviderContext,
    ) -> RawProviderResponse:
        """Return deterministic bytes without network work.

        Args:
            request: Provider request under test.
            context: Common provider execution context.

        Returns:
            Deterministic raw provider response.
        """
        del request, context
        return RawProviderResponse(
            payload=b"fake",
            bytes_received=4,
            total_bytes=4,
            current_offset=4,
        )

    def normalize(
        self,
        response: RawProviderResponse,
        context: ProviderContext,
    ) -> NormalizedDataset:
        """Normalize deterministic bytes.

        Args:
            response: Raw fake response.
            context: Common provider execution context.

        Returns:
            Normalized in-memory dataset.
        """
        return NormalizedDataset(
            data=response.payload,
            output_format=context.request.output_format,
            provenance={"serialized_format": context.request.output_format},
        )


class FakeRegistry(ProviderRegistry):
    """Minimal ProviderRegistry subtype for command tests.

    Attributes:
        _fake_providers: Providers keyed by stable name.
    """

    def __init__(self, *providers: BaseScientificProvider) -> None:
        """Store fake providers without running admission checks.

        Args:
            *providers: Fake providers exposed by get().

        Returns:
            None.
        """
        self._fake_providers = {provider.name: provider for provider in providers}

    def get(self, provider_name: str) -> BaseScientificProvider:
        """Resolve a fake provider or raise the stable registry error.

        Args:
            provider_name: Stable provider name.

        Returns:
            Matching fake provider.
        """
        try:
            return self._fake_providers[provider_name]
        except KeyError as exc:
            raise ProviderUnsupportedError(
                "provider is not registered",
                provider=provider_name,
            ) from exc


def _load_module(name: str, path: Path) -> ModuleType:
    """Load one module from a source file under a canonical name.

    Args:
        name: Canonical module name.
        path: Exact Python source file.

    Returns:
        Loaded module object.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@contextmanager
def _command_modules() -> Iterator[tuple[ModuleType, ModuleType]]:
    """Load progress/control modules without executing commands/__init__.py.

    Yields:
        Progress and operation-control command modules.
    """
    package_name = "science_assistant.commands"
    progress_name = f"{package_name}.provider_progress_command"
    control_name = f"{package_name}.provider_operation_control"
    names = (package_name, progress_name, control_name)
    saved = {name: sys.modules.get(name) for name in names}
    previous_attribute = getattr(science_assistant, "commands", None)
    package_file = science_assistant.__file__
    if package_file is None:
        raise RuntimeError("science_assistant package has no __file__")
    commands_dir = Path(package_file).resolve().parent / "commands"
    package = ModuleType(package_name)
    package.__path__ = [str(commands_dir)]
    sys.modules[package_name] = package
    science_assistant.commands = package
    try:
        progress_module = _load_module(
            progress_name,
            commands_dir / "provider_progress_command.py",
        )
        control_module = _load_module(
            control_name,
            commands_dir / "provider_operation_control.py",
        )
        yield progress_module, control_module
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        if previous_attribute is None:
            delattr(science_assistant, "commands")
        else:
            science_assistant.commands = previous_attribute


def _request(capabilities: TransferCapabilities) -> ProviderRequest:
    """Build one deterministic provider request.

    Args:
        capabilities: Command capabilities used to select controls.

    Returns:
        Validated fake provider request.
    """
    block_size = 256 if capabilities.supports_block_size else None
    return ProviderRequest(
        provider="fake",
        command="download",
        params={},
        output_format="ECSV",
        block_size_bytes=block_size,
    )


def test_capability_validation_and_progress_modes() -> None:
    """Capabilities and exact/indeterminate/unsupported percentages are honest."""
    with pytest.raises(ValueError, match="requires supports_offset"):
        TransferCapabilities(resume_support=ResumeSupport.BYTE_RANGE)
    with pytest.raises(ValueError, match="require supports_block_size"):
        TransferCapabilities(min_block_size=64)
    capabilities = _byte_range_capabilities()
    capabilities.validate_offset(128)
    capabilities.validate_block_size(64)
    capabilities.validate_block_size(1024)
    with pytest.raises(ValueError, match="offset must be"):
        capabilities.validate_offset(-1)
    with pytest.raises(ValueError, match="block_size must be >= 64"):
        capabilities.validate_block_size(63)
    with pytest.raises(ValueError, match="block_size must be <= 1024"):
        capabilities.validate_block_size(1025)

    clock = FakeClock()
    exact_tracker = ProgressTracker(clock=clock.now, monotonic=clock.monotonic)
    exact = exact_tracker.begin(
        provider="fake",
        command="download",
        capabilities=capabilities,
        total_bytes=0,
    )
    assert exact.percent == 0.0
    assert exact_tracker.complete(exact.operation_id).percent == 100.0
    for support in (ProgressSupport.INDETERMINATE, ProgressSupport.UNSUPPORTED):
        tracker = ProgressTracker(clock=clock.now, monotonic=clock.monotonic)
        snapshot = tracker.begin(
            provider="fake",
            command="download",
            capabilities=TransferCapabilities(progress_support=support),
            total_bytes=100,
        )
        snapshot = tracker.update(snapshot.operation_id, bytes_received=50)
        assert snapshot.percent is None


def test_lifecycle_speed_stall_pause_resume_and_terminal_immutability() -> None:
    """Lifecycle telemetry is deterministic and excludes paused time from speed."""
    clock = FakeClock()
    tracker = ProgressTracker(
        sample_window_size=4,
        clock=clock.now,
        monotonic=clock.monotonic,
    )
    initial = tracker.begin(
        provider="fake",
        command="download",
        capabilities=_byte_range_capabilities(),
        total_bytes=1000,
        block_size=256,
        stall_threshold_seconds=2.0,
    )
    operation_id = initial.operation_id
    assert initial.started_at == initial.updated_at == initial.last_activity_at
    clock.advance(1.0)
    connecting = tracker.update(operation_id, state=OperationState.CONNECTING)
    assert connecting.last_activity_at == initial.last_activity_at
    clock.advance(1.0)
    running = tracker.update(
        operation_id,
        bytes_received=100,
        current_offset=100,
        state=OperationState.RUNNING,
    )
    assert running.percent == pytest.approx(10.0)
    assert running.instantaneous_speed_bps == pytest.approx(50.0)
    assert running.average_speed_bps == pytest.approx(50.0)
    clock.advance(2.1)
    stalled = tracker.get(operation_id)
    assert stalled.is_stalled is True
    assert stalled.seconds_since_activity == pytest.approx(2.1)
    clock.advance(0.9)
    active = tracker.update(
        operation_id,
        bytes_received=200,
        current_offset=200,
    )
    assert active.is_stalled is False
    assert active.seconds_since_activity == 0.0
    paused = tracker.pause(operation_id, message="operator pause")
    paused_average = paused.average_speed_bps
    clock.advance(10.0)
    still_paused = tracker.get(operation_id)
    assert still_paused.elapsed_seconds == pytest.approx(15.0)
    assert still_paused.average_speed_bps == pytest.approx(paused_average)
    assert still_paused.is_stalled is False
    resumed = tracker.resume(operation_id, offset=200, block_size=512)
    assert resumed.state is OperationState.RESUMING
    clock.advance(2.0)
    tracker.update(
        operation_id,
        bytes_received=1000,
        current_offset=1000,
    )
    completed = tracker.complete(operation_id)
    assert completed.percent == 100.0
    assert completed.average_speed_bps == pytest.approx(1000 / 7)
    with pytest.raises(ValueError, match="terminal and immutable"):
        tracker.update(operation_id, bytes_received=1000)
    document = json.loads(tracker.to_json(operation_id))
    assert document["state"] == "completed"
    assert document["capabilities"]["supports_percentage"] is True


def test_resume_modes_monotonic_guards_and_terminal_states() -> None:
    """Resume modes, monotonic fields, unknown ids, and terminal states are guarded."""
    clock = FakeClock()
    cursor_tracker = ProgressTracker(clock=clock.now, monotonic=clock.monotonic)
    cursor = cursor_tracker.begin(
        provider="fake",
        command="download",
        capabilities=_cursor_capabilities(),
    )
    cursor_tracker.pause(cursor.operation_id)
    with pytest.raises(ValueError, match="requires a resume_token"):
        cursor_tracker.resume(cursor.operation_id)
    assert (
        cursor_tracker.resume(
            cursor.operation_id,
            resume_token="cursor-1",
        ).resume_token
        == "cursor-1"
    )

    managed_tracker = ProgressTracker(clock=clock.now, monotonic=clock.monotonic)
    managed = managed_tracker.begin(
        provider="fake",
        command="download",
        capabilities=_client_managed_capabilities(),
    )
    managed_tracker.pause(managed.operation_id)
    assert managed_tracker.resume(managed.operation_id).state is OperationState.RESUMING

    unsupported_tracker = ProgressTracker(clock=clock.now, monotonic=clock.monotonic)
    unsupported = unsupported_tracker.begin(
        provider="fake",
        command="download",
        capabilities=TransferCapabilities(),
    )
    unsupported_tracker.pause(unsupported.operation_id)
    with pytest.raises(ValueError, match="explicitly unsupported"):
        unsupported_tracker.resume(unsupported.operation_id)

    tracker = ProgressTracker()
    snapshot = tracker.begin(
        provider="fake",
        command="download",
        capabilities=_byte_range_capabilities(),
        total_bytes=1000,
    )
    tracker.update(
        snapshot.operation_id,
        bytes_received=100,
        current_offset=100,
        attempt=2,
    )
    with pytest.raises(ValueError, match="bytes_received cannot decrease"):
        tracker.update(snapshot.operation_id, bytes_received=99)
    with pytest.raises(ValueError, match="current_offset cannot decrease"):
        tracker.update(snapshot.operation_id, current_offset=99)
    with pytest.raises(ValueError, match="total_bytes cannot decrease"):
        tracker.update(snapshot.operation_id, total_bytes=999)
    with pytest.raises(ValueError, match="attempt cannot decrease"):
        tracker.update(snapshot.operation_id, attempt=1)
    with pytest.raises(KeyError, match="Unknown operation_id"):
        tracker.get("missing")

    for action in ("cancel", "complete"):
        local = ProgressTracker()
        item = local.begin(
            provider="fake",
            command="download",
            capabilities=_byte_range_capabilities(),
        )
        getattr(local, action)(item.operation_id)
        with pytest.raises(ValueError, match="terminal and immutable"):
            local.pause(item.operation_id)
    failed_tracker = ProgressTracker()
    failed = failed_tracker.begin(
        provider="fake",
        command="download",
        capabilities=_byte_range_capabilities(),
    )
    terminal = failed_tracker.fail(failed.operation_id, error_code="FAKE")
    assert terminal.state is OperationState.FAILED
    assert terminal.error_code == "FAKE"


def test_base_provider_exposes_pre_network_handle_and_common_controls() -> None:
    """Base provider owns creation, lookup, pause, resume, and cancellation."""
    clock = FakeClock()
    tracker = ProgressTracker(clock=clock.now, monotonic=clock.monotonic)
    provider = FakeProvider(
        _byte_range_capabilities(),
        progress_tracker=tracker,
    )
    handle = provider.begin_operation(_request(_byte_range_capabilities()))
    assert handle.operation_id
    assert handle.progress.state is OperationState.QUEUED
    assert handle.progress.bytes_received == 0
    assert (
        provider.pause_operation(handle.operation_id).progress.state
        is OperationState.PAUSED
    )
    resumed = provider.resume_operation(
        handle.operation_id,
        offset_bytes=0,
        block_size_bytes=512,
    )
    assert resumed.progress.state is OperationState.RESUMING
    assert (
        provider.cancel_operation(handle.operation_id).progress.state
        is OperationState.CANCELLED
    )
    with pytest.raises(ProviderOperationNotFoundError):
        provider.get_progress("missing")
    unsupported = FakeProvider(TransferCapabilities())
    unsupported_handle = unsupported.begin_operation(_request(TransferCapabilities()))
    with pytest.raises(ProviderUnsupportedError):
        unsupported.pause_operation(unsupported_handle.operation_id)


def test_progress_and_control_commands_are_network_free_and_capability_aware() -> None:
    """Commands serialize progress and enforce every resume mechanism."""
    with _command_modules() as (progress_module, control_module):
        progress_class = progress_module.ScientificProviderProgressCommand
        control_class = control_module.ScientificProviderOperationControlCommand
        capabilities = _byte_range_capabilities()
        provider = FakeProvider(capabilities)
        operation = provider.begin_operation(_request(capabilities))
        registry = FakeRegistry(provider)
        progress_command = progress_class(registry)
        control_command = control_class(registry)
        assert progress_command.get_schema()["additionalProperties"] is False
        assert control_command.get_schema()["additionalProperties"] is False
        progress_result = asyncio.run(
            progress_command.execute(
                provider="fake",
                operation_id=operation.operation_id,
            )
        )
        assert progress_result.success is True
        assert progress_result.data["state"] == "queued"
        assert progress_result.data["percent"] is None
        paused = asyncio.run(
            control_command.execute(
                provider="fake",
                operation_id=operation.operation_id,
                action="pause",
            )
        )
        assert paused.data["state"] == "paused"
        resumed = asyncio.run(
            control_command.execute(
                provider="fake",
                operation_id=operation.operation_id,
                action="resume",
                offset_bytes=0,
                block_size_bytes=512,
            )
        )
        assert resumed.data["state"] == "resuming"
        cancelled = asyncio.run(
            control_command.execute(
                provider="fake",
                operation_id=operation.operation_id,
                action="cancel",
            )
        )
        assert cancelled.data["state"] == "cancelled"
        unknown = asyncio.run(
            progress_command.execute(provider="fake", operation_id="missing")
        )
        assert unknown.error == "OPERATION_NOT_FOUND"
        missing_provider = asyncio.run(
            progress_class(FakeRegistry()).execute(
                provider="missing",
                operation_id="missing",
            )
        )
        assert missing_provider.error == "PROVIDER_NOT_FOUND"

        cursor_provider = FakeProvider(_cursor_capabilities())
        cursor_operation = cursor_provider.begin_operation(
            _request(_cursor_capabilities())
        )
        cursor_provider.pause_operation(cursor_operation.operation_id)
        cursor_command = control_class(FakeRegistry(cursor_provider))
        missing_token = asyncio.run(
            cursor_command.execute(
                provider="fake",
                operation_id=cursor_operation.operation_id,
                action="resume",
            )
        )
        assert missing_token.error == "RESUME_TOKEN_REQUIRED"
        cursor_result = asyncio.run(
            cursor_command.execute(
                provider="fake",
                operation_id=cursor_operation.operation_id,
                action="resume",
                resume_token="cursor-1",
            )
        )
        assert cursor_result.success is True

        managed_provider = FakeProvider(_client_managed_capabilities())
        managed_operation = managed_provider.begin_operation(
            _request(_client_managed_capabilities())
        )
        managed_provider.pause_operation(managed_operation.operation_id)
        managed_result = asyncio.run(
            control_class(FakeRegistry(managed_provider)).execute(
                provider="fake",
                operation_id=managed_operation.operation_id,
                action="resume",
            )
        )
        assert managed_result.success is True

        unsupported_provider = FakeProvider(TransferCapabilities())
        unsupported_operation = unsupported_provider.begin_operation(
            _request(TransferCapabilities())
        )
        unsupported_result = asyncio.run(
            control_class(FakeRegistry(unsupported_provider)).execute(
                provider="fake",
                operation_id=unsupported_operation.operation_id,
                action="resume",
            )
        )
        assert unsupported_result.error == "RESUME_UNSUPPORTED"

        range_provider = FakeProvider(_byte_range_capabilities())
        range_operation = range_provider.begin_operation(
            _request(_byte_range_capabilities())
        )
        range_provider.pause_operation(range_operation.operation_id)
        invalid_token = asyncio.run(
            control_class(FakeRegistry(range_provider)).execute(
                provider="fake",
                operation_id=range_operation.operation_id,
                action="resume",
                resume_token="invalid-for-range",
            )
        )
        assert invalid_token.error == "VALIDATION_ERROR"
