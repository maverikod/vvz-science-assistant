"""Provider-agnostic command for reading scientific operation progress."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from mcp_proxy_adapter.commands.base import (  # type: ignore[import-not-found]
        Command,
        CommandResult,
    )
else:
    try:
        from mcp_proxy_adapter.commands.base import Command, CommandResult
    except ModuleNotFoundError as exc:
        if exc.name != "mcp_proxy_adapter":
            raise

        class Command:
            """Import-only compatibility base without the adapter."""

        @dataclass(slots=True)
        class CommandResult:
            """Import-only compatibility result envelope.

            Attributes:
                success: Whether command execution succeeded.
                data: JSON-compatible result or diagnostic fields.
                error: Optional stable error code.
            """

            success: bool
            data: dict[str, Any] = field(default_factory=dict)
            error: str | None = None


from science_assistant.progress import (  # type: ignore[import-not-found]
    DownloadProgress,
    ProgressSupport,
    ResumeSupport,
)
from science_assistant.provider_contract import (  # type: ignore[import-not-found]
    ProviderError,
    ProviderOperationNotFoundError,
    ProviderUnsupportedError,
)
from science_assistant.provider_registry import (  # type: ignore[import-not-found]
    ProviderRegistry,
)


def _progress_document(progress: DownloadProgress) -> dict[str, Any]:
    """Build the stable JSON-compatible progress response.

    Args:
        progress: Immutable provider progress snapshot.

    Returns:
        Complete provider-independent progress telemetry.
    """
    capabilities = progress.capabilities
    resume_support = capabilities.resume_support
    exact_percentage = (
        progress.percent
        if progress.support is ProgressSupport.EXACT
        and progress.total_bytes is not None
        else None
    )
    return {
        "operation_id": progress.operation_id,
        "provider": progress.provider,
        "command": progress.command,
        "state": progress.state.value,
        "progress_support": progress.support.value,
        "resume_support": resume_support.value,
        "supports_percentage": capabilities.supports_percentage,
        "supports_resume": resume_support is not ResumeSupport.UNSUPPORTED,
        "supports_offset": capabilities.supports_offset,
        "supports_block_size": capabilities.supports_block_size,
        "bytes_received": progress.bytes_received,
        "total_bytes": progress.total_bytes,
        "percent": exact_percentage,
        "started_at": progress.started_at.isoformat(),
        "updated_at": progress.updated_at.isoformat(),
        "last_activity_at": progress.last_activity_at.isoformat(),
        "elapsed_seconds": progress.elapsed_seconds,
        "seconds_since_activity": progress.seconds_since_activity,
        "instantaneous_speed_bps": progress.instantaneous_speed_bps,
        "average_speed_bps": progress.average_speed_bps,
        "is_stalled": progress.is_stalled,
        "stall_threshold_seconds": progress.stall_threshold_seconds,
        "current_offset": progress.current_offset,
        "requested_block_size": progress.requested_block_size,
        "attempt": progress.attempt,
        "message": progress.message,
        "error_code": progress.error_code,
    }


class ScientificProviderProgressCommand(Command):
    """Read progress from one registered provider without network activity.

    Attributes:
        name: Stable MCP command name.
        version: Command implementation version.
        descr: Human-readable command description.
        category: Command catalog category.
        author: Command author.
        email: Command author contact.
        result_class: Result envelope class.
        _providers: Provider registry used for provider resolution.
    """

    name: ClassVar[str] = "scientific_provider_progress"
    version: ClassVar[str] = "1.0.0"
    descr: ClassVar[str] = (
        "Return provider-independent progress, speed, stall, offset, and resume "
        "telemetry for one scientific data operation."
    )
    category: ClassVar[str] = "science-data"
    author: ClassVar[str] = "Vasiliy Zdanovskiy"
    email: ClassVar[str] = "vasilyvz@gmail.com"
    result_class: ClassVar[type[CommandResult]] = CommandResult

    def __init__(self, provider_registry: ProviderRegistry) -> None:
        """Initialize the command with an admitted provider registry.

        Args:
            provider_registry: Thread-safe provider registry.

        Returns:
            None.
        """
        if not isinstance(provider_registry, ProviderRegistry):
            raise TypeError("provider_registry must be a ProviderRegistry")
        self._providers = provider_registry

    @classmethod
    def get_schema(cls) -> dict[str, Any]:
        """Return the closed command parameter schema.

        Returns:
            Closed JSON Schema for provider and operation_id.
        """
        return {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Registered scientific provider name.",
                },
                "operation_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Operation UUID returned before network work.",
                },
            },
            "required": ["provider", "operation_id"],
            "additionalProperties": False,
        }

    @classmethod
    def metadata(cls) -> dict[str, Any]:
        """Return complete command help metadata.

        Returns:
            Command description, parameters, results, examples, and errors.
        """
        return {
            "name": cls.name,
            "version": cls.version,
            "description": cls.descr,
            "category": cls.category,
            "author": cls.author,
            "email": cls.email,
            "parameters": {
                "provider": {
                    "type": "string",
                    "required": True,
                    "description": ("Registered scientific provider name."),
                },
                "operation_id": {
                    "type": "string",
                    "required": True,
                    "description": ("Stable provider operation identifier."),
                },
            },
            "return_value": {
                "success": {
                    "description": ("Current immutable operation telemetry."),
                    "data": {
                        "state": (
                            "Queued, connecting, running, paused, "
                            "terminal, or unsupported state."
                        ),
                        "progress_support": ("exact, indeterminate, or unsupported."),
                        "resume_support": (
                            "byte_range, cursor, client_managed, " "or unsupported."
                        ),
                        "bytes_received": ("Monotonic received-byte count."),
                        "percent": ("Exact percentage or null when unavailable."),
                        "average_speed_bps": ("Average active-session speed."),
                        "is_stalled": ("Active-state inactivity flag."),
                    },
                },
                "error": {
                    "description": (
                        "Unknown provider, operation, or progress failure."
                    ),
                    "code": (
                        "PROVIDER_NOT_FOUND, OPERATION_NOT_FOUND, "
                        "or PROVIDER_PROGRESS_ERROR."
                    ),
                },
            },
            "usage_examples": [
                {
                    "description": "Read one live download state",
                    "command": {
                        "provider": "example-provider",
                        "operation_id": ("00000000-0000-4000-8000-000000000000"),
                    },
                    "explanation": (
                        "Returns state, timestamps, speeds, stall "
                        "status, offset, block size, and resume "
                        "capabilities without network work."
                    ),
                }
            ],
            "error_cases": {
                "VALIDATION_ERROR": {
                    "description": ("provider or operation_id is empty."),
                    "solution": ("Supply both required non-empty strings."),
                },
                "PROVIDER_NOT_FOUND": {
                    "description": ("The registry has no admitted provider."),
                    "solution": ("Use a provider name returned by discovery."),
                },
                "OPERATION_NOT_FOUND": {
                    "description": ("The provider does not own the operation_id."),
                    "solution": ("Use the operation_id returned when work began."),
                },
                "PROVIDER_PROGRESS_ERROR": {
                    "description": ("The provider rejected progress retrieval."),
                    "solution": ("Inspect provider_code and operation state."),
                },
            },
            "best_practices": [
                ("Persist operation_id before starting a large " "transfer."),
                (
                    "Treat percent=null as honest indeterminate or "
                    "unsupported progress."
                ),
                (
                    "Use last_activity_at and is_stalled to determine "
                    "whether work is alive."
                ),
            ],
        }

    async def execute(self, **kwargs: Any) -> CommandResult:
        """Return current progress without transport-specific or network work.

        Args:
            **kwargs: provider, operation_id, and optional adapter context.

        Returns:
            Successful progress document or stable error envelope.
        """
        kwargs.pop("context", None)
        provider_name = str(kwargs.get("provider", "")).strip()
        operation_id = str(kwargs.get("operation_id", "")).strip()
        if not provider_name:
            return CommandResult(
                success=False,
                error="VALIDATION_ERROR",
                data={"field": "provider"},
            )
        if not operation_id:
            return CommandResult(
                success=False,
                error="VALIDATION_ERROR",
                data={"field": "operation_id"},
            )
        try:
            provider = self._providers.get(provider_name)
        except ProviderUnsupportedError as exc:
            return CommandResult(
                success=False,
                error="PROVIDER_NOT_FOUND",
                data={
                    "provider": provider_name,
                    "provider_code": exc.code,
                    "message": str(exc),
                },
            )
        try:
            progress = provider.get_progress(operation_id)
        except ProviderOperationNotFoundError as exc:
            return CommandResult(
                success=False,
                error="OPERATION_NOT_FOUND",
                data={
                    "provider": provider_name,
                    "operation_id": operation_id,
                    "provider_code": exc.code,
                    "message": str(exc),
                },
            )
        except ProviderError as exc:
            return CommandResult(
                success=False,
                error="PROVIDER_PROGRESS_ERROR",
                data={
                    "provider": provider_name,
                    "operation_id": operation_id,
                    "provider_code": exc.code,
                    "message": str(exc),
                },
            )
        return CommandResult(success=True, data=_progress_document(progress))


__all__ = ["ScientificProviderProgressCommand"]
