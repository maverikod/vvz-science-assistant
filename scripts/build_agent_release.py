#!/usr/bin/env python3
"""Build the standalone agent helper release from one source file."""
from __future__ import annotations
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = sys.argv[1]
sources = [root / "scripts/mcp_file_parts.py", root / "agent/mcp_file_sender.py"]
readme = root / "scripts/MCP_FILE_PARTS.md"
for source in sources:
    text = source.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    if not match or match.group(1) != version:
        raise SystemExit(f"agent script version mismatch: {source}: {match.group(1) if match else 'missing'} != {version}")
stage = root / "dist/agent-release"
if stage.exists():
    shutil.rmtree(stage)
stage.mkdir(parents=True)
for source in sources:
    shutil.copy2(source, stage / source.name)
    (stage / source.name).chmod(0o755)
shutil.copy2(readme, stage / "README.md")
manifest = {"version": version, "mode": "model-tool-bridge", "files": []}
for path in sorted(stage.iterdir()):
    if path.is_file():
        manifest["files"].append({
            "path": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
(stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
out = root / f"dist/science-assistant-agent-{version}.zip"
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(stage.iterdir()):
        if path.is_file():
            archive.write(path, path.name)
print(out)
