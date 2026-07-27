"""Safe persistent storage rooted at the server data directory."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from astropy.table import Table

from science_assistant.paths import data_dir

_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(value: str, fallback: str = "dataset") -> str:
    cleaned = _SAFE_COMPONENT.sub("-", value.strip()).strip(".-_")
    return cleaned[:120] or fallback


def ensure_data_root() -> Path:
    root = data_dir().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_data_path(relative_path: str | Path, *, create_parent: bool = False) -> Path:
    """Prefix a user path with the server data root and prevent path escape.

    All command-facing paths are relative to ``SCIENCE_ASSISTANT_DATA_DIR``.
    Absolute paths, empty paths, ``.`` and ``..`` components are rejected.
    """
    root = ensure_data_root()
    raw = str(relative_path).strip().replace("\\", "/")
    if not raw:
        raise ValueError("Data path must not be empty")
    candidate = PurePosixPath(raw)
    if candidate.is_absolute():
        raise ValueError("Absolute paths are not allowed; pass a path relative to the data directory")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("Data path contains an empty, current-directory, or parent-directory component")
    target = (root.joinpath(*candidate.parts)).resolve()
    if target != root and root not in target.parents:
        raise ValueError("Resolved path escaped the server data directory")
    if create_parent:
        target.parent.mkdir(parents=True, exist_ok=True)
    return target


def relative_data_path(path: Path) -> str:
    root = ensure_data_root()
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("Path is outside the server data directory")
    return resolved.relative_to(root).as_posix() if resolved != root else "."


def dataset_directory(dataset_name: str | None, prefix: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = safe_name(dataset_name or prefix, prefix)
    relative = f"{stamp}-{label}-{uuid.uuid4().hex[:8]}"
    target = resolve_data_path(relative)
    target.mkdir(parents=False, exist_ok=False)
    return target


def resolve_output_path(directory: Path, relative_name: str) -> Path:
    """Resolve a relative output name under a generated dataset directory."""
    root = ensure_data_root()
    directory = directory.resolve()
    if directory != root and root not in directory.parents:
        raise ValueError("Dataset directory is outside the server data directory")
    raw = str(relative_name).strip().replace("\\", "/")
    candidate = PurePosixPath(raw)
    if not raw or candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("Output path must be a safe relative path")
    safe_parts = [safe_name(part, "item") for part in candidate.parts]
    target = directory.joinpath(*safe_parts).resolve()
    if target != directory and directory not in target.parents:
        raise ValueError("Output path escaped the dataset directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def path_record(path: Path) -> dict[str, Any]:
    return {
        "relative_path": relative_data_path(path),
        "server_path": str(path.resolve()),
    }


def file_record(path: Path, *, rows: int | None = None, table_name: str | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        **path_record(path),
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if rows is not None:
        record["rows"] = rows
    if table_name is not None:
        record["table_name"] = table_name
    return record


def write_manifest(directory: Path, payload: dict[str, Any]) -> Path:
    manifest = resolve_output_path(directory, "manifest.json")
    document = {
        "schema_version": "1.1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data_root": str(ensure_data_root()),
        "dataset_relative_path": relative_data_path(directory),
        **payload,
    }
    manifest.write_text(json.dumps(document, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return manifest


def dataset_result(directory: Path, manifest: Path, **extra: Any) -> dict[str, Any]:
    return {
        "dataset_relative_path": relative_data_path(directory),
        "dataset_server_path": str(directory.resolve()),
        "manifest_relative_path": relative_data_path(manifest),
        "manifest_server_path": str(manifest.resolve()),
        **extra,
    }


def _write_table(table: Table, path: Path, output_format: str) -> None:
    if output_format == "ecsv":
        table.write(path, format="ascii.ecsv", overwrite=False)
    elif output_format == "csv":
        table.write(path, format="ascii.csv", overwrite=False)
    elif output_format == "fits":
        table.write(path, format="fits", overwrite=False)
    elif output_format == "parquet":
        table.to_pandas().to_parquet(path, index=False)
    else:
        raise ValueError(f"Unsupported output format: {output_format}")


def write_tables(
    tables: Any,
    directory: Path,
    output_format: str,
    *,
    selected_table: str | None = None,
) -> list[dict[str, Any]]:
    extension = {"ecsv": "ecsv", "csv": "csv", "fits": "fits", "parquet": "parquet"}[output_format]
    items: list[tuple[str, Table]] = []
    if isinstance(tables, Table):
        items = [(selected_table or "result", tables)]
    elif hasattr(tables, "keys"):
        for key in tables.keys():
            items.append((str(key), tables[key]))
    elif isinstance(tables, Iterable):
        for index, table in enumerate(tables):
            if isinstance(table, Table):
                items.append((f"table-{index + 1}", table))
    else:
        raise TypeError(f"Unsupported query result type: {type(tables).__name__}")

    if selected_table:
        exact = [(name, table) for name, table in items if name == selected_table]
        if not exact:
            exact = [(name, table) for name, table in items if name.endswith(selected_table)]
        if not exact:
            available = [name for name, _ in items]
            raise KeyError(f"Table {selected_table!r} not found; available: {available}")
        items = exact

    records: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, (name, table) in enumerate(items, start=1):
        base = safe_name(name, f"table-{index}")
        if base in used:
            base = f"{base}-{index}"
        used.add(base)
        path = resolve_output_path(directory, f"{base}.{extension}")
        _write_table(table, path, output_format)
        records.append(file_record(path, rows=len(table), table_name=name))
    return records
