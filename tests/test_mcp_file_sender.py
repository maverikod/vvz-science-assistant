from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "agent" / "mcp_file_sender.py"


def load_sender() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mcp_file_sender_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_response(path: Path, data: dict[str, object]) -> None:
    path.write_text(
        json.dumps({"success": True, "result": {"success": True, "data": data}}),
        encoding="utf-8",
    )


def test_upload_uses_multiple_requests_and_accept_is_idempotent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sender = load_sender()
    source = tmp_path / "source.bin"
    source.write_bytes(b"multipart-upload-content")
    state_path = tmp_path / "upload.json"

    sender.cmd_prepare(
        argparse.Namespace(
            file=source,
            state=state_path,
            name=None,
            part_chars=8,
            ttl=900,
            server_id="science-assistant-vvz",
            copy_number=1,
        )
    )
    capsys.readouterr()
    state = sender.load_state(state_path)
    assert state["direction"] == "upload"
    assert state["part_count"] > 1

    upload_session_id = str(uuid4())
    permanent_file_id = str(uuid4())
    for index in range(state["part_count"]):
        sender.cmd_next(argparse.Namespace(state=state_path))
        payload = json.loads(capsys.readouterr().out)
        assert payload["command"] == "file_receive"
        assert payload["params"]["part_index"] == index
        if index:
            assert payload["params"]["upload_session_id"] == upload_session_id

        response_path = tmp_path / f"upload-response-{index}.json"
        if index == state["part_count"] - 1:
            response_data = {
                "status": "completed",
                "upload_session_id": upload_session_id,
                "file_id": permanent_file_id,
                "size_bytes": source.stat().st_size,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        else:
            response_data = {
                "status": "receiving",
                "upload_session_id": upload_session_id,
                "part_index": index,
            }
        write_response(response_path, response_data)
        sender.cmd_accept(argparse.Namespace(state=state_path, response=str(response_path)))
        capsys.readouterr()

        if index == 0:
            sender.cmd_accept(argparse.Namespace(state=state_path, response=str(response_path)))
            capsys.readouterr()
            assert sender.load_state(state_path)["next_part_index"] == 1

    completed = sender.load_state(state_path)
    assert completed["status"] == "completed"
    assert completed["file_id"] == permanent_file_id


def download_response(
    *,
    file_id: str,
    content: bytes,
    part_size: int,
    index: int,
    sha256: str | None = None,
) -> dict[str, object]:
    chunks = [content[start : start + part_size] for start in range(0, len(content), part_size)] or [b""]
    raw = chunks[index]
    return {
        "file_id": file_id,
        "name": "remote.bin",
        "stored_name": f"{file_id}-remote.bin",
        "relative_path": f"files/{file_id}-remote.bin",
        "size_bytes": len(content),
        "sha256": sha256 or hashlib.sha256(content).hexdigest(),
        "created_at": "2026-07-25T00:00:00+00:00",
        "part_index": index,
        "part_count": len(chunks),
        "part_size_bytes": part_size,
        "offset": index * part_size,
        "bytes_returned": len(raw),
        "eof": index == len(chunks) - 1,
        "data_base64": base64.b64encode(raw).decode("ascii"),
    }


def test_download_uses_multiple_requests_rejects_gaps_and_verifies_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sender = load_sender()
    file_id = str(uuid4())
    content = b"multipart-download-content"
    part_size = 5
    output = tmp_path / "downloaded.bin"
    state_path = tmp_path / "download.json"

    sender.cmd_download_prepare(
        argparse.Namespace(
            file_id=file_id,
            output=output,
            state=state_path,
            part_size_bytes=part_size,
            overwrite=False,
            server_id="science-assistant-vvz",
            copy_number=1,
        )
    )
    capsys.readouterr()

    chunks = [content[start : start + part_size] for start in range(0, len(content), part_size)]
    for index in range(len(chunks)):
        sender.cmd_download_next(argparse.Namespace(state=state_path))
        payload = json.loads(capsys.readouterr().out)
        assert payload["command"] == "file_get"
        assert payload["params"] == {
            "file_id": file_id,
            "part_index": index,
            "part_size_bytes": part_size,
        }

        if index == 1:
            future_path = tmp_path / "future.json"
            write_response(
                future_path,
                download_response(file_id=file_id, content=content, part_size=part_size, index=2),
            )
            with pytest.raises(ValueError, match="out-of-order"):
                sender.cmd_download_accept(
                    argparse.Namespace(state=state_path, response=str(future_path))
                )

        response_path = tmp_path / f"download-response-{index}.json"
        write_response(
            response_path,
            download_response(file_id=file_id, content=content, part_size=part_size, index=index),
        )
        sender.cmd_download_accept(argparse.Namespace(state=state_path, response=str(response_path)))
        capsys.readouterr()

        if index == 0:
            sender.cmd_download_accept(argparse.Namespace(state=state_path, response=str(response_path)))
            capsys.readouterr()
            assert sender.load_state(state_path)["next_part_index"] == 1

    completed = sender.load_state(state_path)
    assert completed["status"] == "completed"
    assert completed["bytes_written"] == len(content)
    assert completed["verified_sha256"] == hashlib.sha256(content).hexdigest()
    assert output.read_bytes() == content
    assert not Path(completed["temporary_path"]).exists()


def test_download_bad_final_checksum_rolls_back_last_part(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sender = load_sender()
    file_id = str(uuid4())
    content = b"abcdef"
    part_size = 3
    output = tmp_path / "bad.bin"
    state_path = tmp_path / "bad.json"
    sender.cmd_download_prepare(
        argparse.Namespace(
            file_id=file_id,
            output=output,
            state=state_path,
            part_size_bytes=part_size,
            overwrite=False,
            server_id="science-assistant-vvz",
            copy_number=1,
        )
    )
    capsys.readouterr()

    wrong_sha = "0" * 64
    first = tmp_path / "first.json"
    write_response(
        first,
        download_response(
            file_id=file_id,
            content=content,
            part_size=part_size,
            index=0,
            sha256=wrong_sha,
        ),
    )
    sender.cmd_download_accept(argparse.Namespace(state=state_path, response=str(first)))
    capsys.readouterr()

    final = tmp_path / "final.json"
    write_response(
        final,
        download_response(
            file_id=file_id,
            content=content,
            part_size=part_size,
            index=1,
            sha256=wrong_sha,
        ),
    )
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        sender.cmd_download_accept(argparse.Namespace(state=state_path, response=str(final)))

    state = sender.load_state(state_path)
    assert state["next_part_index"] == 1
    assert Path(state["temporary_path"]).stat().st_size == part_size
    assert not output.exists()
