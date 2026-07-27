"""Client exceptions."""
from __future__ import annotations
from typing import Any

class ScienceAssistantClientError(RuntimeError):
    """Base client failure."""

class RemoteCommandError(ScienceAssistantClientError):
    def __init__(self, message: str, *, payload: Any = None) -> None:
        super().__init__(message)
        self.payload = payload

class VersionMismatchError(ScienceAssistantClientError):
    """Client and server release versions differ."""

class TransferIntegrityError(ScienceAssistantClientError):
    """Transferred bytes do not match size or SHA-256."""
