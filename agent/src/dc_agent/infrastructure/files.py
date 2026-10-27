"""Atomic small JSON records."""

import os
from pathlib import Path

from ..domain import dumps


def filesystem_path(path: Path) -> Path:
    """Allow deeply nested version/task paths even when Windows long paths are disabled."""
    value = str(path.absolute())
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return path
    if value.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + value[2:])
    return Path("\\\\?\\" + value)


def write_json(path: Path, value: object) -> None:
    path = filesystem_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(dumps(value), encoding="utf-8")
    temporary.replace(path)
