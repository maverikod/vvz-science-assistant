"""Protocol-independent streaming file downloader."""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from science_assistant.services.storage import path_record, resolve_output_path

_ALLOWED_SCHEMES = {"http", "https", "ftp"}


def infer_filename(url: str) -> str:
    parsed = urlparse(url)
    name = Path(unquote(parsed.path)).name
    return name or "download.bin"


def download(
    *,
    url: str,
    directory: Path,
    output_name: str | None,
    timeout_seconds: int,
    max_bytes: int,
    expected_sha256: str | None,
) -> dict[str, object]:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValueError("Only http, https, and ftp URLs are allowed")
    if not parsed.hostname:
        raise ValueError("URL must include a hostname")

    target = resolve_output_path(directory, output_name or infer_filename(url))
    partial = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    total = 0
    request = Request(url, headers={"User-Agent": "Science-Assistant/0.2"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response, partial.open("xb") as output:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > max_bytes:
                    raise ValueError(f"Download exceeded max_bytes={max_bytes}")
                digest.update(block)
                output.write(block)
        actual = digest.hexdigest()
        if expected_sha256 and actual.lower() != expected_sha256.lower():
            raise ValueError(f"SHA-256 mismatch: expected {expected_sha256}, got {actual}")
        partial.replace(target)
        return {
            **path_record(target),
            "name": target.name,
            "size_bytes": total,
            "sha256": actual,
            "scheme": parsed.scheme.lower(),
            "source_url": url,
        }
    except Exception:
        partial.unlink(missing_ok=True)
        raise
