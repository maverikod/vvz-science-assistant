#!/usr/bin/env python3
"""Run the editor-authored gravitational-wave API integration payload."""
from __future__ import annotations

from pathlib import Path

PARTS_DIR = Path(__file__).resolve().with_name("gw_api_payload")
PART_COUNT = 5


def main() -> None:
    source = "".join(
        (PARTS_DIR / f"part{index}.txt").read_text(encoding="utf-8")
        for index in range(PART_COUNT)
    )
    filename = str(Path(__file__).resolve().with_name("apply_gw_api_embedded.py"))
    namespace = {"__name__": "__main__", "__file__": str(Path(__file__).resolve())}
    exec(compile(source, filename, "exec"), namespace)


if __name__ == "__main__":
    main()
