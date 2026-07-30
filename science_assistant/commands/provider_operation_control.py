"""Provider-agnostic pause, resume, and cancel command."""

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


from science_assistant.commands.provider_progress_command import (  # type: ignore
    _progress_document,
)
from science_assistant.progress import (  # type: ignore[import-not-found]
    ResumeSupport,
    TransferCapabilities,
)
from science_assistant.provider_contract import (  # type: ignore[import-not-found]
    ProviderError,
    ProviderOperationNotFoundError,
    ProviderUnsupportedError,
)
from science_assistant.provider_registry import (  # type: ignore[import-not-found]
    ProviderRegistry,
)

_ACTIONS = frozenset({"pause", "resume", "cancel"})
_RESUME_FIELDS = ("offset_bytes", "block_size_bytes", "resume_token")


def _validation_error(field_name: str, message: str) -> CommandResult:
    """Build one stable validation-error envelope.

    Args:
        field_name: Invalid input field or field group.
        message: Human-readable validation failure.

    Returns:
        Failed command result.
    """
    return CommandResult(
        success=False,
        error="VALIDATION_ERROR",
        data={"field": field_name, "message": message},
    )


def _optional_integer(
    values: dict[str, Any],
    field_name: str,
    *,
    minimum: int,
) -> tuple[int | None, CommandResult | None]:
    """Read one optional strict integer parameter.

    Args:
        values: Mutable command input mapping.
        field_name: Optional integer field name.
        minimum: Inclusive minimum accepted value.

    Returns:
        Parsed value and optional validation error.
    """
    raw = values.get(field_name)
    if raw is None:
        return None, None
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None, _validation_error(
            field_name,
            f"{field_name} must be an integer",
        )
    if raw < minimum:
        return None, _validation_error(
            field_name,
            f"{field_name} must be >= {minimum}",
        )
    return raw, None


def _optional_token(
    values: dict[str, Any],
) -> tuple[str | None, CommandResult | None]:
    """Read the optional non-empty resume token.

    Args:
        values: Mutable command input mapping.

    Returns:
        Normalized token and optional validation error.
    """
    if "resume_token" not in values or values["resume_token"] is None:
        return None, None
    token = str(values["resume_token"]).strip()
    if not token:
        return None, _validation_error(
            "resume_token",
            "resume_token must not be empty",
        )
    return token, None


def _resume_error(code: str, message: str) -> CommandResult:
    """Build one stable unsupported-resume envelope.

    Args:
        code: Stable command error code.
        message: Human-readable resume failure.

    Returns:
        Failed command result.
    """
    return CommandResult(
        success=False,
        error=code,
        data={"message": message},
    )


def _validate_resume_controls(
    capabilities: TransferCapabilities,
    *,
    offset_bytes: int | None,
    block_size_bytes: int | None,
    resume_token: str | None,
) -> CommandResult | None:
    """Validate generic resume controls against declared capabilities.

    Args:
        capabilities: Operation transfer-capability declaration.
        offset_bytes: Optional requested byte offset.
        block_size_bytes: Optional requested transfer block size.
        resume_token: Optional provider cursor or client token.

    Returns:
        Validation failure or None when resume may be delegated.
    """
    support = capabilities.resume_support
    if support is ResumeSupport.UNSUPPORTED:
        return _resume_error(
            "RESUME_UNSUPPORTED",
            "the operation does not support resume",
        )
    if support is ResumeSupport.BYTE_RANGE:
        if resume_token is not None:
            return _validation_error(
                "resume_token",
                "byte-range resume does not accept resume_token",
            )
        try:
            if offset_bytes is not None:
                capabilities.validate_offset(offset_bytes)
            if block_size_bytes is not None:
                capabilities.validate_block_size(block_size_bytes)
        except ValueError as exc:
            return _validation_error("resume_parameters", str(exc))
        return None
    if support is ResumeSupport.CURSOR:
        if offset_bytes is not None or block_size_bytes is not None:
            return _validation_error(
                "resume_parameters",
                "cursor resume does not accept offset_bytes or block_size_bytes",
            )
        if resume_token is None:
            return _resume_error(
                "RESUME_TOKEN_REQUIRED",
                "cursor resume requires resume_token",
            )
        return None
    if support is ResumeSupport.CLIENT_MANAGED:
        if offset_bytes is not None or block_size_bytes is not None:
            return _validation_error(
                "resume_parameters",
                "client-managed resume does not accept offset or block controls",
            )
        return None
    return _resume_error(
        "RESUME_UNSUPPORTED",
        f"unknown resume capability: {support.value}",
    )


