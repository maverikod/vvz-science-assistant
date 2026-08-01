"""Tests for CERN Open Data commands and CAS project file delivery."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar, Self
from unittest.mock import AsyncMock

import pytest

from science_assistant.commands import cern_open_data_commands as commands
from science_assistant.services.code_analysis_project_resolver import (
    CodeAnalysisProject,
    CodeAnalysisProjectWriter,
    ProjectResolutionError,
    UploadedProjectFile,
    _safe_project_path,
    resolve_project,
)

PROJECT_ID = "7220337b-e7d3-43cf-bc9f-6ab61d1afdc9"
WATCH_ID = "550e8400-e29b-41d4-a716-446655440001"


def test_safe_project_path_requires_data_subtree() -> None:
    """Only safe project-relative paths below data are accepted."""
    assert _safe_project_path("data/cern_open_data/search/result.json") == (
        "data/cern_open_data/search/result.json"
    )
    for invalid in (
        "/data/result.json",
        "result.json",
        "docs/result.json",
        "data/../result.json",
        "data",
    ):
        with pytest.raises(ProjectResolutionError):
            _safe_project_path(invalid)


@pytest.mark.asyncio
async def test_resolve_project_uses_list_projects() -> None:
    """The official client project listing is the only project resolver."""
    list_projects = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "projects": [
                    {
                        "id": PROJECT_ID,
                        "name": "theta-particle-analysis",
                        "root_path": "theta-particle-analysis",
                        "watch_dir_id": WATCH_ID,
                        "deleted": False,
                    }
                ]
            },
        }
    )
    client = SimpleNamespace(commands=SimpleNamespace(list_projects=list_projects))

    project = await resolve_project(client, PROJECT_ID)  # type: ignore[arg-type]

    assert project.project_id == PROJECT_ID
    assert project.name == "theta-particle-analysis"
    list_projects.assert_awaited_once_with(page_size=200, block_position=1)


@pytest.mark.asyncio
async def test_writer_upload_bytes_uses_adapter_transfer() -> None:
    """Byte artifacts are staged safely and committed through CAS transfer-save."""
    receipt = SimpleNamespace(completed=True, transfer_id="transfer-bytes")
    upload_file = AsyncMock(return_value=receipt)
    call_validated = AsyncMock(
        return_value={
            "success": True,
            "data": {"file_id": "11111111-1111-4111-8111-111111111111"},
        }
    )
    writer = CodeAnalysisProjectWriter(PROJECT_ID, comment="test")
    writer.client = SimpleNamespace(
        rpc=SimpleNamespace(upload_file=upload_file),
        call_validated=call_validated,
    )  # type: ignore[assignment]
    writer.session_id = "22222222-2222-4222-8222-222222222222"
    writer.project = CodeAnalysisProject(
        project_id=PROJECT_ID,
        name="target",
        root_path="target",
        watch_dir_id=WATCH_ID,
    )
    payload = b"exact-json-bytes\n"

    uploaded = await writer.upload_bytes(
        "data/cern_open_data/search/result.json",
        payload,
    )

    assert uploaded.file_path == "data/cern_open_data/search/result.json"
    assert uploaded.sha256 == hashlib.sha256(payload).hexdigest()
    upload_file.assert_awaited_once()
    staged_path = Path(upload_file.await_args.args[0])  # type: ignore[union-attr]
    assert staged_path.name.startswith("science-assistant-cas-")
    assert not staged_path.exists()
    assert call_validated.await_args is not None
    command, params = call_validated.await_args.args
    assert command == "project_file_transfer_upload_save"
    assert params["project_id"] == PROJECT_ID
    assert params["file_path"] == uploaded.file_path
    assert params["transfer_id"] == "transfer-bytes"


@pytest.mark.asyncio
async def test_writer_upload_path_commits_adapter_transfer(tmp_path: Path) -> None:
    """Large staged files use adapter upload plus CAS upload-save."""
    source = tmp_path / "events.root"
    source.write_bytes(b"ROOT-data")
    receipt = SimpleNamespace(completed=True, transfer_id="transfer-1")
    upload_file = AsyncMock(return_value=receipt)
    call_validated = AsyncMock(
        return_value={
            "success": True,
            "data": {"file_id": "33333333-3333-4333-8333-333333333333"},
        }
    )
    writer = CodeAnalysisProjectWriter(PROJECT_ID, comment="test")
    writer.client = SimpleNamespace(
        rpc=SimpleNamespace(upload_file=upload_file),
        call_validated=call_validated,
    )  # type: ignore[assignment]
    writer.session_id = "22222222-2222-4222-8222-222222222222"
    writer.project = CodeAnalysisProject(
        project_id=PROJECT_ID,
        name="target",
        root_path="target",
        watch_dir_id=WATCH_ID,
    )

    uploaded = await writer.upload_path(
        "data/cern_open_data/files/events.root",
        source,
    )

    assert uploaded.size_bytes == len(b"ROOT-data")
    upload_file.assert_awaited_once_with(
        str(source), filename="events.root", compression="identity"
    )
    assert call_validated.await_args is not None
    command, params = call_validated.await_args.args
    assert command == "project_file_transfer_upload_save"
    assert params["project_id"] == PROJECT_ID
    assert params["file_path"] == uploaded.file_path
    assert params["transfer_id"] == "transfer-1"


class FakeWriter:
    """Capture command uploads without a live CAS server."""

    instances: ClassVar[list[FakeWriter]] = []

    def __init__(self, project_id: str, *, comment: str) -> None:
        self.project_id = project_id
        self.comment = comment
        self.project = CodeAnalysisProject(
            project_id=project_id,
            name="target",
            root_path="target",
            watch_dir_id=WATCH_ID,
        )
        self.uploads: list[tuple[str, bytes]] = []
        self.instances.append(self)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def upload_path(
        self,
        file_path: str,
        source_path: str | Path,
        *,
        commit_message: str | None = None,
    ) -> UploadedProjectFile:
        del commit_message
        payload = Path(source_path).read_bytes()
        self.uploads.append((file_path, payload))
        return UploadedProjectFile(
            file_id=f"file-{len(self.uploads)}",
            file_path=file_path,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )

    async def upload_bytes(
        self,
        file_path: str,
        payload: bytes,
        *,
        commit_message: str | None = None,
    ) -> UploadedProjectFile:
        del commit_message
        exact = bytes(payload)
        self.uploads.append((file_path, exact))
        return UploadedProjectFile(
            file_id=f"file-{len(self.uploads)}",
            file_path=file_path,
            size_bytes=len(exact),
            sha256=hashlib.sha256(exact).hexdigest(),
        )


@pytest.mark.asyncio
async def test_search_command_delivers_json_and_manifest_to_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Search stores raw JSON and provenance below project data through CAS."""
    FakeWriter.instances.clear()
    monkeypatch.setattr(commands, "CodeAnalysisProjectWriter", FakeWriter)
    fetch = AsyncMock(
        return_value=(
            {"hits": {"hits": [{"id": 42}]}},
            {"resolved_url": "https://opendata.cern.ch/api/records?q=muon"},
        )
    )
    monkeypatch.setattr(commands, "fetch_cern_json", fetch)

    result = await commands.CernOpenDataSearchCommand().execute(
        project_id=PROJECT_ID,
        query="muon",
        size=1,
    )

    assert result.success is True
    writer = FakeWriter.instances[-1]
    assert len(writer.uploads) == 2
    assert writer.uploads[0][0].startswith("data/cern_open_data/search/")
    assert writer.uploads[1][0].endswith(".manifest.json")
    fetch.assert_awaited_once_with(
        "/api/records",
        params={"q": "muon", "page": 1, "size": 1},
        timeout_seconds=120.0,
    )
