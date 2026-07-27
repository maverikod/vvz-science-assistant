#!/usr/bin/env python3
"""Build a Python 3.13 Linux x86_64 offline client environment bundle."""
from __future__ import annotations
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = sys.argv[1]
release_root = root / "dist/client-py313"
wheelhouse = release_root / "wheels"
stage = release_root / "archive"
for path in (release_root,):
    if path.exists():
        shutil.rmtree(path)
wheelhouse.mkdir(parents=True)
client_wheel = root / f"dist/client-release/wheels/science_assistant_client-{version}-py3-none-any.whl"
if not client_wheel.exists():
    raise SystemExit(f"missing client wheel: {client_wheel}")
shutil.copy2(client_wheel, wheelhouse / client_wheel.name)
cmd = [
    sys.executable, "-m", "pip", "download", "--disable-pip-version-check",
    "--dest", str(wheelhouse), "--only-binary=:all:",
    "--python-version", "3.13", "--implementation", "cp", "--abi", "cp313",
    "--platform", "manylinux_2_34_x86_64",
    "--platform", "manylinux_2_28_x86_64",
    "--platform", "manylinux2014_x86_64",
    "--platform", "manylinux_2_17_x86_64",
    "mcp-proxy-adapter==8.10.20",
]
subprocess.run(cmd, check=True)
stage.mkdir(parents=True)
shutil.copytree(wheelhouse, stage / "wheels")
shutil.copy2(root / "client/README.md", stage / "README.md")
(stage / "create_venv.sh").write_text(
    "#!/bin/sh\nset -eu\n"
    "HERE=$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd)\n"
    "TARGET=${1:-/mnt/data/science-assistant-client-venv}\n"
    "PYTHON=${PYTHON:-python3}\n"
    "\"$PYTHON\" -c 'import sys; assert sys.version_info[:2] == (3, 13), sys.version'\n"
    "\"$PYTHON\" -m venv \"$TARGET\"\n"
    f"\"$TARGET/bin/python\" -m pip install --no-index --find-links \"$HERE/wheels\" \"$HERE/wheels/science_assistant_client-{version}-py3-none-any.whl\"\n"
    "\"$TARGET/bin/python\" -c 'import science_assistant_client, mcp_proxy_adapter; print(science_assistant_client.__version__)'\n",
    encoding="utf-8",
)
(stage / "create_venv.sh").chmod(0o755)
manifest = {
    "version": version,
    "python": "CPython 3.13",
    "platform": "Linux x86_64 manylinux",
    "default_venv": "/mnt/data/science-assistant-client-venv",
    "files": [],
}
for path in sorted(stage.rglob("*")):
    if path.is_file():
        manifest["files"].append({
            "path": path.relative_to(stage).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
(stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
out = root / f"dist/science-assistant-client-py313-linux-x86_64-{version}.zip"
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(stage.rglob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(stage))
print(out)
