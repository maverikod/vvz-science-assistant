#!/usr/bin/env python3
"""Assemble the offline client release archive from a prepared wheelhouse."""
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
out = root / f"dist/science-assistant-client-offline-{version}.zip"
stage = root / "dist/client-release/archive"
if stage.exists():
    shutil.rmtree(stage)
stage.mkdir(parents=True)
shutil.copytree(wheelhouse, stage / "wheels")
for source, target in [
    (root / "client/README.md", stage / "README.md"),
    (root / "client/examples", stage / "examples"),
    (root / "client/tests", stage / "tests"),
]:
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)
(stage / "install.sh").write_text(
    "#!/bin/sh\nset -eu\nHERE=$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd)\n"
    f"python3 -m pip install --no-index --find-links \"$HERE/wheels\" \"$HERE/wheels/science_assistant_client-{version}-py3-none-any.whl\"\n",
    encoding="utf-8",
)
(stage / "install.sh").chmod(0o755)
manifest = {"version": version, "files": []}
for path in sorted(stage.rglob("*")):
    if path.is_file():
        manifest["files"].append({
            "path": path.relative_to(stage).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
(stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(stage.rglob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(stage))
print(out)
