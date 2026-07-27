"""Runtime paths shared by commands and package tooling."""

from __future__ import annotations

import os
from pathlib import Path


def config_dir() -> Path:
    return Path(os.environ.get("SCIENCE_ASSISTANT_CONFIG_DIR", "/etc/science-assistant"))


def data_dir() -> Path:
    return Path(os.environ.get("SCIENCE_ASSISTANT_DATA_DIR", "/var/science-assistant/data"))


def log_dir() -> Path:
    return Path(os.environ.get("SCIENCE_ASSISTANT_LOG_DIR", "/var/log/science-assistant"))
