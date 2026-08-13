"""CERN Open Data HTTP access and project-local artifact persistence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

CERN_OPEN_DATA_BASE_URL = "https://opendata.cern.ch"
_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")


class CernOpenDataError(RuntimeError):
    """CERN Open Data request, selection, or persistence failure."""


@dataclass(frozen=True, slots=True)
class StoredCernArtifact:
    """One persisted CERN response or downloaded file.

    Attributes:
        path: Absolute stored artifact path.
        manifest_path: Absolute provenance manifest path.
        size_bytes: Exact stored artifact size.
        sha256: SHA-256 digest of the stored artifact.
        source_url: Exact source URL used to obtain the artifact.
    """

    path: Path
    manifest_path: Path
    size_bytes: int
    sha256: str
    source_url: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible artifact envelope.

        Returns:
            Artifact paths, size, checksum, and source URL.
        """
        return {
            "path": str(self.path),
            "manifest_path": str(self.manifest_path),
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "source_url": self.source_url,
        }


def _utc_now() -> datetime:
    """Return the current UTC timestamp.

    Returns:
        Timezone-aware current UTC datetime.
    """
    return datetime.now(timezone.utc)


def _safe_component(value: str, fallback: str) -> str:
    """Convert arbitrary text to a safe single path component.

    Args:
        value: Candidate path component.
        fallback: Value used when sanitization removes all characters.

    Returns:
        Safe path component without separators.
    """
    normalized = _SAFE_COMPONENT.sub("_", value.strip()).strip("._-")
    return normalized or fallback


def _json_bytes(payload: Any) -> bytes:
    """Serialize JSON deterministically for storage and checksums.

    Args:
        payload: JSON-compatible response payload.

    Returns:
        UTF-8 encoded pretty JSON ending with a newline.
    """
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    """Atomically replace a file with exact bytes.

    Args:
        path: Final destination path.
        payload: Exact bytes to persist.

    Returns:
        None.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _artifact_name(stem: str, digest: str, suffix: str) -> str:
    """Build a collision-resistant artifact name.

    Args:
        stem: Human-readable artifact stem.
        digest: Lowercase SHA-256 digest.
        suffix: File extension including the leading dot.

    Returns:
        Timestamped safe artifact file name.
    """
    timestamp = _utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    safe_stem = _safe_component(stem, "artifact")
    return f"{safe_stem}-{timestamp}-{digest[:12]}{suffix}"


def _response_headers(response: httpx.Response) -> dict[str, str]:
    """Return stable response headers needed for provenance.

    Args:
        response: Completed HTTP response.

    Returns:
        Lowercase selected response headers.
    """
    selected = (
        "accept-ranges",
        "content-length",
        "content-range",
        "content-type",
        "date",
        "etag",
        "last-modified",
    )
    return {key: response.headers[key] for key in selected if key in response.headers}


async def fetch_cern_json(
    endpoint: str,
    *,
    params: Mapping[str, Any] | None = None,
    timeout_seconds: float = 120.0,
    client: httpx.AsyncClient | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Fetch one CERN Open Data JSON endpoint.

    Args:
        endpoint: Absolute URL or portal-relative endpoint.
        params: Optional query parameters.
        timeout_seconds: Positive request timeout.
        client: Optional injected HTTP client for tests.

    Returns:
        Parsed JSON payload and raw request provenance.
    """
    if timeout_seconds <= 0:
        raise CernOpenDataError("timeout_seconds must be positive")
    if endpoint.startswith("http"):
        url = endpoint
    else:
        url = f"{CERN_OPEN_DATA_BASE_URL}{endpoint}"
    owns_client = client is None
    resolved_client = client or httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout_seconds,
        headers={"User-Agent": "science-assistant/1 CERN-Open-Data"},
    )
    try:
        response = await resolved_client.get(url, params=dict(params or {}))
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise CernOpenDataError(
                f"CERN endpoint returned invalid JSON: {response.url}"
            ) from exc
        provenance = {
            "requested_url": str(response.request.url),
            "resolved_url": str(response.url),
            "status_code": response.status_code,
            "headers": _response_headers(response),
            "fetched_at": _utc_now().isoformat(),
        }
        return payload, provenance
    except httpx.HTTPError as exc:
        raise CernOpenDataError(f"CERN HTTP request failed: {exc}") from exc
    finally:
        if owns_client:
            await resolved_client.aclose()


