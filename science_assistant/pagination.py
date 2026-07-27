"""Stable pagination helpers for command output."""

from __future__ import annotations

from math import ceil
from typing import Any

DEFAULT_PAGE_SIZE = 80
MIN_PAGE_SIZE = 10
MAX_PAGE_SIZE = 500


def paginate_lines(text: str, *, page_size: int, block_position: int) -> dict[str, Any]:
    """Return one stable 1-based block of text measured in lines."""
    if not MIN_PAGE_SIZE <= page_size <= MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between {MIN_PAGE_SIZE} and {MAX_PAGE_SIZE}")
    if block_position < 1:
        raise ValueError("block_position must be at least 1")
    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    total_blocks = max(1, ceil(total_lines / page_size))
    start = (block_position - 1) * page_size
    end = start + page_size
    content = "".join(lines[start:end]) if start < total_lines else ""
    return {
        "paginated": True,
        "page_size": page_size,
        "block_position": block_position,
        "total_lines": total_lines,
        "total_blocks": total_blocks,
        "has_more": block_position < total_blocks,
        "next_block_position": block_position + 1 if block_position < total_blocks else None,
        "content": content,
    }
