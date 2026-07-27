from __future__ import annotations

import base64
import hashlib
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from science_assistant.services import package_transfer


def _common(raw: bytes, *, package_id: str, relative_path: str, part_size: int) -> dict[str, object]:
    return {
        "package_id": package_id,
        "relative_path": relative_path,
        "part_count": max(1, math.ceil(len(raw) / part_size)),
        "part_size_bytes": part_size,
        "total_size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "overwrite": False,
    }


def test_wait_before_out_of_order_parts_and_idempotent_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCIENCE_ASSISTANT_DATA_DIR", str(tmp_path / "data"))
    raw = bytes(range(256)) * 19 + b"\x00\xffpackage-wait"
    part_size = 257
    common = _common(raw, package_id="pkg_wait_test", relative_path="incoming/result.bin", part_size=part_size)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            package_transfer.wait_and_assemble,
            **common,
            timeout_seconds=5,
            poll_interval_ms=50,
        )
        time.sleep(0.1)
        for index in reversed(range(int(common["part_count"]))):
            start = index * part_size
            part = raw[start : start + package_transfer._expected_part_size(common, index)]
            result = package_transfer.receive_part(
                **common,
                part_index=index,
                part_sha256=hashlib.sha256(part).hexdigest(),
                data_base64=base64.b64encode(part).decode("ascii"),
            )
            assert result["part_index"] == index
        first = raw[:part_size]
        retry = package_transfer.receive_part(
            **common,
            part_index=0,
            part_sha256=hashlib.sha256(first).hexdigest(),
            data_base64=base64.b64encode(first).decode("ascii"),
        )
        assert retry["idempotent"] is True
        completed = future.result(timeout=6)

    target = tmp_path / "data/incoming/result.bin"
    assert target.read_bytes() == raw
    assert completed["file"]["sha256"] == hashlib.sha256(raw).hexdigest()
    status = package_transfer.package_status(package_id="pkg_wait_test", page=1, page_size=3)
    assert status["status"] == "completed"
    assert status["missing_count"] == 0
    assert status["received_count"] == int(common["part_count"])
    assert len(status["received_indices"]) == 3
    assert status["pagination"]["has_more"] is True


def test_bad_part_checksum_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCIENCE_ASSISTANT_DATA_DIR", str(tmp_path / "data"))
    raw = b"abcdef"
    common = _common(raw, package_id="pkg_bad_sha", relative_path="incoming/bad.bin", part_size=6)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        package_transfer.receive_part(
            **common,
            part_index=0,
            part_sha256="0" * 64,
            data_base64=base64.b64encode(raw).decode("ascii"),
        )
