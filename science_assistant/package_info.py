"""Package identity helpers."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

PACKAGE_NAME = "science-assistant"
DEBIAN_PACKAGE_NAME = "science-assistant-docker"
SERVER_ID = "science-assistant-vvz"
SERVER_NAME = "Science Assistant"


def package_version() -> str:
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return "0.2.0"
