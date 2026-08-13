#!/usr/bin/env python3
"""Fail when any release artifact version diverges."""
from __future__ import annotations
import re
import subprocess
import sys
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = re.search(r'^version\s*=\s*"([^"]+)"', (root / "pyproject.toml").read_text(), re.M).group(1)
client_version = re.search(r'__version__\s*=\s*"([^"]+)"', (root / "client/science_assistant_client/version.py").read_text()).group(1)
assert client_version == version, (client_version, version)
adapter_pin = re.search(r'"mcp-proxy-adapter==([^"]+)"', (root / "client/pyproject.toml").read_text()).group(1)
required = [
    root / f"dist/server/science_assistant-{version}-py3-none-any.whl",
    root / f"dist/client-release/wheels/science_assistant_client-{version}-py3-none-any.whl",
    root / f"dist/science-assistant-client-offline-{version}.zip",
    root / f"dist/science-assistant-client-portable-{version}.zip",
    root / f"dist/science-assistant-client-py313-linux-x86_64-{version}.zip",
    root / f"dist/client-release/wheels/mcp_proxy_adapter-{adapter_pin}-py3-none-any.whl",
    root / f"dist/science-assistant-docker_{version}_amd64.deb",
    root / "dist/agent-release/mcp_file_parts.py",
    root / "dist/agent-release/mcp_file_sender.py",
    root / f"dist/science-assistant-agent-{version}.zip",
]
missing = [str(path) for path in required if not path.exists()]
if missing:
    raise SystemExit("missing release artifacts:\n" + "\n".join(missing))
with zipfile.ZipFile(required[2]) as archive:
    assert f"wheels/science_assistant_client-{version}-py3-none-any.whl" in archive.namelist()
with zipfile.ZipFile(required[3]) as archive:
    names = archive.namelist()
    assert f"wheels/science_assistant_client-{version}-py3-none-any.whl" in names
    assert f"wheels/mcp_proxy_adapter-{adapter_pin}-py3-none-any.whl" in names
with zipfile.ZipFile(required[4]) as archive:
    names = archive.namelist()
    assert f"wheels/science_assistant_client-{version}-py3-none-any.whl" in names
    assert "create_venv.sh" in names
for agent_path in required[7:9]:
    agent_text = agent_path.read_text(encoding="utf-8")
    agent_version = re.search(r'^__version__\s*=\s*"([^"]+)"', agent_text, re.M).group(1)
    assert agent_version == version, (agent_path, agent_version, version)
with zipfile.ZipFile(required[9]) as archive:
    names = set(archive.namelist())
    assert {"mcp_file_parts.py", "mcp_file_sender.py", "README.md", "manifest.json"} <= names
image = subprocess.check_output([
    "docker", "image", "inspect", f"vasilyvz/science-assistant:{version}",
    "--format", "{{ index .Config.Labels \"org.opencontainers.image.version\" }}",
], text=True).strip()
assert image == version, (image, version)
deb = subprocess.check_output(["dpkg-deb", "-f", str(required[6]), "Version"], text=True).strip()
assert deb == version, (deb, version)
print(f"release-version-ok {version}")