def store_json_artifact(
    project_data_directory: Path,
    *,
    category: str,
    stem: str,
    payload: Any,
    provenance: Mapping[str, Any],
) -> StoredCernArtifact:
    """Store JSON and a provenance manifest below a project data directory.

    Args:
        project_data_directory: Existing project-local ``data`` directory.
        category: CERN artifact category subdirectory.
        stem: Human-readable file-name stem.
        payload: Exact JSON-compatible response payload.
        provenance: Request and source metadata.

    Returns:
        Persisted JSON artifact identity.
    """
    if not project_data_directory.is_dir():
        raise CernOpenDataError(
            f"project data directory is unavailable: {project_data_directory}"
        )
    encoded = _json_bytes(payload)
    digest = hashlib.sha256(encoded).hexdigest()
    directory = (
        project_data_directory
        / "cern_open_data"
        / _safe_component(category, "metadata")
    )
    path = directory / _artifact_name(stem, digest, ".json")
    _atomic_write(path, encoded)
    manifest = {
        "schema_version": "1.0",
        "provider": "cern-open-data",
        "artifact_path": str(path),
        "size_bytes": len(encoded),
        "sha256": digest,
        "stored_at": _utc_now().isoformat(),
        "provenance": dict(provenance),
    }
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    _atomic_write(manifest_path, _json_bytes(manifest))
    source_url = str(
        provenance.get("resolved_url") or provenance.get("requested_url") or ""
    )
    return StoredCernArtifact(
        path=path,
        manifest_path=manifest_path,
        size_bytes=len(encoded),
        sha256=digest,
        source_url=source_url,
    )


def _record_mapping(payload: Any) -> Mapping[str, Any]:
    """Return the record mapping from supported API envelopes.

    Args:
        payload: Parsed record endpoint payload.

    Returns:
        Mapping containing record metadata.
    """
    if not isinstance(payload, Mapping):
        raise CernOpenDataError("record response must be an object")
    for key in ("metadata", "record"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            return value
    return payload


def record_files(payload: Any) -> list[Mapping[str, Any]]:
    """Extract file rows from one CERN record response.

    Args:
        payload: Parsed record endpoint payload.

    Returns:
        Ordered list of file metadata mappings.
    """
    record = _record_mapping(payload)
    candidates: list[Any] = [record.get("files")]
    metadata = record.get("metadata")
    if isinstance(metadata, Mapping):
        candidates.append(metadata.get("files"))
    if isinstance(payload, Mapping):
        candidates.append(payload.get("files"))
    for candidate in candidates:
        if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes)):
            rows = [row for row in candidate if isinstance(row, Mapping)]
            if rows:
                return rows
    raise CernOpenDataError("record contains no downloadable file metadata")


def select_record_file(
    payload: Any,
    *,
    file_name: str | None,
    file_index: int,
) -> Mapping[str, Any]:
    """Select one file from a CERN record.

    Args:
        payload: Parsed record endpoint payload.
        file_name: Optional exact file name or key.
        file_index: Zero-based fallback file index.

    Returns:
        Selected file metadata mapping.
    """
    files = record_files(payload)
    if file_name:
        matches = [
            row
            for row in files
            if str(row.get("key") or row.get("name") or "") == file_name
        ]
        if len(matches) != 1:
            raise CernOpenDataError(
                f"file_name {file_name!r} resolved to {len(matches)} files"
            )
        return matches[0]
    if file_index < 0 or file_index >= len(files):
        raise CernOpenDataError(
            f"file_index {file_index} is outside 0..{len(files) - 1}"
        )
    return files[file_index]


def _file_url(file_metadata: Mapping[str, Any]) -> str:
    """Extract an HTTP download URL from file metadata.

    Args:
        file_metadata: Selected CERN file metadata mapping.

    Returns:
        Absolute HTTP or HTTPS URL.
    """
    candidates: list[Any] = [
        file_metadata.get("uri"),
        file_metadata.get("url"),
    ]
    links = file_metadata.get("links")
    if isinstance(links, Mapping):
        candidates.extend((links.get("self"), links.get("download")))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
            return candidate
    raise CernOpenDataError("selected file has no HTTP download URL")


def _validate_cern_url(url: str) -> None:
    """Reject downloads outside CERN-controlled HTTP hosts.

    Args:
        url: Candidate source URL.

    Returns:
        None.
    """
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"}:
        raise CernOpenDataError("only HTTP and HTTPS downloads are supported")
    if hostname != "cern.ch" and not hostname.endswith(".cern.ch"):
        raise CernOpenDataError(f"download host is not CERN-controlled: {hostname}")


def _expected_checksum(file_metadata: Mapping[str, Any]) -> str | None:
    """Extract a declared CERN checksum.

    Args:
        file_metadata: Selected CERN file metadata mapping.

    Returns:
        Normalized algorithm-prefixed checksum or ``None``.
    """
    value = file_metadata.get("checksum")
    if isinstance(value, str) and ":" in value:
        return value.strip().lower()
    return None


