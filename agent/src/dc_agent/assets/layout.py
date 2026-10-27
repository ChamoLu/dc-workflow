"""Deterministic business paths; original pageTitle/fileType remain the asset identity."""

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from ..domain import AgentError

BUCKETS = {
    **dict.fromkeys(("xls", "xlsx", "csv"), "excel"),
    **dict.fromkeys(("html", "htm", "shtml"), "html"),
    **dict.fromkeys(("md", "markdown", "pdf", "png", "jpg", "jpeg", "doc", "docx"), "md"),
}
RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


@dataclass(frozen=True)
class AssetLayout:
    page_title: str
    file_type: str

    def __post_init__(self):
        if self.file_type not in BUCKETS:
            raise AgentError("UNSUPPORTED_FILE_TYPE", "No registered asset route")
        stem = self.page_title.replace("\\", "_")
        if (
            not stem.strip()
            or stem in {".", ".."}
            or stem.endswith((" ", "."))
            or re.search(r'[<>:"/|?*\x00-\x1f]', stem)
            or stem.split(".", 1)[0].upper() in RESERVED
            or len((stem + self.extension).encode("utf-8")) > 255
        ):
            raise AgentError(
                "INVALID_ARTIFACT_NAME", "pageTitle cannot form a valid portable filename"
            )

    @property
    def extension(self) -> str:
        return ".md" if BUCKETS[self.file_type] == "md" else ".py"

    @property
    def filename(self) -> str:
        return self.page_title.replace("\\", "_") + self.extension

    @property
    def directory(self) -> Path:
        return Path(BUCKETS[self.file_type]) / self.file_type

    @property
    def current(self) -> Path:
        return self.directory / self.filename

    @property
    def path_key(self) -> str:
        # Consistent collision behavior when assets move between Windows and Linux.
        return unicodedata.normalize("NFC", self.current.as_posix()).casefold()