class ScientificProviderOperationControlCommand(Command):
    """Control one registered scientific-provider operation.

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

    name: ClassVar[str] = "scientific_provider_operation_control"
    version: ClassVar[str] = "1.0.0"
    descr: ClassVar[str] = (
        "Pause, resume, or cancel one scientific-provider operation through "
        "the common provider contract."
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
            Closed JSON Schema for generic operation control.
        """
        return {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "minLength": 1},
                "operation_id": {"type": "string", "minLength": 1},
                "action": {
                    "type": "string",
                    "enum": ["pause", "resume", "cancel"],
                },
                "offset_bytes": {"type": "integer", "minimum": 0},
                "block_size_bytes": {"type": "integer", "minimum": 1},
                "resume_token": {"type": "string", "minLength": 1},
            },
            "required": ["provider", "operation_id", "action"],
            "additionalProperties": False,
        }

    @classmethod
    def metadata(cls) -> dict[str, Any]:
        """Return command help metadata.

        Returns:
            Parameters, result semantics, examples, and stable errors.
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
                    "description": "Registered scientific provider name.",
                },
                "operation_id": {
                    "type": "string",
                    "required": True,
                    "description": "Stable provider operation identifier.",
                },
                "action": {
                    "type": "string",
                    "required": True,
                    "enum": ["pause", "resume", "cancel"],
                },
                "offset_bytes": {
                    "type": "integer",
                    "required": False,
                    "minimum": 0,
                },
                "block_size_bytes": {
                    "type": "integer",
                    "required": False,
                    "minimum": 1,
                },
                "resume_token": {
                    "type": "string",
                    "required": False,
                },
            },
            "return_value": {
                "success": {
                    "description": "Updated provider-independent DownloadProgress.",
                },
                "error": {
                    "description": "Validation, lookup, or control failure.",
                    "code": (
                        "VALIDATION_ERROR, PROVIDER_NOT_FOUND, "
                        "OPERATION_NOT_FOUND, RESUME_UNSUPPORTED, "
                        "RESUME_TOKEN_REQUIRED, OPERATION_UNSUPPORTED, "
                        "or PROVIDER_OPERATION_ERROR"
                    ),
                },
            },
            "usage_examples": [
                {
                    "description": "Pause an active operation",
                    "command": {
                        "provider": "example-provider",
                        "operation_id": "operation-uuid",
                        "action": "pause",
                    },
                },
                {
                    "description": "Resume a byte-range operation",
                    "command": {
                        "provider": "example-provider",
                        "operation_id": "operation-uuid",
                        "action": "resume",
                        "offset_bytes": 1048576,
                        "block_size_bytes": 262144,
                    },
                },
            ],
            "best_practices": [
                "Read scientific_provider_progress before changing an operation.",
                "Pass offsets only for byte-range resume.",
                "Persist cursor tokens outside command logs when they are sensitive.",
            ],
        }

    async def execute(self, **kwargs: Any) -> CommandResult:
        """Apply one common operation-control action without direct network work.

        Args:
            **kwargs: Provider, operation, action, and optional resume controls.

        Returns:
            Updated progress or a stable error envelope.
        """
        kwargs.pop("context", None)
        provider_name = str(kwargs.get("provider", "")).strip()
        operation_id = str(kwargs.get("operation_id", "")).strip()
        action = str(kwargs.get("action", "")).strip().lower()
        if not provider_name:
            return _validation_error("provider", "provider must not be empty")
        if not operation_id:
            return _validation_error(
                "operation_id",
                "operation_id must not be empty",
            )
        if action not in _ACTIONS:
            return _validation_error(
                "action",
                "action must be pause, resume, or cancel",
            )
        offset_bytes, error = _optional_integer(
            kwargs,
            "offset_bytes",
            minimum=0,
        )
        if error is not None:
            return error
        block_size_bytes, error = _optional_integer(
            kwargs,
            "block_size_bytes",
            minimum=1,
        )
        if error is not None:
            return error
        resume_token, error = _optional_token(kwargs)
        if error is not None:
            return error
        if action != "resume" and any(name in kwargs for name in _RESUME_FIELDS):
            return _validation_error(
                "resume_parameters",
                "resume controls are valid only when action=resume",
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
            if action == "pause":
                handle = provider.pause_operation(operation_id)
            elif action == "cancel":
                handle = provider.cancel_operation(operation_id)
            else:
                current = provider.get_progress(operation_id)
                error = _validate_resume_controls(
                    current.capabilities,
                    offset_bytes=offset_bytes,
                    block_size_bytes=block_size_bytes,
                    resume_token=resume_token,
                )
                if error is not None:
                    return error
                handle = provider.resume_operation(
                    operation_id,
                    resume_token=resume_token,
                    offset_bytes=offset_bytes,
                    block_size_bytes=block_size_bytes,
                )
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
        except ProviderUnsupportedError as exc:
            return CommandResult(
                success=False,
                error="OPERATION_UNSUPPORTED",
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
                error="PROVIDER_OPERATION_ERROR",
                data={
                    "provider": provider_name,
                    "operation_id": operation_id,
                    "provider_code": exc.code,
                    "message": str(exc),
                },
            )
        return CommandResult(
            success=True,
            data=_progress_document(handle.progress),
        )


__all__ = ["ScientificProviderOperationControlCommand"]
