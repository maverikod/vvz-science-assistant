"""Packaged guide and runtime inventory for info."""

from __future__ import annotations

import grp
import os
import pwd
from importlib.resources import files
from pathlib import Path
from typing import Any

from mcp_proxy_adapter.config import get_config

from science_assistant.package_info import DEBIAN_PACKAGE_NAME, PACKAGE_NAME, package_version
from science_assistant.paths import config_dir, data_dir, log_dir

GUIDE_VERSION = "1.4"


def guide_markdown() -> str:
    return files("science_assistant").joinpath("docs").joinpath("INFO.md").read_text(encoding="utf-8")


def _path_info(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return result
    stat = path.stat()
    result.update({
        "uid": stat.st_uid,
        "gid": stat.st_gid,
        "user": pwd.getpwuid(stat.st_uid).pw_name if stat.st_uid >= 0 else None,
        "group": grp.getgrgid(stat.st_gid).gr_name if stat.st_gid >= 0 else None,
        "mode": oct(stat.st_mode & 0o7777),
    })
    return result


def runtime_info() -> dict[str, Any]:
    cfg = getattr(get_config(), "config_data", {}) or {}
    server = cfg.get("server", {}) if isinstance(cfg, dict) else {}
    registration = cfg.get("registration", {}) if isinstance(cfg, dict) else {}
    uid, gid = os.getuid(), os.getgid()
    user = pwd.getpwuid(uid).pw_name
    group = grp.getgrgid(gid).gr_name
    internal_port = int(server.get("port", 18180))
    host_port = int(os.environ.get("SCIENCE_ASSISTANT_HOST_PORT", internal_port))
    advertised_host = str(server.get("advertised_host", ""))
    return {
        "process": {"user": user, "group": group, "uid": uid, "gid": gid, "pid": os.getpid()},
        "expected_identity": {
            "user": os.environ.get("SCIENCE_ASSISTANT_USER", "scasuser"),
            "group": os.environ.get("SCIENCE_ASSISTANT_GROUP", "scasgrp"),
            "uid": int(os.environ.get("SCAS_UID", uid)),
            "gid": int(os.environ.get("SCAS_GID", gid)),
        },
        "directories": {
            "config": _path_info(config_dir()),
            "data": _path_info(data_dir()),
            "logs": _path_info(log_dir()),
        },
        "ports": {
            "protocol": server.get("protocol", "https"),
            "listen_host": server.get("host", "0.0.0.0"),
            "container_port": internal_port,
            "host_port": host_port,
            "mapping": f"0.0.0.0:{host_port}->{internal_port}/tcp",
            "advertised_host": advertised_host,
            "advertised_url": f"https://{advertised_host}:{host_port}" if advertised_host else None,
        },
        "registration": {
            "enabled": registration.get("enabled", False),
            "server_id": registration.get("server_id"),
            "server_name": registration.get("server_name"),
            "register_url": registration.get("register_url"),
            "heartbeat": registration.get("heartbeat"),
        },
    }


def integrations() -> dict[str, Any]:
    import astropy
    import astroquery
    import pyvo

    return {
        "astroquery": {"version": astroquery.__version__, "services": ["VizieR", "SIMBAD", "NED", "HEASARC", "IRSA", "Gaia"]},
        "astropy": {"version": astropy.__version__},
        "pyvo": {"version": pyvo.__version__, "capability": "custom TAP/ADQL"},
        "file_protocols": ["http", "https", "ftp"],
        "table_formats": ["ecsv", "csv", "fits", "parquet"],
        "client": {"package": "science-assistant-client", "transport": "mcp-proxy-adapter JsonRpcClient", "release_version": package_version()},
        "agent_bridge": {"script": "mcp_file_parts.py", "dependencies": "Python standard library only", "network": "none; the model invokes MCP_proxy.call_server", "release_version": package_version()},
    }


def package_info() -> dict[str, str]:
    version = package_version()
    return {
        "project_name": PACKAGE_NAME,
        "debian_package": DEBIAN_PACKAGE_NAME,
        "version": version,
        "service_image_tag": version,
        "client_package": "science-assistant-client",
        "client_version": version,
        "client_wheel_relative_path": f"releases/science-assistant-client/science_assistant_client-{version}-py3-none-any.whl",
        "client_archive_relative_path": f"releases/science-assistant-client/science-assistant-client-offline-{version}.zip",
        "client_portable_archive_relative_path": f"releases/science-assistant-client/science-assistant-client-portable-{version}.zip",
        "adapter_wheel_relative_path": "releases/science-assistant-client/mcp_proxy_adapter-8.10.20-py3-none-any.whl",
        "client_py313_archive_relative_path": f"releases/science-assistant-client/science-assistant-client-py313-linux-x86_64-{version}.zip",
        "agent_script_version": version,
        "agent_script_relative_path": "releases/science-assistant-agent/mcp_file_parts.py",
        "agent_archive_relative_path": f"releases/science-assistant-agent/science-assistant-agent-{version}.zip",
    }


def registered_commands() -> list[dict[str, str]]:
    from science_assistant.commands import COMMAND_TYPES
    return [{"name": c.name, "version": c.version, "description": c.descr, "category": c.category} for c in COMMAND_TYPES]
