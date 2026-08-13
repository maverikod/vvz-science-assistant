"""Science Assistant HTTPS MCP server entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, cast

from mcp_proxy_adapter.api.app import create_app
from mcp_proxy_adapter.commands.base import Command, CommandResult
from mcp_proxy_adapter.commands.command_registry import CommandRegistry
from mcp_proxy_adapter.commands.hooks import register_custom_commands_hook
from mcp_proxy_adapter.config import get_config
from mcp_proxy_adapter.core.app_factory.ssl_config import build_server_ssl_config
from mcp_proxy_adapter.core.config.simple_config import SimpleConfig
from mcp_proxy_adapter.core.config.simple_config_validator import SimpleConfigValidator
from mcp_proxy_adapter.core.server_engine import ServerEngineFactory

from science_assistant.commands import COMMAND_TYPES
from science_assistant.commands.info_resources import guide_markdown
from science_assistant.commands.provider_operation_control import (
    ScientificProviderOperationControlCommand,
)
from science_assistant.commands.provider_progress_command import (
    ScientificProviderProgressCommand,
)
from science_assistant.package_info import package_version
from science_assistant.progress import ProgressSupport, ResumeSupport
from science_assistant.provider_contract import (
    BaseScientificProvider,
    ProviderCommandSpec,
    ProviderError,
    ProviderOperationHandle,
    ProviderRequest,
)
from science_assistant.provider_registry import ProviderRegistry, discover_providers

_TLS_SECTIONS = ("server", "client", "registration", "server_validation")
_TLS_KEYS = ("cert", "key", "ca", "crl")
_DEMO_COMMANDS = ("echo", "long_task", "job_status", "roletest")
_COMMON_PROVIDER_COMMAND_NAMES = {
    ScientificProviderProgressCommand.name,
    ScientificProviderOperationControlCommand.name,
}

_PROVIDER_REGISTRY = ProviderRegistry()
_DISCOVERED_PROVIDERS = discover_providers(
    _PROVIDER_REGISTRY,
    "science_assistant.providers",
)
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()
_PROVIDER_START_LOCKS: dict[str, asyncio.Lock] = {}


def _provider_operation_ids(provider: BaseScientificProvider) -> frozenset[str]:
    """Return a stable operation-id snapshot for server-side task handoff."""
    tracker = provider._progress
    with tracker._lock:
        return frozenset(tracker._records)


def _discard_background_task(task: asyncio.Task[Any]) -> None:
    """Observe background exceptions and release the strong task reference."""
    _BACKGROUND_TASKS.discard(task)
    if not task.cancelled():
        task.exception()


def _provider_request(
    provider_name: str,
    command_spec: ProviderCommandSpec,
    values: dict[str, Any],
) -> ProviderRequest:
    """Translate an MCP command payload into the shared provider request."""
    params = dict(values)
    params.pop("context", None)
    output_format = str(params.pop("output_format", command_spec.output_formats[0]))
    limit = params.pop("limit", None)
    offset_bytes = params.pop("offset_bytes", 0)
    block_size_bytes = params.pop("block_size_bytes", None)
    resume_operation_id = params.pop("resume_operation_id", None)
    resume_token = params.pop("resume_token", None)
    timeout_seconds = params.pop("timeout_seconds", None)
    max_retries = params.pop("max_retries", None)
    retry_backoff_seconds = params.pop("retry_backoff_seconds", None)
    rate_limit_delay_seconds = params.pop("rate_limit_delay_seconds", None)
    return ProviderRequest(
        provider=provider_name,
        command=command_spec.name,
        params=params,
        output_format=output_format,
        limit=limit,
        offset_bytes=offset_bytes,
        block_size_bytes=block_size_bytes,
        resume_operation_id=resume_operation_id,
        resume_token=resume_token,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        rate_limit_delay_seconds=rate_limit_delay_seconds,
    )


def _runs_in_background(command_spec: ProviderCommandSpec) -> bool:
    """Return whether a provider command needs an early operation handle."""
    capabilities = command_spec.transfer_capabilities
    return (
        capabilities.progress_support is not ProgressSupport.UNSUPPORTED
        or capabilities.resume_support is not ResumeSupport.UNSUPPORTED
    )


def _make_provider_command_type(
    provider_name: str,
    command_spec: ProviderCommandSpec,
    schema: dict[str, Any],
) -> type[Command]:
    """Build one no-argument MCP command class from a provider declaration."""

    class ScientificProviderCommand(Command):
        name = command_spec.name
        version = "1.0.0"
        descr = command_spec.description
        category = "science-data"
        author = "Vasiliy Zdanovskiy"
        email = "vasilyvz@gmail.com"
        result_class = CommandResult

        @classmethod
        def get_schema(cls) -> dict[str, Any]:
            return dict(schema)

        @classmethod
        def metadata(cls) -> dict[str, Any]:
            return {
                "name": cls.name,
                "version": cls.version,
                "description": cls.descr,
                "category": cls.category,
                "author": cls.author,
                "email": cls.email,
                "provider": provider_name,
                "output_formats": list(command_spec.output_formats),
                "transfer_capabilities": (command_spec.transfer_capabilities.to_dict()),
            }

        async def execute(self, **kwargs: Any) -> CommandResult:
            try:
                provider = _PROVIDER_REGISTRY.get(provider_name)
                request = _provider_request(provider_name, command_spec, kwargs)
                request.validate_capabilities(command_spec.transfer_capabilities)
                provider.validate_request(request, command_spec)
                if not _runs_in_background(command_spec):
                    dataset = await provider.execute(request)
                    return CommandResult(success=True, data=dataset.to_dict())

                start_lock = _PROVIDER_START_LOCKS.setdefault(
                    provider_name,
                    asyncio.Lock(),
                )
                async with start_lock:
                    before = _provider_operation_ids(provider)
                    task = asyncio.create_task(provider.execute(request))
                    _BACKGROUND_TASKS.add(task)
                    task.add_done_callback(_discard_background_task)
                    await asyncio.sleep(0)
                    created = _provider_operation_ids(provider) - before
                if len(created) != 1:
                    task.cancel()
                    return CommandResult(
                        success=False,
                        error="PROVIDER_OPERATION_START_ERROR",
                        data={
                            "provider": provider_name,
                            "command": command_spec.name,
                            "created_operation_count": len(created),
                        },
                    )
                operation_id = next(iter(created))
                progress = provider.get_progress(operation_id)
                handle = ProviderOperationHandle(
                    operation_id=operation_id,
                    provider=provider_name,
                    command=command_spec.name,
                    capabilities=command_spec.transfer_capabilities,
                    created_at=progress.started_at,
                    progress=progress,
                ).to_dict()
                handle["background"] = True
                return CommandResult(success=True, data=handle)
            except ProviderError as exc:
                return CommandResult(
                    success=False,
                    error="SCIENTIFIC_PROVIDER_ERROR",
                    data=exc.to_dict(),
                )
            except (TypeError, ValueError) as exc:
                return CommandResult(
                    success=False,
                    error="VALIDATION_ERROR",
                    data={"message": str(exc)},
                )

    class_name = (
        "ScientificProvider_"
        + provider_name.replace("-", "_")
        + "_"
        + command_spec.name.replace("-", "_")
    )
    ScientificProviderCommand.__name__ = class_name
    ScientificProviderCommand.__qualname__ = class_name
    return ScientificProviderCommand


def _build_provider_command_types() -> tuple[type[Command], ...]:
    """Convert discovered provider command specifications exactly once."""
    reserved_names = {command.name for command in COMMAND_TYPES}
    reserved_names.update(_COMMON_PROVIDER_COMMAND_NAMES)
    command_types: list[type[Command]] = []
    command_names: set[str] = set()
    for provider_name in _PROVIDER_REGISTRY.names():
        registered = _PROVIDER_REGISTRY.get_registered(provider_name)
        for command_spec in registered.provider.commands():
            if (
                command_spec.name in reserved_names
                or command_spec.name in command_names
            ):
                raise RuntimeError(
                    f"duplicate scientific command name: {command_spec.name}"
                )
            command_names.add(command_spec.name)
            command_types.append(
                _make_provider_command_type(
                    provider_name,
                    command_spec,
                    dict(registered.command_schemas[command_spec.name]),
                )
            )
    return tuple(command_types)


class RegisteredScientificProviderProgressCommand(ScientificProviderProgressCommand):
    """Bind the common progress command to the server provider registry."""

    def __init__(self) -> None:
        super().__init__(_PROVIDER_REGISTRY)


class RegisteredScientificProviderOperationControlCommand(
    ScientificProviderOperationControlCommand
):
    """Bind the common control command to the server provider registry."""

    def __init__(self) -> None:
        super().__init__(_PROVIDER_REGISTRY)


_PROVIDER_COMMAND_TYPES = _build_provider_command_types()
_COMMON_PROVIDER_COMMAND_TYPES: tuple[type[Command], ...] = (
    RegisteredScientificProviderProgressCommand,
    RegisteredScientificProviderOperationControlCommand,
)


def _registered_command_types() -> tuple[type[Any], ...]:
    """Return legacy and provider commands in registration order."""
    return (
        tuple(COMMAND_TYPES) + _PROVIDER_COMMAND_TYPES + _COMMON_PROVIDER_COMMAND_TYPES
    )


def _register_science_commands(registry: object) -> None:
    """Register legacy and discovered provider commands idempotently."""
    reg = cast(CommandRegistry, registry)
    with reg._lock:
        for name in _DEMO_COMMANDS:
            reg._commands.pop(name, None)
            reg._instances.pop(name, None)
            reg._command_types.pop(name, None)
    for command_type in _registered_command_types():
        with reg._lock:
            if command_type.name in reg._commands:
                continue
        reg.register(cast(Any, command_type), "custom")


_register_science_commands.__auto_import_modules__ = [
    "science_assistant.commands.info_command",
    "science_assistant.commands.astroquery_catalog_command",
    "science_assistant.commands.astroquery_object_command",
    "science_assistant.commands.astroquery_adql_command",
    "science_assistant.commands.download_file_command",
    "science_assistant.commands.data_transfer_commands",
    "science_assistant.commands.package_transfer_commands",
    "science_assistant.commands.file_commands",
    "science_assistant.commands.provider_progress_command",
    "science_assistant.commands.provider_operation_control",
    "science_assistant.provider_registry",
]
register_custom_commands_hook(_register_science_commands)


def _load_config(path: Path) -> tuple[dict[str, Any], object]:
    path = path.resolve()
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    base = path.parent
    changed = False
    for section_name in _TLS_SECTIONS:
        section = data.get(section_name)
        if not isinstance(section, dict):
            continue
        ssl = section.get("ssl")
        if not isinstance(ssl, dict):
            continue
        for key in _TLS_KEYS:
            raw = ssl.get(key)
            if not isinstance(raw, str) or not raw.strip():
                continue
            candidate = Path(raw)
            if candidate.is_absolute():
                continue
            resolved = (base / candidate).resolve()
            ssl[key] = str(resolved)
            changed = True
    if changed:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    simple = SimpleConfig(str(path))
    model = simple.load()
    errors = SimpleConfigValidator(config_path=str(path)).validate(model)
    if errors:
        raise RuntimeError(
            "Invalid config:\n" + "\n".join(f"- {error.message}" for error in errors)
        )
    simple.model = model
    model_data = model.model_dump() if hasattr(model, "model_dump") else {}
    if isinstance(model_data, dict):
        for section, value in model_data.items():
            data[section] = value

    registration = data.setdefault("registration", {})
    if isinstance(registration, dict):
        metadata = registration.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["server_name"] = "Science Assistant"
            metadata["description"] = (
                "Scientific data gateway: astroquery, TAP/ADQL, "
                "and HTTP/HTTPS/FTP downloads."
            )
            metadata["version"] = package_version()
            metadata["commands"] = [
                {
                    "name": command.name,
                    "description": command.descr,
                    "version": command.version,
                }
                for command in _registered_command_types()
            ]
            metadata["scientific_providers"] = list(_DISCOVERED_PROVIDERS)
            metadata["provider_diagnostics"] = [
                diagnostic.to_dict() for diagnostic in _PROVIDER_REGISTRY.diagnostics()
            ]

    cfg = get_config()
    cfg.config_path = str(path)
    cfg.model = model
    cfg.config_data = data
    if getattr(cfg, "feature_manager", None) is not None:
        cfg.feature_manager.config_data = data
    return data, model


def main() -> None:
    parser = argparse.ArgumentParser(description="Science Assistant MCP HTTPS server")
    parser.add_argument(
        "--config", default="/etc/science-assistant/science-assistant.json"
    )
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    app_config, _ = _load_config(config_path)
    app = create_app(
        title="Science Assistant",
        description=guide_markdown(),
        version=package_version(),
        app_config=app_config,
        config_path=str(config_path),
    )
    server = app_config.get("server", {})
    server_config: dict[str, Any] = {
        "host": str(server.get("host", "0.0.0.0")),
        "port": int(server.get("port", 18180)),
        "log_level": str(server.get("log_level", "INFO")).lower(),
        "reload": False,
    }
    ssl_engine = build_server_ssl_config(app_config)
    if ssl_engine:
        server_config.update(ssl_engine)
    engine = ServerEngineFactory.get_engine("hypercorn")
    if engine is None:
        raise RuntimeError("Hypercorn engine unavailable")
    engine.run_server(app, server_config)


if __name__ == "__main__":
    main()
