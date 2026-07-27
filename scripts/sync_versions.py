#!/usr/bin/env python3
"""Synchronize and validate all release version sources."""
from __future__ import annotations
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
root_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
match = re.search(r'^version\s*=\s*"([^"]+)"', root_text, re.M)
if not match:
    raise SystemExit("root pyproject.toml has no static project version")
version = match.group(1)
version_file = ROOT / "client/science_assistant_client/version.py"
version_file.write_text(
    '"""Generated from the server root pyproject.toml."""\n'
    f'__version__ = "{version}"\n',
    encoding="utf-8",
)
for agent_script in (ROOT / "scripts/mcp_file_parts.py", ROOT / "agent/mcp_file_sender.py"):
    agent_text = agent_script.read_text(encoding="utf-8")
    agent_text, replaced = re.subn(
        r'^__version__\s*=\s*"[^"]+"',
        f'__version__ = "{version}"',
        agent_text,
        count=1,
        flags=re.M,
    )
    if replaced != 1:
        raise SystemExit(f"agent helper has no unique __version__ assignment: {agent_script}")
    agent_script.write_text(agent_text, encoding="utf-8")
for path in (ROOT / "docker/etc-default-science-assistant", ROOT / "docker/science-assistant.json.template"):
    if not path.exists():
        raise SystemExit(f"missing required release template: {path}")
print(version)
