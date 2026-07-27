#!/usr/bin/env python3
"""Build a small cross-Python client bundle from pure-Python wheels."""
from __future__ import annotations
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = sys.argv[1]
wheelhouse = root / "dist/client-release/wheels"
client = wheelhouse / f"science_assistant_client-{version}-py3-none-any.whl"
adapter_matches = sorted(wheelhouse.glob("mcp_proxy_adapter-*-py3-none-any.whl"))
if not client.exists() or len(adapter_matches) != 1:
    raise SystemExit("portable release requires one client wheel and one mcp-proxy-adapter pure wheel")
adapter = adapter_matches[0]
stage = root / "dist/client-release/portable"
if stage.exists():
    shutil.rmtree(stage)
(stage / "wheels").mkdir(parents=True)
for source in (client, adapter):
    shutil.copy2(source, stage / "wheels" / source.name)
shutil.copy2(root / "client/README.md", stage / "README.md")
(stage / "install.sh").write_text(
    "#!/bin/sh\nset -eu\nHERE=$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd)\n"
    f"python3 -m pip install \"$HERE/wheels/{adapter.name}\" \"$HERE/wheels/{client.name}\"\n",
    encoding="utf-8",
)
(stage / "install.sh").chmod(0o755)
manifest = {"version": version, "mode": "portable-online-dependencies", "files": []}
for path in sorted(stage.rglob("*")):
    if path.is_file():
        manifest["files"].append({"path": path.relative_to(stage).as_posix(), "size_bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
(stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
out = root / f"dist/science-assistant-client-portable-{version}.zip"
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(stage.rglob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(stage))
print(out)
