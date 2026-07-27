"""Stable result models."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class FileTransferReceipt:
    direction: str
    transfer_id: str
    local_path: Path
    remote_path: str
    size_bytes: int
    sha256: str
    remote_payload: dict[str, Any]
