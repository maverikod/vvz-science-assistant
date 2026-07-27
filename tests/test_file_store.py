from __future__ import annotations

import base64
import hashlib
import math
import uuid
from pathlib import Path

import pytest

from science_assistant.services import file_store


def _parts(raw: bytes, chars: int) -> list[str]:
    encoded = base64.b64encode(raw).decode("ascii")
    return [encoded[index:index + chars] for index in range(0, len(encoded), chars)] or [""]


def test_receive_last_returns_file_id_get_and_paginated_ls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCIENCE_ASSISTANT_DATA_DIR", str(tmp_path / "data"))
    raw = bytes(range(256)) * 11 + b"\x00\xffscience-file"
    digest = hashlib.sha256(raw).hexdigest()
    parts = _parts(raw, 113)

    first = file_store.receive_file_part(
        filename="theta report.bin",
        part_index=0,
        part_count=len(parts),
        size_bytes=len(raw),
        sha256=digest,
        ttl_seconds=60,
        data_base64_part=parts[0],
    )
    assert first["status"] == "receiving"
    assert "file_id" not in first
    session_id = first["upload_session_id"]
    assert uuid.UUID(session_id).version == 4

    for index, fragment in enumerate(parts[1:-1], start=1):
        middle = file_store.receive_file_part(
            upload_session_id=session_id,
            part_index=index,
            data_base64_part=fragment,
        )
        assert middle["status"] == "receiving"
        assert "file_id" not in middle

    last_index = len(parts) - 1
    completed = file_store.receive_file_part(
        upload_session_id=session_id,
        part_index=last_index,
        data_base64_part=parts[last_index],
    )
    assert completed["status"] == "completed"
    file_id = completed["file_id"]
    assert uuid.UUID(file_id).version == 4
    assert completed["stored_name"].startswith(file_id + "-")
    assert completed["sha256"] == digest

    rebuilt = bytearray()
    part_size = 199
    count = max(1, math.ceil(len(raw) / part_size))
    for index in range(count):
        item = file_store.get_file_part(
            file_id=file_id,
            part_index=index,
            part_size_bytes=part_size,
        )
        rebuilt.extend(base64.b64decode(item["data_base64"], validate=True))
        assert item["part_count"] == count
    assert bytes(rebuilt) == raw

    listing = file_store.list_files(page=1, page_size=1, name_pattern="theta*")
    assert listing["items"][0]["file_id"] == file_id
    assert listing["pagination"] == {
        "page": 1,
        "page_size": 1,
        "total_items": 1,
        "total_pages": 1,
        "has_more": False,
        "next_page": None,
    }


def test_single_portion_returns_file_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCIENCE_ASSISTANT_DATA_DIR", str(tmp_path / "data"))
    raw = b"single packet"
    result = file_store.receive_file_part(
        filename="single.txt",
        part_index=0,
        part_count=1,
        size_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        ttl_seconds=30,
        data_base64_part=base64.b64encode(raw).decode("ascii"),
    )
    assert result["status"] == "completed"
    assert uuid.UUID(result["file_id"]).version == 4


def test_ttl_expiry_removes_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCIENCE_ASSISTANT_DATA_DIR", str(tmp_path / "data"))
    clock = {"value": 1000.0}
    monkeypatch.setattr(file_store, "_epoch", lambda: clock["value"])
    raw = b"ttl test"
    encoded = base64.b64encode(raw).decode("ascii")
    first = file_store.receive_file_part(
        filename="ttl.bin",
        part_index=0,
        part_count=2,
        size_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        ttl_seconds=5,
        data_base64_part=encoded[:4],
    )
    session_id = first["upload_session_id"]
    clock["value"] = 1006.0
    with pytest.raises(TimeoutError, match="TTL has expired"):
        file_store.receive_file_part(
            upload_session_id=session_id,
            part_index=1,
            data_base64_part=encoded[4:],
        )


def test_delete_file_removes_binary_and_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCIENCE_ASSISTANT_DATA_DIR", str(tmp_path / "data"))
    raw = b"delete me through MCP"
    completed = file_store.receive_file_part(
        filename="delete-me.txt",
        part_index=0,
        part_count=1,
        size_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        ttl_seconds=30,
        data_base64_part=base64.b64encode(raw).decode("ascii"),
    )
    file_id = completed["file_id"]
    metadata = file_store._load_file_metadata(file_id)
    stored_path = Path(tmp_path / "data") / metadata["relative_path"]
    metadata_path = file_store._metadata_path(file_id)
    assert stored_path.is_file()
    assert metadata_path.is_file()

    deleted = file_store.delete_file(file_id=file_id)
    assert deleted["status"] == "deleted"
    assert deleted["file_id"] == file_id
    assert deleted["sha256"] == hashlib.sha256(raw).hexdigest()
    assert not stored_path.exists()
    assert not metadata_path.exists()

    listing = file_store.list_files(page=1, page_size=10, name_pattern="delete-*")
    assert listing["items"] == []
    assert listing["pagination"]["total_items"] == 0
    with pytest.raises(FileNotFoundError, match="Unknown file_id"):
        file_store.get_file_part(file_id=file_id)
    with pytest.raises(FileNotFoundError, match="Unknown file_id"):
        file_store.delete_file(file_id=file_id)
