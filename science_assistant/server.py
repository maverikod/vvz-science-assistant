"""Science Assistant HTTPS MCP server entrypoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from mcp_proxy_adapter.api.app import create_app
from mcp_proxy_adapter.commands.command_registry import CommandRegistry
from mcp_proxy_adapter.commands.hooks import register_custom_commands_hook
from mcp_proxy_adapter.config import get_config
from mcp_proxy_adapter.core.app_factory.ssl_config import build_server_ssl_config
from mcp_proxy_adapter.core.config.simple_config import SimpleConfig
from mcp_proxy_adapter.core.config.simple_config_validator import SimpleConfigValidator
from mcp_proxy_adapter.core.server_engine import ServerEngineFactory

from science_assistant.commands import COMMAND_TYPES
from science_assistant.commands.info_resources import guide_markdown
from science_assistant.package_info import package_version

_TLS_SECTIONS = ("server", "client", "registration", "server_validation")
_TLS_KEYS = ("cert", "key", "ca", "crl")
_DEMO_COMMANDS = ("echo", "long_task", "job_status", "roletest")


def _register_science_commands(registry: object) -> None:
    reg = cast(CommandRegistry, registry)
    with reg._lock:
        for name in _DEMO_COMMANDS:
            reg._commands.pop(name, None)
            reg._instances.pop(name, None)
            reg._command_types.pop(name, None)
    for command_type in COMMAND_TYPES:
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
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    simple = SimpleConfig(str(path))
    model = simple.load()
    errors = SimpleConfigValidator(config_path=str(path)).validate(model)
    if errors:
        raise RuntimeError("Invalid config:\n" + "\n".join(f"- {error.message}" for error in errors))
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
            metadata["description"] = "Scientific data gateway: astroquery, TAP/ADQL, and HTTP/HTTPS/FTP downloads."
            metadata["version"] = package_version()
            metadata["commands"] = [
                {"name": command.name, "description": command.descr, "version": command.version}
                for command in COMMAND_TYPES
            ]

    cfg = get_config()
    cfg.config_path = str(path)
    setattr(cfg, "model", model)
    cfg.config_data = data
    if getattr(cfg, "feature_manager", None) is not None:
        cfg.feature_manager.config_data = data
    return data, model


def main() -> None:
    parser = argparse.ArgumentParser(description="Science Assistant MCP HTTPS server")
    parser.add_argument("--config", default="/etc/science-assistant/science-assistant.json")
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