def _verify_checksum(path: Path, expected: str | None) -> tuple[str, str]:
    """Compute SHA-256 and verify an optional CERN checksum.

    Args:
        path: Completed downloaded file.
        expected: Optional ``algorithm:value`` checksum.

    Returns:
        SHA-256 and Adler-32 hexadecimal digests.
    """
    sha256 = hashlib.sha256()
    adler32 = 1
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha256.update(block)
            adler32 = zlib.adler32(block, adler32)
    sha_value = sha256.hexdigest()
    adler_value = f"{adler32 & 0xFFFFFFFF:08x}"
    if expected:
        algorithm, declared = expected.split(":", 1)
        if algorithm == "sha256":
            actual = sha_value
        elif algorithm == "adler32":
            actual = adler_value
        else:
            raise CernOpenDataError(f"unsupported CERN checksum algorithm: {algorithm}")
        if actual.lower() != declared.lower():
            raise CernOpenDataError(
                f"checksum mismatch for {path.name}: "
                f"expected {expected}, got {algorithm}:{actual}"
            )
    return sha_value, adler_value


async def download_record_file(
    project_data_directory: Path,
    *,
    file_metadata: Mapping[str, Any],
    output_name: str | None = None,
    timeout_seconds: float = 3600.0,
    max_bytes: int = 50 * 1024**3,
    resume: bool = True,
    client: httpx.AsyncClient | None = None,
) -> StoredCernArtifact:
    """Download one record file into the target project's data directory.

    Args:
        project_data_directory: Existing project-local ``data`` directory.
        file_metadata: Selected CERN file metadata mapping.
        output_name: Optional destination file name override.
        timeout_seconds: Positive transfer timeout.
        max_bytes: Positive safety limit for the final file.
        resume: Whether an existing partial file may be resumed with HTTP Range.
        client: Optional injected HTTP client for tests.

    Returns:
        Persisted file artifact and manifest identity.
    """
    if timeout_seconds <= 0 or max_bytes <= 0:
        raise CernOpenDataError("timeout_seconds and max_bytes must be positive")
    source_url = _file_url(file_metadata)
    _validate_cern_url(source_url)
    source_name = str(
        file_metadata.get("key") or file_metadata.get("name") or "download.bin"
    )
    destination_name = _safe_component(output_name or source_name, "download.bin")
    directory = project_data_directory / "cern_open_data" / "files"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / destination_name
    partial = destination.with_suffix(destination.suffix + ".part")
    existing_size = partial.stat().st_size if resume and partial.exists() else 0
    headers = {"Range": f"bytes={existing_size}-"} if existing_size else {}

    owns_client = client is None
    resolved_client = client or httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout_seconds,
        headers={"User-Agent": "science-assistant/1 CERN-Open-Data"},
    )
    response_headers: dict[str, str] = {}
    try:
        async with resolved_client.stream(
            "GET", source_url, headers=headers
        ) as response:
            response.raise_for_status()
            if existing_size and response.status_code != 206:
                existing_size = 0
                partial.unlink(missing_ok=True)
            mode = "ab" if existing_size else "wb"
            total = existing_size
            with partial.open(mode) as stream:
                async for chunk in response.aiter_bytes(1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise CernOpenDataError(
                            f"download exceeds max_bytes={max_bytes}"
                        )
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            response_headers = _response_headers(response)
    except httpx.HTTPError as exc:
        raise CernOpenDataError(f"CERN file download failed: {exc}") from exc
    finally:
        if owns_client:
            await resolved_client.aclose()

    expected = _expected_checksum(file_metadata)
    sha256, adler32 = _verify_checksum(partial, expected)
    partial.replace(destination)
    manifest = {
        "schema_version": "1.0",
        "provider": "cern-open-data",
        "source_url": source_url,
        "file_metadata": dict(file_metadata),
        "response_headers": response_headers,
        "stored_path": str(destination),
        "size_bytes": destination.stat().st_size,
        "sha256": sha256,
        "adler32": adler32,
        "expected_checksum": expected,
        "stored_at": _utc_now().isoformat(),
        "resumed_from_bytes": existing_size,
    }
    manifest_path = destination.with_suffix(destination.suffix + ".manifest.json")
    _atomic_write(manifest_path, _json_bytes(manifest))
    return StoredCernArtifact(
        path=destination,
        manifest_path=manifest_path,
        size_bytes=destination.stat().st_size,
        sha256=sha256,
        source_url=source_url,
    )
