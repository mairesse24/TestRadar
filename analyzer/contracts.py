"""Typed representations shared by the TestRadar analysis components."""

from __future__ import annotations

import re
from typing import Literal, TypedDict

_WINDOWS_DEVICE = re.compile(r'CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[1-9¹²³]', re.IGNORECASE)

PINNED_SQLGLOT = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"

ChangeStatus = Literal["added", "deleted", "modified", "renamed", "copied", "binary", "unknown"]


class FileChange(TypedDict):
    path: str
    old_path: str | None
    new_path: str | None
    status: ChangeStatus
    old_lines: list[int]
    new_lines: list[int]
    added_text: list[str]
    removed_text: list[str]
    uncertain: bool


class TestRecord(TypedDict):
    number: int
    id: str
    file: str
    seconds: float


def safe_path(path: object) -> bool:
    """Accept portable repository-relative paths without rewriting them."""
    return (
        isinstance(path, str)
        and not any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in path)
        and not any(char in path for char in '\\:*?"<>|')
        and all(
            part not in {"", ".", ".."}
            and not part.endswith((".", " "))
            and not _WINDOWS_DEVICE.fullmatch(part.split(".", 1)[0].rstrip())
            for part in path.split("/")
        )
    )
